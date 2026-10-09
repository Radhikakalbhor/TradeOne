from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, Depends, Request, Response, Form, Query, HTTPException, BackgroundTasks
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from app.config import settings
from app.database import get_db, SessionLocal
from app.models import User, UserSession, EmailOtp
from app.services.auth_service import (
    request_email_otp, verify_email_otp, match_or_create_user,
    create_user_session, terminate_session, terminate_all_user_sessions,
    get_current_user_from_session, get_dev_test_otp
)
from app.services.oidc_service import (
    get_google_auth_url, validate_and_consume_state, exchange_google_code
)
from app.security import is_email_allowed, is_request_secure, verify_secret
from app.shared_identity import normalize_email

router = APIRouter(prefix="/auth", tags=["Authentication"])
templates = Jinja2Templates(directory="app/templates")

def _background_sync_brokers(user_id: int):
    """Run broker data synchronization asynchronously in the background on every login."""
    bg_db = SessionLocal()
    try:
        bg_user = bg_db.query(User).filter(User.id == user_id).first()
        if bg_user:
            from app.services.broker_adapter import broker_adapter
            broker_adapter.sync_all_brokers(bg_db, bg_user, only_stale=False)
    except Exception:
        pass
    finally:
        bg_db.close()

def set_auth_cookie(response: Response, session_token: str, request: Request):
    secure = is_request_secure(request)
    response.set_cookie(
        key="nd_session",
        value=session_token,
        httponly=True,
        samesite="lax",
        secure=secure,
        max_age=86400 * 7
    )

@router.get("/login", response_class=HTMLResponse)
def login_page(
    request: Request,
    email: Optional[str] = Query(None),
    error: Optional[str] = Query(None),
    next: Optional[str] = Query(None),
    db: Session = Depends(get_db)
):
    # If already logged in, redirect to next or dashboard
    session_token = request.cookies.get("nd_session")
    if session_token:
        user = get_current_user_from_session(db, session_token)
        if user:
            return RedirectResponse(url=next or "/dashboard", status_code=303)

    return templates.TemplateResponse(request=request, name="login.html", context={
        "email": email or "",
        "error": error,
        "next": next or "",
        "otp_dev_mode": settings.OTP_DEV_MODE,
        "has_google": bool(settings.GOOGLE_CLIENT_ID)
    })

@router.post("/email/request-otp")
def handle_request_otp(
    request: Request,
    email: str = Form(...),
    next: Optional[str] = Form(None),
    db: Session = Depends(get_db)
):
    email = normalize_email(email)
    success, message, _ = request_email_otp(db, email)
    if not success:
        return templates.TemplateResponse(request=request, name="login.html", context={
            "email": email,
            "error": message,
            "next": next or "",
            "otp_dev_mode": settings.OTP_DEV_MODE,
            "has_google": bool(settings.GOOGLE_CLIENT_ID)
        })

    redirect_url = f"/auth/email/verify?email={email}"
    if next:
        redirect_url += f"&next={next}"

    return RedirectResponse(url=redirect_url, status_code=303)

@router.get("/email/verify", response_class=HTMLResponse)
def verify_otp_page(
    request: Request,
    email: str = Query(...),
    next: Optional[str] = Query(None),
    error: Optional[str] = Query(None)
):
    email = normalize_email(email)
    return templates.TemplateResponse(request=request, name="otp_verify.html", context={
        "email": email,
        "next": next or "",
        "error": error,
        "expiry_minutes": settings.OTP_EXPIRY_MINUTES,
        "resend_seconds": 45
    })

@router.post("/email/verify-otp")
def handle_verify_otp(
    request: Request,
    background_tasks: BackgroundTasks,
    email: str = Form(...),
    otp_code: Optional[str] = Form(None),
    digit_1: Optional[str] = Form(None),
    digit_2: Optional[str] = Form(None),
    digit_3: Optional[str] = Form(None),
    digit_4: Optional[str] = Form(None),
    digit_5: Optional[str] = Form(None),
    digit_6: Optional[str] = Form(None),
    next: Optional[str] = Form(None),
    db: Session = Depends(get_db)
):
    email = normalize_email(email)
    raw_code = (otp_code or "").strip()
    if not raw_code:
        # Fallback to individual digit inputs if JS didn't populate hidden input
        raw_code = f"{digit_1 or ''}{digit_2 or ''}{digit_3 or ''}{digit_4 or ''}{digit_5 or ''}{digit_6 or ''}".strip()

    success, message, user = verify_email_otp(db, email, raw_code)

    if not success or not user:
        # Check for idempotent handling of duplicate concurrent POST requests for a recently accepted valid OTP
        # (e.g. from network retries or browser duplicate submissions within a 30-second window)
        now_utc = datetime.now(timezone.utc)
        recent_otp = db.query(EmailOtp).filter(
            EmailOtp.email == email,
            EmailOtp.used == True,
            EmailOtp.attempts < 5
        ).order_by(EmailOtp.id.desc()).first()

        if recent_otp and raw_code and len(raw_code) == 6 and verify_secret(raw_code, recent_otp.code_hash):
            otp_exp = recent_otp.expires_at
            if otp_exp.tzinfo is None:
                otp_exp = otp_exp.replace(tzinfo=timezone.utc)
            otp_created = recent_otp.created_at
            if otp_created.tzinfo is None:
                otp_created = otp_created.replace(tzinfo=timezone.utc)

            # Idempotent grace window: allow duplicate/concurrent submission of valid code within 120 seconds of creation
            if now_utc <= otp_exp and (now_utc - otp_created).total_seconds() <= 120:
                user = match_or_create_user(db, email=email, provider="email")
                success = True

    if not success or not user:
        return templates.TemplateResponse(request=request, name="otp_verify.html", context={
            "email": email,
            "next": next or "",
            "error": message,
            "expiry_minutes": settings.OTP_EXPIRY_MINUTES,
            "resend_seconds": 45
        })

    client_ip = request.client.host if request.client else ""
    user_agent = request.headers.get("user-agent", "")
    token = create_user_session(db, user, ip_address=client_ip, user_agent=user_agent)

    if settings.DEMO_AUTO_LINK:
        background_tasks.add_task(_background_sync_brokers, user.id)

    resp = RedirectResponse(url=next or "/dashboard", status_code=303)
    set_auth_cookie(resp, token, request)
    return resp

@router.post("/demo-login")
def demo_login(
    request: Request,
    email: str = Form(...),
    next: Optional[str] = Form(None),
    db: Session = Depends(get_db)
):
    """Direct demo login for Aarav Mehta or Priya Nair when OTP_DEV_MODE is true."""
    if not settings.OTP_DEV_MODE:
        return RedirectResponse(url="/auth/login?error=Demo+logins+are+disabled", status_code=303)

    email = normalize_email(email)
    user = match_or_create_user(db, email=email, provider="email")

    client_ip = request.client.host if request.client else ""
    user_agent = request.headers.get("user-agent", "")
    token = create_user_session(db, user, ip_address=client_ip, user_agent=user_agent)

    resp = RedirectResponse(url=next or "/dashboard", status_code=303)
    set_auth_cookie(resp, token, request)
    return resp

@router.get("/google")
def google_auth_redirect():
    if not settings.GOOGLE_CLIENT_ID:
        return RedirectResponse(url="/auth/login?error=Google+login+is+not+configured+yet", status_code=303)
    url = get_google_auth_url()
    return RedirectResponse(url=url, status_code=303)

@router.get("/google/callback")
async def google_callback(
    request: Request,
    background_tasks: BackgroundTasks,
    code: Optional[str] = Query(None),
    state: Optional[str] = Query(None),
    error: Optional[str] = Query(None),
    db: Session = Depends(get_db)
):
    if error or not code or not state:
        return RedirectResponse(url=f"/auth/login?error={error or 'Google login cancelled'}", status_code=303)

    nonce = validate_and_consume_state(state, "google")
    if not nonce:
        return RedirectResponse(url="/auth/login?error=Invalid+or+expired+security+state", status_code=303)

    success, msg, profile = await exchange_google_code(code, nonce)
    if not success or not profile:
        return RedirectResponse(url=f"/auth/login?error={msg}", status_code=303)

    email = normalize_email(profile["email"])
    allowed, allow_msg = is_email_allowed(email)
    if not allowed:
        return RedirectResponse(url=f"/auth/login?error={allow_msg}", status_code=303)

    user = match_or_create_user(
        db,
        email=email,
        provider="google",
        provider_sub=profile["provider_sub"],
        name=profile.get("name")
    )

    # Sync and provision across all three brokers for unified portfolio view
    background_tasks.add_task(_background_sync_brokers, user.id)

    client_ip = request.client.host if request.client else ""
    user_agent = request.headers.get("user-agent", "")
    token = create_user_session(db, user, ip_address=client_ip, user_agent=user_agent)

    resp = RedirectResponse(url="/dashboard", status_code=303)
    set_auth_cookie(resp, token, request)
    return resp


@router.get("/logout")
def logout(request: Request, db: Session = Depends(get_db)):
    session_token = request.cookies.get("nd_session")
    terminate_session(db, session_token)
    resp = RedirectResponse(url="/auth/login", status_code=303)
    resp.delete_cookie("nd_session")
    return resp

@router.get("/dev/test-otp")
def dev_test_otp(email: str = Query(...)):
    """Development-only testing endpoint separate from normal login flow."""
    if not settings.OTP_DEV_MODE:
        raise HTTPException(status_code=403, detail="Development OTP testing is disabled in production.")
    code = get_dev_test_otp(email)
    if not code:
        raise HTTPException(status_code=404, detail="No active development code found.")
    return {"email": email, "code": code}
