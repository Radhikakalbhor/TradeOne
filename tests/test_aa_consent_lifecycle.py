import json
from datetime import datetime, timezone, timedelta
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.database import SessionLocal, Base, engine
from app.models import Consent, User, RegisteredApp
from app.services.seed_service import seed_database
from app.security import compute_hmac_sha256
from app.config import settings

client = TestClient(app)

@pytest.fixture(scope="module", autouse=True)
def setup_data():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    seed_database(db)
    db.close()

def test_consent_creation_and_approval_flow():
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == "aarav.mehta@example.com").first()
        assert user is not None
        all_accounts = user.demat_accounts
        assert len(all_accounts) == 3

        headers = {
            "x-client-id": "portfolio-aggregator",
            "x-client-secret": "nd-demo-secret"
        }

        # Step 1: Create consent
        create_payload = {
            "purpose": {"code": "101", "text": "Portfolio aggregator analysis"},
            "fiTypes": ["EQUITIES", "REIT", "BONDS"],
            "dataRange": {"from": "2026-01-01", "to": "2026-10-08"},
            "consentDurationDays": 90,
            "fetchFrequency": {"unit": "DAY", "value": 2},
            "customerEmail": "aarav.mehta@example.com",
            "redirectUrl": "http://localhost:8000/callback",
            "webhookUrl": "http://localhost:8000/webhook"
        }
        res = client.post("/aa/v1/consents", json=create_payload, headers=headers)
        assert res.status_code == 200
        data = res.json()
        assert "consentHandle" in data
        assert data["status"] == "PENDING"
        handle = data["consentHandle"]

        # Step 2: Simulate User approving only 1 of their 3 accounts (da_5521)
        consent = db.query(Consent).filter(Consent.consent_handle == handle).first()
        assert consent is not None

        selected_account_ids = ["da_5521"]
        consent.status = "ACTIVE"
        consent.user_id = user.id
        consent.approved_at = datetime.now(timezone.utc)
        consent.expires_at = datetime.now(timezone.utc) + timedelta(days=90)
        consent.selected_account_ids_json = json.dumps(selected_account_ids)
        
        artefact_dict = {
            "consentId": consent.consent_id,
            "consentHandle": consent.consent_handle,
            "selectedAccounts": selected_account_ids
        }
        signature = compute_hmac_sha256(json.dumps(artefact_dict).encode("utf-8"), settings.SECRET_KEY)
        consent.artefact_json = json.dumps(artefact_dict)
        consent.signature = signature
        db.commit()

        # Step 3: Check consent status via API
        res_poll = client.get(f"/aa/v1/consents/{handle}", headers=headers)
        assert res_poll.status_code == 200
        poll_data = res_poll.json()
        assert poll_data["status"] == "ACTIVE"
        assert poll_data["consentId"] == consent.consent_id
        assert poll_data["signature"] is not None

        # Step 4: Create data session
        session_res = client.post(
            "/aa/v1/sessions",
            json={"consentId": consent.consent_id},
            headers=headers
        )
        assert session_res.status_code == 200
        ses_data = session_res.json()
        assert "sessionId" in ses_data
        session_id = ses_data["sessionId"]

        # Step 5: Test fetch frequency limit (limit is 2/day)
        # 2nd call should pass
        res2 = client.post("/aa/v1/sessions", json={"consentId": consent.consent_id}, headers=headers)
        assert res2.status_code == 200

        # 3rd call should trigger 429 FETCH_LIMIT_EXCEEDED
        res3 = client.post("/aa/v1/sessions", json={"consentId": consent.consent_id}, headers=headers)
        assert res3.status_code == 429
        err = res3.json()
        assert err["detail"]["code"] == "FETCH_LIMIT_EXCEEDED"

        # Step 6: Test pause behavior
        consent.status = "PAUSED"
        db.commit()

        res_paused = client.post("/aa/v1/sessions", json={"consentId": consent.consent_id}, headers=headers)
        assert res_paused.status_code == 403
        assert res_paused.json()["detail"]["code"] == "CONSENT_PAUSED"

        # Step 7: Test revoke endpoint
        res_revoked = client.post(f"/aa/v1/consents/{consent.consent_id}/revoke", headers=headers)
        assert res_revoked.status_code == 200
        assert res_revoked.json()["status"] == "REVOKED"

        # After revoke, creating session returns 403 CONSENT_REVOKED
        res_after = client.post("/aa/v1/sessions", json={"consentId": consent.consent_id}, headers=headers)
        assert res_after.status_code == 403
        assert res_after.json()["detail"]["code"] == "CONSENT_REVOKED"
    finally:
        db.close()
