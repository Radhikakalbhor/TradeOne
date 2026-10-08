import asyncio
import json
import httpx
from datetime import datetime, timezone
from typing import Optional
from app.config import settings
from app.database import SessionLocal
from app.models import WebhookLog
from app.security import compute_hmac_sha256

async def send_webhook_with_retry(
    target_url: str,
    event: str,
    consent_id: str,
    session_id: Optional[str] = None
):
    """Deliver webhook with HMAC-SHA256 signature and up to 3 retry attempts."""
    if not target_url:
        return

    now_iso = datetime.now(timezone.utc).isoformat()
    payload_dict = {
        "event": event,
        "consentId": consent_id,
        "sessionId": session_id or "",
        "occurredAt": now_iso
    }
    payload_json = json.dumps(payload_dict)
    signature = compute_hmac_sha256(payload_json.encode("utf-8"), settings.SECRET_KEY)

    headers = {
        "Content-Type": "application/json",
        "X-ND-Signature": signature,
        "User-Agent": "TradeOne-Webhook/1.0"
    }

    max_attempts = 3
    success = False
    last_status = None
    last_error = None

    for attempt in range(1, max_attempts + 1):
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.post(target_url, content=payload_json, headers=headers)
                last_status = resp.status_code
                if resp.status_code in (200, 201, 202, 204):
                    success = True
                    break
                else:
                    last_error = f"HTTP {resp.status_code}: {resp.text[:100]}"
        except Exception as e:
            last_error = str(e)
            
        if attempt < max_attempts:
            await asyncio.sleep(attempt * 1.5)  # Backoff: 1.5s, 3.0s

    # Log delivery in DB
    db = SessionLocal()
    try:
        log = WebhookLog(
            event=event,
            consent_id=consent_id,
            session_id=session_id,
            target_url=target_url,
            payload=payload_json,
            signature=signature,
            status_code=last_status,
            attempts=attempt,
            success=success,
            error_message=last_error if not success else None,
            timestamp=datetime.now(timezone.utc)
        )
        db.add(log)
        db.commit()
    except Exception as e:
        print(f"[TradeOne Webhook Logging Error]: {e}")
    finally:
        db.close()

def trigger_webhook_event(
    target_url: str,
    event: str,
    consent_id: str,
    session_id: Optional[str] = None
):
    """Trigger background webhook task safely."""
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(send_webhook_with_retry(target_url, event, consent_id, session_id))
    except RuntimeError:
        # If no loop running, create one
        asyncio.run(send_webhook_with_retry(target_url, event, consent_id, session_id))
