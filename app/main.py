import asyncio
import secrets
from datetime import datetime, timezone
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Response
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from app.config import settings
from app.database import engine, Base, SessionLocal
from app.models import AdminSetting
from app.services.seed_service import seed_database
from app.routers import auth, depository, aa_api, internal, admin, health

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create DB tables
    Base.metadata.create_all(bind=engine)
    # Seed database
    db = SessionLocal()
    try:
        seed_database(db)
    finally:
        db.close()
    yield

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

