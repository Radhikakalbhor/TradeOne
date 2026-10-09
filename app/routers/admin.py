import random
from datetime import datetime, timezone, timedelta
from typing import Optional
from fastapi import APIRouter, Depends, Request, Response, Form, HTTPException, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from app.config import settings
from app.database import get_db
from app.models import (
    User, DematAccount, Holding, Instrument, CorporateAction, Consent,
    DataSession, WebhookLog, ConsentAccessLog, AdminSetting, AuthMethod
)
from app.services.seed_service import seed_database
from app.services.webhook_service import trigger_webhook_event

router = APIRouter(prefix="/admin", tags=["Admin Controls"])
templates = Jinja2Templates(directory="app/templates")

def verify_admin_auth(request: Request) -> bool:
    admin_auth = request.cookies.get("nd_admin_auth")
    return admin_auth == "authenticated"

@router.get("", response_class=HTMLResponse)
def admin_dashboard(request: Request, db: Session = Depends(get_db)):
    if not verify_admin_auth(request):
        return templates.TemplateResponse(request=request, name="admin_login.html", context={"error": None})

    users = db.query(User).all()
    user_list = []
    for u in users:
        methods = [am.provider for am in u.auth_methods]
        acc_count = len(u.demat_accounts)
        total_val = sum(
            h.total_units * (h.instrument.last_price if h.instrument else 0.0)
            for acc in u.demat_accounts
            for h in acc.holdings
        )
        user_list.append({
            "id": u.id,
            "email": u.email,
            "name": u.name,
            "bo_id": u.bo_id,
            "masked_pan": u.masked_pan,
            "methods": methods,
            "account_count": acc_count,
            "total_val": total_val
        })

    # Consents
    consents = db.query(Consent).order_by(Consent.created_at.desc()).limit(20).all()
    # Webhook logs
    webhook_logs = db.query(WebhookLog).order_by(WebhookLog.timestamp.desc()).limit(25).all()
    # Access logs
    access_logs = db.query(ConsentAccessLog).order_by(ConsentAccessLog.timestamp.desc()).limit(25).all()
    # Instruments
    instruments = db.query(Instrument).all()

    # Settings
    admin_settings = {s.key: s.value for s in db.query(AdminSetting).all()}

    return templates.TemplateResponse(request=request, name="admin.html", context={
        "users": user_list,
        "consents": consents,
        "webhook_logs": webhook_logs,
        "access_logs": access_logs,
        "instruments": instruments,
        "settings": admin_settings
    })

@router.post("/login")
def admin_login(request: Request, password: str = Form(...)):
    if password == settings.ADMIN_PASSWORD:
        resp = RedirectResponse(url="/admin", status_code=303)
        resp.set_cookie("nd_admin_auth", "authenticated", httponly=True, max_age=3600)
        return resp
    return templates.TemplateResponse(request=request, name="admin_login.html", context={
        "error": "Invalid admin password."
    })

@router.get("/logout")
def admin_logout():
    resp = RedirectResponse(url="/admin", status_code=303)
    resp.delete_cookie("nd_admin_auth")
    return resp

@router.post("/settings/update")
def update_settings(
    request: Request,
    data_prep_delay: str = Form(...),
    fail_next_session: Optional[str] = Form(None),
    simulate_outage: Optional[str] = Form(None),
    slow_mode: Optional[str] = Form(None),
    db: Session = Depends(get_db)
):
    if not verify_admin_auth(request):
        raise HTTPException(status_code=401)

    s_delay = db.query(AdminSetting).filter(AdminSetting.key == "data_prep_delay").first()
    if s_delay:
        s_delay.value = str(max(0, int(data_prep_delay)))

    s_fail = db.query(AdminSetting).filter(AdminSetting.key == "fail_next_session").first()
    if s_fail:
        s_fail.value = "true" if fail_next_session else "false"

    s_outage = db.query(AdminSetting).filter(AdminSetting.key == "simulate_outage").first()
    if s_outage:
        # If toggling outage, set until now + 60s
        s_outage.value = "true" if simulate_outage else "false"

    s_slow = db.query(AdminSetting).filter(AdminSetting.key == "slow_mode").first()
    if s_slow:
        s_slow.value = "true" if slow_mode else "false"

    db.commit()
    return RedirectResponse(url="/admin", status_code=303)

@router.post("/consent/{consent_id}/action")
def admin_consent_action(
    consent_id: str,
    action: str = Form(...), # EXPIRE, REVOKE, PAUSE, RESUME
    request: Request = None,
    db: Session = Depends(get_db)
):
    if not verify_admin_auth(request):
        raise HTTPException(status_code=401)

    consent = db.query(Consent).filter(Consent.consent_id == consent_id).first()
    if consent:
        if action == "EXPIRE":
            consent.status = "EXPIRED"
            trigger_webhook_event(consent.webhook_url, "CONSENT_EXPIRED", consent.consent_id)
        elif action == "REVOKE":
            consent.status = "REVOKED"
            trigger_webhook_event(consent.webhook_url, "CONSENT_REVOKED", consent.consent_id)
        elif action == "PAUSE":
            consent.status = "PAUSED"
        elif action == "RESUME":
            consent.status = "ACTIVE"
        db.commit()

    return RedirectResponse(url="/admin", status_code=303)

@router.post("/corporate-action")
def simulate_corporate_action(
    isin: str = Form(...),
    action_type: str = Form(...), # DIVIDEND, BONUS, SPLIT
    ratio_or_amount: str = Form(...),
    description: str = Form(...),
    request: Request = None,
    db: Session = Depends(get_db)
):
    if not verify_admin_auth(request):
        raise HTTPException(status_code=401)

    ca = CorporateAction(
        isin=isin,
        action_type=action_type,
        record_date=datetime.now().strftime("%Y-%m-%d"),
        ratio_or_amount=ratio_or_amount,
        description=description
    )
    db.add(ca)

    # If bonus or split, optionally adjust holdings
    holdings = db.query(Holding).filter(Holding.isin == isin).all()
    if action_type == "BONUS" and "1:1" in ratio_or_amount:
        for h in holdings:
            h.free_units *= 2
    elif action_type == "SPLIT" and "1:2" in ratio_or_amount:
        for h in holdings:
            h.free_units *= 2
            h.avg_price /= 2

    db.commit()
    return RedirectResponse(url="/admin", status_code=303)

@router.post("/reset-user")
def reset_user(
    email: str = Form(...),
    request: Request = None,
    db: Session = Depends(get_db)
):
    if not verify_admin_auth(request):
        raise HTTPException(status_code=401)

    user = db.query(User).filter(User.email == email.lower().strip()).first()
    if user:
        db.delete(user)
        db.commit()
        # Reseed if demo user
        seed_database(db)

    return RedirectResponse(url="/admin", status_code=303)

@router.post("/reset-all")
def reset_all_data(
    request: Request = None,
    db: Session = Depends(get_db)
):
    if not verify_admin_auth(request):
        raise HTTPException(status_code=401)

    # Delete all users, accounts, consents
    db.query(ConsentAccessLog).delete()
    db.query(WebhookLog).delete()
    db.query(DataSession).delete()
    db.query(Consent).delete()
    db.query(Holding).delete()
    db.query(DematAccount).delete()
    db.query(User).delete()
    db.commit()

    seed_database(db)
    return RedirectResponse(url="/admin", status_code=303)


@router.post("/purge-stale-data")
def admin_purge_stale_data(
    request: Request,
    db: Session = Depends(get_db)
):
    if not verify_admin_auth(request):
        raise HTTPException(status_code=401)

    from scripts.purge_stale_data import purge_and_rebuild
    purge_and_rebuild(db)
    return RedirectResponse(url="/admin", status_code=303)


# User-Scoped Cross-Broker Portfolio Consistency Verification
def check_portfolio_consistency(db: Session, user: User) -> dict:
    """Verifies live broker state against TradeOne stored snapshots for the authenticated user.
    Scoped strictly to the authenticated user's account.
    """
    from app.services.broker_adapter import broker_adapter

    overall_in_sync = True
    total_differences = 0
    providers_result = {}

    for p_code in ("a", "b", "c"):
        config = broker_adapter.get_provider_config(p_code)
        dp_id = config["dp_id"]
        broker_name = config["broker_name"]

        db_acc = db.query(DematAccount).filter(
            DematAccount.user_id == user.id,
            DematAccount.dp_id == dp_id
        ).first()

        db_holdings = {}
        if db_acc:
            for h in db_acc.holdings:
                price = h.instrument.last_price if h.instrument else 0.0
                db_holdings[h.isin] = {
                    "isin": h.isin,
                    "symbol": h.instrument.symbol if h.instrument else h.isin,
                    "name": h.instrument.name if h.instrument else h.isin,
                    "quantity": h.total_units,
                    "value": h.total_units * price,
                    "last_price": price
                }

        # Live fetch from broker
        prof_ok, prof_data = broker_adapter.get_profile(p_code, user.email)
        hold_ok, hold_data = broker_adapter.get_holdings(p_code, user.email)
        summ_ok, summ_data = broker_adapter.get_summary(p_code, user.email)

        # 1. Identity Check
        identity_details = {}
        if prof_ok and isinstance(prof_data, dict):
            prof_name = prof_data.get("name", "").strip()
            prof_pan = prof_data.get("masked_pan", "").strip()
            prof_mobile = prof_data.get("mobile", "").strip()

            name_matches = (user.name.strip().lower() == prof_name.lower()) if prof_name else True
            pan_matches = (user.masked_pan.strip() == prof_pan) if prof_pan else True
            mobile_matches = (user.mobile.strip() == prof_mobile) if prof_mobile else True
            identity_match = (name_matches and pan_matches and mobile_matches)

            identity_details = {
                "status": "match" if identity_match else "mismatch",
                "broker_name": prof_name,
                "tradeone_name": user.name,
                "broker_pan": prof_pan,
                "tradeone_pan": user.masked_pan,
                "broker_mobile": prof_mobile,
                "tradeone_mobile": user.mobile
            }
        else:
            identity_details = {
                "status": "unavailable",
                "message": prof_data.get("message", "Profile unreachable") if isinstance(prof_data, dict) else "Profile unreachable"
            }

        # 2. Holdings Check
        live_holdings = {}
        if hold_ok and isinstance(hold_data, dict):
            for item in hold_data.get("holdings", []):
                isin = item["isin"]
                qty = float(item.get("free_units", item.get("quantity", item.get("total_units", 0.0))))
                price = float(item.get("last_price", 0.0))
                live_holdings[isin] = {
                    "isin": isin,
                    "symbol": item.get("symbol", isin),
                    "name": item.get("security_name", item.get("name", isin)),
                    "quantity": qty,
                    "value": qty * price,
                    "last_price": price
                }

        differences = []
        all_isins = set(live_holdings.keys()).union(set(db_holdings.keys()))
        in_sync_positions = []

        for isin in sorted(all_isins):
            in_broker = isin in live_holdings
            in_tradeone = isin in db_holdings

            if in_broker and in_tradeone:
                b_item = live_holdings[isin]
                t_item = db_holdings[isin]
                qty_diff = round(b_item["quantity"] - t_item["quantity"], 3)
                val_diff = round(b_item["value"] - t_item["value"], 2)

                if abs(qty_diff) > 0.001 or abs(val_diff) > 1.0:
                    differences.append({
                        "type": "QUANTITY_OR_VALUE_DIFF",
                        "isin": isin,
                        "symbol": b_item["symbol"],
                        "name": b_item["name"],
                        "broker_qty": b_item["quantity"],
                        "tradeone_qty": t_item["quantity"],
                        "qty_diff": qty_diff,
                        "broker_val": b_item["value"],
                        "tradeone_val": t_item["value"],
                        "val_diff": val_diff
                    })
                else:
                    in_sync_positions.append({
                        "isin": isin,
                        "symbol": b_item["symbol"],
                        "name": b_item["name"],
                        "broker_qty": b_item["quantity"],
                        "tradeone_qty": t_item["quantity"],
                        "value": t_item["value"]
                    })
            elif in_broker and not in_tradeone:
                b_item = live_holdings[isin]
                differences.append({
                    "type": "MISSING_IN_TRADEONE",
                    "isin": isin,
                    "symbol": b_item["symbol"],
                    "name": b_item["name"],
                    "broker_qty": b_item["quantity"],
                    "tradeone_qty": 0.0,
                    "qty_diff": b_item["quantity"],
                    "broker_val": b_item["value"],
                    "tradeone_val": 0.0,
                    "val_diff": b_item["value"]
                })
            elif in_tradeone and not in_broker:
                t_item = db_holdings[isin]
                differences.append({
                    "type": "EXTRA_IN_TRADEONE",
                    "isin": isin,
                    "symbol": t_item["symbol"],
                    "name": t_item["name"],
                    "broker_qty": 0.0,
                    "tradeone_qty": t_item["quantity"],
                    "qty_diff": -t_item["quantity"],
                    "broker_val": 0.0,
                    "tradeone_val": t_item["value"],
                    "val_diff": -t_item["value"]
                })

        broker_total_val = summ_data.get("current_value", 0.0) if (summ_ok and isinstance(summ_data, dict)) else sum(h["value"] for h in live_holdings.values())
        tradeone_total_val = sum(h["value"] for h in db_holdings.values())

        p_in_sync = (len(differences) == 0 and identity_details.get("status") in ("match", "unavailable"))
        if not p_in_sync:
            overall_in_sync = False
        total_differences += len(differences)

        providers_result[p_code] = {
            "provider": p_code,
            "broker_name": broker_name,
            "dp_id": dp_id,
            "url": config["url"],
            "in_sync": p_in_sync,
            "broker_total_val": broker_total_val,
            "tradeone_total_val": tradeone_total_val,
            "identity": identity_details,
            "differences": differences,
            "in_sync_positions": in_sync_positions
        }

    return {
        "overall_in_sync": overall_in_sync,
        "total_differences": total_differences,
        "providers": providers_result
    }

@router.get("/consistency", response_class=HTMLResponse)
def user_portfolio_consistency_page(
    request: Request,
    synced: Optional[str] = None,
    provider: Optional[str] = None,
    db: Session = Depends(get_db)
):
    from app.routers.depository import get_required_user
    from app.services.depository_service import format_inr
    user = get_required_user(request, db)
    consistency = check_portfolio_consistency(db, user)

    return templates.TemplateResponse(request=request, name="consistency.html", context={
        "user": user,
        "consistency": consistency,
        "synced": synced == "true",
        "provider": provider,
        "format_inr": format_inr
    })

@router.post("/consistency/resync")
def consistency_resync(
    request: Request,
    provider: str = Form("all"),
    db: Session = Depends(get_db)
):
    from app.routers.depository import get_required_user
    from app.services.broker_adapter import broker_adapter
    user = get_required_user(request, db)

    p_code = (provider or "").strip().lower()
    if p_code in ("a", "b", "c"):
        broker_adapter.sync_user_from_broker(db, user, p_code)
    else:
        broker_adapter.sync_all_brokers(db, user)

    return RedirectResponse(url=f"/admin/consistency?synced=true&provider={p_code}", status_code=303)

@router.get("/consistency/api")
def consistency_json_api(
    request: Request,
    db: Session = Depends(get_db)
):
    from app.routers.depository import get_required_user
    user = get_required_user(request, db)
    return check_portfolio_consistency(db, user)
