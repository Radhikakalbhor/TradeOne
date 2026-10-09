from datetime import datetime, timezone
from sqlalchemy import (
    Column, Integer, String, Float, Boolean, DateTime, ForeignKey, Text
)
from sqlalchemy.orm import relationship
from app.database import Base

def utcnow():
    return datetime.now(timezone.utc)

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), unique=True, index=True, nullable=False)
    name = Column(String(255), nullable=False)
    bo_id = Column(String(32), unique=True, nullable=False)  # 16-digit Beneficiary Owner ID
    masked_pan = Column(String(16), nullable=False)
    dob = Column(String(16), nullable=False, default="15081992")  # DDMMYYYY format
    mobile = Column(String(32), nullable=False)
    wallet_balance = Column(Float, default=1000000.0)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    auth_methods = relationship("AuthMethod", back_populates="user", cascade="all, delete-orphan")
    demat_accounts = relationship("DematAccount", back_populates="user", cascade="all, delete-orphan")
    nominees = relationship("Nominee", back_populates="user", cascade="all, delete-orphan")
    consents = relationship("Consent", back_populates="user", cascade="all, delete-orphan")
    sessions = relationship("UserSession", back_populates="user", cascade="all, delete-orphan")

class AuthMethod(Base):
    __tablename__ = "auth_methods"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    provider = Column(String(32), nullable=False)  # email, google
    provider_sub = Column(String(255), nullable=True)
    email = Column(String(255), nullable=False)
    created_at = Column(DateTime, default=utcnow)

    user = relationship("User", back_populates="auth_methods")

class EmailOtp(Base):
    __tablename__ = "email_otps"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), index=True, nullable=False)
    code_hash = Column(String(255), nullable=False)
    attempts = Column(Integer, default=0)
    created_at = Column(DateTime, default=utcnow)
    expires_at = Column(DateTime, nullable=False)
    used = Column(Boolean, default=False)

class RateLimitLog(Base):
    __tablename__ = "rate_limit_logs"

    id = Column(Integer, primary_key=True, index=True)
    key = Column(String(255), index=True, nullable=False)  # ip, email, client_id
    action = Column(String(64), nullable=False)
    timestamp = Column(DateTime, default=utcnow)

class UserSession(Base):
    __tablename__ = "user_sessions"

    session_id = Column(String(64), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    ip_address = Column(String(64), default="")
    user_agent = Column(String(255), default="")
    created_at = Column(DateTime, default=utcnow)
    last_activity = Column(DateTime, default=utcnow)
    is_active = Column(Boolean, default=True)

    user = relationship("User", back_populates="sessions")

class Nominee(Base):
    __tablename__ = "nominees"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    name = Column(String(255), nullable=False)
    relationship_type = Column(String(64), nullable=False, default="SPOUSE")
    percentage = Column(Integer, default=100)
    dob = Column(String(16), nullable=True)
    guardian_name = Column(String(255), nullable=True)

    user = relationship("User", back_populates="nominees")

class Instrument(Base):
    __tablename__ = "instruments"

    isin = Column(String(12), primary_key=True, index=True)
    symbol = Column(String(32), index=True, nullable=False)
    name = Column(String(255), nullable=False)
    isin_description = Column(String(64), nullable=False, default="EQUITY SHARES")
    asset_class = Column(String(32), nullable=False)  # EQUITY, REIT, INVIT, ETF, BOND, MUTUAL_FUND
    fi_type = Column(String(32), nullable=False)      # EQUITIES, REIT, INVIT, ETF, BONDS, MUTUAL_FUNDS
    last_price = Column(Float, nullable=False, default=0.0)
    prev_close = Column(Float, nullable=False, default=0.0)
    change_pct = Column(Float, nullable=False, default=0.0)

    holdings = relationship("Holding", back_populates="instrument")
    transactions = relationship("Transaction", back_populates="instrument")
    corporate_actions = relationship("CorporateAction", back_populates="instrument")

class DematAccount(Base):
    __tablename__ = "demat_accounts"

    id = Column(String(64), primary_key=True)  # e.g. da_5521
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    dp_name = Column(String(255), nullable=False)
    dp_id = Column(String(32), nullable=False)
    account_number = Column(String(32), nullable=False)
    masked_account_number = Column(String(32), nullable=False)
    account_type = Column(String(32), default="INDIVIDUAL")  # INDIVIDUAL, JOINT
    status = Column(String(32), default="ACTIVE")           # ACTIVE, FROZEN
    opened_date = Column(String(32), default="2021-04-12")
    nominee_status = Column(String(32), default="REGISTERED") # REGISTERED, NOT_REGISTERED
    provider_code = Column(String(32), nullable=True) # a, b, c
    sync_status = Column(String(32), default="connected") # connected, stale, unavailable
    last_synced_at = Column(DateTime, nullable=True)
    sync_error = Column(Text, nullable=True)
    connection_method = Column(String(64), default="Auto-linked (demo)")

    user = relationship("User", back_populates="demat_accounts")

    @property
    def effective_connection_method(self) -> str:
        """Prefers consent connection if active consent exists, otherwise auto-linked."""
        if self.user and self.user.consents:
            for c in self.user.consents:
                if c.status == "ACTIVE":
                    try:
                        import json
                        sel_accs = json.loads(c.selected_account_ids_json or "[]")
                        if not sel_accs or self.id in sel_accs or self.dp_id in sel_accs:
                            return "Connected via consent"
                    except Exception:
                        return "Connected via consent"
        return self.connection_method or "Auto-linked (demo)"
    holdings = relationship("Holding", back_populates="demat_account", cascade="all, delete-orphan")
    transactions = relationship("Transaction", back_populates="demat_account", cascade="all, delete-orphan")

class Holding(Base):
    __tablename__ = "holdings"

    id = Column(Integer, primary_key=True, index=True)
    demat_account_id = Column(String(64), ForeignKey("demat_accounts.id"), nullable=False)
    isin = Column(String(12), ForeignKey("instruments.isin"), nullable=False)
    free_units = Column(Float, default=0.0)
    pledged_units = Column(Float, default=0.0)
    locked_units = Column(Float, default=0.0)
    avg_price = Column(Float, default=0.0)
    metadata_json = Column(Text, nullable=True)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    demat_account = relationship("DematAccount", back_populates="holdings")
    instrument = relationship("Instrument", back_populates="holdings")

    @property
    def total_units(self) -> float:
        return self.free_units + self.pledged_units + self.locked_units

    @property
    def current_value(self) -> float:
        price = self.instrument.last_price if self.instrument else 0.0
        return self.total_units * price

    @property
    def investment_value(self) -> float:
        return self.total_units * self.avg_price

class Transaction(Base):
    __tablename__ = "transactions"

    id = Column(Integer, primary_key=True, index=True)
    demat_account_id = Column(String(64), ForeignKey("demat_accounts.id"), nullable=False)
    isin = Column(String(12), ForeignKey("instruments.isin"), nullable=False)
    trans_date = Column(DateTime, default=utcnow)
    quantity = Column(Float, nullable=False)
    price = Column(Float, nullable=False)
    trans_type = Column(String(64), nullable=False) # BUY_SETTLEMENT, SELL_SETTLEMENT, CORPORATE_ACTION, OFF_MARKET_TRANSFER
    reference_id = Column(String(64), nullable=False)
    description = Column(String(255), default="")

    demat_account = relationship("DematAccount", back_populates="transactions")
    instrument = relationship("Instrument", back_populates="transactions")

class CorporateAction(Base):
    __tablename__ = "corporate_actions"

    id = Column(Integer, primary_key=True, index=True)
    isin = Column(String(12), ForeignKey("instruments.isin"), nullable=False)
    action_type = Column(String(32), nullable=False) # DIVIDEND, BONUS, SPLIT, RIGHTS
    record_date = Column(String(32), nullable=False)
    ratio_or_amount = Column(String(64), nullable=False)
    description = Column(String(255), default="")

    instrument = relationship("Instrument", back_populates="corporate_actions")

class RegisteredApp(Base):
    __tablename__ = "registered_apps"

    client_id = Column(String(64), primary_key=True)
    client_secret_hash = Column(String(255), nullable=False)
    name = Column(String(255), nullable=False)
    allowed_redirect_uris = Column(Text, default="*")
    allowed_webhook_uris = Column(Text, default="*")

    consents = relationship("Consent", back_populates="app")

class Consent(Base):
    __tablename__ = "consents"

    consent_id = Column(String(64), primary_key=True) # cns_...
    consent_handle = Column(String(64), unique=True, index=True, nullable=False) # ch_...
    client_id = Column(String(64), ForeignKey("registered_apps.client_id"), nullable=False)
    customer_email = Column(String(255), index=True, nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    status = Column(String(32), default="PENDING") # PENDING, ACTIVE, PAUSED, REVOKED, EXPIRED, REJECTED
    purpose_code = Column(String(32), nullable=False)
    purpose_text = Column(String(255), nullable=False)
    fi_types_json = Column(Text, nullable=False)
    data_from = Column(String(32), nullable=False)
    data_to = Column(String(32), nullable=False)
    consent_duration_days = Column(Integer, default=90)
    fetch_frequency_unit = Column(String(16), default="DAY")
    fetch_frequency_value = Column(Integer, default=4)
    redirect_url = Column(Text, nullable=False)
    webhook_url = Column(Text, nullable=False)
    created_at = Column(DateTime, default=utcnow)
    approved_at = Column(DateTime, nullable=True)
    expires_at = Column(DateTime, nullable=True)
    selected_account_ids_json = Column(Text, default="[]")
    artefact_json = Column(Text, nullable=True)
    signature = Column(String(255), nullable=True)
    fetch_count_today = Column(Integer, default=0)
    last_fetch_date = Column(String(16), nullable=True)

    app = relationship("RegisteredApp", back_populates="consents")
    user = relationship("User", back_populates="consents")
    data_sessions = relationship("DataSession", back_populates="consent", cascade="all, delete-orphan")
    access_logs = relationship("ConsentAccessLog", back_populates="consent", cascade="all, delete-orphan")

class DataSession(Base):
    __tablename__ = "data_sessions"

    session_id = Column(String(64), primary_key=True) # ses_...
    consent_id = Column(String(64), ForeignKey("consents.consent_id"), nullable=False)
    status = Column(String(32), default="PENDING") # PENDING, READY, FAILED
    created_at = Column(DateTime, default=utcnow)
    ready_at = Column(DateTime, nullable=True)
    data_from = Column(String(32), nullable=True)
    data_to = Column(String(32), nullable=True)
    data_json = Column(Text, nullable=True)

    consent = relationship("Consent", back_populates="data_sessions")

class ConsentAccessLog(Base):
    __tablename__ = "consent_access_logs"

    id = Column(Integer, primary_key=True, index=True)
    consent_id = Column(String(64), ForeignKey("consents.consent_id"), nullable=False)
    session_id = Column(String(64), nullable=True)
    timestamp = Column(DateTime, default=utcnow)
    ip_address = Column(String(64), default="")
    action = Column(String(64), nullable=False)
    status_code = Column(Integer, default=200)
    details = Column(String(255), default="")

    consent = relationship("Consent", back_populates="access_logs")

class WebhookLog(Base):
    __tablename__ = "webhook_logs"

    id = Column(Integer, primary_key=True, index=True)
    event = Column(String(64), nullable=False)
    consent_id = Column(String(64), nullable=False)
    session_id = Column(String(64), nullable=True)
    target_url = Column(Text, nullable=False)
    payload = Column(Text, nullable=False)
    signature = Column(String(255), nullable=False)
    status_code = Column(Integer, nullable=True)
    attempts = Column(Integer, default=1)
    success = Column(Boolean, default=False)
    error_message = Column(Text, nullable=True)
    timestamp = Column(DateTime, default=utcnow)

class AdminSetting(Base):
    __tablename__ = "admin_settings"

    key = Column(String(64), primary_key=True)
    value = Column(String(255), nullable=False)

class OutboxEvent(Base):
    __tablename__ = "outbox_events"

    id = Column(Integer, primary_key=True, index=True)
    provider = Column(String(32), nullable=False)
    email = Column(String(255), index=True, nullable=False)
    event = Column(String(64), nullable=False, default="HOLDINGS_CHANGED")
    occurred_at = Column(DateTime, default=utcnow)
    status = Column(String(32), default="PENDING")  # PENDING, SENT, FAILED
    attempts = Column(Integer, default=0)
    max_attempts = Column(Integer, default=10)
    next_retry_at = Column(DateTime, default=utcnow)
    last_error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utcnow)
