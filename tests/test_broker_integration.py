import pytest
from unittest.mock import patch, MagicMock
import httpx
from fastapi.testclient import TestClient

from app.main import app
from app.config import settings
from app.database import SessionLocal
from app.models import User, DematAccount, Holding, Instrument
from app.shared_identity import normalize_email
from app.services.broker_adapter import broker_adapter
from app.services.depository_service import get_user_holdings, get_portfolio_summary
from app.services.auth_service import create_user_session

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

# 1. Provider Configuration & Deployed URLs
def test_provider_configuration_and_urls():
    assert settings.NIFTYTRADE_URL == "https://nifty-50-5nzz.onrender.com"
    assert settings.BHARATINVEST_URL == "https://bharatinvest.onrender.com"
    assert settings.BONDBAZAAR_URL == "https://bondbazaar-1.onrender.com"

    providers = settings.BROKER_PROVIDERS
    assert providers["a"]["broker_name"] == "NiftyTrade"
    assert providers["a"]["dp_id"] == "IN300001"
    assert providers["a"]["url"] == "https://nifty-50-5nzz.onrender.com"

    assert providers["b"]["broker_name"] == "BharatInvest"
    assert providers["b"]["dp_id"] == "IN300002"
    assert providers["b"]["url"] == "https://bharatinvest.onrender.com"

    assert providers["c"]["broker_name"] == "BondBazaar"
    assert providers["c"]["dp_id"] == "IN300003"
    assert providers["c"]["url"] == "https://bondbazaar-1.onrender.com"

    # Verify secret is not embedded inside the URL
    for p in ("a", "b", "c"):
        assert "key" not in providers[p]["url"].lower()
        assert "secret" not in providers[p]["url"].lower()

# 2. Normalized Email before sending to providers
def test_normalized_email():
    raw_email = "  Investor.Special+Test@Domain.COM  "
    norm = normalize_email(raw_email)
    assert norm == "investor.special+test@domain.com"

    headers = broker_adapter._headers("a")
    assert "x-internal-key" in headers
    assert headers["x-internal-key"] != ""

# 3. Provisioning across all 3 providers & Idempotency
@patch("httpx.Client.post")
def test_provisioning_all_three_providers_and_idempotency(mock_post):
    # First response: CREATED
    resp_created = MagicMock()
    resp_created.status_code = 200
    resp_created.json.return_value = {"status": "CREATED", "client_code": "1208160012345678", "email": "sync@example.com"}

    # Second response: EXISTS (Idempotent)
    resp_exists = MagicMock()
    resp_exists.status_code = 200
    resp_exists.json.return_value = {"status": "EXISTS", "client_code": "1208160012345678", "email": "sync@example.com"}

    mock_post.side_effect = [resp_created, resp_exists, resp_created, resp_created]

    # Provider A: first call (CREATED)
    ok1, data1 = broker_adapter.provision_user("a", "  Sync@Example.COM  ", "Sync User")
    assert ok1 is True
    assert data1["status"] == "CREATED"

    # Provider A: second call (EXISTS - Idempotent)
    ok2, data2 = broker_adapter.provision_user("a", "sync@example.com", "Sync User")
    assert ok2 is True
    assert data2["status"] == "EXISTS"

    # Provider B & C
    ok_b, _ = broker_adapter.provision_user("b", "sync@example.com")
    assert ok_b is True
    ok_c, _ = broker_adapter.provision_user("c", "sync@example.com")
    assert ok_c is True

# 4. Successful Profile, Holdings, and Summary Fetch
@patch("httpx.Client.get")
@patch("httpx.Client.post")
def test_successful_profile_holdings_summary_fetch(mock_post, mock_get):
    mock_post.return_value = MagicMock(status_code=200, json=lambda: {"status": "EXISTS"})

    mock_profile = MagicMock(status_code=200, json=lambda: {
        "name": "Test User", "email": "test@example.com", "client_code": "120816000001", "masked_pan": "ABCXX1234X"
    })
    mock_holdings = MagicMock(status_code=200, json=lambda: {
        "total": 1, "page": 1, "page_size": 50, "total_pages": 1,
        "holdings": [{
            "isin": "INE002A01018", "symbol": "RELIANCE", "name": "Reliance Industries Limited",
            "asset_class": "EQUITY", "free_units": 10.0, "total_units": 10.0,
            "last_price": 2842.5, "avg_price": 2700.0
        }]
    })
    mock_summary = MagicMock(status_code=200, json=lambda: {
        "email": "test@example.com", "invested": 27000.0, "current_value": 28425.0, "day_change": 250.0
    })

    mock_get.side_effect = [mock_profile, mock_holdings, mock_summary]

    bundle = broker_adapter.fetch_provider_bundle("a", "test@example.com")
    assert bundle["status"] == "connected"
    assert bundle["broker_name"] == "NiftyTrade"
    assert bundle["dp_id"] == "IN300001"
    assert len(bundle["holdings"]) == 1
    assert bundle["holdings"][0]["symbol"] == "RELIANCE"
    assert bundle["summary"]["invested"] == 27000.0

# 5. Unified Holdings, Same ISIN Merging, and Broker Attribution
@patch("app.services.broker_adapter.BrokerAdapter.fetch_provider_bundle")
def test_unified_holdings_and_same_isin_merge_across_providers(mock_bundle, db):
    test_email = "unified.investor@example.com"
    user = db.query(User).filter(User.email == test_email).first()
    if not user:
        user = User(
            email=test_email,
            name="Unified Investor",
            bo_id="1208160099991111",
            masked_pan="XYZXX9999X",
            dob="15081992",
            mobile="9876543210",
            wallet_balance=1000000.0
        )
        db.add(user)
        db.commit()
        db.refresh(user)

    # Provider A has RELIANCE (10 units @ 2800) and TCS (5 units @ 4100)
    bundle_a = {
        "provider": "a", "broker_name": "NiftyTrade", "dp_name": "NiftyTrade Securities", "dp_id": "IN300001",
        "status": "connected", "error": None, "profile": {"masked_demat_number": "XXXX5521"},
        "holdings": [
            {"isin": "INE002A01018", "symbol": "RELIANCE", "name": "Reliance Industries Limited", "asset_class": "EQUITY", "free_units": 10.0, "last_price": 2842.5, "avg_price": 2800.0},
            {"isin": "INE467B01029", "symbol": "TCS", "name": "Tata Consultancy Services Ltd", "asset_class": "EQUITY", "free_units": 5.0, "last_price": 4120.8, "avg_price": 4100.0}
        ]
    }
    # Provider B has RELIANCE (5 units @ 2900) and INFY (20 units @ 1800) -> RELIANCE overlaps with A!
    bundle_b = {
        "provider": "b", "broker_name": "BharatInvest", "dp_name": "BharatInvest Securities", "dp_id": "IN300002",
        "status": "connected", "error": None, "profile": {"masked_demat_number": "XXXX8842"},
        "holdings": [
            {"isin": "INE002A01018", "symbol": "RELIANCE", "name": "Reliance Industries Limited", "asset_class": "EQUITY", "free_units": 5.0, "last_price": 2842.5, "avg_price": 2900.0},
            {"isin": "INE009A01021", "symbol": "INFY", "name": "Infosys Limited", "asset_class": "EQUITY", "free_units": 20.0, "last_price": 1895.3, "avg_price": 1800.0}
        ]
    }
    # Provider C has GOI Bond (100 units @ 101.4)
    bundle_c = {
        "provider": "c", "broker_name": "BondBazaar", "dp_name": "BondBazaar Depository Services", "dp_id": "IN300003",
        "status": "connected", "error": None, "profile": {"masked_demat_number": "XXXX1920"},
        "holdings": [
            {"isin": "IN0020230085", "symbol": "GS2033-718", "name": "Government of India 7.18% 2033 GS", "asset_class": "BOND", "free_units": 100.0, "last_price": 101.4, "avg_price": 100.0}
        ]
    }

    mock_bundle.side_effect = lambda code, email, name=None: {"a": bundle_a, "b": bundle_b, "c": bundle_c}[code]

    # Synchronize all 3 brokers
    sync_res = broker_adapter.sync_all_brokers(db, user)
    assert sync_res["a"]["status"] == "connected"
    assert sync_res["b"]["status"] == "connected"
    assert sync_res["c"]["status"] == "connected"

    # 1. Unmerged view: individual positions retain broker attribution
    unmerged = get_user_holdings(db, user, merge_by_isin=False)
    assert len(unmerged) == 5

    reliance_positions = [h for h in unmerged if h["isin"] == "INE002A01018"]
    assert len(reliance_positions) == 2
    brokers_with_reliance = {h["broker_name"] for h in reliance_positions}
    assert brokers_with_reliance == {"NiftyTrade", "BharatInvest"}

    # 2. Merged view: Merge by ISIN
    merged = get_user_holdings(db, user, merge_by_isin=True)
    assert len(merged) == 4  # RELIANCE, TCS, INFY, GS2033-718

    merged_reliance = next(h for h in merged if h["isin"] == "INE002A01018")
    assert merged_reliance["total_units"] == 15.0  # 10 + 5
    # Weighted avg price: (10 * 2800 + 5 * 2900) / 15 = (28000 + 14500)/15 = 42500/15 = 2833.33
    assert abs(merged_reliance["avg_price"] - 2833.33) < 0.1

    # Verify broker-level breakdown is preserved in merged view
    breakdown = merged_reliance["broker_breakdown"]
    assert len(breakdown) == 2
    breakdown_brokers = {b["broker_name"]: b["quantity"] for b in breakdown}
    assert breakdown_brokers["NiftyTrade"] == 10.0
    assert breakdown_brokers["BharatInvest"] == 5.0

    # 3. Portfolio Summary totals
    summary = get_portfolio_summary(db, user)
    assert summary["total_value"] > 0
    assert summary["num_accounts"] == 3
    assert "provider_statuses" in summary
    assert summary["provider_statuses"]["a"]["status"] == "connected"
    assert summary["provider_statuses"]["b"]["status"] == "connected"
    assert summary["provider_statuses"]["c"]["status"] == "connected"

# 6. One-Provider Failure & Stale State Resilience
@patch("app.services.broker_adapter.BrokerAdapter.fetch_provider_bundle")
def test_one_provider_failure_resilience(mock_bundle, db):
    test_email = "resilience.user@example.com"
    user = db.query(User).filter(User.email == test_email).first()
    if user:
        db.query(DematAccount).filter(DematAccount.user_id == user.id).delete()
        db.commit()
    else:
        user = User(email=test_email, name="Resilience User", bo_id="1208160088882222", masked_pan="ABCXX5555X", dob="15081992", mobile="9876543210")
        db.add(user)
        db.commit()

    # Provider A & B succeed; Provider C fails (unavailable)
    def mock_fetch(code, email, name=None):
        if code == "c":
            return {"provider": "c", "broker_name": "BondBazaar", "dp_id": "IN300003", "status": "unavailable", "error": "HTTP 500 error", "holdings": []}
        return {
            "provider": code, "broker_name": "Broker " + code.upper(), "dp_id": f"IN30000{1 if code=='a' else 2}",
            "status": "connected", "error": None,
            "holdings": [{"isin": "INE002A01018", "symbol": "RELIANCE", "name": "Reliance", "asset_class": "EQUITY", "free_units": 10.0, "last_price": 2842.5, "avg_price": 2800.0}]
        }

    mock_bundle.side_effect = mock_fetch

    # Sync
    res = broker_adapter.sync_all_brokers(db, user)
    assert res["a"]["status"] == "connected"
    assert res["b"]["status"] == "connected"
    assert res["c"]["status"] in ("unavailable", "stale")

    # Verify TradeOne summary still works smoothly without crashing
    summary = get_portfolio_summary(db, user)
    assert summary["total_value"] > 0
    assert summary["provider_statuses"]["c"]["status"] in ("unavailable", "stale")


# 7. Timeout Handling
@patch("httpx.Client.get")
@patch("httpx.Client.post")
def test_timeout_handling(mock_post, mock_get):
    mock_post.side_effect = httpx.TimeoutException("Read timed out")
    mock_get.side_effect = httpx.TimeoutException("Read timed out")

    ok, data = broker_adapter.provision_user("a", "timeout@example.com")
    assert ok is False
    assert data["code"] == "TIMEOUT"

    bundle = broker_adapter.fetch_provider_bundle("a", "timeout@example.com")
    assert bundle["status"] in ("unavailable", "stale")
    assert "timed out" in bundle["error"].lower()

# 8. HOLDINGS_CHANGED Webhooks for Provider a, b, c
@patch("app.routers.internal.trigger_webhook_event")
@patch("app.services.broker_adapter.BrokerAdapter.sync_user_from_broker")
def test_holdings_changed_webhooks_for_providers_a_b_c(mock_sync, mock_trigger, client, db):
    mock_sync.return_value = {"provider": "a", "status": "connected", "holdings_count": 5}
    headers = {"x-internal-key": settings.INTERNAL_API_KEY}

    for p in ("a", "b", "c"):
        resp = client.post("/internal/v1/events", json={
            "provider": p,
            "email": "test.webhook@example.com",
            "event": "HOLDINGS_CHANGED",
            "occurredAt": "2026-10-09T01:30:00Z"
        }, headers=headers)

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "RECEIVED"
        assert data["provider"] == p
        assert data["email"] == "test.webhook@example.com"

# 9. Demo Users Remain Unaffected when DEMO_MODE is True
def test_demo_users_remain_unaffected(db):
    import os
    from app.services.seed_service import seed_database
    with patch.dict(os.environ, {"DEMO_MODE": "true"}):
        seed_database(db)
        aarav = db.query(User).filter(User.email == "aarav.mehta@example.com").first()
        assert aarav is not None

        # Syncing demo user must not make external HTTP requests or wipe accounts
        sync_res = broker_adapter.sync_all_brokers(db, aarav)
        assert sync_res["a"]["status"] == "connected"
        assert sync_res["b"]["status"] == "connected"
        assert sync_res["c"]["status"] == "connected"

        # Aarav still has his 3 original accounts and 10 holdings
        assert len(aarav.demat_accounts) == 3
        holdings = get_user_holdings(db, aarav, merge_by_isin=False)
        assert len(holdings) == 10

# 10. Secrets are Not Exposed to Public Endpoints or Frontend
def test_secrets_not_exposed(client, db):
    from app.services.seed_service import provision_new_user
    user = provision_new_user(db, email="secrets.check.user@example.com", provider="google")
    token = create_user_session(db, user)
    client.cookies.set("nd_session", token)

    # 1. Public Profile
    resp_prof = client.get("/api/v1/profile")
    assert resp_prof.status_code == 200
    prof_text = resp_prof.text
    assert settings.INTERNAL_API_KEY not in prof_text
    assert settings.SECRET_KEY not in prof_text

    # 2. Public Holdings
    resp_hold = client.get("/api/v1/holdings")
    assert resp_hold.status_code == 200
    hold_text = resp_hold.text
    assert settings.INTERNAL_API_KEY not in hold_text

    # 3. HTML Dashboard
    resp_dash = client.get("/dashboard")
    assert resp_dash.status_code == 200
    dash_text = resp_dash.text
    assert settings.INTERNAL_API_KEY not in dash_text
    client.cookies.delete("nd_session")

# 11. Broker Header Sanitization and Key Fallback
def test_broker_headers_sanitize_and_fallback():
    import os
    # Test specific key takes precedence and strips quotes
    with patch.dict(os.environ, {"NIFTYTRADE_INTERNAL_KEY": ' "custom-nifty-key" '}):
        headers = broker_adapter._headers("a")
        assert headers["x-internal-key"] == "custom-nifty-key"

    # Test fallback to INTERNAL_API_KEY when specific key is blank
    with patch.dict(os.environ, {"NIFTYTRADE_INTERNAL_KEY": "", "INTERNAL_API_KEY": "fallback-hub-key"}):
        headers = broker_adapter._headers("a")
        assert headers["x-internal-key"] == "fallback-hub-key"

# 12. Broker Missing Key Early Detection
def test_broker_missing_key_early_detection():
    import os
    with patch.dict(os.environ, {"NIFTYTRADE_INTERNAL_KEY": "", "INTERNAL_API_KEY": ""}):
        bundle = broker_adapter.fetch_provider_bundle("a", "user@example.com")
        assert bundle["status"] == "unavailable"
        assert bundle["http_status"] == 401
        assert "not configured" in bundle["error"].lower()
        assert bundle["holdings"] == []

# 13. Broker 401 Unauthorized Error Handling
@patch("httpx.Client.post")
def test_broker_401_unauthorized_handling(mock_post):
    mock_resp = MagicMock()
    mock_resp.status_code = 401
    mock_resp.json.return_value = {"detail": "Invalid internal key"}
    mock_post.return_value = mock_resp

    bundle = broker_adapter.fetch_provider_bundle("a", "user@example.com")
    assert bundle["status"] == "unavailable"
    assert bundle["http_status"] == 401
    assert "401" in bundle["error"]
    assert bundle["holdings"] == []

# 14. Provider Failure Isolation (One Broker 401 Does Not Block Others)
@patch.object(broker_adapter, "fetch_provider_bundle")
def test_provider_failure_isolation(mock_bundle, db):
    from app.services.seed_service import provision_new_user
    user = provision_new_user(db, email="isolated.sync.user@example.com", provider="email")

    def side_effect(provider_code, email, full_name=None):
        if provider_code == "a":
            # NiftyTrade fails with 401
            return {
                "provider": "a",
                "broker_name": "NiftyTrade",
                "dp_name": "NiftyTrade Securities",
                "dp_id": "IN300001",
                "status": "unavailable",
                "error": "Provider returned HTTP 401 (Authentication failed)",
                "profile": None,
                "holdings": [],
                "summary": None,
                "http_status": 401,
                "response_time_ms": 10.0
            }
        elif provider_code == "b":
            # BharatInvest succeeds with 1 holding
            return {
                "provider": "b",
                "broker_name": "BharatInvest",
                "dp_name": "BharatInvest Securities",
                "dp_id": "IN300002",
                "status": "connected",
                "error": None,
                "profile": {"email": email, "name": "Isolated User"},
                "holdings": [
                    {
                        "isin": "INE002A01018",
                        "symbol": "RELIANCE",
                        "name": "Reliance Industries Limited",
                        "quantity": 10,
                        "last_price": 2800.0,
                        "avg_price": 2700.0
                    }
                ],
                "summary": {"total_current": 28000.0, "total_invested": 27000.0},
                "http_status": 200,
                "response_time_ms": 15.0
            }
        else:
            # BondBazaar succeeds with 0 holdings
            return {
                "provider": "c",
                "broker_name": "BondBazaar",
                "dp_name": "BondBazaar Depository Services",
                "dp_id": "IN300003",
                "status": "connected",
                "error": None,
                "profile": {"email": email, "name": "Isolated User"},
                "holdings": [],
                "summary": {"total_current": 0.0, "total_invested": 0.0},
                "http_status": 200,
                "response_time_ms": 20.0
            }

    mock_bundle.side_effect = side_effect

    res = broker_adapter.sync_all_brokers(db, user, only_stale=False)

    # Provider A failed with 401
    assert res["a"]["status"] in ("unavailable", "stale")
    assert "401" in res["a"]["error"]

    # Provider B succeeded and holding was recorded
    assert res["b"]["status"] == "connected"
    assert res["b"]["holdings_count"] == 1

    # Provider C succeeded
    assert res["c"]["status"] == "connected"
    assert res["c"]["holdings_count"] == 0

    # User's demat accounts in DB reflect isolation
    acc_b = db.query(DematAccount).filter(DematAccount.user_id == user.id, DematAccount.dp_id == "IN300002").first()
    assert acc_b is not None
    assert acc_b.sync_status == "connected"
    assert len(acc_b.holdings) == 1

    acc_a = db.query(DematAccount).filter(DematAccount.user_id == user.id, DematAccount.dp_id == "IN300001").first()
    assert acc_a is not None
    assert acc_a.sync_status in ("unavailable", "stale")

