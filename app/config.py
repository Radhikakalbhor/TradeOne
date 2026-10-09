import os
from pathlib import Path
from typing import List, Set
from dotenv import load_dotenv

ENV_FILE = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(dotenv_path=ENV_FILE, override=True)

class Settings:
    def reload(self):
        """Reload configuration from .env file."""
        if ENV_FILE.exists():
            load_dotenv(dotenv_path=ENV_FILE, override=True)

    @property
    def ENVIRONMENT(self) -> str:
        return os.getenv("ENVIRONMENT", os.getenv("APP_ENV", "development")).strip().lower()

    @property
    def IS_PRODUCTION(self) -> bool:
        return self.ENVIRONMENT in ("production", "prod")

    @property
    def SECRET_KEY(self) -> str:
        return os.getenv("SECRET_KEY", "nationaldepo-super-secure-secret-key-change-in-prod-2026").strip()

    @property
    def SESSION_SECRET(self) -> str:
        return os.getenv("SESSION_SECRET", "nationaldepo-session-encryption-key-32-chars-long!").strip()

    # Google OAuth
    @property
    def GOOGLE_CLIENT_ID(self) -> str:
        return os.getenv("GOOGLE_CLIENT_ID", "").strip().strip('"').strip("'")

    @property
    def GOOGLE_CLIENT_SECRET(self) -> str:
        return os.getenv("GOOGLE_CLIENT_SECRET", "").strip().strip('"').strip("'")

    @property
    def GOOGLE_REDIRECT_URI(self) -> str:
        return os.getenv("GOOGLE_REDIRECT_URI", "http://localhost:8000/auth/google/callback").strip().strip('"').strip("'")

    # SMTP Settings
    @property
    def SMTP_HOST(self) -> str:
        return os.getenv("SMTP_HOST", "").strip()

    @property
    def SMTP_PORT(self) -> int:
        try:
            return int(os.getenv("SMTP_PORT", "587"))
        except ValueError:
            return 587

    @property
    def SMTP_USER(self) -> str:
        return os.getenv("SMTP_USER", "").strip()

    @property
    def SMTP_PASSWORD(self) -> str:
        return os.getenv("SMTP_PASSWORD", "").strip().replace(" ", "")

    @property
    def SMTP_FROM(self) -> str:
        from_val = os.getenv("SMTP_FROM", "").strip()
        return from_val if from_val else self.SMTP_USER

    # HTTPS Email API (Resend) Settings
    @property
    def RESEND_API_KEY(self) -> str:
        raw = os.getenv("RESEND_API_KEY") or os.getenv("RESEND_KEY") or ""
        return raw.strip().strip('"').strip("'")

    @property
    def RESEND_FROM(self) -> str:
        raw = os.getenv("RESEND_FROM") or os.getenv("RESEND_FROM_EMAIL") or ""
        val = raw.strip().strip('"').strip("'")
        if val:
            return val
        if self.SMTP_FROM and "@" in self.SMTP_FROM and not self.SMTP_FROM.endswith("example.com"):
            return f"TradeOne <{self.SMTP_FROM}>"
        return "TradeOne <onboarding@resend.dev>"

    # Depository Settings
    @property
    def OTP_DEV_MODE(self) -> bool:
        if self.IS_PRODUCTION:
            return False
        return os.getenv("OTP_DEV_MODE", "true").lower() in ("true", "1", "yes")

    @property
    def OTP_EXPIRY_MINUTES(self) -> int:
        try:
            return int(os.getenv("OTP_EXPIRY_MINUTES", "10"))
        except ValueError:
            return 10

    @property
    def CONSENT_DEFAULT_DAYS(self) -> int:
        try:
            return int(os.getenv("CONSENT_DEFAULT_DAYS", "90"))
        except ValueError:
            return 90

    @property
    def DEMO_MODE(self) -> bool:
        return os.getenv("DEMO_MODE", "false").lower() in ("true", "1", "yes")

    @property
    def SEED_STARTER_ACCOUNTS(self) -> bool:
        if not self.DEMO_MODE:
            return False
        return os.getenv("SEED_STARTER_ACCOUNTS", "false").lower() in ("true", "1", "yes")

    # Admin & Ingest
    @property
    def ADMIN_PASSWORD(self) -> str:
        return os.getenv("ADMIN_PASSWORD", "adminsecret123")

    @property
    def INGEST_API_KEY(self) -> str:
        return os.getenv("INGEST_API_KEY", "nd-ingest-secret-key-2026")

    # Shared Identity & Cross-Broker Sandbox Settings
    @property
    def SHARED_IDENTITY_SALT(self) -> str:
        return os.getenv("SHARED_IDENTITY_SALT", "tradeone-shared-identity-salt-2026").strip().strip('"').strip("'")

    @property
    def INTERNAL_API_KEY(self) -> str:
        return os.getenv("INTERNAL_API_KEY", "tradeone-internal-key-2026").strip().strip('"').strip("'")

    @property
    def INTERNAL_API_ENABLED(self) -> bool:
        return os.getenv("INTERNAL_API_ENABLED", "true").lower() in ("true", "1", "yes")

    @property
    def SEED_STARTER_PORTFOLIO(self) -> bool:
        if not self.DEMO_MODE:
            return False
        return os.getenv("SEED_STARTER_PORTFOLIO", "false").lower() in ("true", "1", "yes")

    @property
    def STARTING_FUNDS(self) -> float:
        try:
            return float(os.getenv("STARTING_FUNDS", "1000000.0"))
        except ValueError:
            return 1000000.0

    @property
    def TRADEONE_URL(self) -> str:
        return os.getenv("TRADEONE_URL", "").strip().rstrip("/")

    @property
    def PROVIDER_CODE(self) -> str:
        return os.getenv("PROVIDER_CODE", "tradeone").strip().lower()

    @property
    def DP_NAME(self) -> str:
        return os.getenv("DP_NAME", "TradeOne Depository").strip()

    @property
    def DP_ID(self) -> str:
        return os.getenv("DP_ID", "IN300000").strip()

    # External Mock Broker Providers (TradeOne Hub Integration)
    @property
    def NIFTYTRADE_URL(self) -> str:
        return os.getenv("NIFTYTRADE_URL", "https://nifty-50-5nzz.onrender.com").strip().rstrip("/")

    @property
    def NIFTYTRADE_INTERNAL_KEY(self) -> str:
        return os.getenv("NIFTYTRADE_INTERNAL_KEY", "").strip().strip('"').strip("'")

    @property
    def BHARATINVEST_URL(self) -> str:
        return os.getenv("BHARATINVEST_URL", "https://bharatinvest.onrender.com").strip().rstrip("/")

    @property
    def BHARATINVEST_INTERNAL_KEY(self) -> str:
        return os.getenv("BHARATINVEST_INTERNAL_KEY", "").strip().strip('"').strip("'")

    @property
    def BONDBAZAAR_URL(self) -> str:
        return os.getenv("BONDBAZAAR_URL", "https://bondbazaar-1.onrender.com").strip().rstrip("/")

    @property
    def BONDBAZAAR_INTERNAL_KEY(self) -> str:
        return os.getenv("BONDBAZAAR_INTERNAL_KEY", "").strip().strip('"').strip("'")

    @property
    def BROKER_PROVIDERS(self) -> dict:
        """Returns standard configuration mapping for the three sibling broker providers."""
        return {
            "a": {
                "provider_code": "a",
                "broker_name": "NiftyTrade",
                "dp_name": "NiftyTrade Securities",
                "dp_id": "IN300001",
                "url": self.NIFTYTRADE_URL,
                "internal_key": self.NIFTYTRADE_INTERNAL_KEY or self.INTERNAL_API_KEY
            },
            "b": {
                "provider_code": "b",
                "broker_name": "BharatInvest",
                "dp_name": "BharatInvest Securities",
                "dp_id": "IN300002",
                "url": self.BHARATINVEST_URL,
                "internal_key": self.BHARATINVEST_INTERNAL_KEY or self.INTERNAL_API_KEY
            },
            "c": {
                "provider_code": "c",
                "broker_name": "BondBazaar",
                "dp_name": "BondBazaar Depository Services",
                "dp_id": "IN300003",
                "url": self.BONDBAZAAR_URL,
                "internal_key": self.BONDBAZAAR_INTERNAL_KEY or self.INTERNAL_API_KEY
            }
        }

    # Access Control
    @property
    def ALLOWED_EMAILS_RAW(self) -> str:
        if hasattr(self, "_allowed_emails_raw"):
            return self._allowed_emails_raw
        return os.getenv("ALLOWED_EMAILS", "")

    @ALLOWED_EMAILS_RAW.setter
    def ALLOWED_EMAILS_RAW(self, val: str):
        self._allowed_emails_raw = val

    @ALLOWED_EMAILS_RAW.deleter
    def ALLOWED_EMAILS_RAW(self):
        if hasattr(self, "_allowed_emails_raw"):
            del self._allowed_emails_raw

    @property
    def ALLOWED_EMAIL_DOMAINS_RAW(self) -> str:
        if hasattr(self, "_allowed_email_domains_raw"):
            return self._allowed_email_domains_raw
        return os.getenv("ALLOWED_EMAIL_DOMAINS", "")

    @ALLOWED_EMAIL_DOMAINS_RAW.setter
    def ALLOWED_EMAIL_DOMAINS_RAW(self, val: str):
        self._allowed_email_domains_raw = val

    @ALLOWED_EMAIL_DOMAINS_RAW.deleter
    def ALLOWED_EMAIL_DOMAINS_RAW(self):
        if hasattr(self, "_allowed_email_domains_raw"):
            del self._allowed_email_domains_raw

    # App & Database
    @property
    def DATABASE_URL(self) -> str:
        return os.getenv("DATABASE_URL", "sqlite:///./nationaldepo.db")

    @property
    def CORS_ORIGINS_RAW(self) -> str:
        return os.getenv("CORS_ORIGINS", "http://localhost:3000,http://localhost:8000,http://127.0.0.1:8000")

    @property
    def BASE_URL(self) -> str:
        return os.getenv("BASE_URL", "http://localhost:8000")

    @property
    def DEMO_AUTO_LINK(self) -> bool:
        if not self.DEMO_MODE:
            return False
        return os.getenv("DEMO_AUTO_LINK", "false").lower() in ("true", "1", "yes")

    @property
    def PUBLIC_BASE_URL(self) -> str:
        val = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")
        if val:
            return val
        tradeone = os.getenv("TRADEONE_URL", "").strip().rstrip("/")
        if tradeone:
            return tradeone
        return self.BASE_URL.strip().rstrip("/")

    @property
    def ALLOWED_EMAILS(self) -> Set[str]:
        if not self.ALLOWED_EMAILS_RAW.strip():
            return set()
        return {e.strip().lower() for e in self.ALLOWED_EMAILS_RAW.split(",") if e.strip()}

    @property
    def ALLOWED_EMAIL_DOMAINS(self) -> Set[str]:
        if not self.ALLOWED_EMAIL_DOMAINS_RAW.strip():
            return set()
        return {d.strip().lower().lstrip("@") for d in self.ALLOWED_EMAIL_DOMAINS_RAW.split(",") if d.strip()}

    @property
    def CORS_ORIGINS(self) -> List[str]:
        return [o.strip() for o in self.CORS_ORIGINS_RAW.split(",") if o.strip()]

    def validate_production_configuration(self) -> List[str]:
        """Validates that production environment has secure keys and non-placeholder values.
        Raises RuntimeError in production if insecure placeholders or missing secrets are detected.
        """
        if not self.IS_PRODUCTION:
            return []

        errors = []
        secret_checks = {
            "SECRET_KEY": (self.SECRET_KEY, 32, ["nationaldepo-super-secure-secret-key-change-in-prod-2026"]),
            "SESSION_SECRET": (self.SESSION_SECRET, 32, ["nationaldepo-session-encryption-key-32-chars-long!"]),
            "ADMIN_PASSWORD": (self.ADMIN_PASSWORD, 8, ["adminsecret123", "password", "admin"]),
            "INGEST_API_KEY": (self.INGEST_API_KEY, 16, ["nd-ingest-secret-key-2026"]),
            "SHARED_IDENTITY_SALT": (self.SHARED_IDENTITY_SALT, 16, ["tradeone-shared-identity-salt-2026"]),
            "INTERNAL_API_KEY": (self.INTERNAL_API_KEY, 16, ["tradeone-internal-key-2026"]),
        }

        for name, (val, min_len, defaults) in secret_checks.items():
            if not val or not val.strip():
                errors.append(f"{name} must not be empty in production.")
            elif val in defaults:
                errors.append(f"{name} is using a default insecure placeholder in production.")
            elif val.lower().startswith("change-me") or val.lower().startswith("your-"):
                errors.append(f"{name} contains template placeholder text in production.")
            elif len(val) < min_len:
                errors.append(f"{name} must be at least {min_len} characters in production.")

        # OTP_DEV_MODE must be False in production
        if os.getenv("OTP_DEV_MODE", "false").lower() in ("true", "1", "yes"):
            errors.append("OTP_DEV_MODE cannot be enabled in production.")

        # DEMO_MODE must be False in production
        if self.DEMO_MODE:
            errors.append("DEMO_MODE must be false in production to prevent simulated portfolio data.")

        # Email OTP delivery configuration required in production (RESEND_API_KEY or SMTP)
        has_resend = bool(self.RESEND_API_KEY and self.RESEND_API_KEY.strip())
        has_smtp = bool(self.SMTP_HOST and self.SMTP_USER and self.SMTP_PASSWORD)
        if not (has_resend or has_smtp):
            errors.append("Email delivery configuration (RESEND_API_KEY or SMTP_HOST/SMTP_USER/SMTP_PASSWORD) is required in production for Email OTP delivery.")
        if has_resend and (self.RESEND_API_KEY.lower().startswith("change-me") or self.RESEND_API_KEY.lower().startswith("your-")):
            errors.append("RESEND_API_KEY contains placeholder text in production.")
        if has_smtp and (self.SMTP_PASSWORD.lower().startswith("change-me") or self.SMTP_PASSWORD.lower().startswith("your-")):
            errors.append("SMTP_PASSWORD contains placeholder text in production.")

        # Check broker keys for placeholders if provided
        for b_name, b_val in [
            ("NIFTYTRADE_INTERNAL_KEY", self.NIFTYTRADE_INTERNAL_KEY),
            ("BHARATINVEST_INTERNAL_KEY", self.BHARATINVEST_INTERNAL_KEY),
            ("BONDBAZAAR_INTERNAL_KEY", self.BONDBAZAAR_INTERNAL_KEY),
        ]:
            if b_val and (b_val.lower().startswith("change-me") or b_val.lower().startswith("your-")):
                errors.append(f"{b_name} contains placeholder text in production.")

        if errors:
            raise RuntimeError("Production configuration security violation:\n - " + "\n - ".join(errors))

        return errors

settings = Settings()
