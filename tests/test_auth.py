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
    send_otp_email, is_smtp_configured, is_resend_configured,
    send_email_via_resend, is_gmail_configured, get_gmail_access_token,
    send_email_via_gmail_api, _gmail_token_cache
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


def test_resend_email_delivery_success(monkeypatch):
    """Test successful email delivery via Resend HTTPS API mocking httpx.Client."""
    monkeypatch.setenv("RESEND_API_KEY", "re_test_api_key_1234567890")
    monkeypatch.setenv("RESEND_FROM", "TradeOne <onboarding@resend.dev>")
    assert is_resend_configured() is True

    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.json.return_value = {"id": "re_msg_12345"}

    with patch("httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__.return_value = mock_client
        mock_client.post.return_value = fake_response
        mock_client_cls.return_value = mock_client

        sent, msg = send_email_via_resend("user@example.com", "Your Code", "<p>Code 123456</p>")
        assert sent is True
        assert "sent" in msg.lower()

        # Check call arguments
        mock_client.post.assert_called_once()
        args, kwargs = mock_client.post.call_args
        assert args[0] == "https://api.resend.com/emails"
        assert kwargs["headers"]["Authorization"] == "Bearer re_test_api_key_1234567890"
        assert kwargs["json"]["to"] == ["user@example.com"]
        assert kwargs["json"]["from"] == "TradeOne <onboarding@resend.dev>"
        assert kwargs["json"]["subject"] == "Your Code"
        assert "<p>Code 123456</p>" in kwargs["json"]["html"]


def test_resend_email_delivery_provider_error(monkeypatch):
    """Test handling of Resend API error responses (e.g. 422 unverified domain) without crashing."""
    monkeypatch.setenv("RESEND_API_KEY", "re_test_key")
    monkeypatch.setenv("RESEND_FROM", "TradeOne <invalid@customdomain.com>")

    fake_response = MagicMock()
    fake_response.status_code = 422
    fake_response.json.return_value = {"message": "Domain customdomain.com is not verified."}

    with patch("httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__.return_value = mock_client
        mock_client.post.return_value = fake_response
        mock_client_cls.return_value = mock_client

        sent, msg = send_email_via_resend("user@example.com", "Subject", "<p>Body</p>")
        assert sent is False
        assert "422" in msg
        assert "Domain customdomain.com is not verified" in msg


def test_resend_email_delivery_network_timeout(monkeypatch):
    """Test handling of network timeouts contacting Resend HTTPS API."""
    import httpx
    monkeypatch.setenv("RESEND_API_KEY", "re_test_key")

    with patch("httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__.return_value = mock_client
        mock_client.post.side_effect = httpx.ConnectTimeout("Connect timed out")
        mock_client_cls.return_value = mock_client

        sent, msg = send_email_via_resend("user@example.com", "Subject", "<p>Body</p>")
        assert sent is False
        assert "timed out" in msg.lower()


def test_resend_priority_and_no_smtp_fallback_on_failure(monkeypatch):
    """Verify that when Resend is configured, it is prioritized and SMTP is NOT called on Resend failure."""
    monkeypatch.setenv("RESEND_API_KEY", "re_active_key")
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_PORT", "587")
    monkeypatch.setenv("SMTP_USER", "smtp_user")
    monkeypatch.setenv("SMTP_PASSWORD", "smtp_pass")

    assert is_resend_configured() is True
    assert is_smtp_configured() is True

    # When Resend fails, send_otp_email must NOT fall back to SMTP (which causes Errno 101 on Render)
    fake_response = MagicMock()
    fake_response.status_code = 401
    fake_response.json.return_value = {"message": "API key invalid"}

    with patch("httpx.Client") as mock_client_cls, patch("smtplib.SMTP") as mock_smtp:
        mock_client = MagicMock()
        mock_client.__enter__.return_value = mock_client
        mock_client.post.return_value = fake_response
        mock_client_cls.return_value = mock_client

        sent, msg = send_otp_email("user@example.com", "123456")
        assert sent is False
        assert "401" in msg
        # Ensure SMTP was NEVER called
        mock_smtp.assert_not_called()


def test_otp_request_and_verification_via_resend(monkeypatch):
    """Full end-to-end OTP cycle using Resend email delivery mock."""
    monkeypatch.setenv("RESEND_API_KEY", "re_valid_key")
    db = SessionLocal()
    try:
        import time
        email = f"test.resend.e2e.{int(time.time()*1000)}@example.com"

        fake_response = MagicMock()
        fake_response.status_code = 200
        fake_response.json.return_value = {"id": "re_ok_123"}

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__.return_value = mock_client
            mock_client.post.return_value = fake_response
            mock_client_cls.return_value = mock_client

            # Request OTP with send_email=True (triggers Resend)
            success, msg, _ = request_email_otp(db, email, send_email=True)
            assert success is True
            assert "sent" in msg.lower()

            # Retrieve active OTP from DB
            otp = db.query(EmailOtp).filter(EmailOtp.email == email, EmailOtp.used == False).first()
            assert otp is not None
            exp_time = otp.expires_at if otp.expires_at.tzinfo else otp.expires_at.replace(tzinfo=timezone.utc)
            assert exp_time > datetime.now(timezone.utc)

            # Retrieve hashed code from OTP by attempting verification with correct code
            # In order to verify, find code in mock_client call body
            call_kwargs = mock_client.post.call_args[1]
            import re
            match = re.search(r'([0-9]{6})</span>', call_kwargs["json"]["html"])
            assert match is not None
            code = match.group(1)

            # Verify OTP
            verified, vmsg, user = verify_email_otp(db, email, code)
            assert verified is True
            assert user is not None
            assert user.email == email

            # One-time use: re-verification must fail
            verified2, vmsg2, _ = verify_email_otp(db, email, code)
            assert verified2 is False
    finally:
        db.close()


def test_is_gmail_configured(monkeypatch):
    """Verify is_gmail_configured requires all three credentials."""
    monkeypatch.setenv("GMAIL_CLIENT_ID", "test_id")
    monkeypatch.setenv("GMAIL_CLIENT_SECRET", "test_secret")
    monkeypatch.setenv("GMAIL_REFRESH_TOKEN", "test_refresh")
    assert is_gmail_configured() is True

    monkeypatch.setenv("GMAIL_REFRESH_TOKEN", "")
    assert is_gmail_configured() is False


def test_gmail_token_refresh_and_caching(monkeypatch):
    """Verify OAuth access token acquisition and in-memory caching."""
    monkeypatch.setenv("GMAIL_CLIENT_ID", "test_client_id")
    monkeypatch.setenv("GMAIL_CLIENT_SECRET", "test_client_secret")
    monkeypatch.setenv("GMAIL_REFRESH_TOKEN", "test_refresh_token")

    # Clear cache
    _gmail_token_cache["token"] = ""
    _gmail_token_cache["expires_at"] = 0.0

    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.json.return_value = {
        "access_token": "ya29.test_cached_access_token",
        "expires_in": 3600
    }

    with patch("httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__.return_value = mock_client
        mock_client.post.return_value = fake_resp
        mock_client_cls.return_value = mock_client

        # Call 1: Fetches token via POST
        ok, token = get_gmail_access_token()
        assert ok is True
        assert token == "ya29.test_cached_access_token"
        assert mock_client.post.call_count == 1

        # Call 2: Uses cached token without additional HTTP call
        ok2, token2 = get_gmail_access_token()
        assert ok2 is True
        assert token2 == "ya29.test_cached_access_token"
        assert mock_client.post.call_count == 1


def test_gmail_token_refresh_failure(monkeypatch):
    """Verify graceful error reporting when token refresh fails."""
    monkeypatch.setenv("GMAIL_CLIENT_ID", "test_client_id")
    monkeypatch.setenv("GMAIL_CLIENT_SECRET", "test_client_secret")
    monkeypatch.setenv("GMAIL_REFRESH_TOKEN", "invalid_refresh_token")

    _gmail_token_cache["token"] = ""
    _gmail_token_cache["expires_at"] = 0.0

    fake_resp = MagicMock()
    fake_resp.status_code = 400
    fake_resp.json.return_value = {
        "error": "invalid_grant",
        "error_description": "Token has been expired or revoked."
    }

    with patch("httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__.return_value = mock_client
        mock_client.post.return_value = fake_resp
        mock_client_cls.return_value = mock_client

        ok, err_msg = get_gmail_access_token()
        assert ok is False
        assert "400" in err_msg
        assert "invalid_grant" in err_msg


def test_send_email_via_gmail_api_success(monkeypatch):
    """Verify Gmail API message payload encoding and HTTP dispatch."""
    monkeypatch.setenv("GMAIL_CLIENT_ID", "test_client_id")
    monkeypatch.setenv("GMAIL_CLIENT_SECRET", "test_client_secret")
    monkeypatch.setenv("GMAIL_REFRESH_TOKEN", "test_refresh_token")
    monkeypatch.setenv("GMAIL_SENDER", "hacksmiths360@gmail.com")

    # Set active token in cache
    import time
    _gmail_token_cache["token"] = "ya29.mock_token_for_send"
    _gmail_token_cache["expires_at"] = time.time() + 3000

    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.json.return_value = {"id": "18e123456789abcd"}

    with patch("httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__.return_value = mock_client
        mock_client.post.return_value = fake_resp
        mock_client_cls.return_value = mock_client

        sent, msg = send_email_via_gmail_api("recipient@domain.com", "Your OTP Code", "<p>Code 987654</p>")
        assert sent is True
        assert "sent" in msg.lower()

        # Check call arguments
        mock_client.post.assert_called_once()
        args, kwargs = mock_client.post.call_args
        assert args[0] == "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
        assert kwargs["headers"]["Authorization"] == "Bearer ya29.mock_token_for_send"
        # Validate base64url payload
        import base64
        decoded_raw = base64.urlsafe_b64decode(kwargs["json"]["raw"]).decode("utf-8")
        assert "hacksmiths360@gmail.com" in decoded_raw
        assert "recipient@domain.com" in decoded_raw
        assert "Your OTP Code" in decoded_raw
        assert "987654" in decoded_raw


def test_send_email_via_gmail_api_error(monkeypatch):
    """Verify Gmail API HTTP error handling without crashing."""
    monkeypatch.setenv("GMAIL_CLIENT_ID", "test_client_id")
    monkeypatch.setenv("GMAIL_CLIENT_SECRET", "test_client_secret")
    monkeypatch.setenv("GMAIL_REFRESH_TOKEN", "test_refresh_token")

    import time
    _gmail_token_cache["token"] = "ya29.mock_token"
    _gmail_token_cache["expires_at"] = time.time() + 3000

    fake_resp = MagicMock()
    fake_resp.status_code = 403
    fake_resp.json.return_value = {"error": {"message": "Daily sending quota exceeded."}}

    with patch("httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__.return_value = mock_client
        mock_client.post.return_value = fake_resp
        mock_client_cls.return_value = mock_client

        sent, msg = send_email_via_gmail_api("user@test.com", "Subject", "<p>Body</p>")
        assert sent is False
        assert "403" in msg
        assert "Daily sending quota exceeded" in msg


def test_send_email_token_refresh_failure_sanitized_for_user(monkeypatch):
    """Verify that when token refresh fails, send_email_via_gmail_api returns a sanitized user message."""
    monkeypatch.setenv("GMAIL_CLIENT_ID", "test_client_id")
    monkeypatch.setenv("GMAIL_CLIENT_SECRET", "test_client_secret")
    monkeypatch.setenv("GMAIL_REFRESH_TOKEN", "bad_token")

    _gmail_token_cache["token"] = ""
    _gmail_token_cache["expires_at"] = 0.0

    fake_resp = MagicMock()
    fake_resp.status_code = 400
    fake_resp.json.return_value = {"error": "invalid_grant", "error_description": "Bad Request"}

    with patch("httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__.return_value = mock_client
        mock_client.post.return_value = fake_resp
        mock_client_cls.return_value = mock_client

        sent, msg = send_email_via_gmail_api("user@test.com", "Subject", "<p>Body</p>")
        assert sent is False
        assert "Failed to authenticate with Gmail API provider" in msg
        # Raw internal error details and tokens must not be exposed to the user
        assert "invalid_grant" not in msg
        assert "bad_token" not in msg


def test_explicit_email_provider_selection(monkeypatch):
    """Verify explicit EMAIL_PROVIDER overrides auto-detection."""
    monkeypatch.setenv("GMAIL_CLIENT_ID", "test_client_id")
    monkeypatch.setenv("GMAIL_CLIENT_SECRET", "test_client_secret")
    monkeypatch.setenv("GMAIL_REFRESH_TOKEN", "test_refresh_token")
    monkeypatch.setenv("RESEND_API_KEY", "re_test_key")

    with patch("app.services.auth_service.send_email_via_gmail_api", return_value=(True, "Gmail ok")) as mock_gmail, \
         patch("app.services.auth_service.send_email_via_resend", return_value=(True, "Resend ok")) as mock_resend:

        # 1. EMAIL_PROVIDER=gmail explicitly calls Gmail API
        monkeypatch.setenv("EMAIL_PROVIDER", "gmail")
        ok1, msg1 = send_otp_email("user@test.com", "111111")
        assert ok1 is True
        mock_gmail.assert_called_once()
        mock_resend.assert_not_called()

        mock_gmail.reset_mock()
        mock_resend.reset_mock()

        # 2. EMAIL_PROVIDER=resend explicitly calls Resend API
        monkeypatch.setenv("EMAIL_PROVIDER", "resend")
        ok2, msg2 = send_otp_email("user@test.com", "222222")
        assert ok2 is True
        mock_resend.assert_called_once()
        mock_gmail.assert_not_called()

        mock_gmail.reset_mock()
        mock_resend.reset_mock()

        # 3. Unset EMAIL_PROVIDER auto-detects Gmail API as Priority 1
        monkeypatch.setenv("EMAIL_PROVIDER", "")
        ok3, msg3 = send_otp_email("user@test.com", "333333")
        assert ok3 is True
        mock_gmail.assert_called_once()
        mock_resend.assert_not_called()


def test_otp_request_and_verification_via_gmail_api(monkeypatch):
    """Full end-to-end OTP cycle using Gmail API mock."""
    monkeypatch.setenv("EMAIL_PROVIDER", "gmail")
    monkeypatch.setenv("GMAIL_CLIENT_ID", "test_client_id")
    monkeypatch.setenv("GMAIL_CLIENT_SECRET", "test_client_secret")
    monkeypatch.setenv("GMAIL_REFRESH_TOKEN", "test_refresh_token")
    monkeypatch.setenv("GMAIL_SENDER", "hacksmiths360@gmail.com")

    import time
    _gmail_token_cache["token"] = "ya29.mock_token_e2e"
    _gmail_token_cache["expires_at"] = time.time() + 3000

    db = SessionLocal()
    try:
        email = f"test.gmail.e2e.{int(time.time()*1000)}@externaldomain.in"

        fake_resp = MagicMock()
        fake_resp.status_code = 200
        fake_resp.json.return_value = {"id": "msg_gmail_e2e"}

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__.return_value = mock_client
            mock_client.post.return_value = fake_resp
            mock_client_cls.return_value = mock_client

            # Request OTP with send_email=True (triggers Gmail API)
            success, msg, _ = request_email_otp(db, email, send_email=True)
            assert success is True
            assert "sent" in msg.lower()

            # Retrieve active OTP from DB
            otp = db.query(EmailOtp).filter(EmailOtp.email == email, EmailOtp.used == False).first()
            assert otp is not None
            exp_time = otp.expires_at if otp.expires_at.tzinfo else otp.expires_at.replace(tzinfo=timezone.utc)
            assert exp_time > datetime.now(timezone.utc)

            # Retrieve code from encoded email body
            call_kwargs = mock_client.post.call_args[1]
            import base64, re
            decoded = base64.urlsafe_b64decode(call_kwargs["json"]["raw"]).decode("utf-8")
            match = re.search(r'([0-9]{6})</span>', decoded)
            assert match is not None
            code = match.group(1)

            # Verify OTP
            verified, vmsg, user = verify_email_otp(db, email, code)
            assert verified is True
            assert user is not None
            assert user.email == email

            # One-time use: re-verification must fail
            verified2, vmsg2, _ = verify_email_otp(db, email, code)
            assert verified2 is False
    finally:
        db.close()


def test_login_page_renders_hacksmiths_and_removes_aarav_and_priya():
    """Verify that TradeOne login page displays Hacksmiths demo account and removes Aarav and Priya."""
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app)
    resp = client.get("/auth/login")
    assert resp.status_code == 200
    html = resp.text

    # Hacksmiths demo account must be present
    assert "Hacksmiths" in html
    assert "demo-login-hacksmiths" in html
    assert "/auth/demo-login" in html

    # Aarav Mehta and Priya Nair must be completely removed
    assert "Aarav Mehta" not in html
    assert "Priya Nair" not in html
    assert "demo-login-aarav" not in html
    assert "demo-login-priya" not in html
    assert "aarav.mehta@example.com" not in html
    assert "priya.nair@example.com" not in html


def test_demo_login_hacksmiths_direct_redirect_and_dashboard():
    """Verify clicking Hacksmiths demo account logs in directly and opens the dashboard."""
    from fastapi.testclient import TestClient
    from app.main import app
    from app.database import SessionLocal
    from app.models import User

    client = TestClient(app)
    # Direct POST to /auth/demo-login
    resp = client.post("/auth/demo-login", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/dashboard"
    assert "nd_session" in client.cookies

    # Opening the dashboard with the session cookie
    dash_resp = client.get("/dashboard")
    assert dash_resp.status_code == 200
    assert "TradeOne" in dash_resp.text

    # Verify user in database
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == "hacksmiths360@gmail.com").first()
        assert user is not None
        assert user.name == "Hacksmiths"
        assert len(user.demat_accounts) >= 3
    finally:
        db.close()


def test_demo_login_idempotent_no_duplicate_accounts():
    """Verify repeated demo login clicks reuse the Hacksmiths user without creating duplicates."""
    from fastapi.testclient import TestClient
    from app.main import app
    from app.database import SessionLocal
    from app.models import User, DematAccount

    client = TestClient(app)
    # Click 3 times
    for _ in range(3):
        resp = client.post("/auth/demo-login", follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/dashboard"

    db = SessionLocal()
    try:
        users = db.query(User).filter(User.email == "hacksmiths360@gmail.com").all()
        assert len(users) == 1
        user = users[0]
        acc_ids = [a.id for a in user.demat_accounts]
        assert len(acc_ids) == len(set(acc_ids))  # No duplicate account IDs
    finally:
        db.close()


def test_google_login_flow_preserved():
    """Verify Google OAuth login flow remains intact."""
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app)
    resp = client.get("/auth/google", follow_redirects=False)
    if settings.GOOGLE_CLIENT_ID:
        assert resp.status_code == 303
        assert "accounts.google.com" in resp.headers["location"]
        assert settings.GOOGLE_CLIENT_ID in resp.headers["location"]
    else:
        assert resp.status_code == 303
        assert "login?error=" in resp.headers["location"]

