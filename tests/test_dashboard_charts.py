import json
import uuid
from decimal import Decimal
from datetime import datetime, timezone, timedelta, date
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.database import SessionLocal
from app.models import User, DematAccount, Holding, Instrument, HoldingSnapshot
from app.services.seed_service import provision_new_user
from app.services.auth_service import create_user_session
from app.services.broker_adapter import broker_adapter
from app.services.snapshot_service import record_holding_snapshot, get_holding_snapshots
from app.services.dashboard_chart_service import get_dashboard_charts_data


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


def test_chart_data_totals_equal_summary(client, db):
    """Test that every chart slice total adds up exactly to summary total value."""
    user_email = f"totals.check.{uuid.uuid4().hex[:8]}@gmail.com"
    user = provision_new_user(db, email=user_email, provider="google")
    token = create_user_session(db, user)

    # Inject multi-broker, multi-asset-class holdings
    bundle_a = {
        "provider": "a", "broker_name": "NiftyTrade", "dp_name": "NiftyTrade Securities", "dp_id": "IN300001",
        "status": "connected", "error": None, "profile": {"email": user_email},
        "holdings": [
            {"isin": "INE002A01018", "symbol": "RELIANCE", "name": "Reliance", "quantity": 10, "last_price": 2500.0, "avg_price": 2400.0, "asset_class": "EQUITY"},
            {"isin": "INF204KB14I2", "symbol": "NIFTYBEES", "name": "Nippon ETF", "quantity": 50, "last_price": 260.0, "avg_price": 250.0, "asset_class": "ETF"}
        ]
    }
    bundle_b = {
        "provider": "b", "broker_name": "BharatInvest", "dp_name": "BharatInvest Securities", "dp_id": "IN300002",
        "status": "connected", "error": None, "profile": {"email": user_email},
        "holdings": [
            {"isin": "INE041025011", "symbol": "EMBASSY", "name": "Embassy REIT", "quantity": 100, "last_price": 380.0, "avg_price": 370.0, "asset_class": "REIT"}
        ]
    }
    bundle_c = {
        "provider": "c", "broker_name": "BondBazaar", "dp_name": "BondBazaar Debt", "dp_id": "IN300003",
        "status": "connected", "error": None, "profile": {"email": user_email},
        "holdings": [
            {
                "isin": "IN0020230085", "symbol": "GS2033", "name": "7.18% GS 2033",
                "quantity": 20, "last_price": 1000.0, "avg_price": 1000.0, "asset_class": "BOND",
                "coupon": "7.18", "face_value": "1000", "next_coupon_date": (date.today() + timedelta(days=60)).isoformat()
            }
        ]
    }

    broker_adapter._apply_bundle_to_db(db, user, "a", bundle_a)
    broker_adapter._apply_bundle_to_db(db, user, "b", bundle_b)
    broker_adapter._apply_bundle_to_db(db, user, "c", bundle_c)

    client.cookies.set("nd_session", token)
    resp = client.get("/api/dashboard/charts")
    assert resp.status_code == 200
    data = resp.json()

    total_value = data["summary"]["total_value"]
    # Expected: (10*2500) + (50*260) + (100*380) + (20*1000) = 25000 + 13000 + 38000 + 20000 = 96000.0
    assert total_value == pytest.approx(96000.0, rel=1e-3)

    # 1. Asset class donut sum == summary.total_value
    asset_slices_sum = sum(s["value"] for s in data["asset_class_donut"]["slices"])
    assert asset_slices_sum == pytest.approx(total_value, rel=1e-3)

    # 2. Broker donut sum == summary.total_value
    broker_slices_sum = sum(s["value"] for s in data["broker_donut"]["slices"])
    assert broker_slices_sum == pytest.approx(total_value, rel=1e-3)

    # 3. Broker x Asset Class sum == summary.total_value
    broker_matrix_sum = sum(sum(ds["data"]) for ds in data["broker_x_asset_class"]["datasets"])
    assert broker_matrix_sum == pytest.approx(total_value, rel=1e-3)

    # 4. Merged top holdings total value attribute matches
    assert data["top_holdings"]["total_value"] == pytest.approx(total_value, rel=1e-3)

    client.cookies.delete("nd_session")


def test_zero_holdings_user_gets_empty_charts(client, db):
    """Test that a user with 0 holdings receives empty chart structures and is_empty flag."""
    user_email = f"zero.user.{uuid.uuid4().hex[:8]}@gmail.com"
    user = provision_new_user(db, email=user_email, provider="google")
    token = create_user_session(db, user)

    client.cookies.set("nd_session", token)
    resp = client.get("/api/dashboard/charts")
    assert resp.status_code == 200
    data = resp.json()

    assert data["is_empty"] is True
    assert data["summary"]["total_value"] == 0.0
    assert data["summary"]["total_invested"] == 0.0
    assert data["summary"]["total_pnl"] == 0.0
    assert len(data["asset_class_donut"]["slices"]) == 0
    assert len(data["broker_donut"]["slices"]) == 0
    assert len(data["top_holdings"]["holdings"]) == 0
    assert len(data["pnl_diverging"]["items"]) == 0
    assert data["portfolio_history"]["insufficient_history"] is True
    assert "day you linked" in data["portfolio_history"]["empty_message"]

    client.cookies.delete("nd_session")


def test_two_users_never_see_each_others_data(client, db):
    """Test strict tenant isolation: two users never see or leak each other's portfolio data."""
    email_1 = f"user1.{uuid.uuid4().hex[:8]}@gmail.com"
    email_2 = f"user2.{uuid.uuid4().hex[:8]}@gmail.com"

    user1 = provision_new_user(db, email=email_1, provider="google")
    user2 = provision_new_user(db, email=email_2, provider="google")

    token1 = create_user_session(db, user1)
    token2 = create_user_session(db, user2)

    # User 1 holds RELIANCE (50,000)
    bundle_1 = {
        "provider": "a", "broker_name": "NiftyTrade", "dp_name": "NiftyTrade Securities", "dp_id": "IN300001",
        "status": "connected", "error": None, "profile": {"email": email_1},
        "holdings": [{"isin": "INE002A01018", "symbol": "RELIANCE", "name": "Reliance", "quantity": 20, "last_price": 2500.0, "avg_price": 2500.0}]
    }
    broker_adapter._apply_bundle_to_db(db, user1, "a", bundle_1)

    # User 2 holds TCS (100,000)
    bundle_2 = {
        "provider": "b", "broker_name": "BharatInvest", "dp_name": "BharatInvest Securities", "dp_id": "IN300002",
        "status": "connected", "error": None, "profile": {"email": email_2},
        "holdings": [{"isin": "INE467B01029", "symbol": "TCS", "name": "TCS", "quantity": 25, "last_price": 4000.0, "avg_price": 4000.0}]
    }
    broker_adapter._apply_bundle_to_db(db, user2, "b", bundle_2)

    # Check User 1
    client.cookies.set("nd_session", token1)
    resp1 = client.get("/api/dashboard/charts")
    assert resp1.status_code == 200
    data1 = resp1.json()
    assert data1["summary"]["total_value"] == pytest.approx(50000.0, rel=1e-3)
    symbols1 = [h["symbol"] for h in data1["top_holdings"]["holdings"]]
    assert "RELIANCE" in symbols1
    assert "TCS" not in symbols1

    # Check User 2
    client.cookies.set("nd_session", token2)
    resp2 = client.get("/api/dashboard/charts")
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert data2["summary"]["total_value"] == pytest.approx(100000.0, rel=1e-3)
    symbols2 = [h["symbol"] for h in data2["top_holdings"]["holdings"]]
    assert "TCS" in symbols2
    assert "RELIANCE" not in symbols2

    client.cookies.delete("nd_session")


def test_snapshot_job_stores_one_row_per_user_per_day_and_never_invents_history(db):
    """Test that snapshot job records at most 1 row per user per day and never invents history."""
    email = f"snap.test.{uuid.uuid4().hex[:8]}@gmail.com"
    user = provision_new_user(db, email=email, provider="google")

    # Initial state: 0 snapshots
    count_init = db.query(HoldingSnapshot).filter(HoldingSnapshot.user_id == user.id).count()
    assert count_init == 0

    # Add holding
    bundle = {
        "provider": "a", "broker_name": "NiftyTrade", "dp_name": "NiftyTrade Securities", "dp_id": "IN300001",
        "status": "connected", "error": None, "profile": {"email": email},
        "holdings": [{"isin": "INE002A01018", "symbol": "RELIANCE", "name": "Reliance", "quantity": 10, "last_price": 2500.0, "avg_price": 2500.0}]
    }
    broker_adapter._apply_bundle_to_db(db, user, "a", bundle)

    # First sync trigger
    snap1 = record_holding_snapshot(db, user, force=False)
    assert snap1 is not None
    assert snap1.total_value == pytest.approx(25000.0, rel=1e-3)

    count_after_first = db.query(HoldingSnapshot).filter(HoldingSnapshot.user_id == user.id).count()
    assert count_after_first == 1

    # Second sync on same day (throttled within 1 hour)
    snap2 = record_holding_snapshot(db, user, force=False)
    assert snap2.id == snap1.id
    count_after_second = db.query(HoldingSnapshot).filter(HoldingSnapshot.user_id == user.id).count()
    assert count_after_second == 1

    # Forced sync on same day updates the existing daily row rather than creating a duplicate
    snap3 = record_holding_snapshot(db, user, force=True)
    assert snap3.id == snap1.id
    count_after_forced = db.query(HoldingSnapshot).filter(HoldingSnapshot.user_id == user.id).count()
    assert count_after_forced == 1

    # History query returns only real points (1 point)
    snaps = get_holding_snapshots(db, user, range_filter="ALL")
    assert len(snaps) == 1
    assert snaps[0].total_value == pytest.approx(25000.0, rel=1e-3)


def test_income_calendar_math_for_sample_bond(db):
    """Test bond coupon and maturity arithmetic:
    Bond with 7.5% coupon, face value 1000, 100 units.
    Semi-annual payout = 1000 * 100 * 0.075 / 2 = 3750.
    """
    email = f"bond.math.{uuid.uuid4().hex[:8]}@gmail.com"
    user = provision_new_user(db, email=email, provider="google")

    today = date.today()
    next_coupon = today + timedelta(days=45)
    maturity = today + timedelta(days=225)

    bond_bundle = {
        "provider": "c", "broker_name": "BondBazaar", "dp_name": "BondBazaar Debt", "dp_id": "IN300003",
        "status": "connected", "error": None, "profile": {"email": email},
        "holdings": [
            {
                "isin": "IN0020230085",
                "symbol": "GS2027",
                "name": "7.5% Sovereign Bond 2027",
                "asset_class": "BOND",
                "quantity": 100,
                "last_price": 1000.0,
                "avg_price": 1000.0,
                "coupon": "7.5",
                "face_value": "1000",
                "next_coupon_date": next_coupon.isoformat(),
                "maturity_date": maturity.isoformat()
            }
        ]
    }
    broker_adapter._apply_bundle_to_db(db, user, "c", bond_bundle)

    chart_data = get_dashboard_charts_data(db, user)
    inc = chart_data["income_calendar"]
    assert inc["has_bonds"] is True

    # Payout check:
    # Semi-annual coupon = 1000 * 100 * 0.075 / 2 = 3750.00
    events = inc["events"]
    coupon_events = [e for e in events if e["type"] == "Coupon"]
    assert len(coupon_events) >= 1
    assert coupon_events[0]["amount"] == 3750.00

    # Maturity payout = 1000 * 100 = 100,000.00
    mat_events = [e for e in events if e["type"] == "Maturity"]
    assert len(mat_events) == 1
    assert mat_events[0]["amount"] == 100000.00
