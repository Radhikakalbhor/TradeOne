import random
import secrets
from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from sqlalchemy.orm import Session
from app.config import settings
from app.database import get_db
from app.models import (
    User, DematAccount, Holding, Instrument, Transaction, Consent
)
from app.schemas import IngestHoldingsRequest, ProvisionUserRequest, InternalEventRequest
from app.services.seed_service import provision_new_user
from app.services.webhook_service import trigger_webhook_event
from app.services.outbox_service import enqueue_outbox_event, dispatch_pending_outbox
from app.services.depository_service import get_portfolio_summary
from app.routers.depository import serialize_user_profile, serialize_user_holdings
from app.shared_identity import normalize_email
from app.security import check_rate_limit

router = APIRouter(prefix="/internal/v1", tags=["internal"])

def require_internal_key(
    request: Request,
    x_internal_key: Optional[str] = Header(None, alias="x-internal-key"),
    db: Session = Depends(get_db)
):
    """Authenticate internal server-to-server requests using constant-time comparison.

    Enforces INTERNAL_API_ENABLED check and 60 requests/minute rate limit.
    Never logs the internal key.
    """
    if not settings.INTERNAL_API_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "INTERNAL_API_DISABLED", "message": "Internal API is disabled on this server."}
        )

    if not x_internal_key or not secrets.compare_digest(x_internal_key, settings.INTERNAL_API_KEY):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "UNAUTHORIZED", "message": "Invalid or missing x-internal-key header."}
        )

    client_ip = request.client.host if request.client else "unknown"
    if not check_rate_limit(db, f"internal_api:{client_ip}", "internal_call", max_requests=60, window_seconds=60):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"code": "RATE_LIMIT_EXCEEDED", "message": "Internal rate limit of 60 req/min exceeded."}
        )

@router.post("/users/provision", dependencies=[Depends(require_internal_key)])
def provision_user(
    payload: ProvisionUserRequest,
    db: Session = Depends(get_db)
):
    """Idempotently find or provision a user by normalized email.

    Returns {"status": "CREATED" | "EXISTS", "client_code": "...", "email": "..."}
    """
    email = normalize_email(payload.email)
    if not email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_EMAIL", "message": "Email is required."}
        )

    user = db.query(User).filter(User.email == email).first()
    if user:
        return {
            "status": "EXISTS",
            "client_code": user.bo_id,
            "email": normalize_email(user.email)
        }

    user = provision_new_user(db, email=email, name=payload.full_name, provider="internal")
    return {
        "status": "CREATED",
        "client_code": user.bo_id,
        "email": normalize_email(user.email)
    }

@router.get("/users/{email}/profile", dependencies=[Depends(require_internal_key)])
def get_user_profile(
    email: str,
    db: Session = Depends(get_db)
):
    """Returns normalized identity profile for the requested user."""
    norm_email = normalize_email(email)
    user = db.query(User).filter(User.email == norm_email).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "USER_NOT_FOUND", "message": f"User {norm_email} not found."}
        )

    return serialize_user_profile(user, db)

@router.get("/users/{email}/holdings", dependencies=[Depends(require_internal_key)])
def get_user_holdings_internal(
    email: str,
    dp: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    merge: Optional[str] = Query(None),
    db: Session = Depends(get_db)
):
    """Returns holdings for the requested user in the exact same format as the public holdings endpoint."""
    norm_email = normalize_email(email)
    user = db.query(User).filter(User.email == norm_email).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "USER_NOT_FOUND", "message": f"User {norm_email} not found."}
        )

    merge_by_isin = (merge == "true" or merge == "1")
    return serialize_user_holdings(db, user, dp=dp, page=page, page_size=page_size, merge_by_isin=merge_by_isin)

@router.get("/users/{email}/summary", dependencies=[Depends(require_internal_key)])
def get_user_summary(
    email: str,
    db: Session = Depends(get_db)
):
    """Returns totals (invested, current value, day change) for portfolio reconciliation."""
    norm_email = normalize_email(email)
    user = db.query(User).filter(User.email == norm_email).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "USER_NOT_FOUND", "message": f"User {norm_email} not found."}
        )

    summary = get_portfolio_summary(db, user)
    return {
        "email": norm_email,
        "invested": round(summary["total_invested"], 2),
        "current_value": round(summary["total_value"], 2),
        "day_change": round(summary["day_change"], 2),
        "day_change_pct": round(summary["day_change_pct"], 2),
        "total_pnl": round(summary["total_pnl"], 2),
        "total_pnl_pct": round(summary["total_pnl_pct"], 2)
    }

import time
from threading import Lock

_webhook_sync_timestamps = {}
_webhook_lock = Lock()

@router.post("/events", dependencies=[Depends(require_internal_key)])
def receive_internal_event(
    payload: InternalEventRequest,
    db: Session = Depends(get_db)
):
    """Receive real-time change events from sibling sandbox brokers.

    Signals TradeOne to refresh the affected broker's data and dispatch AA webhooks.
    Includes a 5-second debounce window per (user, provider) to coalesce rapid bursts.
    """
    norm_email = normalize_email(payload.email)
    user = db.query(User).filter(User.email == norm_email).first()

    webhooks_triggered = 0
    sync_result = None
    provider_code = (payload.provider or "").lower().strip()

    if user and payload.event == "HOLDINGS_CHANGED":
        # Re-fetch only the affected provider (non-destructive with 5s debounce)
        if provider_code in ("a", "b", "c"):
            key = (norm_email, provider_code)
            now_ts = time.time()
            should_sync = True
            with _webhook_lock:
                last_ts = _webhook_sync_timestamps.get(key, 0.0)
                if now_ts - last_ts < 5.0:
                    should_sync = False
                else:
                    _webhook_sync_timestamps[key] = now_ts

            if should_sync:
                from app.services.broker_adapter import broker_adapter
                sync_result = broker_adapter.sync_user_from_broker(db, user, provider_code)
            else:
                sync_result = {"provider": provider_code, "status": "debounced", "message": "Debounced within 5s window"}

        active_consents = db.query(Consent).filter(
            Consent.user_id == user.id,
            Consent.status == "ACTIVE"
        ).all()
        for c in active_consents:
            trigger_webhook_event(
                target_url=c.webhook_url,
                event="HOLDINGS_CHANGED",
                consent_id=c.consent_id
            )
            webhooks_triggered += 1

    return {
        "status": "RECEIVED",
        "provider": payload.provider,
        "email": norm_email,
        "event": payload.event,
        "syncResult": sync_result,
        "webhooksNotified": webhooks_triggered
    }


@router.post("/ingest/holdings")
def ingest_holding_change(
    payload: IngestHoldingsRequest,
    x_api_key: Optional[str] = Header(None, alias="x-api-key"),
    x_internal_key: Optional[str] = Header(None, alias="x-internal-key"),
    db: Session = Depends(get_db)
):
    """Sync holdings from mock brokers. Creates account/holding if missing and records transaction."""
    auth_ok = False
    if x_api_key and secrets.compare_digest(x_api_key, settings.INGEST_API_KEY):
        auth_ok = True
    elif x_internal_key and secrets.compare_digest(x_internal_key, settings.INTERNAL_API_KEY):
        auth_ok = True

    if not auth_ok:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "UNAUTHORIZED", "message": "Invalid or missing x-api-key or x-internal-key header."}
        )

    email = normalize_email(payload.email)
    user = db.query(User).filter(User.email == email).first()
    if not user:
        user = provision_new_user(db, email=email)

    # Identify provider code from dpId or payload
    dp_code_map = {"IN300001": "a", "IN300002": "b", "IN300003": "c"}
    provider_code = dp_code_map.get(payload.dpId, "a")

    # Ingest triggers only a fresh snapshot pull of that provider, never delta additions
    from app.services.broker_adapter import broker_adapter
    sync_result = broker_adapter.sync_user_from_broker(db, user, provider_code)

    # Fire HOLDINGS_CHANGED webhooks for active consents of this user
    active_consents = db.query(Consent).filter(
        Consent.user_id == user.id,
        Consent.status == "ACTIVE"
    ).all()

    for c in active_consents:
        trigger_webhook_event(
            target_url=c.webhook_url,
            event="HOLDINGS_CHANGED",
            consent_id=c.consent_id
        )

    return {
        "status": "SUCCESS",
        "action": "SNAPSHOT_RESYNC",
        "provider": provider_code,
        "email": email,
        "syncResult": sync_result,
        "webhooksNotified": len(active_consents)
    }
