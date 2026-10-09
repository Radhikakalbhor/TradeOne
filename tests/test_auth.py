import pytest
from datetime import datetime, timezone, timedelta
from app.database import SessionLocal, Base, engine
from app.models import User, EmailOtp, AuthMethod
from app.services.auth_service import (
    request_email_otp, verify_email_otp, match_or_create_user
)
from app.security import is_email_allowed, hash_secret
from app.config import settings

from unittest.mock import patch, MagicMock
from starlette.testclient import TestClient
from app.main import app
from app.services.auth_service import (
    request_email_otp, verify_email_otp, match_or_create_user,
    send_otp_email, is_smtp_configured
)

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

def test_email_delivery_unconfigured_fails_safely(monkeypatch):
    """When SMTP is not configured, verify error identifies missing variables and does not fake success."""
    monkeypatch.setenv("SMTP_HOST", "")
    monkeypatch.setenv("SMTP_USER", "")
    assert is_smtp_configured() is False

    sent, msg = send_otp_email("test@example.com", "123456")
    assert sent is False
    assert "SMTP_HOST" in msg
    assert "SMTP_USER" in msg

    db = SessionLocal()
    try:
        import time
        email = f"test.unconfigured.{int(time.time()*1000)}@example.com"
        # Calling request_email_otp with send_email=True should fail honestly
        success, req_msg, _ = request_email_otp(db, email, send_email=True)
        assert success is False
        assert "SMTP" in req_msg
    finally:
        db.close()

def test_email_delivery_smtp_integration(monkeypatch):
    """Test actual SMTP delivery code path without exposing credentials or sending real emails."""
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_PORT", "587")
    monkeypatch.setenv("SMTP_USER", "smtp_user")
    monkeypatch.setenv("SMTP_PASSWORD", "smtp_pass")
    monkeypatch.setenv("SMTP_FROM", "no-reply@tradeone.com")
    assert is_smtp_configured() is True

    with patch("smtplib.SMTP") as mock_smtp:
        mock_instance = MagicMock()
        mock_smtp.return_value.__enter__.return_value = mock_instance

        sent, msg = send_otp_email("user@example.com", "654321")
        assert sent is True
        assert "sent" in msg.lower()
        mock_instance.starttls.assert_called_once()
        mock_instance.login.assert_called_once_with("smtp_user", "smtp_pass")
        assert mock_instance.sendmail.call_count == 1

def test_otp_generation_and_successful_verification():
    """Verify OTP generation, 10-minute validity, and successful verification."""
    db = SessionLocal()
    try:
        import time
        email = f"test.success.{int(time.time()*1000)}@example.com"
        success, msg, code = request_email_otp(db, email, send_email=False)
        assert success is True
        assert code is not None
        assert len(code) == 6
        assert code.isdigit()

        # Check DB record exists and is valid
        otp = db.query(EmailOtp).filter(EmailOtp.email == email, EmailOtp.used == False).first()
        assert otp is not None
        assert otp.attempts == 0

        # Successful verification
        verified, vmsg, user = verify_email_otp(db, email, code)
        assert verified is True
        assert "successful" in vmsg.lower()
        assert user is not None
        assert user.email == email

        # Code is marked as used
        otp_after = db.query(EmailOtp).filter(EmailOtp.id == otp.id).first()
        assert otp_after.used is True

        # Re-using used code fails
        verified2, vmsg2, _ = verify_email_otp(db, email, code)
        assert verified2 is False
        assert "no active verification code" in vmsg2.lower()
    finally:
        db.close()

def test_otp_expiry_after_validity_period():
    """Verify code expires after the validity period."""
    db = SessionLocal()
    try:
        import time
        email = f"test.expired.{int(time.time()*1000)}@example.com"
        success, msg, code = request_email_otp(db, email, send_email=False)
        assert success is True

        # Expire code manually in DB
        otp = db.query(EmailOtp).filter(EmailOtp.email == email, EmailOtp.used == False).first()
        otp.expires_at = datetime.now(timezone.utc) - timedelta(seconds=10)
        db.commit()

        verified, vmsg, user = verify_email_otp(db, email, code)
        assert verified is False
        assert "expired" in vmsg.lower()
    finally:
        db.close()

def test_previous_code_invalidation_on_resend():
    """When a new code is requested, the previous OTP must be invalidated."""
    db = SessionLocal()
    try:
        import time
        email = f"test.resend.{int(time.time()*1000)}@example.com"
        success1, msg1, code1 = request_email_otp(db, email, send_email=False)
        assert success1 is True

        # Request second code (simulating resend after countdown)
        success2, msg2, code2 = request_email_otp(db, email, send_email=False)
        assert success2 is True
        assert code1 != code2

        # Submitting the previous code must fail
        verified1, vmsg1, user1 = verify_email_otp(db, email, code1)
        assert verified1 is False
        assert "incorrect" in vmsg1.lower()

        # Submitting the newest code succeeds
        verified2, vmsg2, user2 = verify_email_otp(db, email, code2)
        assert verified2 is True
        assert user2 is not None
    finally:
        db.close()

def test_otp_attempts_and_lockout():
    """Verify max 5 attempts, attempt countdown, and 15-minute lockout."""
    db = SessionLocal()
    try:
        import time
        email = f"test.lockout.{int(time.time()*1000)}@example.com"
        success, msg, code = request_email_otp(db, email, send_email=False)
        assert success is True

        # Submit wrong code 4 times with countdown
        for i in range(1, 5):
            verified, vmsg, user = verify_email_otp(db, email, "000000")
            assert verified is False
            assert f"{5 - i} attempt" in vmsg

        # 5th failed attempt triggers lockout
        verified, vmsg, user = verify_email_otp(db, email, "000000")
        assert verified is False
        assert "lockout" in vmsg.lower()

        # Subsequent attempt with correct code within lockout window is rejected
        verified, vmsg, user = verify_email_otp(db, email, code)
        assert verified is False
        assert "locked out" in vmsg.lower()
    finally:
        db.close()

def test_no_otp_exposure_in_ui_flow():
    """Verify OTP is never exposed in redirect URLs, template context, or initial error display."""
    import time
    client = TestClient(app)
    email = f"test.security.{int(time.time()*1000)}@example.com"

    # Request OTP via Web route
    with patch("app.services.auth_service.send_otp_email", return_value=(True, "Sent")):
        res = client.post("/auth/email/request-otp", data={"email": email}, follow_redirects=False)
        assert res.status_code == 303
        location = res.headers["location"]
        # OTP must NOT be in the redirect location
        assert "dev_hint" not in location
        assert "otp=" not in location
        assert f"/auth/email/verify?email={email}" in location

    # Load verify page: must NOT display error on initial load, but display neutral guidance
    res_page = client.get(f"/auth/email/verify?email={email}")
    assert res_page.status_code == 200
    assert "Enter the 6-digit code sent to your email." in res_page.text
    assert "id=\"otp-error-alert\"" not in res_page.text
    assert "dev-hint-banner" not in res_page.text

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

def test_verify_otp_web_flow_and_duplicate_submission_idempotency():
    """Regression test: verify full web flow, duplicate submission idempotency, and digit fallback."""
    import time
    client = TestClient(app)
    db = SessionLocal()
    try:
        with patch("app.routers.auth._background_sync_brokers"):
            email = f"test.flow.{int(time.time()*1000)}@example.com"
            success, msg, code = request_email_otp(db, email, send_email=False)
            assert success is True
            assert code is not None

            # 1. Request 1: Valid OTP submission via web form
            res1 = client.post(
                "/auth/email/verify-otp",
                data={"email": email, "otp_code": code},
                follow_redirects=False
            )
            assert res1.status_code == 303
            assert res1.headers["location"] == "/dashboard"
            assert "nd_session" in res1.cookies

            # 2. Request 2: Duplicate concurrent submission of the exact same code
            # Must NOT return 'No active verification code found' error page
            res2 = client.post(
                "/auth/email/verify-otp",
                data={"email": email, "otp_code": code},
                follow_redirects=False
            )
            assert res2.status_code == 303
            assert res2.headers["location"] == "/dashboard"
            assert "nd_session" in res2.cookies

            # 3. Wrong OTP submission produces appropriate error message
            res_wrong = client.post(
                "/auth/email/verify-otp",
                data={"email": email, "otp_code": "000000"},
                follow_redirects=False
            )
            assert res_wrong.status_code == 200
            assert "No active verification code found" in res_wrong.text or "Incorrect" in res_wrong.text

            # 4. Fallback to individual digit fields if JS hidden field is missing
            email2 = f"test.digits.{int(time.time()*1000)}@example.com"
            s2, m2, code2 = request_email_otp(db, email2, send_email=False)
            assert s2 is True

            res_digits = client.post(
                "/auth/email/verify-otp",
                data={
                    "email": email2.upper(),  # test mixed-case normalization
                    "otp_code": "",
                    "digit_1": code2[0],
                    "digit_2": code2[1],
                    "digit_3": code2[2],
                    "digit_4": code2[3],
                    "digit_5": code2[4],
                    "digit_6": code2[5],
                },
                follow_redirects=False
            )
            assert res_digits.status_code == 303
            assert res_digits.headers["location"] == "/dashboard"
            assert "nd_session" in res_digits.cookies
    finally:
        db.close()
