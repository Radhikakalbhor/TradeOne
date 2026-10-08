import pytest
from datetime import datetime, timezone, timedelta
from app.database import SessionLocal, Base, engine
from app.models import User, EmailOtp, AuthMethod
from app.services.auth_service import (
    request_email_otp, verify_email_otp, match_or_create_user
)
from app.security import is_email_allowed, hash_secret
from app.config import settings

@pytest.fixture(scope="module", autouse=True)
def setup_db():
    Base.metadata.create_all(bind=engine)
    yield

def test_allowed_emails_enforcement(monkeypatch):
    monkeypatch.setattr(settings, "ALLOWED_EMAILS_RAW", "allowed@test.com")
    allowed, msg = is_email_allowed("allowed@test.com")
    assert allowed is True

    allowed, msg = is_email_allowed("forbidden@test.com")
    assert allowed is False
    assert "not permitted" in msg

    monkeypatch.setattr(settings, "ALLOWED_EMAILS_RAW", "")
    monkeypatch.setattr(settings, "ALLOWED_EMAIL_DOMAINS_RAW", "corp.in")
    allowed, msg = is_email_allowed("worker@corp.in")
    assert allowed is True

    allowed, msg = is_email_allowed("worker@gmail.com")
    assert allowed is False
    assert "not permitted" in msg

    monkeypatch.setattr(settings, "ALLOWED_EMAIL_DOMAINS_RAW", "")

def test_otp_generation_and_expiry():
    db = SessionLocal()
    try:
        email = "test.otp@example.com"
        success, msg, code = request_email_otp(db, email)
        assert success is True
        assert code is not None
        assert len(code) == 6

        # Check DB record
        otp = db.query(EmailOtp).filter(EmailOtp.email == email).order_by(EmailOtp.created_at.desc()).first()
        assert otp is not None
        assert otp.used is False

        # Expire it manually
        otp.expires_at = datetime.now(timezone.utc) - timedelta(minutes=5)
        db.commit()

        # Attempt verification should fail due to expiry
        verified, vmsg, user = verify_email_otp(db, email, code)
        assert verified is False
        assert "expired" in vmsg.lower() or "invalid" in vmsg.lower()
    finally:
        db.close()

def test_otp_attempts_and_lockout():
    db = SessionLocal()
    try:
        import time
        email = f"test.lockout.{int(time.time()*1000)}@example.com"
        success, msg, code = request_email_otp(db, email)
        assert success is True

        # Submit wrong code 5 times
        for i in range(1, 5):
            verified, vmsg, user = verify_email_otp(db, email, "000000")
            assert verified is False
            assert f"{5 - i} attempt" in vmsg

        # 5th attempt triggers lockout
        verified, vmsg, user = verify_email_otp(db, email, "000000")
        assert verified is False
        assert "lockout" in vmsg.lower() or "exceeded" in vmsg.lower()

        # Subsequent attempts are locked out
        verified, vmsg, user = verify_email_otp(db, email, code)
        assert verified is False
        assert "locked out" in vmsg.lower()
    finally:
        db.close()

def test_account_matching_by_email():
    db = SessionLocal()
    try:
        email = "matching.user@example.com"
        # First login via email creates user
        user1 = match_or_create_user(db, email=email, provider="email")
        assert user1.email == email
        user_id = user1.id

        # Second login via google with same lowercase email links provider and returns same user
        user2 = match_or_create_user(db, email=email.upper(), provider="google", provider_sub="goog_123")
        assert user2.id == user_id

        methods = [am.provider for am in user2.auth_methods]
        assert "email" in methods
        assert "google" in methods
    finally:
        db.close()
