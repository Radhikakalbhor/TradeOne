import os
import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient

from app.main import app
from app.config import Settings
from app.security import serializer

client = TestClient(app)

def test_development_allows_defaults():
    s = Settings()
    with patch.dict(os.environ, {"ENVIRONMENT": "development"}):
        errors = s.validate_production_configuration()
        assert errors == []
        assert s.IS_PRODUCTION is False

def test_production_rejects_placeholder_secret_key():
    s = Settings()
    prod_env = {
        "ENVIRONMENT": "production",
        "SECRET_KEY": "nationaldepo-super-secure-secret-key-change-in-prod-2026",
        "SESSION_SECRET": "a" * 32,
        "ADMIN_PASSWORD": "ValidProdPassword99!",
        "INGEST_API_KEY": "a" * 20,
        "SHARED_IDENTITY_SALT": "a" * 20,
        "INTERNAL_API_KEY": "a" * 20,
        "OTP_DEV_MODE": "false",
        "DEMO_MODE": "false",
        "SMTP_HOST": "smtp.gmail.com",
        "SMTP_PORT": "587",
        "SMTP_USER": "test@example.com",
        "SMTP_PASSWORD": "valid-app-password"
    }
    with patch.dict(os.environ, prod_env, clear=True):
        with pytest.raises(RuntimeError) as excinfo:
            s.validate_production_configuration()
        assert "SECRET_KEY" in str(excinfo.value)

def test_production_rejects_placeholder_secrets_all():
    s = Settings()
    placeholders = [
        ("SECRET_KEY", "change-me-to-a-random-secret-key-at-least-32-chars"),
        ("SESSION_SECRET", "change-me-to-a-random-session-secret-32-chars"),
        ("ADMIN_PASSWORD", "change-me-admin-password"),
        ("INGEST_API_KEY", "change-me-ingest-api-key"),
        ("SHARED_IDENTITY_SALT", "your-shared-salt-across-all-4-brokers"),
        ("INTERNAL_API_KEY", "your-secure-internal-api-key")
    ]
    for key_name, placeholder_val in placeholders:
        base_env = {
            "ENVIRONMENT": "production",
            "SECRET_KEY": "valid_secret_key_long_enough_32_chars!",
            "SESSION_SECRET": "valid_session_secret_long_enough_32!",
            "ADMIN_PASSWORD": "ValidProdPassword99!",
            "INGEST_API_KEY": "valid_ingest_key_32_chars_long!!",
            "SHARED_IDENTITY_SALT": "valid_shared_salt_32_chars_long!",
            "INTERNAL_API_KEY": "valid_internal_key_32_chars_long!",
            "OTP_DEV_MODE": "false",
            "DEMO_MODE": "false",
            "SMTP_HOST": "smtp.gmail.com",
            "SMTP_PORT": "587",
            "SMTP_USER": "test@example.com",
            "SMTP_PASSWORD": "valid-app-password"
        }
        base_env[key_name] = placeholder_val
        with patch.dict(os.environ, base_env, clear=True):
            with pytest.raises(RuntimeError) as excinfo:
                s.validate_production_configuration()
            assert key_name in str(excinfo.value)

def test_production_rejects_otp_dev_mode():
    s = Settings()
    base_env = {
        "ENVIRONMENT": "production",
        "SECRET_KEY": "valid_secret_key_long_enough_32_chars!",
        "SESSION_SECRET": "valid_session_secret_long_enough_32!",
        "ADMIN_PASSWORD": "ValidProdPassword99!",
        "INGEST_API_KEY": "valid_ingest_key_32_chars_long!!",
        "SHARED_IDENTITY_SALT": "valid_shared_salt_32_chars_long!",
        "INTERNAL_API_KEY": "valid_internal_key_32_chars_long!",
        "OTP_DEV_MODE": "true",
        "DEMO_MODE": "false",
        "SMTP_HOST": "smtp.gmail.com",
        "SMTP_PORT": "587",
        "SMTP_USER": "test@example.com",
        "SMTP_PASSWORD": "valid-app-password"
    }
    with patch.dict(os.environ, base_env, clear=True):
        with pytest.raises(RuntimeError) as excinfo:
            s.validate_production_configuration()
        assert "OTP_DEV_MODE" in str(excinfo.value)
        # Also verify s.OTP_DEV_MODE property evaluates to False in production
        assert s.OTP_DEV_MODE is False

def test_production_rejects_missing_smtp():
    s = Settings()
    base_env = {
        "ENVIRONMENT": "production",
        "SECRET_KEY": "valid_secret_key_long_enough_32_chars!",
        "SESSION_SECRET": "valid_session_secret_long_enough_32!",
        "ADMIN_PASSWORD": "ValidProdPassword99!",
        "INGEST_API_KEY": "valid_ingest_key_32_chars_long!!",
        "SHARED_IDENTITY_SALT": "valid_shared_salt_32_chars_long!",
        "INTERNAL_API_KEY": "valid_internal_key_32_chars_long!",
        "OTP_DEV_MODE": "false",
        "DEMO_MODE": "false",
        "SMTP_HOST": "",
        "SMTP_PORT": "587",
        "SMTP_USER": "",
        "SMTP_PASSWORD": ""
    }
    with patch.dict(os.environ, base_env, clear=True):
        with pytest.raises(RuntimeError) as excinfo:
            s.validate_production_configuration()
        assert "SMTP" in str(excinfo.value)

def test_production_passes_with_valid_secure_configuration():
    s = Settings()
    valid_env = {
        "ENVIRONMENT": "production",
        "SECRET_KEY": "valid_secret_key_long_enough_32_chars!",
        "SESSION_SECRET": "valid_session_secret_long_enough_32!",
        "ADMIN_PASSWORD": "ValidProdPassword99!",
        "INGEST_API_KEY": "valid_ingest_key_32_chars_long!!",
        "SHARED_IDENTITY_SALT": "valid_shared_salt_32_chars_long!",
        "INTERNAL_API_KEY": "valid_internal_key_32_chars_long!",
        "OTP_DEV_MODE": "false",
        "DEMO_MODE": "false",
        "SMTP_HOST": "smtp.gmail.com",
        "SMTP_PORT": "587",
        "SMTP_USER": "test@example.com",
        "SMTP_PASSWORD": "valid-app-password"
    }
    with patch.dict(os.environ, valid_env, clear=True):
        errors = s.validate_production_configuration()
        assert errors == []
        assert s.IS_PRODUCTION is True
        assert s.OTP_DEV_MODE is False
        assert s.DEMO_MODE is False

def test_starter_portfolio_prevented_when_demo_mode_false():
    s = Settings()
    with patch.dict(os.environ, {"DEMO_MODE": "false", "SEED_STARTER_PORTFOLIO": "true", "SEED_STARTER_ACCOUNTS": "true"}):
        assert s.DEMO_MODE is False
        assert s.SEED_STARTER_PORTFOLIO is False
        assert s.SEED_STARTER_ACCOUNTS is False

def test_tradeone_url_and_public_base_url_fallback():
    s = Settings()
    with patch.dict(os.environ, {"PUBLIC_BASE_URL": "", "TRADEONE_URL": "https://tradeone.onrender.com"}):
        assert s.TRADEONE_URL == "https://tradeone.onrender.com"
        assert s.PUBLIC_BASE_URL == "https://tradeone.onrender.com"

    with patch.dict(os.environ, {"PUBLIC_BASE_URL": "https://custom.example.com", "TRADEONE_URL": ""}):
        assert s.TRADEONE_URL == ""
        assert s.PUBLIC_BASE_URL == "https://custom.example.com"

def test_google_login_hidden_when_oauth_not_configured():
    with patch.dict(os.environ, {"GOOGLE_CLIENT_ID": "", "GOOGLE_CLIENT_SECRET": ""}):
        res = client.get("/auth/login")
        assert res.status_code == 200
        assert "Continue with Google" not in res.text

def test_admin_auth_cookie_signed():
    res = client.post("/admin/login", data={"password": "wrongpassword"})
    assert res.status_code == 200
    assert "Invalid admin password" in res.text

    # Successful login sets signed cookie
    res_ok = client.post("/admin/login", data={"password": Settings().ADMIN_PASSWORD}, follow_redirects=False)
    assert res_ok.status_code == 303
    cookie = res_ok.cookies.get("nd_admin_auth")
    assert cookie is not None
    data = serializer.loads(cookie)
    assert data.get("admin") is True

    # 1. Reject unsigned client-supplied string "authenticated"
    client.cookies.set("nd_admin_auth", "authenticated")
    res_unsigned = client.get("/admin")
    assert res_unsigned.status_code == 200
    assert "Administrator Sign In" in res_unsigned.text or "password" in res_unsigned.text
    assert "Simulation testbed controls" not in res_unsigned.text

    # 2. Reject tampered token
    client.cookies.set("nd_admin_auth", cookie + "tampered")
    res_tampered = client.get("/admin")
    assert res_tampered.status_code == 200
    assert "Simulation testbed controls" not in res_tampered.text

    # 3. Accept valid signed token
    client.cookies.set("nd_admin_auth", cookie)
    res_valid = client.get("/admin")
    assert res_valid.status_code == 200
    assert "Simulation testbed controls" in res_valid.text


def test_production_passes_with_resend_without_smtp():
    """Verify that in production, configuring RESEND_API_KEY satisfies email delivery without requiring SMTP."""
    s = Settings()
    prod_env = {
        "ENVIRONMENT": "production",
        "SECRET_KEY": "valid_secret_key_long_enough_32_chars!",
        "SESSION_SECRET": "valid_session_secret_long_enough_32!",
        "ADMIN_PASSWORD": "ValidProdPassword99!",
        "INGEST_API_KEY": "valid_ingest_key_32_chars_long!!",
        "SHARED_IDENTITY_SALT": "valid_shared_salt_32_chars_long!",
        "INTERNAL_API_KEY": "valid_internal_key_32_chars_long!",
        "OTP_DEV_MODE": "false",
        "DEMO_MODE": "false",
        "RESEND_API_KEY": "re_prod_api_key_valid_987654321",
        "RESEND_FROM": "TradeOne <onboarding@resend.dev>",
        "SMTP_HOST": "",
        "SMTP_PORT": "587",
        "SMTP_USER": "",
        "SMTP_PASSWORD": ""
    }
    with patch.dict(os.environ, prod_env, clear=True):
        errors = s.validate_production_configuration()
        assert errors == []
        assert s.IS_PRODUCTION is True
        assert s.RESEND_API_KEY == "re_prod_api_key_valid_987654321"


def test_production_rejects_placeholder_resend_key():
    """Verify that placeholder RESEND_API_KEY values are rejected in production."""
    s = Settings()
    prod_env = {
        "ENVIRONMENT": "production",
        "SECRET_KEY": "valid_secret_key_long_enough_32_chars!",
        "SESSION_SECRET": "valid_session_secret_long_enough_32!",
        "ADMIN_PASSWORD": "ValidProdPassword99!",
        "INGEST_API_KEY": "valid_ingest_key_32_chars_long!!",
        "SHARED_IDENTITY_SALT": "valid_shared_salt_32_chars_long!",
        "INTERNAL_API_KEY": "valid_internal_key_32_chars_long!",
        "OTP_DEV_MODE": "false",
        "DEMO_MODE": "false",
        "RESEND_API_KEY": "change-me-resend-key",
        "SMTP_HOST": "",
        "SMTP_PORT": "587",
        "SMTP_USER": "",
        "SMTP_PASSWORD": ""
    }
    with patch.dict(os.environ, prod_env, clear=True):
        with pytest.raises(RuntimeError) as excinfo:
            s.validate_production_configuration()
        assert "RESEND_API_KEY" in str(excinfo.value)

