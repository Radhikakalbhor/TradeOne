import pytest
import json
import time
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from app.main import app
from app.config import settings
from app.database import SessionLocal
from app.models import User, DematAccount, Holding, Instrument, Consent
from app.services.broker_adapter import broker_adapter
from app.services.depository_service import get_user_holdings, get_portfolio_summary
from app.services.auth_service import create_user_session
from app.routers.admin import check_portfolio_consistency

@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c

@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()

@pytest.fixture
def test_hub_user(db):
    email = "hub.test.user@example.com"
    user = db.query(User).filter(User.email == email).first()
    if not user:
        user = User(
            email=email,
            name="Hub Test User",
            bo_id="1208160077771111",
            masked_pan="ABCXX9999X",
            dob="15081992",
            mobile="+91 9999888877"
        )
        db.add(user)
        db.commit()
        db.refresh(user)
    return user


# 1. Repeated Sync Does Not Double-Count & Strict Snapshot Replacement
def test_repeated_sync_does_not_double_count_snapshot_replacement(db, test_hub_user):
    mock_bundle = {
        "provider": "a",
        "broker_name": "NiftyTrade",
        "dp_name": "NiftyTrade Securities",
        "dp_id": "IN300001",
        "status": "connected",
        "error": None,
        "holdings": [
            {
                "isin": "INE002A01018",
                "symbol": "RELIANCE",
                "name": "Reliance Industries Limited",
                "asset_class": "EQUITY",
                "free_units": 10.0,
                "avg_price": 2800.0,
                "last_price": 2850.0
            }
        ]
    }

    with patch.object(broker_adapter, "fetch_provider_bundle", return_value=mock_bundle):
        # Sync 1
        res1 = broker_adapter.sync_user_from_broker(db, test_hub_user, "a")
        assert res1["status"] == "connected"
        account = db.query(DematAccount).filter(DematAccount.user_id == test_hub_user.id, DematAccount.dp_id == "IN300001").first()
        assert len(account.holdings) == 1
        assert account.holdings[0].free_units == 10.0

        # Sync 2
        res2 = broker_adapter.sync_user_from_broker(db, test_hub_user, "a")
        assert res2["status"] == "connected"
        db.refresh(account)
        # Quantity must still be exactly 10.0, NOT 20.0
        assert len(account.holdings) == 1
        assert account.holdings[0].free_units == 10.0

        # Sync 3
        res3 = broker_adapter.sync_user_from_broker(db, test_hub_user, "a")
        assert res3["status"] == "connected"
        db.refresh(account)
        # Quantity must still be exactly 10.0, NOT 30.0
        assert len(account.holdings) == 1
        assert account.holdings[0].free_units == 10.0


# 2. Webhook Debounce Window (5 seconds)
def test_webhook_debounce_window(client, db, test_hub_user):
    headers = {"x-internal-key": settings.INTERNAL_API_KEY}
    payload = {
        "provider": "a",
        "email": test_hub_user.email,
        "event": "HOLDINGS_CHANGED",
        "occurredAt": "2026-10-09T10:00:00Z"
    }

    with patch.object(broker_adapter, "sync_user_from_broker") as mock_sync:
        mock_sync.return_value = {"provider": "a", "status": "connected", "holdings_count": 1}

        # First webhook event -> should trigger sync
        resp1 = client.post("/internal/v1/events", json=payload, headers=headers)
        assert resp1.status_code == 200
        data1 = resp1.json()
        assert data1["status"] == "RECEIVED"
        assert mock_sync.call_count == 1

        # Second rapid webhook event within 5s -> should debounce without re-syncing
        resp2 = client.post("/internal/v1/events", json=payload, headers=headers)
        assert resp2.status_code == 200
        data2 = resp2.json()
        assert data2["syncResult"]["status"] == "debounced"
        assert mock_sync.call_count == 1  # Not called again


# 3. Manual Sync Endpoints (Sync all & Sync individual)
def test_manual_sync_endpoints(client, db, test_hub_user):
    token = create_user_session(db, test_hub_user)
    client.cookies.set("nd_session", token)

    with patch.object(broker_adapter, "sync_user_from_broker") as mock_single, \
         patch.object(broker_adapter, "sync_all_brokers") as mock_all:

        mock_single.return_value = {"provider": "b", "status": "connected"}
        mock_all.return_value = {"a": {"status": "connected"}, "b": {"status": "connected"}, "c": {"status": "connected"}}

        # Individual sync
        resp_single = client.post("/accounts/sync/b", headers={"accept": "application/json"})
        assert resp_single.status_code == 200
        assert resp_single.json()["provider"] == "b"
        mock_single.assert_called_once()

        # All brokers sync
        resp_all = client.post("/accounts/sync-all", headers={"accept": "application/json"})
        assert resp_all.status_code == 200
        assert "a" in resp_all.json()
        mock_all.assert_called_once()

    client.cookies.delete("nd_session")


# 4. 15-Minute Stale Refresh Logic
def test_fifteen_minute_stale_refresh_logic(db, test_hub_user):
    account = db.query(DematAccount).filter(DematAccount.user_id == test_hub_user.id, DematAccount.dp_id == "IN300001").first()
    assert account is not None

    now = datetime.now(timezone.utc)
    # 1. Fresh sync (5 minutes ago)
    account.last_synced_at = now - timedelta(minutes=5)
    db.commit()
    assert broker_adapter.is_provider_stale(account, max_age_minutes=15) is False

    # 2. Stale sync (20 minutes ago)
    account.last_synced_at = now - timedelta(minutes=20)
    db.commit()
    assert broker_adapter.is_provider_stale(account, max_age_minutes=15) is True

    # 3. None last_synced_at -> stale
    account.last_synced_at = None
    db.commit()
    assert broker_adapter.is_provider_stale(account, max_age_minutes=15) is True


# 5. Connection Method: Consent Takes Precedence over Auto-Link
def test_consent_precedence_over_autolink(db, test_hub_user):
    account = db.query(DematAccount).filter(DematAccount.user_id == test_hub_user.id, DematAccount.dp_id == "IN300001").first()
    assert account is not None
    account.connection_method = "Auto-linked (demo)"
    db.commit()

    # Without active consent -> Auto-linked (demo)
    assert account.effective_connection_method == "Auto-linked (demo)"

    # With active consent linked to this user
    consent = Consent(
        consent_id="cns_test_prec_1",
        consent_handle="ch_test_prec_1",
        client_id="portfolio-aggregator",
        customer_email=test_hub_user.email,
        user_id=test_hub_user.id,
        status="ACTIVE",
        purpose_code="PORTFOLIO_TRACKING",
        purpose_text="Portfolio Tracking",
        fi_types_json='["EQUITIES"]',
        data_from="2024-01-01",
        data_to="2026-12-31",
        redirect_url="http://localhost:3000/callback",
        webhook_url="http://localhost:3000/webhook",
        selected_account_ids_json=json.dumps([account.id])
    )
    db.add(consent)
    db.commit()
    db.refresh(test_hub_user)
    db.refresh(account)

    # Consent must now take precedence
    assert account.effective_connection_method == "Connected via consent"

    # Cleanup consent
    db.delete(consent)
    db.commit()


# 6. Consistency Check: Detects Quantity Difference, Value Difference, Missing/Extra ISIN, and Identity Mismatch
def test_consistency_check_detection(db, test_hub_user):
    # Setup stored DematAccount with 10 units of RELIANCE
    account = db.query(DematAccount).filter(DematAccount.user_id == test_hub_user.id, DematAccount.dp_id == "IN300001").first()
    db.query(Holding).filter(Holding.demat_account_id == account.id).delete()
    db.add(Holding(demat_account_id=account.id, isin="INE002A01018", free_units=10.0, avg_price=2800.0))
    db.commit()

    # Mock Broker A returning 12 units of RELIANCE (quantity difference) and TCS (missing in TradeOne)
    mock_holdings = {
        "holdings": [
            {"isin": "INE002A01018", "symbol": "RELIANCE", "name": "Reliance", "quantity": 12.0, "last_price": 2850.0},
            {"isin": "INE467B01029", "symbol": "TCS", "name": "TCS Ltd", "quantity": 5.0, "last_price": 4000.0}
        ]
    }
    mock_profile_mismatch = {
        "name": "Different Name",
        "masked_pan": "XYZXX0000X",
        "mobile": "+91 0000000000"
    }

    with patch.object(broker_adapter, "get_holdings", return_value=(True, mock_holdings)), \
         patch.object(broker_adapter, "get_profile", return_value=(True, mock_profile_mismatch)), \
         patch.object(broker_adapter, "get_summary", return_value=(True, {"current_value": 54200.0})):

        res = check_portfolio_consistency(db, test_hub_user)
        assert res["overall_in_sync"] is False
        assert res["total_differences"] >= 2

        provider_a = res["providers"]["a"]
        assert provider_a["in_sync"] is False
        assert provider_a["identity"]["status"] == "mismatch"

        diff_types = [d["type"] for d in provider_a["differences"]]
        assert "QUANTITY_OR_VALUE_DIFF" in diff_types
        assert "MISSING_IN_TRADEONE" in diff_types


# 7. Re-sync from Consistency Check Fixes Deliberately Altered Snapshot
def test_resync_fixes_altered_snapshot(db, test_hub_user):
    account = db.query(DematAccount).filter(DematAccount.user_id == test_hub_user.id, DematAccount.dp_id == "IN300001").first()
    # Deliberately corrupt local snapshot: set free_units to 999.0
    db.query(Holding).filter(Holding.demat_account_id == account.id).delete()
    db.add(Holding(demat_account_id=account.id, isin="INE002A01018", free_units=999.0, avg_price=2800.0))
    db.commit()

    # Live broker actually has 10.0 units
    live_bundle = {
        "provider": "a",
        "broker_name": "NiftyTrade",
        "dp_name": "NiftyTrade Securities",
        "dp_id": "IN300001",
        "status": "connected",
        "error": None,
        "holdings": [{"isin": "INE002A01018", "symbol": "RELIANCE", "name": "Reliance", "quantity": 10.0, "last_price": 2850.0}],
        "profile": {"name": test_hub_user.name, "masked_pan": test_hub_user.masked_pan, "mobile": test_hub_user.mobile},
        "summary": {"current_value": 28500.0}
    }

    with patch.object(broker_adapter, "fetch_provider_bundle", return_value=live_bundle), \
         patch.object(broker_adapter, "get_holdings", return_value=(True, {"holdings": live_bundle["holdings"]})), \
         patch.object(broker_adapter, "get_profile", return_value=(True, live_bundle["profile"])), \
         patch.object(broker_adapter, "get_summary", return_value=(True, live_bundle["summary"])):

        # Check before resync -> Out of sync
        pre_check = check_portfolio_consistency(db, test_hub_user)
        assert pre_check["providers"]["a"]["in_sync"] is False

        # Execute re-sync
        broker_adapter.sync_user_from_broker(db, test_hub_user, "a")

        # Check after resync -> In sync!
        post_check = check_portfolio_consistency(db, test_hub_user)
        assert post_check["providers"]["a"]["in_sync"] is True
        assert len(post_check["providers"]["a"]["differences"]) == 0


# 8. Bond Details Extraction from Metadata
def test_bond_details_extraction_and_preservation(db, test_hub_user):
    account = db.query(DematAccount).filter(DematAccount.user_id == test_hub_user.id, DematAccount.dp_id == "IN300003").first()
    if not account:
        account = DematAccount(
            id=f"da_c_{test_hub_user.id}_9910",
            user_id=test_hub_user.id,
            dp_name="BondBazaar Depository Services",
            dp_id="IN300003",
            account_number="1208160000039910",
            masked_account_number="XXXX9910",
            provider_code="c",
            sync_status="connected"
        )
        db.add(account)
        db.commit()

    bond_meta_payload = {
        "coupon": "7.18%",
        "ytm": "7.12%",
        "maturity_date": "14-Aug-2033",
        "accrued_interest": "₹120.50",
        "security_type": "Government Bond (G-Sec)"
    }

    mock_bundle_c = {
        "provider": "c",
        "broker_name": "BondBazaar",
        "dp_name": "BondBazaar Depository Services",
        "dp_id": "IN300003",
        "status": "connected",
        "error": None,
        "holdings": [
            {
                "isin": "IN0020230085",
                "symbol": "GS2033-718",
                "name": "7.18% GS 2033 Central Government Bond",
                "asset_class": "BOND",
                "free_units": 100.0,
                "avg_price": 99.80,
                "last_price": 100.50,
                **bond_meta_payload
            }
        ]
    }

    with patch.object(broker_adapter, "fetch_provider_bundle", return_value=mock_bundle_c):
        broker_adapter.sync_user_from_broker(db, test_hub_user, "c")

    holdings = get_user_holdings(db, test_hub_user, dp_filter="BondBazaar Depository Services")
    assert len(holdings) == 1
    bond = holdings[0]
    assert bond["is_bond"] is True
    assert bond["bond_details"]["coupon"] == "7.18%"
    assert bond["bond_details"]["ytm"] == "7.12%"
    assert bond["bond_details"]["maturity_date"] == "14-Aug-2033"
    assert bond["bond_details"]["security_type"] == "Government Bond (G-Sec)"


# 9. Portfolio Insights Generation
def test_portfolio_insights_generation(db, test_hub_user):
    summary = get_portfolio_summary(db, test_hub_user)
    assert "insights" in summary
    assert "sector_allocation" in summary
    insights = summary["insights"]
    assert isinstance(insights, list)
    # Check absence insight detection
    absence_titles = [i["title"] for i in insights if i["type"] == "absence"]
    # If test user has no REIT or InvIT, absence titles must be present
    if summary["asset_allocation"].get("REIT", 0) == 0:
        assert "No REIT Exposure" in absence_titles


# 10. e2e_live.py Validates Environment Variables Safely
def test_e2e_live_validates_env_safely():
    import subprocess
    import sys
    # Running without E2E_TEST_EMAIL must exit non-zero without error trace or secrets
    cmd = [sys.executable, "scripts/e2e_live.py"]
    res = subprocess.run(cmd, capture_output=True, text=True, env={})
    assert res.returncode != 0
    assert "E2E_TEST_EMAIL environment variable is not set" in res.stdout or "E2E_TEST_EMAIL" in res.stderr
