import smtplib
import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime, timezone, timedelta
from typing import Optional, Tuple
import secrets
from app.config import settings
from app.models import User, AuthMethod, EmailOtp, UserSession, RateLimitLog
from app.security import (
    hash_secret, verify_secret, is_email_allowed, check_rate_limit,
    generate_otp_code, serializer
)
from app.services.seed_service import provision_new_user
from app.shared_identity import normalize_email, generate_identity

logger = logging.getLogger("app.services.auth_service")

# In-memory testing cache only accessible in OTP_DEV_MODE via dedicated test endpoint
_dev_otp_store = {}

def get_dev_test_otp(email: str) -> Optional[str]:
    """Retrieve last generated OTP code for test automation only when OTP_DEV_MODE is enabled."""
    if not settings.OTP_DEV_MODE:
        return None
    return _dev_otp_store.get(normalize_email(email))

def is_smtp_configured() -> bool:
    """Check if SMTP credentials are configured in the environment."""
    return bool(
        settings.SMTP_HOST and settings.SMTP_HOST.strip()
        and settings.SMTP_USER and settings.SMTP_USER.strip()
        and settings.SMTP_PASSWORD and settings.SMTP_PASSWORD.strip()
    )

def is_resend_configured() -> bool:
    """Check if Resend HTTPS API key is configured in the environment."""
    return bool(settings.RESEND_API_KEY and settings.RESEND_API_KEY.strip())

def send_email_via_resend(to_email: str, subject: str, html_content: str) -> Tuple[bool, str]:
    """Deliver email via Resend HTTPS API over port 443.
    Bypasses cloud datacenter outbound SMTP port blocks (Errno 101 Network unreachable).
    Never logs or exposes the API key or verification code.
    """
    api_key = settings.RESEND_API_KEY.strip().strip('"').strip("'")
    if not api_key:
        return False, "RESEND_API_KEY is not configured."

    from_addr = settings.RESEND_FROM or "TradeOne <onboarding@resend.dev>"

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "User-Agent": "TradeOne-Depository/1.0"
    }
    payload = {
        "from": from_addr,
        "to": [to_email],
        "subject": subject,
        "html": html_content
    }

    try:
        import httpx
        with httpx.Client(timeout=10.0) as client:
            resp = client.post("https://api.resend.com/emails", json=payload, headers=headers)
            if 200 <= resp.status_code < 300:
                return True, "Verification code sent to your email."

            try:
                err_data = resp.json()
                err_msg = err_data.get("message") or err_data.get("error", {}).get("message") or resp.text
            except Exception:
                err_msg = resp.text
            return False, f"Email delivery provider error (HTTP {resp.status_code}): {err_msg}"
    except (httpx.TimeoutException, httpx.ConnectTimeout):
        return False, "Email delivery timed out contacting provider."
    except Exception as exc:
        return False, f"Failed to deliver email via HTTPS API: {type(exc).__name__}: {str(exc)}"

def send_email_via_smtp(to_email: str, subject: str, html_content: str) -> Tuple[bool, str]:
    """Deliver email via SMTP over port 587 (STARTTLS) or 465 (SSL)."""
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = settings.SMTP_FROM
        msg["To"] = to_email
        msg.attach(MIMEText(html_content, "html"))

        port = settings.SMTP_PORT
        if port == 465:
            with smtplib.SMTP_SSL(settings.SMTP_HOST, port, timeout=10) as server:
                if settings.SMTP_USER and settings.SMTP_PASSWORD:
                    server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
                server.sendmail(settings.SMTP_FROM, [to_email], msg.as_string())
        else:
            with smtplib.SMTP(settings.SMTP_HOST, port, timeout=10) as server:
                server.starttls()
                if settings.SMTP_USER and settings.SMTP_PASSWORD:
                    server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
                server.sendmail(settings.SMTP_FROM, [to_email], msg.as_string())

        return True, "Verification code sent to your email."
    except Exception as e:
        return False, f"Failed to deliver verification email: {str(e)}"

def send_otp_email(to_email: str, code: str) -> Tuple[bool, str]:
    """Send clean HTML email with OTP using configured HTTPS API provider (Resend) or SMTP fallback.
    Returns (success, status_or_error_message).
    """
    subject = f"{code} is your TradeOne verification code"
    html_content = f"""
    <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; max-width: 520px; margin: 0 auto; padding: 24px; border: 1px solid #e2e8f0; border-radius: 8px; background: #ffffff;">
        <div style="margin-bottom: 20px;">
            <span style="font-size: 20px; font-weight: 700; color: #1e293b; letter-spacing: -0.5px;">Trade<span style="color: #f59e0b;">One</span></span>
            <div style="font-size: 11px; text-transform: uppercase; letter-spacing: 1px; color: #64748b; margin-top: 2px;">Unified Depository &amp; Portfolio Platform</div>
        </div>
        <p style="color: #334155; font-size: 15px; line-height: 1.5;">Hello,</p>
        <p style="color: #334155; font-size: 15px; line-height: 1.5;">Use the one-time verification code below to sign in to your TradeOne account. This code is valid for {settings.OTP_EXPIRY_MINUTES} minutes.</p>
        <div style="margin: 24px 0; padding: 18px; background: #f8fafc; border-radius: 6px; text-align: center; border: 1px dashed #cbd5e1;">
            <span style="font-size: 32px; font-weight: 800; letter-spacing: 8px; color: #0f172a; font-family: monospace;">{code}</span>
        </div>
        <p style="color: #64748b; font-size: 13px; line-height: 1.4;">If you did not request this code, you can safely ignore this email.</p>
        <hr style="border: none; border-top: 1px solid #e2e8f0; margin: 24px 0;" />
        <p style="color: #94a3b8; font-size: 11px; text-align: center;">TradeOne Depository Platform &bull; Security Verification</p>
    </div>
    """

    # Priority 1: HTTPS Email API (Resend) - immune to outbound SMTP network restrictions
    if is_resend_configured():
        logger.info("Selected email provider: Resend (HTTPS API over port 443). Recipient: %s, Sender: %s", to_email, settings.RESEND_FROM)
        success, msg = send_email_via_resend(to_email, subject, html_content)
        if success:
            logger.info("Resend HTTPS API email successfully dispatched for %s", to_email)
        else:
            logger.warning("Resend HTTPS API email dispatch failed for %s: %s", to_email, msg)
        return success, msg

    # Priority 2: SMTP fallback (when explicitly configured and Resend is not set)
    if is_smtp_configured():
        logger.info("Selected email provider: SMTP fallback (Host: %s, Port: %s). Recipient: %s", settings.SMTP_HOST, settings.SMTP_PORT, to_email)
        success, msg = send_email_via_smtp(to_email, subject, html_content)
        if success:
            logger.info("SMTP email successfully dispatched for %s", to_email)
        else:
            logger.warning("SMTP email dispatch failed for %s: %s", to_email, msg)
        return success, msg

    logger.warning("No email provider configured. RESEND_API_KEY is unset and SMTP credentials are not configured.")
    return False, (
        "Email delivery is not configured. Please configure RESEND_API_KEY (recommended for cloud/Render) "
        "or SMTP credentials (SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD) in your environment."
    )

def request_email_otp(db, email: str, send_email: bool = True) -> Tuple[bool, str, Optional[str]]:
    """Generate and record OTP. Invalidate previous OTPs.
    Returns (success, message, dev_code_for_testing).
    """
    email = normalize_email(email)
    allowed, err_msg = is_email_allowed(email)
    if not allowed:
        return False, err_msg, None

    # Rate limit: max 5 OTP requests per email per hour (3600 seconds)
    if not check_rate_limit(db, f"otp:{email}", "request_otp", max_requests=5, window_seconds=3600):
        return False, "Too many OTP requests. Please try again after 1 hour.", None

    # Requirement 7: Invalidate any existing unused OTPs for this email before generating a new one
    db.query(EmailOtp).filter(
        EmailOtp.email == email,
        EmailOtp.used == False
    ).update({"used": True})
    db.commit()

    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=settings.OTP_EXPIRY_MINUTES)
    code = generate_otp_code()
    code_hash = hash_secret(code)

    otp_record = EmailOtp(
        email=email,
        code_hash=code_hash,
        attempts=0,
        created_at=now,
        expires_at=expires_at,
        used=False
    )
    db.add(otp_record)
    db.commit()

    if settings.OTP_DEV_MODE:
        _dev_otp_store[email] = code

    if send_email:
        email_sent, email_msg = send_otp_email(email, code)
        if not email_sent:
            return False, email_msg, None

    return True, "One-time code sent successfully.", code

def verify_email_otp(db, email: str, code: str) -> Tuple[bool, str, Optional[User]]:
    """Verify submitted OTP code with lockout enforcement and clear error messaging."""
    email = normalize_email(email)
    allowed, err_msg = is_email_allowed(email)
    if not allowed:
        return False, err_msg, None

    code = (code or "").strip()
    if not code or len(code) != 6 or not code.isdigit():
        return False, "Please enter a valid 6-digit verification code.", None

    now = datetime.now(timezone.utc)
    
    # Check if there is an active lockout for this email (5 failed attempts within 15 minutes)
    lockout_cutoff = now - timedelta(minutes=15)
    recent_failed_attempts = db.query(EmailOtp).filter(
        EmailOtp.email == email,
        EmailOtp.attempts >= 5
    ).order_by(EmailOtp.id.desc()).first()

    if recent_failed_attempts:
        failed_time = recent_failed_attempts.created_at
        if failed_time.tzinfo is None:
            failed_time = failed_time.replace(tzinfo=timezone.utc)
        if failed_time >= lockout_cutoff:
            return False, "Account locked out due to too many failed attempts. Please try again in 15 minutes.", None

    # Find the latest unused OTP for this email
    otp = db.query(EmailOtp).filter(
        EmailOtp.email == email,
        EmailOtp.used == False
    ).order_by(EmailOtp.id.desc()).first()

    if not otp:
        return False, "No active verification code found. Please request a new code.", None

    # Check expiration with timezone normalization
    otp_expires_at = otp.expires_at
    if otp_expires_at.tzinfo is None:
        otp_expires_at = otp_expires_at.replace(tzinfo=timezone.utc)

    if now > otp_expires_at:
        otp.used = True
        db.commit()
        return False, "The verification code has expired. Please request a new code.", None

    if otp.attempts >= 5:
        return False, "Maximum attempts exceeded for this code. Please request a new code.", None

    # Check code match
    if not verify_secret(code, otp.code_hash):
        otp.attempts += 1
        db.commit()
        remaining = 5 - otp.attempts
        if remaining <= 0:
            return False, "Maximum attempts exceeded. 15-minute lockout initiated.", None
        return False, f"Incorrect verification code. {remaining} attempt(s) remaining.", None

    # Mark as used
    otp.used = True
    db.commit()

    if email in _dev_otp_store:
        _dev_otp_store.pop(email, None)

    # Find or provision user
    user = match_or_create_user(db, email=email, provider="email")
    return True, "Verification successful.", user

def match_or_create_user(
    db, 
    email: str, 
    provider: str, 
    provider_sub: Optional[str] = None, 
    name: Optional[str] = None
) -> User:
    """Account matching logic:
    1. First by provider + provider_sub
    2. Else by lowercase email (link provider)
    3. Else auto-provision new user
    """
    email = normalize_email(email)

    # 1. Match by provider subject
    if provider_sub:
        auth = db.query(AuthMethod).filter(
            AuthMethod.provider == provider,
            AuthMethod.provider_sub == provider_sub
        ).first()
        if auth and auth.user:
            return auth.user

    # 2. Match by email
    user = db.query(User).filter(User.email == email).first()
    if user:
        # Check if auth method is linked, else link it
        auth = db.query(AuthMethod).filter(
            AuthMethod.user_id == user.id,
            AuthMethod.provider == provider
        ).first()
        if not auth:
            db.add(AuthMethod(
                user_id=user.id,
                provider=provider,
                provider_sub=provider_sub,
                email=email
            ))
            db.commit()
        # If user was provisioned with fallback name, and Google name claim is available, update name
        if name and name.strip():
            identity = generate_identity(email)
            if user.name == identity["full_name_fallback"]:
                user.name = name.strip()
                db.commit()
        return user

    # 3. Provision new user
    return provision_new_user(db, email=email, name=name, provider=provider)

def create_user_session(db, user: User, ip_address: str = "", user_agent: str = "") -> str:
    """Create a new session record and return signed session ID."""
    raw_token = secrets.token_urlsafe(32)
    session_id = hash_secret(raw_token)
    now = datetime.now(timezone.utc)

    db_session = UserSession(
        session_id=session_id,
        user_id=user.id,
        ip_address=ip_address,
        user_agent=user_agent[:250],
        created_at=now,
        last_activity=now,
        is_active=True
    )
    db.add(db_session)
    db.commit()

    # Return signed token for cookie
    return serializer.dumps({"raw": raw_token, "session_id": session_id})

def get_current_user_from_session(db, session_cookie: Optional[str]) -> Optional[User]:
    """Retrieve user from session cookie with 30-minute idle timeout enforcement."""
    if not session_cookie:
        return None

    try:
        # Max age can be up to 7 days, but idle timeout is 30 mins
        data = serializer.loads(session_cookie, max_age=86400 * 7)
        session_id = data.get("session_id")
    except Exception:
        return None

    db_session = db.query(UserSession).filter(
        UserSession.session_id == session_id,
        UserSession.is_active == True
    ).first()

    if not db_session:
        return None

    now = datetime.now(timezone.utc)
    # 30 minutes idle timeout check
    last_act = db_session.last_activity
    if last_act.tzinfo is None:
        last_act = last_act.replace(tzinfo=timezone.utc)
        
    if now - last_act > timedelta(minutes=30):
        db_session.is_active = False
        db.commit()
        return None

    # Update last activity
    db_session.last_activity = now
    db.commit()

    return db_session.user

def terminate_session(db, session_cookie: Optional[str]):
    if not session_cookie:
        return
    try:
        data = serializer.loads(session_cookie)
        session_id = data.get("session_id")
        db_session = db.query(UserSession).filter(UserSession.session_id == session_id).first()
        if db_session:
            db_session.is_active = False
            db.commit()
    except Exception:
        pass

def terminate_all_user_sessions(db, user_id: int):
    db.query(UserSession).filter(
        UserSession.user_id == user_id,
        UserSession.is_active == True
    ).update({"is_active": False})
    db.commit()
