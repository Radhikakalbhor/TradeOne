import asyncio
import secrets
import json
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any
from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, BackgroundTasks, status
from sqlalchemy.orm import Session
from app.config import settings
from app.database import get_db
from app.models import (
    RegisteredApp, Consent, DataSession, DematAccount, Holding,
    Instrument, ConsentAccessLog, AdminSetting
)
from app.schemas import (
    ConsentCreateRequest, ConsentCreateResponse, ConsentDetailResponse,
    DataSessionCreateRequest, DataSessionCreateResponse
)
from app.security import (
    verify_secret, check_rate_limit, sign_data
)
from app.services.aa_serializer import serialize_fi_data
from app.services.webhook_service import trigger_webhook_event

router = APIRouter(prefix="/aa/v1", tags=["Account Aggregator"])

def make_error_response(code: str, message: str, status_code: int = 400):
    ref_id = f"ref_{secrets.token_hex(4)}"
    raise HTTPException(
        status_code=status_code,
        detail={"code": code, "message": message, "ref": ref_id}
    )

def authenticate_client(
    db: Session = Depends(get_db),
    x_client_id: Optional[str] = Header(None, alias="x-client-id"),
    x_client_secret: Optional[str] = Header(None, alias="x-client-secret")
) -> RegisteredApp:
    """Validate client credentials and enforce 60 req/min rate limit."""
    if not x_client_id or not x_client_secret:
        make_error_response("UNAUTHORIZED", "Missing x-client-id or x-client-secret header.", 401)

    app = db.query(RegisteredApp).filter(RegisteredApp.client_id == x_client_id).first()
    if not app or not verify_secret(x_client_secret, app.client_secret_hash):
        make_error_response("INVALID_CLIENT", "Invalid client credentials.", 401)

    # 60 requests/minute rate limit per client
    if not check_rate_limit(db, f"aa_client:{x_client_id}", "api_call", max_requests=60, window_seconds=60):
        make_error_response("RATE_LIMIT_EXCEEDED", "Client rate limit of 60 req/min exceeded.", 429)

    return app

@router.post("/consents", response_model=ConsentCreateResponse)
def create_consent(
    payload: ConsentCreateRequest,
    request: Request,
    app: RegisteredApp = Depends(authenticate_client),
    db: Session = Depends(get_db)
):
    """Step 1: External app initiates consent request."""
    consent_handle = f"ch_{secrets.token_hex(8)}"
    consent_id = f"cns_{secrets.token_hex(8)}"
    
    # Store pending consent
    consent = Consent(
        consent_id=consent_id,
        consent_handle=consent_handle,
        client_id=app.client_id,
        customer_email=payload.customerEmail.lower().strip(),
        status="PENDING",
        purpose_code=payload.purpose.code,
        purpose_text=payload.purpose.text,
        fi_types_json=json.dumps(payload.fiTypes),
        data_from=payload.dataRange.from_,
        data_to=payload.dataRange.to,
        consent_duration_days=payload.consentDurationDays,
        fetch_frequency_unit=payload.fetchFrequency.unit,
        fetch_frequency_value=payload.fetchFrequency.value,
        redirect_url=payload.redirectUrl,
        webhook_url=payload.webhookUrl,
        created_at=datetime.now(timezone.utc),
        fetch_count_today=0
    )
    db.add(consent)
    db.commit()

    # Form approval URL
    base = settings.BASE_URL.rstrip("/")
    approval_url = f"{base}/consent/approve?handle={consent_handle}"

    return ConsentCreateResponse(
        consentHandle=consent_handle,
        status="PENDING",
        approvalUrl=approval_url
    )

@router.get("/consents/{consent_handle}")
def get_consent_status(
    consent_handle: str,
    app: RegisteredApp = Depends(authenticate_client),
    db: Session = Depends(get_db)
):
    """Step 3: External app polls or fetches consent status & signed artefact."""
    consent = db.query(Consent).filter(
        Consent.consent_handle == consent_handle,
        Consent.client_id == app.client_id
    ).first()

    if not consent:
        make_error_response("CONSENT_NOT_FOUND", "Consent handle not found.", 404)

    # Check for expiration
    now = datetime.now(timezone.utc)
    if consent.status == "ACTIVE" and consent.expires_at and consent.expires_at.replace(tzinfo=timezone.utc) < now:
        consent.status = "EXPIRED"
        db.commit()
        trigger_webhook_event(consent.webhook_url, "CONSENT_EXPIRED", consent.consent_id)

    artefact = json.loads(consent.artefact_json) if consent.artefact_json else None

    return {
        "consentHandle": consent.consent_handle,
        "consentId": consent.consent_id if consent.status == "ACTIVE" else None,
        "status": consent.status,
        "artefact": artefact,
        "signature": consent.signature
    }

async def process_data_session_background(session_id: str):
    """Simulate data preparation delay and generate scoped FI data."""
    db = next(get_db())
    try:
        session = db.query(DataSession).filter(DataSession.session_id == session_id).first()
        if not session:
            return

        consent = session.consent

        # Check admin delay setting
        delay_setting = db.query(AdminSetting).filter(AdminSetting.key == "data_prep_delay").first()
        delay_sec = int(delay_setting.value) if delay_setting and delay_setting.value.isdigit() else 4

        # Check fail setting
        fail_setting = db.query(AdminSetting).filter(AdminSetting.key == "fail_next_session").first()
        should_fail = (fail_setting.value.lower() == "true") if fail_setting else False
        if should_fail:
            fail_setting.value = "false"
            db.commit()

        await asyncio.sleep(delay_sec)

        if should_fail:
            session.status = "FAILED"
            db.commit()
            return

        # Prepare data
        user = consent.user
        selected_account_ids = json.loads(consent.selected_account_ids_json) if consent.selected_account_ids_json else []
        allowed_fi_types = json.loads(consent.fi_types_json) if consent.fi_types_json else []

        accounts = db.query(DematAccount).filter(
            DematAccount.id.in_(selected_account_ids)
        ).all()

        fi_data = serialize_fi_data(
            session=session,
            consent=consent,
            user=user,
            accounts=accounts,
            allowed_fi_types=allowed_fi_types
        )

        session.data_json = json.dumps(fi_data)
        session.status = "READY"
        session.ready_at = datetime.now(timezone.utc)
        db.commit()

        # Fire DATA_READY webhook
        trigger_webhook_event(
            target_url=consent.webhook_url,
            event="DATA_READY",
            consent_id=consent.consent_id,
            session_id=session.session_id
        )
    finally:
        db.close()

@router.post("/sessions", response_model=DataSessionCreateResponse)
def create_data_session(
    payload: DataSessionCreateRequest,
    background_tasks: BackgroundTasks,
    app: RegisteredApp = Depends(authenticate_client),
    db: Session = Depends(get_db)
):
    """Step 4: Request data session against an approved consent."""
    consent = db.query(Consent).filter(
        Consent.consent_id == payload.consentId,
        Consent.client_id == app.client_id
    ).first()

    if not consent:
        make_error_response("CONSENT_NOT_FOUND", "Consent ID not found.", 404)

    now = datetime.now(timezone.utc)

    # Status validations
    if consent.status == "EXPIRED" or (consent.expires_at and consent.expires_at.replace(tzinfo=timezone.utc) < now):
        consent.status = "EXPIRED"
        db.commit()
        make_error_response("CONSENT_EXPIRED", "The consent has expired.", 403)

    if consent.status == "REVOKED":
        make_error_response("CONSENT_REVOKED", "The consent has been revoked by user.", 403)

    if consent.status == "PAUSED":
        make_error_response("CONSENT_PAUSED", "Data sharing has been paused by user.", 403)

    if consent.status != "ACTIVE":
        make_error_response("CONSENT_NOT_ACTIVE", f"Consent is not active (current status: {consent.status}).", 403)

    # Enforce fetch frequency (e.g. 4 fetches/day)
    today_str = now.strftime("%Y-%m-%d")
    if consent.last_fetch_date != today_str:
        consent.last_fetch_date = today_str
        consent.fetch_count_today = 0

    if consent.fetch_count_today >= consent.fetch_frequency_value:
        make_error_response(
            "FETCH_LIMIT_EXCEEDED",
            f"Daily fetch frequency limit of {consent.fetch_frequency_value} reached.",
            429
        )

    consent.fetch_count_today += 1
    session_id = f"ses_{secrets.token_hex(4)}"

    data_session = DataSession(
        session_id=session_id,
        consent_id=consent.consent_id,
        status="PENDING",
        created_at=now,
        data_from=payload.dataRange.get("from") if payload.dataRange else consent.data_from,
        data_to=payload.dataRange.get("to") if payload.dataRange else consent.data_to
    )
    db.add(data_session)
    db.commit()

    # Log action
    db.add(ConsentAccessLog(
        consent_id=consent.consent_id,
        session_id=session_id,
        action="CREATE_SESSION",
        status_code=200,
        details="Data session initiated"
    ))
    db.commit()

    # Trigger background preparation
    background_tasks.add_task(process_data_session_background, session_id)

    return DataSessionCreateResponse(
        sessionId=session_id,
        status="PENDING"
    )

@router.get("/sessions/{session_id}/data")
def fetch_session_data(
    session_id: str,
    request: Request,
    app: RegisteredApp = Depends(authenticate_client),
    db: Session = Depends(get_db)
):
    """Step 5: Fetch structured FI data. Returns 202 if PENDING, 200 when READY."""
    session = db.query(DataSession).filter(DataSession.session_id == session_id).first()
    if not session or session.consent.client_id != app.client_id:
        make_error_response("SESSION_NOT_FOUND", "Data session not found.", 404)

    consent = session.consent

    # Log access
    client_ip = request.client.host if request.client else "unknown"

    if session.status == "PENDING":
        db.add(ConsentAccessLog(
            consent_id=consent.consent_id,
            session_id=session_id,
            ip_address=client_ip,
            action="FETCH_DATA",
            status_code=202,
            details="Data preparation still pending"
        ))
        db.commit()
        return Response(
            content=json.dumps({"status": "PENDING", "message": "Data preparation in progress"}),
            status_code=202,
            media_type="application/json"
        )

    if session.status == "FAILED":
        db.add(ConsentAccessLog(
            consent_id=consent.consent_id,
            session_id=session_id,
            ip_address=client_ip,
            action="FETCH_DATA",
            status_code=500,
            details="Data session processing failed"
        ))
        db.commit()
        make_error_response("DATA_FETCH_FAILED", "Data session processing failed.", 500)

    # Status READY
    db.add(ConsentAccessLog(
        consent_id=consent.consent_id,
        session_id=session_id,
        ip_address=client_ip,
        action="FETCH_DATA",
        status_code=200,
        details="Data fetched successfully"
    ))
    db.commit()

    return Response(
        content=session.data_json,
        status_code=200,
        media_type="application/json"
    )

@router.post("/consents/{consent_id}/revoke")
def revoke_consent_api(
    consent_id: str,
    app: RegisteredApp = Depends(authenticate_client),
    db: Session = Depends(get_db)
):
    """Step 6: External app programmatically revokes consent."""
    consent = db.query(Consent).filter(
        Consent.consent_id == consent_id,
        Consent.client_id == app.client_id
    ).first()

    if not consent:
        make_error_response("CONSENT_NOT_FOUND", "Consent not found.", 404)

    consent.status = "REVOKED"
    db.commit()

    # Fire webhook
    trigger_webhook_event(
        target_url=consent.webhook_url,
        event="CONSENT_REVOKED",
        consent_id=consent.consent_id
    )

    return {"consentId": consent_id, "status": "REVOKED", "message": "Consent revoked successfully."}
