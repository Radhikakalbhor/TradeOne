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
        return os.getenv("SMTP_PASSWORD", "")

    @property
    def SMTP_FROM(self) -> str:
        return os.getenv("SMTP_FROM", "no-reply@nationaldepo.sim").strip()
    
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
    def SEED_STARTER_ACCOUNTS(self) -> bool:
        return os.getenv("SEED_STARTER_ACCOUNTS", "true").lower() in ("true", "1", "yes")
    
    # Admin & Ingest
    @property
    def ADMIN_PASSWORD(self) -> str:
        return os.getenv("ADMIN_PASSWORD", "adminsecret123")

    @property
    def INGEST_API_KEY(self) -> str:
        return os.getenv("INGEST_API_KEY", "nd-ingest-secret-key-2026")
    
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
