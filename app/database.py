from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
from app.config import settings

engine = create_engine(
    settings.DATABASE_URL,
    connect_args={"check_same_thread": False} if "sqlite" in settings.DATABASE_URL else {}
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

def ensure_schema(target_engine=engine):
    Base.metadata.create_all(bind=target_engine)
    try:
        from sqlalchemy import inspect, text
        insp = inspect(target_engine)
        tables = insp.get_table_names()
        if "users" in tables:
            cols = [c["name"] for c in insp.get_columns("users")]
            if "wallet_balance" not in cols:
                with target_engine.connect() as conn:
                    conn.execute(text("ALTER TABLE users ADD COLUMN wallet_balance FLOAT DEFAULT 1000000.0"))
                    conn.commit()
        if "demat_accounts" in tables:
            d_cols = [c["name"] for c in insp.get_columns("demat_accounts")]
            with target_engine.connect() as conn:
                if "provider_code" not in d_cols:
                    conn.execute(text("ALTER TABLE demat_accounts ADD COLUMN provider_code VARCHAR(32)"))
                if "sync_status" not in d_cols:
                    conn.execute(text("ALTER TABLE demat_accounts ADD COLUMN sync_status VARCHAR(32) DEFAULT 'connected'"))
                if "last_synced_at" not in d_cols:
                    conn.execute(text("ALTER TABLE demat_accounts ADD COLUMN last_synced_at DATETIME"))
                if "sync_error" not in d_cols:
                    conn.execute(text("ALTER TABLE demat_accounts ADD COLUMN sync_error TEXT"))
                if "connection_method" not in d_cols:
                    conn.execute(text("ALTER TABLE demat_accounts ADD COLUMN connection_method VARCHAR(64) DEFAULT 'Auto-linked (demo)'"))
                conn.commit()
        if "holdings" in tables:
            h_cols = [c["name"] for c in insp.get_columns("holdings")]
            if "metadata_json" not in h_cols:
                with target_engine.connect() as conn:
                    conn.execute(text("ALTER TABLE holdings ADD COLUMN metadata_json TEXT"))
                    conn.commit()
    except Exception:
        pass

ensure_schema(engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
