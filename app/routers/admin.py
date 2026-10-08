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
