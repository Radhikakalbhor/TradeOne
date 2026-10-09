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
    def SECRET_KEY(self) -> str:
        return os.getenv("SECRET_KEY", "nationaldepo-super-secure-secret-key-change-in-prod-2026").strip()

    @property
    def SESSION_SECRET(self) -> str:
        return os.getenv("SESSION_SECRET", "nationaldepo-session-encryption-key-32-chars-long!").strip()

    # Google OAuth
    @property
    def GOOGLE_CLIENT_ID(self) -> str:
        val = os.getenv("GOOGLE_CLIENT_ID", "").strip().strip('"').strip("'")
        if not val and ENV_FILE.exists():
            self.reload()
            val = os.getenv("GOOGLE_CLIENT_ID", "").strip().strip('"').strip("'")
        return val

    @property
    def GOOGLE_CLIENT_SECRET(self) -> str:
        val = os.getenv("GOOGLE_CLIENT_SECRET", "").strip().strip('"').strip("'")
        if not val and ENV_FILE.exists():
            self.reload()
            val = os.getenv("GOOGLE_CLIENT_SECRET", "").strip().strip('"').strip("'")
        return val

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

    # Depository Settings
    @property
    def OTP_DEV_MODE(self) -> bool:
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
        return os.getenv("SHARED_IDENTITY_SALT", "tradeone-shared-identity-salt-2026").strip()

    @property
    def INTERNAL_API_KEY(self) -> str:
        return os.getenv("INTERNAL_API_KEY", "tradeone-internal-key-2026").strip()

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
        return os.getenv("NIFTYTRADE_INTERNAL_KEY", "").strip()

    @property
    def BHARATINVEST_URL(self) -> str:
        return os.getenv("BHARATINVEST_URL", "https://bharatinvest.onrender.com").strip().rstrip("/")

    @property
    def BHARATINVEST_INTERNAL_KEY(self) -> str:
        return os.getenv("BHARATINVEST_INTERNAL_KEY", "").strip()

    @property
    def BONDBAZAAR_URL(self) -> str:
        return os.getenv("BONDBAZAAR_URL", "https://bondbazaar-1.onrender.com").strip().rstrip("/")

    @property
    def BONDBAZAAR_INTERNAL_KEY(self) -> str:
        return os.getenv("BONDBAZAAR_INTERNAL_KEY", "").strip()

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
        return os.getenv("PUBLIC_BASE_URL", self.BASE_URL).strip().rstrip("/")

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

settings = Settings()
