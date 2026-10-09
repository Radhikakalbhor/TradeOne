import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient
from app.main import app
from app.database import SessionLocal, Base, engine
from app.models import DematAccount, Holding, Transaction
from app.services.seed_service import seed_database
from app.security import compute_hmac_sha256
from app.config import settings

client = TestClient(app)

@pytest.fixture(scope="module", autouse=True)
def setup_seed():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    seed_database(db)
    db.close()

def test_ingest_holdings_endpoint():
    db = SessionLocal()
    try:
        # Reset holding to seeded value (15 units) so this test is idempotent
        holding = db.query(Holding).filter(
            Holding.demat_account_id == "da_5521",
            Holding.isin == "INE467B01029"
        ).first()
        if holding:
            holding.free_units = 15.0
            db.commit()
            db.refresh(holding)
        initial_units = holding.free_units if holding else 0.0

        # Ingest a buy settlement of 10 units TCS for Aarav Mehta at NiftyTrade
        payload = {
            "email": "aarav.mehta@example.com",
            "dpName": "NiftyTrade Securities",
            "dpId": "IN300001",
            "maskedAccNumber": "XXXX5521",
            "isin": "INE467B01029",
            "quantityDelta": 10.0,
            "avgPrice": 4100.0,
            "reason": "BUY_SETTLEMENT"
        }

        # Without API key -> 401
        res_no_key = client.post("/internal/v1/ingest/holdings", json=payload)
        assert res_no_key.status_code == 401

        # With valid API key
        headers = {"x-api-key": settings.INGEST_API_KEY}
        with patch("app.services.broker_adapter.broker_adapter.sync_user_from_broker") as mock_sync:
            mock_sync.return_value = {"provider": "a", "status": "connected", "holdings_count": 1}
            res = client.post("/internal/v1/ingest/holdings", json=payload, headers=headers)
            assert res.status_code == 200
            data = res.json()
            assert data["status"] == "SUCCESS"
            assert data["action"] == "SNAPSHOT_RESYNC"
            assert data["provider"] == "a"
            assert mock_sync.called
    finally:
        db.close()

def test_webhook_hmac_signature():
    # Test webhook payload signature matching
    test_body = b'{"event":"DATA_READY","consentId":"cns_123","occurredAt":"2026-10-08T12:00:00Z"}'
    sig1 = compute_hmac_sha256(test_body, settings.SECRET_KEY)
    sig2 = compute_hmac_sha256(test_body, settings.SECRET_KEY)
    assert sig1 == sig2
    assert len(sig1) == 64 # SHA-256 hex digest length
