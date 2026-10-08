import smtplib
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

def send_otp_email(to_email: str, code: str):
    """Send clean HTML email with OTP if SMTP configured, else log."""
    subject = f"{code} is your TradeOne verification code"
    html_content = f"""
    <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; max-width: 520px; margin: 0 auto; padding: 24px; border: 1px solid #e2e8f0; border-radius: 8px; background: #ffffff;">
        <div style="margin-bottom: 20px;">
            <span style="font-size: 20px; font-weight: 700; color: #1e293b; letter-spacing: -0.5px;">National<span style="color: #f59e0b;">Depo</span></span>
            <div style="font-size: 11px; text-transform: uppercase; letter-spacing: 1px; color: #64748b; margin-top: 2px;">Simulated Depository Platform</div>
        </div>
        <p style="color: #334155; font-size: 15px; line-height: 1.5;">Hello,</p>
        <p style="color: #334155; font-size: 15px; line-height: 1.5;">Use the one-time verification code below to sign in to your TradeOne account. This code is valid for {settings.OTP_EXPIRY_MINUTES} minutes.</p>
        <div style="margin: 24px 0; padding: 18px; background: #f8fafc; border-radius: 6px; text-align: center; border: 1px dashed #cbd5e1;">
            <span style="font-size: 32px; font-weight: 800; letter-spacing: 8px; color: #0f172a; font-family: monospace;">{code}</span>
        </div>
        <p style="color: #64748b; font-size: 13px; line-height: 1.4;">If you did not request this code, you can safely ignore this email.</p>
        <hr style="border: none; border-top: 1px solid #e2e8f0; margin: 24px 0;" />
        <p style="color: #94a3b8; font-size: 11px; text-align: center;">Simulated Depository Sandbox &bull; For integration testing only</p>
    </div>
    """

    if settings.SMTP_HOST and settings.SMTP_USER:
        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = settings.SMTP_FROM
            msg["To"] = to_email
            msg.attach(MIMEText(html_content, "html"))

            with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT) as server:
                server.starttls()
                server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
                server.sendmail(settings.SMTP_FROM, [to_email], msg.as_string())
        except Exception as e:
            print(f"[TradeOne SMTP ERROR] Failed to send email: {e}")

    if settings.OTP_DEV_MODE:
        print(f"\n==========================================")
        print(f"[TradeOne DEV OTP] To: {to_email}")
        print(f"[TradeOne DEV OTP] CODE: {code}")
        print(f"==========================================\n")

def request_email_otp(db, email: str) -> Tuple[bool, str, Optional[str]]:
    """Generate and record OTP. Returns (success, message, dev_code_if_dev_mode)."""
    email = email.strip().lower()
    allowed, err_msg = is_email_allowed(email)
    if not allowed:
        return False, err_msg, None

    # Rate limit: max 5 OTP requests per email per hour (3600 seconds)
    if not check_rate_limit(db, f"otp:{email}", "request_otp", max_requests=5, window_seconds=3600):
        return False, "Too many OTP requests. Please try again after 1 hour.", None

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

    send_otp_email(email, code)

    dev_hint = code if settings.OTP_DEV_MODE else None
    return True, "One-time code sent successfully.", dev_hint

def verify_email_otp(db, email: str, code: str) -> Tuple[bool, str, Optional[User]]:
    """Verify submitted OTP code with lockout enforcement."""
    email = email.strip().lower()
    allowed, err_msg = is_email_allowed(email)
    if not allowed:
        return False, err_msg, None

    now = datetime.now(timezone.utc)
    
    # Check if there is an active lockout for this email (5 failed attempts within 15 minutes)
    lockout_cutoff = now - timedelta(minutes=15)
    recent_failed_attempts = db.query(EmailOtp).filter(
        EmailOtp.email == email,
        EmailOtp.created_at >= lockout_cutoff,
        EmailOtp.attempts >= 5
    ).first()
    if recent_failed_attempts:
        return False, "Account locked out due to too many failed attempts. Please try again in 15 minutes.", None

    # Find the latest unused, unexpired OTP for this email
    otp = db.query(EmailOtp).filter(
        EmailOtp.email == email,
        EmailOtp.used == False,
        EmailOtp.expires_at >= now
    ).order_by(EmailOtp.created_at.desc()).first()

    if not otp:
        return False, "Code has expired or is invalid. Please request a new code.", None

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
    email = email.strip().lower()

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
