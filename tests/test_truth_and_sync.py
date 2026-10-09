import pytest
from decimal import Decimal
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
from app.services.seed_service import provision_new_user
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


# 1. New user with no trades shows zero everywhere
def test_new_user_with_no_trades_shows_zero_everywhere(db, client):
    email = "new.user.zero.trades@example.com"
    # Ensure clean slate
    user = db.query(User).filter(User.email == email).first()
    if user:
        for a in user.demat_accounts:
            db.query(Holding).filter(Holding.demat_account_id == a.id).delete()
            db.delete(a)
        db.delete(user)
        db.commit()

    user = provision_new_user(db, email=email, provider="google")

    # Mock all three brokers returning empty holdings
    def mock_bundle(code, user_email, name=None):
        config = broker_adapter.get_provider_config(code)
        return {
            "provider": code,
            "broker_name": config["broker_name"],
            "dp_name": config["dp_name"],
            "dp_id": config["dp_id"],
            "status": "connected",
            "error": None,
            "profile": {"email": user_email, "name": "New User"},
            "holdings": [],
            "summary": {"invested": 0.0, "current_value": 0.0}
        }

    with patch.object(broker_adapter, "fetch_provider_bundle", side_effect=mock_bundle):
        broker_adapter.sync_all_brokers(db, user, only_stale=False)

    summary = get_portfolio_summary(db, user)
    holdings = get_user_holdings(db, user, merge_by_isin=True)

    assert summary["total_value"] == 0.0
    assert summary["total_invested"] == 0.0
    assert summary["total_pnl"] == 0.0
    assert summary["num_securities"] == 0
    assert len(summary["insights"]) == 0
    assert summary["asset_allocation"] == {}
    assert summary["dp_allocation"] == {}
    assert summary["sector_allocation"] == {}
    assert len(holdings) == 0

    # Test HTML dashboard zero state display
    token = create_user_session(db, user)
    client.cookies.set("nd_session", token)
    resp = client.get("/dashboard")
    assert resp.status_code == 200
    assert "No holdings yet" in resp.text
    assert "Trade on NiftyTrade, BharatInvest or BondBazaar with this same Google account" in resp.text
    client.cookies.delete("nd_session")


# 2. User with holdings on two brokers and none on the third shows only those two
def test_user_with_holdings_on_two_brokers_and_none_on_third(db):
    email = "two.brokers.user@example.com"
    user = db.query(User).filter(User.email == email).first()
    if user:
        for a in user.demat_accounts:
            db.query(Holding).filter(Holding.demat_account_id == a.id).delete()
            db.delete(a)
        db.delete(user)
        db.commit()

    user = provision_new_user(db, email=email, provider="google")

    def mock_bundle(code, user_email, name=None):
        config = broker_adapter.get_provider_config(code)
        if code == "a":
            # NiftyTrade has RELIANCE (10 qty @ 2800)
            return {
                "provider": "a", "broker_name": "NiftyTrade", "dp_name": config["dp_name"], "dp_id": "IN300001",
                "status": "connected", "error": None, "profile": {"email": user_email},
                "holdings": [{"isin": "INE002A01018", "symbol": "RELIANCE", "name": "Reliance", "free_units": 10.0, "last_price": 2800.0, "avg_price": 2700.0}]
            }
        elif code == "b":
            # BharatInvest has INFY (20 qty @ 1800) returning string numbers!
            return {
                "provider": "b", "broker_name": "BharatInvest", "dp_name": config["dp_name"], "dp_id": "IN300002",
                "status": "connected", "error": None, "profile": {"email": user_email},
                "holdings": [{"isin": "INE009A01021", "symbol": "INFY", "name": "Infosys", "free_units": "20.0", "last_price": "1800.0", "avg_price": "1750.0"}]
            }
        else:
            # BondBazaar has 0 holdings
            return {
                "provider": "c", "broker_name": "BondBazaar", "dp_name": config["dp_name"], "dp_id": "IN300003",
                "status": "connected", "error": None, "profile": {"email": user_email},
                "holdings": []
            }

    with patch.object(broker_adapter, "fetch_provider_bundle", side_effect=mock_bundle):
        broker_adapter.sync_all_brokers(db, user, only_stale=False)

    summary = get_portfolio_summary(db, user)
    holdings = get_user_holdings(db, user, merge_by_isin=False)

    # Subtotals verification
    subtotals = summary["broker_subtotals"]
    assert subtotals["a"]["holdings_count"] == 1
    assert subtotals["a"]["current_value"] == Decimal("28000.0")  # 10 * 2800
    assert subtotals["b"]["holdings_count"] == 1
    assert subtotals["b"]["current_value"] == Decimal("36000.0")  # 20 * 1800
    assert subtotals["c"]["holdings_count"] == 0
    assert subtotals["c"]["current_value"] == Decimal("0")

    # Grand total adds up exactly to subtotals
    assert summary["total_value_dec"] == subtotals["a"]["current_value"] + subtotals["b"]["current_value"]
    assert summary["total_value_dec"] == Decimal("64000.0")

    # BondBazaar is not in dp_allocation since it has 0 value
    assert "BondBazaar Depository Services" not in summary["dp_allocation"]
    assert len(holdings) == 2


# 3. A second Google user never sees the first user's data (strict isolation)
def test_user_isolation_second_google_user_never_sees_first_user_data(db):
    user1_email = "user1.isolated@example.com"
    user2_email = "user2.isolated@example.com"

    user1 = provision_new_user(db, email=user1_email, provider="google")
    user2 = provision_new_user(db, email=user2_email, provider="google")

    # Give user1 a holding on broker A
    bundle_u1 = {
        "provider": "a", "broker_name": "NiftyTrade", "dp_name": "NiftyTrade Securities", "dp_id": "IN300001",
        "status": "connected", "error": None, "profile": {"email": user1_email},
        "holdings": [{"isin": "INE002A01018", "symbol": "RELIANCE", "name": "Reliance", "free_units": 50.0, "last_price": 2800.0, "avg_price": 2800.0}]
    }
    with patch.object(broker_adapter, "fetch_provider_bundle", return_value=bundle_u1):
        broker_adapter.sync_user_from_broker(db, user1, "a")

    # Sync user2 with 0 holdings
    bundle_u2 = {
        "provider": "a", "broker_name": "NiftyTrade", "dp_name": "NiftyTrade Securities", "dp_id": "IN300001",
        "status": "connected", "error": None, "profile": {"email": user2_email},
        "holdings": []
    }
    with patch.object(broker_adapter, "fetch_provider_bundle", return_value=bundle_u2):
        broker_adapter.sync_user_from_broker(db, user2, "a")

    u1_holdings = get_user_holdings(db, user1)
    u2_holdings = get_user_holdings(db, user2)
    u2_summary = get_portfolio_summary(db, user2)

    assert len(u1_holdings) == 1
    assert len(u2_holdings) == 0
    assert u2_summary["total_value"] == 0.0


# 4. Repeated syncs never double count (snapshot replacement idempotency)
def test_repeated_syncs_never_double_count(db):
    email = "idempotent.sync@example.com"
    user = provision_new_user(db, email=email, provider="google")

    bundle = {
        "provider": "a", "broker_name": "NiftyTrade", "dp_name": "NiftyTrade Securities", "dp_id": "IN300001",
        "status": "connected", "error": None, "profile": {"email": email},
        "holdings": [{"isin": "INE002A01018", "symbol": "RELIANCE", "name": "Reliance", "free_units": 10.0, "last_price": 2842.5, "avg_price": 2800.0}]
    }

    with patch.object(broker_adapter, "fetch_provider_bundle", return_value=bundle):
        # Sync 1
        res1 = broker_adapter.sync_user_from_broker(db, user, "a")
        h1 = get_user_holdings(db, user)
        assert len(h1) == 1
        assert h1[0]["total_units"] == 10.0

        # Sync 2 (repeated)
        res2 = broker_adapter.sync_user_from_broker(db, user, "a")
        h2 = get_user_holdings(db, user)
        assert len(h2) == 1
        assert h2[0]["total_units"] == 10.0  # Not 20.0!

        # Sync 3 (repeated again)
        res3 = broker_adapter.sync_user_from_broker(db, user, "a")
        h3 = get_user_holdings(db, user)
        assert len(h3) == 1
        assert h3[0]["total_units"] == 10.0  # Still 10.0!


# 5. A sell on a broker (quantity reduced) is reflected after next sync
def test_sell_on_broker_reflected_after_next_sync(db):
    email = "trader.sell@example.com"
    user = provision_new_user(db, email=email, provider="google")

    # Initial state: 10 units
    bundle_buy = {
        "provider": "a", "broker_name": "NiftyTrade", "dp_name": "NiftyTrade Securities", "dp_id": "IN300001",
        "status": "connected", "error": None, "profile": {"email": email},
        "holdings": [{"isin": "INE002A01018", "symbol": "RELIANCE", "name": "Reliance", "free_units": 10.0, "last_price": 2800.0, "avg_price": 2800.0}]
    }
    with patch.object(broker_adapter, "fetch_provider_bundle", return_value=bundle_buy):
        broker_adapter.sync_user_from_broker(db, user, "a")

    h_before = get_user_holdings(db, user)[0]
    assert h_before["total_units"] == 10.0

    # After sell: 4 sold, now 6 remaining
    bundle_after_sell = {
        "provider": "a", "broker_name": "NiftyTrade", "dp_name": "NiftyTrade Securities", "dp_id": "IN300001",
        "status": "connected", "error": None, "profile": {"email": email},
        "holdings": [{"isin": "INE002A01018", "symbol": "RELIANCE", "name": "Reliance", "free_units": 6.0, "last_price": 2850.0, "avg_price": 2800.0}]
    }
    with patch.object(broker_adapter, "fetch_provider_bundle", return_value=bundle_after_sell):
        broker_adapter.sync_user_from_broker(db, user, "a")

    h_after = get_user_holdings(db, user)[0]
    assert h_after["total_units"] == 6.0
    assert h_after["current_value"] == 6.0 * 2850.0


# 6. Broker returning USER_NOT_FOUND gives zero holdings and appropriate note
def test_broker_returning_user_not_found_gives_zero(db):
    email = "notfound.broker@example.com"
    user = provision_new_user(db, email=email, provider="google")

    bundle_not_found = {
        "provider": "c", "broker_name": "BondBazaar", "dp_name": "BondBazaar Depository Services", "dp_id": "IN300003",
        "status": "no_account", "error": "No account on BondBazaar for this email", "profile": None, "holdings": [], "summary": None
    }
    with patch.object(broker_adapter, "fetch_provider_bundle", return_value=bundle_not_found):
        res = broker_adapter.sync_user_from_broker(db, user, "c")

    assert res["status"] == "no_account"
    assert res["holdings_count"] == 0

    summary = get_portfolio_summary(db, user)
    acc = db.query(DematAccount).filter(DematAccount.user_id == user.id, DematAccount.dp_id == "IN300003").first()
    assert acc.sync_status == "no_account"
    assert "No account on BondBazaar" in acc.sync_error
    assert summary["broker_subtotals"]["c"]["holdings_count"] == 0


# 7. Broker timing out keeps last good data marked STALE
def test_broker_timing_out_keeps_last_good_data_marked_stale(db):
    email = "timeout.broker@example.com"
    user = provision_new_user(db, email=email, provider="google")

    # Initial good sync: 15 units
    bundle_good = {
        "provider": "a", "broker_name": "NiftyTrade", "dp_name": "NiftyTrade Securities", "dp_id": "IN300001",
        "status": "connected", "error": None, "profile": {"email": email},
        "holdings": [{"isin": "INE002A01018", "symbol": "RELIANCE", "name": "Reliance", "free_units": 15.0, "last_price": 2800.0, "avg_price": 2800.0}]
    }
    with patch.object(broker_adapter, "fetch_provider_bundle", return_value=bundle_good):
        broker_adapter.sync_user_from_broker(db, user, "a")

    # Timeout on second sync
    bundle_timeout = {
        "provider": "a", "broker_name": "NiftyTrade", "dp_name": "NiftyTrade Securities", "dp_id": "IN300001",
        "status": "stale", "error": "Connection timed out contacting provider", "profile": None, "holdings": [], "summary": None
    }
    with patch.object(broker_adapter, "fetch_provider_bundle", return_value=bundle_timeout):
        res = broker_adapter.sync_user_from_broker(db, user, "a")

    assert res["status"] == "stale"
    assert res["holdings_count"] == 1  # 1 holding preserved!

    # Holding is untouched
    h = get_user_holdings(db, user)[0]
    assert h["total_units"] == 15.0

    acc = db.query(DematAccount).filter(DematAccount.user_id == user.id, DematAccount.dp_id == "IN300001").first()
    assert acc.sync_status == "stale"
    assert "timed out" in acc.sync_error.lower()


# 8. Mismatched profile email is rejected and flagged ERROR
def test_mismatched_profile_email_rejected(db):
    email = "victim.user@example.com"
    user = provision_new_user(db, email=email, provider="google")

    # External broker returns profile with completely different email!
    mock_post = MagicMock(status_code=200, json=lambda: {"status": "EXISTS"})
    mock_prof = MagicMock(status_code=200, json=lambda: {
        "status": "success", "email": "attacker.different@badactor.com", "name": "Attacker"
    })
    mock_hold = MagicMock(status_code=200, json=lambda: {
        "status": "success", "data": [{"isin": "INE002A01018", "quantity": 1000.0, "last_price": 2800.0}]
    })
    mock_summ = MagicMock(status_code=200, json=lambda: {"current_value": 2800000.0})

    with patch("httpx.Client.post", return_value=mock_post), \
         patch("httpx.Client.get", side_effect=[mock_prof, mock_hold, mock_summ]):
        bundle = broker_adapter.fetch_provider_bundle("a", email)

    assert bundle["status"] == "error"
    assert "mismatch" in bundle["error"].lower()
    assert len(bundle["holdings"]) == 0  # Data discarded!

    res = broker_adapter._apply_bundle_to_db(db, user, "a", bundle)
    assert res["status"] == "error"
    acc = db.query(DematAccount).filter(DematAccount.user_id == user.id, DematAccount.dp_id == "IN300001").first()
    assert acc.sync_status == "error"


# 9. No seeded data ever appears for Google users
def test_no_seeded_data_ever_appears_for_google_users(db):
    google_email = "real.google.trader.99@gmail.com"
    user = db.query(User).filter(User.email == google_email).first()
    if user:
        for a in user.demat_accounts:
            db.query(Holding).filter(Holding.demat_account_id == a.id).delete()
            db.delete(a)
        db.delete(user)
        db.commit()

    # Provision user as Google user
    user = provision_new_user(db, email=google_email, provider="google")

    # Demat accounts must NOT have seeded starter holdings
    holdings = get_user_holdings(db, user)
    assert len(holdings) == 0

    summary = get_portfolio_summary(db, user)
    assert summary["total_value"] == 0.0
    assert summary["total_invested"] == 0.0
    assert summary["num_securities"] == 0

# 10. Dashboard Rendering Regression Tests (Decimal Arithmetic & Zero State)
def test_dashboard_rendering_with_decimal_monetary_values(client, db):
    user_email = "dashboard.render.test@gmail.com"
    user = provision_new_user(db, email=user_email, provider="google")
    token = create_user_session(db, user)
    client.cookies.set("nd_session", token)

    # Sync holdings for user
    mock_bundle_a = {
        "provider": "a", "broker_name": "NiftyTrade", "dp_name": "NiftyTrade Securities", "dp_id": "IN300001",
        "status": "connected", "error": None, "profile": {"email": user_email},
        "holdings": [{"isin": "INE002A01018", "symbol": "RELIANCE", "name": "Reliance", "quantity": 10, "last_price": 2800.0, "avg_price": 2500.0}],
        "summary": {"total_current": 28000.0, "total_invested": 25000.0}
    }
    mock_bundle_b = {
        "provider": "b", "broker_name": "BharatInvest", "dp_name": "BharatInvest Securities", "dp_id": "IN300002",
        "status": "connected", "error": None, "profile": {"email": user_email},
        "holdings": [{"isin": "INE467B01029", "symbol": "TCS", "name": "TCS", "quantity": 5, "last_price": 4000.0, "avg_price": 3800.0}],
        "summary": {"total_current": 20000.0, "total_invested": 19000.0}
    }
    broker_adapter._apply_bundle_to_db(db, user, "a", mock_bundle_a)
    broker_adapter._apply_bundle_to_db(db, user, "b", mock_bundle_b)

    # Fetch dashboard (MUST return HTTP 200 and NOT crash with TypeError on sub.current_value / summary.total_value)
    resp = client.get("/dashboard")
    assert resp.status_code == 200
    html = resp.text
    assert "NiftyTrade" in html
    assert "BharatInvest" in html
    assert "Grand Total Consolidated" in html
    assert "table-broker-subtotals" in html
    # Check percentage formatting is present
    assert "% of Portfolio" in html

    client.cookies.delete("nd_session")

def test_dashboard_rendering_zero_total_portfolio(client, db):
    user_email = "zero.portfolio.render@gmail.com"
    user = provision_new_user(db, email=user_email, provider="google")
    token = create_user_session(db, user)
    client.cookies.set("nd_session", token)

    # Fetch dashboard for user with 0 holdings
    resp = client.get("/dashboard")
    assert resp.status_code == 200
    html = resp.text
    assert "No holdings yet" in html

    client.cookies.delete("nd_session")

