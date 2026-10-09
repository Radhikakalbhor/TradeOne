import os
import hmac
import httpx
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, List
from sqlalchemy.orm import Session
from app.config import settings
from app.models import OutboxEvent
from app.shared_identity import normalize_email

logger = logging.getLogger(__name__)

def enqueue_outbox_event(
    db: Session,
    email: str,
    event: str = "HOLDINGS_CHANGED",
    provider: Optional[str] = None
) -> Optional[OutboxEvent]:
    """Enqueue a lightweight event into the outbox table.
    
    NEVER blocks or fails the transaction if enqueueing encounters an issue.
    """
    try:
        norm_email = normalize_email(email)
        provider_code = (provider or settings.PROVIDER_CODE or "tradeone").lower().strip()
        now = datetime.now(timezone.utc)
        
        evt = OutboxEvent(
            provider=provider_code,
            email=norm_email,
            event=event,
            occurred_at=now,
            status="PENDING",
            attempts=0,
            max_attempts=10,
            next_retry_at=now
        )
        db.add(evt)
        db.commit()
        db.refresh(evt)
        return evt
    except Exception as e:
        logger.error(f"[Outbox] Failed to enqueue event for {email}: {e}")
        try:
            db.rollback()
        except Exception:
            pass
        return None

def dispatch_pending_outbox(db: Session, max_batch: int = 50) -> int:
    """Synchronously dispatch pending outbox events to TRADEONE_URL.
    
    Safe to call from background worker, tests, or task runners.
    If TRADEONE_URL is not configured, gracefully marks or ignores without failing.
    """
    tradeone_url = settings.TRADEONE_URL
    if not tradeone_url:
        logger.debug("[Outbox] TRADEONE_URL not set; skipping dispatch.")
        return 0

    now = datetime.now(timezone.utc)
    pending_events: List[OutboxEvent] = db.query(OutboxEvent).filter(
        OutboxEvent.status == "PENDING",
        OutboxEvent.attempts < OutboxEvent.max_attempts,
        OutboxEvent.next_retry_at <= now
    ).order_by(OutboxEvent.created_at.asc()).limit(max_batch).all()

    if not pending_events:
        return 0

    target_url = f"{tradeone_url.rstrip('/')}/internal/v1/events"
    headers = {
        "x-internal-key": settings.INTERNAL_API_KEY,
        "Content-Type": "application/json"
    }

    dispatched = 0
    with httpx.Client(timeout=60.0) as client:
        for evt in pending_events:
            evt.attempts += 1
            payload = {
                "provider": evt.provider,
                "email": evt.email,
                "event": evt.event,
                "occurredAt": evt.occurred_at.isoformat() if evt.occurred_at else now.isoformat()
            }
            try:
                resp = client.post(target_url, json=payload, headers=headers)
                if 200 <= resp.status_code < 300:
                    evt.status = "SENT"
                    evt.last_error = None
                    dispatched += 1
                else:
                    backoff_secs = min(3600, 2 ** evt.attempts)
                    evt.next_retry_at = now + timedelta(seconds=backoff_secs)
                    evt.last_error = f"HTTP {resp.status_code}: {resp.text[:200]}"
                    if evt.attempts >= evt.max_attempts:
                        evt.status = "FAILED"
            except Exception as exc:
                backoff_secs = min(3600, 2 ** evt.attempts)
                evt.next_retry_at = now + timedelta(seconds=backoff_secs)
                evt.last_error = str(exc)[:200]
                if evt.attempts >= evt.max_attempts:
                    evt.status = "FAILED"
            
            db.commit()

    return dispatched
