from typing import Optional
from fastapi import APIRouter, Depends, Request, Response, Form, Query, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from app.config import settings
from app.database import get_db
from app.models import User, UserSession
from app.services.auth_service import (
    request_email_otp, verify_email_otp, match_or_create_user,
    create_user_session, terminate_session, terminate_all_user_sessions,
    get_current_user_from_session
)
from app.services.oidc_service import (
    get_google_auth_url, get_microsoft_auth_url,
    validate_and_consume_state, exchange_google_code, exchange_microsoft_code
)
from app.security import is_email_allowed, is_request_secure

router = APIRouter(prefix="/auth", tags=["Authentication"])
templates = Jinja2Templates(directory="app/templates")

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

    return templates.TemplateResponse("login.html", {
        "request": request,
        "email": email or "",
        "error": error,
        "next": next or "",
        "otp_dev_mode": settings.OTP_DEV_MODE,
        "has_google": bool(settings.GOOGLE_CLIENT_ID),
        "has_microsoft": bool(settings.MICROSOFT_CLIENT_ID)
    })

@router.post("/email/request-otp")
def handle_request_otp(
    request: Request,
    email: str = Form(...),
    next: Optional[str] = Form(None),
    db: Session = Depends(get_db)
):
    email = email.strip().lower()
    success, message, dev_code = request_email_otp(db, email)
    if not success:
        return templates.TemplateResponse("login.html", {
            "request": request,
            "email": email,
            "error": message,
            "next": next or "",
            "otp_dev_mode": settings.OTP_DEV_MODE,
            "has_google": bool(settings.GOOGLE_CLIENT_ID),
            "has_microsoft": bool(settings.MICROSOFT_CLIENT_ID)
        })

    redirect_url = f"/auth/email/verify?email={email}"
    if next:
        redirect_url += f"&next={next}"
    if dev_code:
        redirect_url += f"&dev_hint={dev_code}"

    return RedirectResponse(url=redirect_url, status_code=303)

@router.get("/email/verify", response_class=HTMLResponse)
def verify_otp_page(
    request: Request,
    email: str = Query(...),
    dev_hint: Optional[str] = Query(None),
    next: Optional[str] = Query(None),
    error: Optional[str] = Query(None)
):
    return templates.TemplateResponse("otp_verify.html", {
        "request": request,
        "email": email,
        "dev_hint": dev_hint if settings.OTP_DEV_MODE else None,
        "next": next or "",
        "error": error,
        "expiry_minutes": settings.OTP_EXPIRY_MINUTES
    })

@router.post("/email/verify-otp")
def handle_verify_otp(
    request: Request,
    email: str = Form(...),
    otp_code: str = Form(...),
    next: Optional[str] = Form(None),
    db: Session = Depends(get_db)
):
    email = email.strip().lower()
    success, message, user = verify_email_otp(db, email, otp_code.strip())
    if not success or not user:
        return templates.TemplateResponse("otp_verify.html", {
            "request": request,
            "email": email,
            "dev_hint": None,
            "next": next or "",
            "error": message,
            "expiry_minutes": settings.OTP_EXPIRY_MINUTES
        })

    client_ip = request.client.host if request.client else ""
    user_agent = request.headers.get("user-agent", "")
    token = create_user_session(db, user, ip_address=client_ip, user_agent=user_agent)

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

    email = email.strip().lower()
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

    email = profile["email"]
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

    client_ip = request.client.host if request.client else ""
    user_agent = request.headers.get("user-agent", "")
    token = create_user_session(db, user, ip_address=client_ip, user_agent=user_agent)

    resp = RedirectResponse(url="/dashboard", status_code=303)
    set_auth_cookie(resp, token, request)
    return resp

@router.get("/microsoft")
def microsoft_auth_redirect():
    if not settings.MICROSOFT_CLIENT_ID:
        return RedirectResponse(url="/auth/login?error=Microsoft+login+is+not+configured+yet", status_code=303)
    url = get_microsoft_auth_url()
    return RedirectResponse(url=url, status_code=303)

@router.get("/microsoft/callback")
async def microsoft_callback(
    request: Request,
    code: Optional[str] = Query(None),
    state: Optional[str] = Query(None),
    error: Optional[str] = Query(None),
    db: Session = Depends(get_db)
):
    if error or not code or not state:
        return RedirectResponse(url=f"/auth/login?error={error or 'Microsoft login cancelled'}", status_code=303)

    nonce = validate_and_consume_state(state, "microsoft")
    if not nonce:
        return RedirectResponse(url="/auth/login?error=Invalid+or+expired+security+state", status_code=303)

    success, msg, profile = await exchange_microsoft_code(code, nonce)
    if not success or not profile:
        return RedirectResponse(url=f"/auth/login?error={msg}", status_code=303)

    email = profile["email"]
    allowed, allow_msg = is_email_allowed(email)
    if not allowed:
        return RedirectResponse(url=f"/auth/login?error={allow_msg}", status_code=303)

    user = match_or_create_user(
        db,
        email=email,
        provider="microsoft",
        provider_sub=profile["provider_sub"],
        name=profile.get("name")
    )

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
