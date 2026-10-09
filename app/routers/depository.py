import json
from datetime import datetime, timezone, timedelta
from typing import Optional, List
from fastapi import APIRouter, Depends, Request, Response, Form, Query, HTTPException, status
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from app.config import settings
from app.database import get_db
from app.models import (
    User, DematAccount, Holding, Instrument, Transaction, CorporateAction,
    Consent, ConsentAccessLog, Nominee, UserSession
)
from app.services.auth_service import get_current_user_from_session, terminate_all_user_sessions
from app.services.depository_service import (
    format_inr, get_portfolio_summary, get_user_holdings, export_transactions_csv
)
from app.services.cas_pdf_service import generate_cas_pdf
from app.services.webhook_service import trigger_webhook_event
from app.security import sign_data
from app.shared_identity import normalize_email

router = APIRouter(tags=["Depository UI"])
templates = Jinja2Templates(directory="app/templates")

def get_required_user(request: Request, db: Session = Depends(get_db)) -> User:
    token = request.cookies.get("nd_session")
    user = get_current_user_from_session(db, token)
    if not user:
        # Redirect to login with return URL
        raise HTTPException(
            status_code=status.HTTP_307_TEMPORARY_REDIRECT,
            headers={"Location": f"/auth/login?next={request.url.path}"}
        )
    return user

@router.get("/", response_class=HTMLResponse)
def index(request: Request, db: Session = Depends(get_db)):
    token = request.cookies.get("nd_session")
    user = get_current_user_from_session(db, token)
    if user:
        return RedirectResponse(url="/dashboard", status_code=303)
    return RedirectResponse(url="/auth/login", status_code=303)

@router.get("/dashboard", response_class=HTMLResponse)
def dashboard(
    request: Request,
    refresh: Optional[str] = Query(None),
    user: User = Depends(get_required_user),
    db: Session = Depends(get_db)
):
    from app.services.broker_adapter import broker_adapter

    # Auto-sync if refresh requested, no demat accounts, or any linked provider has sync > 60s old
    should_sync = (refresh in ("true", "1") or not user.demat_accounts)
    if not should_sync:
        for p_code, p_meta in settings.BROKER_PROVIDERS.items():
            acc = next((a for a in user.demat_accounts if a.dp_id == p_meta["dp_id"] or getattr(a, "provider_code", None) == p_code), None)
            if not acc or broker_adapter.is_provider_stale(acc, max_age_seconds=60):
                should_sync = True
                break

    if should_sync:
        broker_adapter.sync_all_brokers(db, user, only_stale=False)
        db.refresh(user)

    summary = get_portfolio_summary(db, user)
    holdings = get_user_holdings(db, user, merge_by_isin=True)[:6]

    active_consents_count = db.query(Consent).filter(
        Consent.user_id == user.id,
        Consent.status == "ACTIVE"
    ).count()

    return templates.TemplateResponse(request=request, name="dashboard.html", context={
        "user": user,
        "summary": summary,
        "top_holdings": holdings,
        "active_consents_count": active_consents_count,
        "format_inr": format_inr
    })

@router.get("/accounts", response_class=HTMLResponse)
def demat_accounts(
    request: Request,
    user: User = Depends(get_required_user),
    db: Session = Depends(get_db)
):
    accounts = db.query(DematAccount).filter(DematAccount.user_id == user.id).all()
    account_cards = []

    for acc in accounts:
        h_count = len(acc.holdings)
        val = sum(h.total_units * (h.instrument.last_price if h.instrument else 0.0) for h in acc.holdings)
        account_cards.append({
            "account": acc,
            "holding_count": h_count,
            "total_value": val
        })

    return templates.TemplateResponse(request=request, name="accounts.html", context={
        "user": user,
        "account_cards": account_cards,
        "format_inr": format_inr
    })

@router.get("/accounts/{account_id}", response_class=HTMLResponse)
def account_detail(
    account_id: str,
    request: Request,
    user: User = Depends(get_required_user),
    db: Session = Depends(get_db)
):
    acc = db.query(DematAccount).filter(
        DematAccount.id == account_id,
        DematAccount.user_id == user.id
    ).first()

    if not acc:
        raise HTTPException(status_code=404, detail="Demat account not found")

    holdings = []
    total_val = 0.0
    for h in acc.holdings:
        if h.instrument:
            val = h.total_units * h.instrument.last_price
            total_val += val
            holdings.append({
                "holding": h,
                "current_value": val
            })

    return templates.TemplateResponse(request=request, name="account_detail.html", context={
        "user": user,
        "account": acc,
        "holdings": holdings,
        "total_value": total_val,
        "format_inr": format_inr
    })

@router.get("/holdings", response_class=HTMLResponse)
def holdings_view(
    request: Request,
    dp: Optional[str] = Query(None),
    asset_class: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    merge: Optional[str] = Query(None),
    user: User = Depends(get_required_user),
    db: Session = Depends(get_db)
):
    merge_by_isin = (merge == "true" or merge == "1")
    holdings_list = get_user_holdings(
        db, user,
        dp_filter=dp,
        asset_filter=asset_class,
        search=search,
        merge_by_isin=merge_by_isin
    )

    # Distinct DPs for filter dropdown
    user_dps = [acc.dp_name for acc in user.demat_accounts]

    return templates.TemplateResponse(request=request, name="holdings.html", context={
        "user": user,
        "holdings": holdings_list,
        "dp_filter": dp or "",
        "asset_filter": asset_class or "",
        "search": search or "",
        "merge_by_isin": merge_by_isin,
        "user_dps": user_dps,
        "format_inr": format_inr
    })

@router.get("/statement", response_class=HTMLResponse)
def statement_view(
    request: Request,
    account_id: Optional[str] = Query(None),
    txn_type: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    user: User = Depends(get_required_user),
    db: Session = Depends(get_db)
):
    acc_ids = [a.id for a in user.demat_accounts]
    query = db.query(Transaction).filter(Transaction.demat_account_id.in_(acc_ids))

    if account_id:
        query = query.filter(Transaction.demat_account_id == account_id)
    if txn_type:
        query = query.filter(Transaction.trans_type == txn_type)
    if search:
        s = f"%{search.lower()}%"
        query = query.join(Instrument).filter(
            (Instrument.name.ilike(s)) | (Instrument.symbol.ilike(s)) | (Transaction.isin.ilike(s))
        )

    transactions = query.order_by(Transaction.trans_date.desc()).limit(100).all()

    return templates.TemplateResponse(request=request, name="transactions.html", context={
        "user": user,
        "transactions": transactions,
        "account_id": account_id or "",
        "txn_type": txn_type or "",
        "search": search or "",
        "accounts": user.demat_accounts,
        "format_inr": format_inr
    })

@router.get("/statement/export.csv")
def export_statement_csv(
    account_id: Optional[str] = Query(None),
    user: User = Depends(get_required_user),
    db: Session = Depends(get_db)
):
    acc_ids = [a.id for a in user.demat_accounts]
    query = db.query(Transaction).filter(Transaction.demat_account_id.in_(acc_ids))
    if account_id:
        query = query.filter(Transaction.demat_account_id == account_id)

    transactions = query.order_by(Transaction.trans_date.desc()).all()
    csv_data = export_transactions_csv(transactions)

    filename = f"TradeOne_Statement_{datetime.now().strftime('%Y%m%d')}.csv"
    return Response(
        content=csv_data,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )

@router.get("/cas", response_class=HTMLResponse)
def cas_page(
    request: Request,
    user: User = Depends(get_required_user)
):
    pan = user.masked_pan or "ABCXX1234X"
    pan_digits = "".join([c for c in pan if c.isdigit()])[-4:] or "1234"
    dob = user.dob or "15081992"
    ddmm = dob[:4]
    password_hint = f"{pan_digits}{ddmm}"

    return templates.TemplateResponse(request=request, name="cas.html", context={
        "user": user,
        "password_hint": password_hint
    })

@router.get("/cas/download.pdf")
def download_cas_pdf(
    months: int = Query(6),
    user: User = Depends(get_required_user),
    db: Session = Depends(get_db)
):
    now = datetime.now(timezone.utc)
    start_date = now - timedelta(days=months * 30)

    from_str = start_date.strftime("%d-%b-%Y")
    to_str = now.strftime("%d-%b-%Y")

    accounts = db.query(DematAccount).filter(DematAccount.user_id == user.id).all()
    acc_ids = [a.id for a in accounts]

    transactions = db.query(Transaction).filter(
        Transaction.demat_account_id.in_(acc_ids),
        Transaction.trans_date >= start_date
    ).order_by(Transaction.trans_date.desc()).all()

    pdf_bytes = generate_cas_pdf(
        user=user,
        accounts=accounts,
        transactions=transactions,
        from_date_str=from_str,
        to_date_str=to_str
    )

    filename = f"TradeOne_CAS_{user.bo_id}_{now.strftime('%Y%m%d')}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )

@router.get("/corporate-actions", response_class=HTMLResponse)
def corporate_actions_view(
    request: Request,
    user: User = Depends(get_required_user),
    db: Session = Depends(get_db)
):
    # Find all ISINs user holds
    acc_ids = [a.id for a in user.demat_accounts]
    user_isins = {h.isin for h in db.query(Holding).filter(Holding.demat_account_id.in_(acc_ids)).all()}

    actions = db.query(CorporateAction).order_by(CorporateAction.record_date.desc()).all()

    return templates.TemplateResponse(request=request, name="corporate_actions.html", context={
        "user": user,
        "actions": actions,
        "user_isins": user_isins
    })

@router.get("/profile", response_class=HTMLResponse)
def profile_view(
    request: Request,
    success: Optional[str] = Query(None),
    user: User = Depends(get_required_user)
):
    nominee = user.nominees[0] if user.nominees else None
    return templates.TemplateResponse(request=request, name="profile.html", context={
        "user": user,
        "nominee": nominee,
        "success": success
    })

@router.post("/profile/nominee")
def update_nominee(
    name: str = Form(...),
    relationship_type: str = Form(...),
    percentage: int = Form(...),
    dob: Optional[str] = Form(None),
    user: User = Depends(get_required_user),
    db: Session = Depends(get_db)
):
    nominee = user.nominees[0] if user.nominees else None
    if not nominee:
        nominee = Nominee(user_id=user.id)
        db.add(nominee)

    nominee.name = name.strip()
    nominee.relationship_type = relationship_type
    nominee.percentage = percentage
    nominee.dob = dob
    db.commit()

    return RedirectResponse(url="/profile?success=Nominee+details+updated+successfully", status_code=303)

@router.get("/consents", response_class=HTMLResponse)
def consents_view(
    request: Request,
    user: User = Depends(get_required_user),
    db: Session = Depends(get_db)
):
    consents = db.query(Consent).filter(Consent.user_id == user.id).order_by(Consent.created_at.desc()).all()
    consent_ids = [c.consent_id for c in consents]

    logs = db.query(ConsentAccessLog).filter(ConsentAccessLog.consent_id.in_(consent_ids)).order_by(
        ConsentAccessLog.timestamp.desc()
    ).limit(50).all()

    return templates.TemplateResponse(request=request, name="consents.html", context={
        "user": user,
        "consents": consents,
        "logs": logs
    })

@router.post("/consents/{consent_id}/toggle-pause")
def toggle_consent_pause(
    consent_id: str,
    user: User = Depends(get_required_user),
    db: Session = Depends(get_db)
):
    consent = db.query(Consent).filter(Consent.consent_id == consent_id, Consent.user_id == user.id).first()
    if consent:
        consent.status = "PAUSED" if consent.status == "ACTIVE" else "ACTIVE"
        db.commit()
    return RedirectResponse(url="/consents", status_code=303)

@router.post("/consents/{consent_id}/revoke")
def revoke_consent(
    consent_id: str,
    user: User = Depends(get_required_user),
    db: Session = Depends(get_db)
):
    consent = db.query(Consent).filter(Consent.consent_id == consent_id, Consent.user_id == user.id).first()
    if consent:
        consent.status = "REVOKED"
        db.commit()
        trigger_webhook_event(consent.webhook_url, "CONSENT_REVOKED", consent.consent_id)
    return RedirectResponse(url="/consents", status_code=303)

@router.get("/consent/approve", response_class=HTMLResponse)
def consent_approve_page(
    request: Request,
    handle: str = Query(...),
    db: Session = Depends(get_db)
):
    token = request.cookies.get("nd_session")
    user = get_current_user_from_session(db, token)
    if not user:
        return RedirectResponse(url=f"/auth/login?next=/consent/approve%3Fhandle={handle}", status_code=303)

    consent = db.query(Consent).filter(Consent.consent_handle == handle).first()
    if not consent:
        raise HTTPException(status_code=404, detail="Consent request not found")

    fi_types = json.loads(consent.fi_types_json) if consent.fi_types_json else []
    return templates.TemplateResponse(request=request, name="consent_approve.html", context={
        "user": user,
        "consent": consent,
        "fi_types": fi_types,
        "accounts": user.demat_accounts
    })

@router.post("/consent/approve")
def handle_consent_approval(
    handle: str = Form(...),
    action: str = Form(...), # APPROVE or REJECT
    account_ids: List[str] = Form([]),
    user: User = Depends(get_required_user),
    db: Session = Depends(get_db)
):
    consent = db.query(Consent).filter(Consent.consent_handle == handle).first()
    if not consent:
        raise HTTPException(status_code=404, detail="Consent request not found")

    now = datetime.now(timezone.utc)

    if action == "REJECT":
        consent.status = "REJECTED"
        db.commit()
        trigger_webhook_event(consent.webhook_url, "CONSENT_REJECTED", consent.consent_id)
        separator = "&" if "?" in consent.redirect_url else "?"
        return RedirectResponse(url=f"{consent.redirect_url}{separator}handle={handle}&status=REJECTED", status_code=303)

    # Approve
    consent.status = "ACTIVE"
    consent.user_id = user.id
    consent.approved_at = now
    consent.expires_at = now + timedelta(days=consent.consent_duration_days)
    consent.selected_account_ids_json = json.dumps(account_ids)

    # Create signed artefact
    artefact_dict = {
        "consentId": consent.consent_id,
        "consentHandle": consent.consent_handle,
        "clientId": consent.client_id,
        "customerEmail": user.email,
        "purpose": {"code": consent.purpose_code, "text": consent.purpose_text},
        "fiTypes": json.loads(consent.fi_types_json) if consent.fi_types_json else [],
        "dataRange": {"from": consent.data_from, "to": consent.data_to},
        "approvedAt": now.isoformat(),
        "expiresAt": consent.expires_at.isoformat(),
        "fetchFrequency": {"unit": consent.fetch_frequency_unit, "value": consent.fetch_frequency_value},
        "selectedAccounts": account_ids
    }
    signature = sign_data(artefact_dict)
    artefact_dict["signature"] = signature

    consent.artefact_json = json.dumps(artefact_dict)
    consent.signature = signature
    db.commit()

    # Fire webhook
    trigger_webhook_event(consent.webhook_url, "CONSENT_APPROVED", consent.consent_id)

    separator = "&" if "?" in consent.redirect_url else "?"
    return RedirectResponse(url=f"{consent.redirect_url}{separator}handle={handle}&status=ACTIVE", status_code=303)

@router.get("/security", response_class=HTMLResponse)
def security_page(
    request: Request,
    user: User = Depends(get_required_user),
    db: Session = Depends(get_db)
):
    sessions = db.query(UserSession).filter(UserSession.user_id == user.id).order_by(UserSession.last_activity.desc()).limit(15).all()
    current_session_token = request.cookies.get("nd_session")

    return templates.TemplateResponse(request=request, name="security.html", context={
        "user": user,
        "sessions": sessions
    })

@router.post("/security/logout-all")
def logout_all_sessions(
    user: User = Depends(get_required_user),
    db: Session = Depends(get_db)
):
    terminate_all_user_sessions(db, user.id)
    resp = RedirectResponse(url="/auth/login", status_code=303)
    resp.delete_cookie("nd_session")
    return resp

def serialize_user_profile(user: User, db: Session) -> dict:
    """Standardized serialization of user profile used by public and internal endpoints."""
    primary_acc = user.demat_accounts[0] if user.demat_accounts else None
    return {
        "name": user.name,
        "email": normalize_email(user.email),
        "client_code": user.bo_id,
        "masked_pan": user.masked_pan,
        "masked_demat_number": primary_acc.masked_account_number if primary_acc else "XXXX0000",
        "dp_name": primary_acc.dp_name if primary_acc else settings.DP_NAME,
        "dp_id": primary_acc.dp_id if primary_acc else settings.DP_ID,
        "mobile": user.mobile
    }

def serialize_user_holdings(
    db: Session,
    user: User,
    dp: Optional[str] = None,
    page: int = 1,
    page_size: int = 50,
    merge_by_isin: bool = False
) -> dict:
    """Standardized serialization of user holdings used by public and internal endpoints."""
    all_holdings = get_user_holdings(db, user, dp_filter=dp, merge_by_isin=merge_by_isin)
    total = len(all_holdings)
    start = max(0, (page - 1) * page_size)
    end = start + page_size
    items = all_holdings[start:end]
    total_pages = (total + page_size - 1) // page_size if total > 0 else 1

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
        "holdings": items
    }

# Public JSON endpoints for authenticated users
@router.get("/api/v1/profile")
def get_public_profile(
    user: User = Depends(get_required_user),
    db: Session = Depends(get_db)
):
    """Public profile endpoint for the currently authenticated session."""
    return serialize_user_profile(user, db)

@router.get("/api/v1/holdings")
def get_public_holdings(
    dp: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    merge: Optional[str] = Query(None),
    user: User = Depends(get_required_user),
    db: Session = Depends(get_db)
):
    """Public holdings endpoint for the currently authenticated session."""
    merge_by_isin = (merge == "true" or merge == "1")
    return serialize_user_holdings(db, user, dp=dp, page=page, page_size=page_size, merge_by_isin=merge_by_isin)

@router.post("/accounts/sync/{provider_code}")
def sync_individual_broker(
    provider_code: str,
    request: Request,
    user: User = Depends(get_required_user),
    db: Session = Depends(get_db)
):
    """Sync or retry a single broker provider."""
    from app.services.broker_adapter import broker_adapter
    p_code = (provider_code or "").lower().strip()
    res = broker_adapter.sync_user_from_broker(db, user, p_code)

    if request.headers.get("accept") == "application/json" or request.headers.get("x-requested-with") == "XMLHttpRequest":
        return res

    referrer = request.headers.get("referer", "/dashboard")
    return RedirectResponse(url=referrer, status_code=303)

@router.post("/accounts/sync-all")
@router.get("/accounts/sync-all")
def sync_all_brokers_endpoint(
    request: Request,
    user: User = Depends(get_required_user),
    db: Session = Depends(get_db)
):
    """Sync all broker providers."""
    from app.services.broker_adapter import broker_adapter
    res = broker_adapter.sync_all_brokers(db, user, only_stale=False)

    if request.headers.get("accept") == "application/json" or request.headers.get("x-requested-with") == "XMLHttpRequest":
        return res

    referrer = request.headers.get("referer", "/dashboard")
    return RedirectResponse(url=referrer, status_code=303)

@router.get("/verify", response_class=HTMLResponse)
def verify_page(
    request: Request,
    user: User = Depends(get_required_user),
    db: Session = Depends(get_db)
):
    """Live verification of stored TradeOne rows against external brokers."""
    import time
    from decimal import Decimal
    from app.services.broker_adapter import broker_adapter
    from app.services.depository_service import parse_decimal

    norm_email = user.email.strip().lower()
    providers_verification = []
    overall_matched = True

    for p_code in ("a", "b", "c"):
        config = broker_adapter.get_provider_config(p_code)
        dp_id = config["dp_id"]
        broker_name = config["broker_name"]

        # 1. Fetch live from broker
        t0 = time.time()
        hold_ok, hold_data = broker_adapter.get_holdings(p_code, norm_email)
        summ_ok, summ_data = broker_adapter.get_summary(p_code, norm_email)
        elapsed_ms = round((time.time() - t0) * 1000, 1)

        http_status = 200 if hold_ok else (
            hold_data.get("status_code") or hold_data.get("_status_code") or 500
        )
        if hold_data.get("code") == "USER_NOT_FOUND" or http_status == 404:
            http_status = 404

        # 2. Stored holdings in TradeOne SQLite
        acc = db.query(DematAccount).filter(
            DematAccount.user_id == user.id,
            DematAccount.dp_id == dp_id
        ).first()

        stored_map = {}
        if acc:
            for h in acc.holdings:
                qty = parse_decimal(h.total_units)
                if qty > Decimal("0"):
                    last_p = parse_decimal(h.instrument.last_price if h.instrument else h.avg_price)
                    avg_p = parse_decimal(h.avg_price)
                    stored_map[h.isin] = {
                        "isin": h.isin,
                        "symbol": h.instrument.symbol if h.instrument else h.isin,
                        "name": h.instrument.name if h.instrument else h.isin,
                        "quantity": qty,
                        "avg_price": avg_p,
                        "value": qty * last_p
                    }

        # 3. Parse live holdings
        live_map = {}
        raw_list = []
        if hold_ok:
            if isinstance(hold_data, list):
                raw_list = hold_data
            elif isinstance(hold_data, dict):
                raw_list = (
                    hold_data.get("holdings") or
                    (hold_data.get("data") if isinstance(hold_data.get("data"), list) else (hold_data.get("data", {}).get("holdings") if isinstance(hold_data.get("data"), dict) else [])) or
                    []
                )
                if not isinstance(raw_list, list):
                    raw_list = []

        for raw_h in raw_list:
            scrip = raw_h.get("scrip", {}) if isinstance(raw_h.get("scrip"), dict) else {}
            isin = raw_h.get("isin") or raw_h.get("ISIN") or scrip.get("ISIN") or scrip.get("isin") or ""
            if not isin:
                continue

            raw_qty = raw_h.get("free_units") if raw_h.get("free_units") is not None else (
                raw_h.get("quantity") if raw_h.get("quantity") is not None else (
                    raw_h.get("qty") if raw_h.get("qty") is not None else raw_h.get("total_units")
                )
            )
            qty = parse_decimal(raw_qty)
            if qty <= Decimal("0"):
                continue

            # Price parsing with paise conversion
            if "last_price_paise" in raw_h and raw_h["last_price_paise"] is not None:
                price = parse_decimal(raw_h["last_price_paise"]) / Decimal("100")
            elif "last_price" in raw_h and raw_h["last_price"] is not None:
                price = parse_decimal(raw_h["last_price"])
            elif "ltp" in raw_h and raw_h["ltp"] is not None:
                price = parse_decimal(raw_h["ltp"])
            else:
                price = Decimal("0")

            if "avg_price_paise" in raw_h and raw_h["avg_price_paise"] is not None:
                avg_p = parse_decimal(raw_h["avg_price_paise"]) / Decimal("100")
            elif "avg_price" in raw_h and raw_h["avg_price"] is not None:
                avg_p = parse_decimal(raw_h["avg_price"])
            elif "avg_cost" in raw_h and raw_h["avg_cost"] is not None:
                avg_p = parse_decimal(raw_h["avg_cost"])
            elif "average_price" in raw_h and raw_h["average_price"] is not None:
                avg_p = parse_decimal(raw_h["average_price"])
            else:
                avg_p = price

            sym = raw_h.get("symbol") or raw_h.get("tradingsymbol") or scrip.get("symbol") or isin
            name = raw_h.get("security_name") or raw_h.get("name") or scrip.get("name") or sym

            live_map[isin] = {
                "isin": isin,
                "symbol": sym,
                "name": name,
                "quantity": qty,
                "avg_price": avg_p,
                "value": qty * price
            }

        # 4. Compare stored vs live rows
        comparison_rows = []
        all_isins = set(stored_map.keys()).union(set(live_map.keys()))
        provider_matched = True

        for isin in sorted(all_isins):
            s_item = stored_map.get(isin)
            l_item = live_map.get(isin)

            s_qty = s_item["quantity"] if s_item else Decimal("0")
            l_qty = l_item["quantity"] if l_item else Decimal("0")
            s_avg = s_item["avg_price"] if s_item else Decimal("0")
            l_avg = l_item["avg_price"] if l_item else Decimal("0")
            s_val = s_item["value"] if s_item else Decimal("0")
            l_val = l_item["value"] if l_item else Decimal("0")

            sym = (l_item or s_item)["symbol"]
            name = (l_item or s_item)["name"]

            qty_matches = (abs(s_qty - l_qty) < Decimal("0.001"))
            avg_matches = (abs(s_avg - l_avg) < Decimal("0.05"))
            val_matches = (abs(s_val - l_val) < Decimal("1.00"))
            row_match = (qty_matches and avg_matches and val_matches)

            if not row_match:
                provider_matched = False
                overall_matched = False

            comparison_rows.append({
                "isin": isin,
                "symbol": sym,
                "name": name,
                "stored_qty": s_qty,
                "live_qty": l_qty,
                "stored_avg": s_avg,
                "live_avg": l_avg,
                "stored_val": s_val,
                "live_val": l_val,
                "qty_diff": l_qty - s_qty,
                "val_diff": l_val - s_val,
                "status": "Match" if row_match else "Mismatch",
                "is_match": row_match
            })

        if http_status == 404:
            # No account on this broker
            if len(stored_map) > 0:
                provider_matched = False
                overall_matched = False

        stored_total = sum((r["stored_val"] for r in comparison_rows), Decimal("0"))
        live_total = sum((r["live_val"] for r in comparison_rows), Decimal("0"))

        providers_verification.append({
            "provider": p_code,
            "broker_name": broker_name,
            "dp_id": dp_id,
            "http_status": http_status,
            "response_time_ms": elapsed_ms,
            "rows": comparison_rows,
            "all_matched": provider_matched and (http_status in (200, 404)),
            "is_reachable": hold_ok or http_status == 404,
            "stored_total": stored_total,
            "live_total": live_total,
            "error": hold_data.get("message") if not hold_ok and http_status != 404 else None
        })

    return templates.TemplateResponse(request=request, name="verify.html", context={
        "user": user,
        "providers": providers_verification,
        "overall_matched": overall_matched,
        "format_inr": format_inr
    })

@router.post("/verify/resync/{provider_code}")
def verify_resync_individual(
    provider_code: str,
    request: Request,
    user: User = Depends(get_required_user),
    db: Session = Depends(get_db)
):
    from app.services.broker_adapter import broker_adapter
    p_code = (provider_code or "").lower().strip()
    broker_adapter.sync_user_from_broker(db, user, p_code)
    return RedirectResponse(url="/verify", status_code=303)

@router.post("/verify/resync-all")
def verify_resync_all(
    request: Request,
    user: User = Depends(get_required_user),
    db: Session = Depends(get_db)
):
    from app.services.broker_adapter import broker_adapter
    broker_adapter.sync_all_brokers(db, user, only_stale=False)
    return RedirectResponse(url="/verify", status_code=303)
