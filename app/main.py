import asyncio
import secrets
from datetime import datetime, timezone
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Response
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

import logging
from app.config import settings
from app.database import engine, Base, SessionLocal
from app.models import AdminSetting
from app.services.seed_service import seed_database
from app.routers import auth, depository, aa_api, internal, admin, health

logger = logging.getLogger("app.main")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Validate production configuration if running in production
    settings.validate_production_configuration()

    # Diagnostic email provider logging on startup (safe, no secrets)
    if settings.RESEND_API_KEY:
        logger.info("Email OTP provider: Resend HTTPS API (sender: %s)", settings.RESEND_FROM)
    elif settings.SMTP_HOST and settings.SMTP_USER and settings.SMTP_PASSWORD:
        logger.info("Email OTP provider: SMTP fallback (host: %s, port: %s)", settings.SMTP_HOST, settings.SMTP_PORT)
    else:
        logger.warning("Email OTP provider: Neither Resend nor SMTP is configured.")

    # Create DB tables
    Base.metadata.create_all(bind=engine)
    # Ensure columns exist on existing DBs
    try:
        from sqlalchemy import inspect, text
        insp = inspect(engine)
        tables = insp.get_table_names()
        if "users" in tables:
            cols = [c["name"] for c in insp.get_columns("users")]
            if "wallet_balance" not in cols:
                with engine.connect() as conn:
                    conn.execute(text("ALTER TABLE users ADD COLUMN wallet_balance FLOAT DEFAULT 1000000.0"))
                    conn.commit()
        if "demat_accounts" in tables:
            d_cols = [c["name"] for c in insp.get_columns("demat_accounts")]
            with engine.connect() as conn:
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
                with engine.connect() as conn:
                    conn.execute(text("ALTER TABLE holdings ADD COLUMN metadata_json TEXT"))
                    conn.commit()
    except Exception as e:
        pass


    # Seed database
    db = SessionLocal()
    try:
        seed_database(db)
    finally:
        db.close()

    # Launch 30-minute periodic broker sync background worker
    sync_task = asyncio.create_task(periodic_broker_sync_loop())
    try:
        yield
    finally:
        sync_task.cancel()
        try:
            await sync_task
        except asyncio.CancelledError:
            pass

_periodic_sync_running = False

async def periodic_broker_sync_loop():
    """Runs periodic 5-minute portfolio reconciliation across sibling brokers.
    Prevents overlapping sync jobs, uses snapshot replacement, handles failures independently.
    """
    global _periodic_sync_running
    while True:
        try:
            await asyncio.sleep(300)  # 5 minutes
            if _periodic_sync_running:
                continue
            _periodic_sync_running = True

            def do_sync():
                db = SessionLocal()
                try:
                    from app.services.broker_adapter import broker_adapter
                    from app.models import User
                    users = db.query(User).all()
                    for u in users:
                        try:
                            broker_adapter.sync_all_brokers(db, u, only_stale=True, max_age_minutes=5)
                        except Exception:
                            pass
                finally:
                    db.close()

            await asyncio.to_thread(do_sync)
        except asyncio.CancelledError:
            break
        except Exception:
            pass
        finally:
            _periodic_sync_running = False

app = FastAPI(
    title="TradeOne",
    description="Simulated Indian Depository Sandbox (NSDL/CDSL Mock) with Account Aggregator data sharing.",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan
)

# Proxy headers for Render and reverse proxies
app.add_middleware(ProxyHeadersMiddleware, trusted_hosts=["*"])

# CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Admin Outage & Slow Mode Middleware
@app.middleware("http")
async def admin_simulation_middleware(request: Request, call_next):
    # Exclude admin and static endpoints from outage
    path = request.url.path
    if not path.startswith("/admin") and not path.startswith("/static"):
        db = SessionLocal()
        try:
            # Check slow mode
            slow_mode = db.query(AdminSetting).filter(AdminSetting.key == "slow_mode").first()
            if slow_mode and slow_mode.value.lower() == "true":
                await asyncio.sleep(5.0)

            # Check simulated outage
            outage = db.query(AdminSetting).filter(AdminSetting.key == "simulate_outage").first()
            if outage and outage.value.lower() == "true":
                ref_id = f"ref_{secrets.token_hex(4)}"
                if path.startswith("/aa/v1") or path.startswith("/internal/v1"):
                    return JSONResponse(
                        status_code=503,
                        content={"code": "SERVICE_UNAVAILABLE", "message": "Simulated depository maintenance outage in progress.", "ref": ref_id}
                    )
                return Response(
                    content="<html><body style='font-family:sans-serif;text-align:center;padding:50px;'><h1>503 Service Unavailable</h1><p>TradeOne simulated maintenance outage is currently active.</p></body></html>",
                    status_code=503,
                    media_type="text/html"
                )
        finally:
            db.close()

    response = await call_next(request)
    return response

# Mount Static files
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# Include Routers
app.include_router(health.router)
app.include_router(auth.router)
app.include_router(depository.router)
app.include_router(aa_api.router)
app.include_router(internal.router)
app.include_router(admin.router)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True, reload_includes=["*.env"])

