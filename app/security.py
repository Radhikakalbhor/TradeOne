import hashlib
import hmac
import secrets
import json
from datetime import datetime, timezone, timedelta
from typing import Optional, Tuple
from fastapi import Request, HTTPException, status
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from app.config import settings
from app.models import RateLimitLog, EmailOtp

serializer = URLSafeTimedSerializer(settings.SESSION_SECRET)

def hash_secret(value: str) -> str:
    """Hash password or secret with SHA-256 and secret salt."""
    salted = f"{settings.SECRET_KEY}:{value}".encode("utf-8")
    return hashlib.sha256(salted).hexdigest()

def verify_secret(value: str, hashed_value: str) -> bool:
    return hmac.compare_digest(hash_secret(value), hashed_value)

def compute_hmac_sha256(data_bytes: bytes, secret: Optional[str] = None) -> str:
    sec = (secret or settings.SECRET_KEY).encode("utf-8")
    return hmac.new(sec, data_bytes, hashlib.sha256).hexdigest()

def sign_data(data: dict) -> str:
    serialized = json.dumps(data, sort_keys=True, separators=(',', ':')).encode("utf-8")
    return compute_hmac_sha256(serialized)

def is_email_allowed(email: str) -> Tuple[bool, str]:
    email = email.strip().lower()
    if not email or "@" not in email:
        return False, "Invalid email address format."

    domain = email.split("@")[-1].strip().lower()

    if settings.ALLOWED_EMAILS and email not in settings.ALLOWED_EMAILS:
        return False, "Access restricted: this email address is not permitted to log in."

    if settings.ALLOWED_EMAIL_DOMAINS and domain not in settings.ALLOWED_EMAIL_DOMAINS:
        return False, f"Access restricted: @{domain} accounts are not permitted to log in."

    return True, ""

def check_rate_limit(db, key: str, action: str, max_requests: int, window_seconds: int) -> bool:
    """Returns True if within limit, False if exceeded."""
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(seconds=window_seconds)
    
    count = db.query(RateLimitLog).filter(
        RateLimitLog.key == key,
        RateLimitLog.action == action,
        RateLimitLog.timestamp >= cutoff
    ).count()

    if count >= max_requests:
        return False

    log = RateLimitLog(key=key, action=action, timestamp=now)
    db.add(log)
    db.commit()
    return True

def generate_otp_code() -> str:
    """Generate 6-digit numerical OTP."""
    return f"{secrets.randbelow(900000) + 100000:06d}"

def is_request_secure(request: Request) -> bool:
    if request.url.scheme == "https":
        return True
    proto = request.headers.get("x-forwarded-proto", "").lower()
    return proto == "https"
