import random
from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.orm import Session
from app.config import settings
from app.database import get_db
from app.models import (
    User, DematAccount, Holding, Instrument, Transaction, Consent
)
from app.schemas import IngestHoldingsRequest
from app.services.seed_service import provision_new_user
from app.services.webhook_service import trigger_webhook_event

router = APIRouter(prefix="/internal/v1", tags=["Internal Broker Ingestion"])

@router.post("/ingest/holdings")
def ingest_holding_change(
    payload: IngestHoldingsRequest,
    x_api_key: Optional[str] = Header(None, alias="x-api-key"),
    db: Session = Depends(get_db)
):
    """Sync holdings from mock brokers. Creates account/holding if missing and records transaction."""
    if not x_api_key or x_api_key != settings.INGEST_API_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "UNAUTHORIZED", "message": "Invalid or missing x-api-key header."}
        )

    email = payload.email.lower().strip()
    user = db.query(User).filter(User.email == email).first()
    if not user:
        user = provision_new_user(db, email=email)

    # 1. Find or create DematAccount
    account = db.query(DematAccount).filter(
        DematAccount.user_id == user.id,
        DematAccount.dp_id == payload.dpId
    ).first()

    if not account:
        acc_id = f"da_{random.randint(1000, 9999)}"
        acc_num = f"1208160000{random.randint(100000, 999999)}"
        account = DematAccount(
            id=acc_id,
            user_id=user.id,
            dp_name=payload.dpName,
            dp_id=payload.dpId,
            account_number=acc_num,
            masked_account_number=payload.maskedAccNumber or f"XXXX{acc_num[-4:]}",
            account_type="INDIVIDUAL",
            status="ACTIVE",
            opened_date=datetime.now().strftime("%Y-%m-%d"),
            nominee_status="REGISTERED"
        )
        db.add(account)
        db.commit()
        db.refresh(account)

    # 2. Check instrument exists
    instrument = db.query(Instrument).filter(Instrument.isin == payload.isin).first()
    if not instrument:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "INSTRUMENT_NOT_FOUND", "message": f"Instrument with ISIN {payload.isin} not found in master."}
        )

    # 3. Find or create Holding
    holding = db.query(Holding).filter(
        Holding.demat_account_id == account.id,
        Holding.isin == payload.isin
    ).first()

    if not holding:
        holding = Holding(
            demat_account_id=account.id,
            isin=payload.isin,
            free_units=0.0,
            pledged_units=0.0,
            locked_units=0.0,
            avg_price=payload.avgPrice or instrument.last_price
        )
        db.add(holding)
        db.commit()
        db.refresh(holding)

    new_free_units = holding.free_units + payload.quantityDelta
    if new_free_units < -1e-6:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "INSUFFICIENT_HOLDINGS",
                "message": f"Holding cannot be negative. Current: {holding.free_units}, delta: {payload.quantityDelta}"
            }
        )

    # Update weighted average price if buying
    if payload.quantityDelta > 0:
        total_prev_cost = holding.free_units * holding.avg_price
        new_cost = payload.quantityDelta * (payload.avgPrice or instrument.last_price)
        holding.avg_price = (total_prev_cost + new_cost) / (holding.free_units + payload.quantityDelta)

    holding.free_units = max(0.0, new_free_units)
    holding.updated_at = datetime.now(timezone.utc)

    # 4. Record statement transaction
    ref_id = f"INGEST-{random.randint(100000, 999999)}"
    txn = Transaction(
        demat_account_id=account.id,
        isin=payload.isin,
        trans_date=datetime.now(timezone.utc),
        quantity=payload.quantityDelta,
        price=payload.avgPrice or instrument.last_price,
        trans_type=payload.reason or "BUY_SETTLEMENT",
        reference_id=ref_id,
        description=f"Broker sync: {payload.dpName} ({payload.reason})"
    )
    db.add(txn)
    db.commit()

    # 5. Fire HOLDINGS_CHANGED webhooks for active consents of this user
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
        "dematAccountId": account.id,
        "isin": payload.isin,
        "updatedFreeUnits": holding.free_units,
        "referenceId": ref_id,
        "webhooksNotified": len(active_consents)
    }
