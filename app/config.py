import os
from typing import List, Set
from dotenv import load_dotenv

load_dotenv()

class Settings:
    SECRET_KEY: str = os.getenv("SECRET_KEY", "nationaldepo-super-secure-secret-key-change-in-prod-2026")
    SESSION_SECRET: str = os.getenv("SESSION_SECRET", "nationaldepo-session-encryption-key-32-chars-long!")
    
    # Google OAuth
    GOOGLE_CLIENT_ID: str = os.getenv("GOOGLE_CLIENT_ID", "")
    GOOGLE_CLIENT_SECRET: str = os.getenv("GOOGLE_CLIENT_SECRET", "")
    GOOGLE_REDIRECT_URI: str = os.getenv("GOOGLE_REDIRECT_URI", "http://localhost:8000/auth/google/callback")
    
    # Microsoft OAuth
    MICROSOFT_CLIENT_ID: str = os.getenv("MICROSOFT_CLIENT_ID", "")
    MICROSOFT_CLIENT_SECRET: str = os.getenv("MICROSOFT_CLIENT_SECRET", "")
    MICROSOFT_TENANT: str = os.getenv("MICROSOFT_TENANT", "common")
    MICROSOFT_REDIRECT_URI: str = os.getenv("MICROSOFT_REDIRECT_URI", "http://localhost:8000/auth/microsoft/callback")
    
    # SMTP Settings
    SMTP_HOST: str = os.getenv("SMTP_HOST", "")
    SMTP_PORT: int = int(os.getenv("SMTP_PORT", "587"))
    SMTP_USER: str = os.getenv("SMTP_USER", "")
    SMTP_PASSWORD: str = os.getenv("SMTP_PASSWORD", "")
    SMTP_FROM: str = os.getenv("SMTP_FROM", "no-reply@nationaldepo.sim")
    
    # Depository Settings
    OTP_DEV_MODE: bool = os.getenv("OTP_DEV_MODE", "true").lower() in ("true", "1", "yes")
    OTP_EXPIRY_MINUTES: int = int(os.getenv("OTP_EXPIRY_MINUTES", "10"))
    CONSENT_DEFAULT_DAYS: int = int(os.getenv("CONSENT_DEFAULT_DAYS", "90"))
    SEED_STARTER_ACCOUNTS: bool = os.getenv("SEED_STARTER_ACCOUNTS", "true").lower() in ("true", "1", "yes")
    
    # Admin & Ingest
    ADMIN_PASSWORD: str = os.getenv("ADMIN_PASSWORD", "adminsecret123")
    INGEST_API_KEY: str = os.getenv("INGEST_API_KEY", "nd-ingest-secret-key-2026")
    
    # Access Control
    ALLOWED_EMAILS_RAW: str = os.getenv("ALLOWED_EMAILS", "")
    ALLOWED_EMAIL_DOMAINS_RAW: str = os.getenv("ALLOWED_EMAIL_DOMAINS", "")
    
    # App & Database
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///./nationaldepo.db")
    CORS_ORIGINS_RAW: str = os.getenv("CORS_ORIGINS", "http://localhost:3000,http://localhost:8000,http://127.0.0.1:8000")
    BASE_URL: str = os.getenv("BASE_URL", "http://localhost:8000")

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
