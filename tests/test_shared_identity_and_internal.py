import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.config import settings
from app.database import SessionLocal, Base, engine
from app.models import User, DematAccount, Holding, OutboxEvent, AuthMethod
from app.shared_identity import normalize_email, generate_identity
from app.services.portfolio_service import generate_starter_portfolio
from app.services.outbox_service import enqueue_outbox_event, dispatch_pending_outbox
from app.services.auth_service import match_or_create_user, create_user_session
from app.routers.depository import serialize_user_profile, serialize_user_holdings

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

# 1. Deterministic and Unique Identity Tests
def test_generate_identity_deterministic_and_unique():
    email1 = "kavita.deshmukh@example.com"
    email2 = "rahul.sharma@example.com"

    id1_a = generate_identity(email1)
    id1_b = generate_identity(email1)
    id2 = generate_identity(email2)

    # Identical output for the same email
    assert id1_a == id1_b
    assert id1_a["full_name_fallback"] == "Kavita Deshmukh"
    
    # Different output for different emails
    assert id1_a["masked_pan"] != id2["masked_pan"]
    assert id1_a["mobile"] != id2["mobile"]
    assert id1_a["client_code_suffix"] != id2["client_code_suffix"]

    # Format verification
    # Fake PAN: ABCXX1234X
    pan = id1_a["masked_pan"]
    assert len(pan) == 10
    assert pan[:3].isalpha() and pan[:3].isupper()
    assert pan[3:5] == "XX"
    assert pan[5:9].isdigit()
    assert pan[9] == "X"

    # Mobile: 10 digits starting with 9
    mobile = id1_a["mobile"]
    assert len(mobile) == 10
    assert mobile.startswith("9")
    assert mobile.isdigit()

    # DOB: DDMM only (e.g. 1508)
    dob = id1_a["dob"]
    assert len(dob) == 4
    day = int(dob[:2])
    month = int(dob[2:])
    assert 1 <= day <= 28
    assert 1 <= month <= 12

    # Nominee and city presence
    assert id1_a["address_city"] != ""
    assert id1_a["nominee_name"] != ""

# 2. Email Normalization Tests
def test_email_normalization():
    assert normalize_email("  Rohan.Patel@Domain.COM  ") == "rohan.patel@domain.com"
    assert normalize_email("USER@TEST.IN") == "user@test.in"
    assert normalize_email("   ") == ""
    assert normalize_email(None) == ""

    # generate_identity produces identical output regardless of casing or whitespace
    res_clean = generate_identity("rohan.patel@domain.com")
    res_dirty = generate_identity("  Rohan.Patel@Domain.COM  ")
    assert res_clean == res_dirty

# 3. Provisioning Idempotency
def test_provisioning_idempotency(client, db):
    test_email = "vikram.malhotra@testdomain.com"
    
    # Cleanup if exists
    existing = db.query(User).filter(User.email == test_email).first()
    if existing:
        db.delete(existing)
        db.commit()

    headers = {"x-internal-key": settings.INTERNAL_API_KEY}
    payload = {"email": "  Vikram.Malhotra@TestDomain.COM  ", "full_name": "Vikram Malhotra"}

    # First call: CREATED
    resp1 = client.post("/internal/v1/users/provision", json=payload, headers=headers)
    assert resp1.status_code == 200
    data1 = resp1.json()
    assert data1["status"] == "CREATED"
    assert data1["email"] == test_email
    client_code = data1["client_code"]
    assert client_code.startswith("12081600")

    # Verify user in database
    user = db.query(User).filter(User.email == test_email).first()
    assert user is not None
    assert user.wallet_balance == 1000000.0
    assert len(user.demat_accounts) >= 1

    # Second call: EXISTS (Idempotent)
    resp2 = client.post("/internal/v1/users/provision", json=payload, headers=headers)
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert data2["status"] == "EXISTS"
    assert data2["client_code"] == client_code
    assert data2["email"] == test_email

# 4. Deterministic Starter Portfolio
def test_deterministic_starter_portfolio():
    email = "sneha.iyer@testdomain.com"

    # Theme B (stocks + ETFs + 1 InvIT)
    holdings_b1 = generate_starter_portfolio(email, provider_code="b")
    holdings_b2 = generate_starter_portfolio(email, provider_code="b")
    assert holdings_b1 == holdings_b2
    assert 6 <= len(holdings_b1) <= 10

    # Verify quantities and price deviation within +/-15%
    for h in holdings_b1:
        assert h["free_units"] > 0
        assert h["total_units"] == h["free_units"]
        # avg_price within +/-15% of last_price
        ratio = h["avg_price"] / h["last_price"]
        assert 0.849 <= ratio <= 1.151

    # Theme A (stocks + 1 REIT)
    holdings_a = generate_starter_portfolio(email, provider_code="a")
    assert 6 <= len(holdings_a) <= 10

    # Verify overlap across brokers on RELIANCE, TCS, HDFCBANK
    isins_a = {h["isin"] for h in holdings_a}
    isins_b = {h["isin"] for h in holdings_b1}
    overlap = isins_a.intersection(isins_b)
    assert "INE002A01018" in overlap  # RELIANCE
    assert "INE467B01029" in overlap  # TCS
    assert "INE040A01034" in overlap  # HDFCBANK

# 5. Internal Endpoints Key Authentication and Error Handling
def test_internal_endpoints_key_authentication(client):
    test_email = "aarav.mehta@example.com"

    # 1. Missing key -> 401
    resp_no_key = client.get(f"/internal/v1/users/{test_email}/profile")
    assert resp_no_key.status_code == 401
    assert resp_no_key.json()["detail"]["code"] == "UNAUTHORIZED"

    # 2. Wrong key -> 401
    resp_wrong_key = client.get(
        f"/internal/v1/users/{test_email}/profile",
        headers={"x-internal-key": "invalid-secret-key"}
    )
    assert resp_wrong_key.status_code == 401
    assert resp_wrong_key.json()["detail"]["code"] == "UNAUTHORIZED"

    # 3. Missing user with valid key -> 404 USER_NOT_FOUND
    valid_headers = {"x-internal-key": settings.INTERNAL_API_KEY}
    resp_missing_user = client.get(
        "/internal/v1/users/nonexistent.user.99@example.com/profile",
        headers=valid_headers
    )
    assert resp_missing_user.status_code == 404
    assert resp_missing_user.json()["detail"]["code"] == "USER_NOT_FOUND"

    resp_missing_holdings = client.get(
        "/internal/v1/users/nonexistent.user.99@example.com/holdings",
        headers=valid_headers
    )
    assert resp_missing_holdings.status_code == 404
    assert resp_missing_holdings.json()["detail"]["code"] == "USER_NOT_FOUND"

    resp_missing_summary = client.get(
        "/internal/v1/users/nonexistent.user.99@example.com/summary",
        headers=valid_headers
    )
    assert resp_missing_summary.status_code == 404
    assert resp_missing_summary.json()["detail"]["code"] == "USER_NOT_FOUND"

# 6. Internal Holdings JSON Equals Public Holdings JSON
def test_internal_holdings_equals_public_holdings(client, db):
    email = "aarav.mehta@example.com"
    user = db.query(User).filter(User.email == email).first()
    assert user is not None

    # Fetch via internal endpoint
    internal_resp = client.get(
        f"/internal/v1/users/{email}/holdings",
        headers={"x-internal-key": settings.INTERNAL_API_KEY}
    )
    assert internal_resp.status_code == 200
    internal_data = internal_resp.json()

    # Authenticate session for public endpoint
    session_token = create_user_session(db, user)
    client.cookies.set("nd_session", session_token)

    public_resp = client.get("/api/v1/holdings")
    assert public_resp.status_code == 200
    public_data = public_resp.json()

    # Clear cookie
    client.cookies.delete("nd_session")

    # Assert exact match of JSON structure, pagination, and values
    assert internal_data["total"] == public_data["total"]
    assert internal_data["page"] == public_data["page"]
    assert internal_data["page_size"] == public_data["page_size"]
    assert internal_data["total_pages"] == public_data["total_pages"]
    assert len(internal_data["holdings"]) == len(public_data["holdings"])
    assert internal_data["holdings"] == public_data["holdings"]

# 7. Google Sign-In Links to Provisioned User
def test_google_signin_links_to_provisioned_user(client, db):
    link_email = "aditya.joshi@example.com"

    # Provision user first via internal endpoint
    prov_resp = client.post(
        "/internal/v1/users/provision",
        json={"email": link_email, "full_name": "Aditya Joshi"},
        headers={"x-internal-key": settings.INTERNAL_API_KEY}
    )
    assert prov_resp.status_code == 200
    client_code = prov_resp.json()["client_code"]

    user_prov = db.query(User).filter(User.email == link_email).first()
    assert user_prov is not None
    prov_user_id = user_prov.id
    prov_holdings_count = len(user_prov.demat_accounts[0].holdings)

    # Later, person signs in with Google OAuth using the same email
    google_user = match_or_create_user(
        db,
        email="  Aditya.Joshi@Example.COM  ",
        provider="google",
        provider_sub="google-oauth2-id-998877",
        name="Aditya Joshi"
    )

    # Must link to the exact same account
    assert google_user.id == prov_user_id
    assert google_user.bo_id == client_code
    assert google_user.email == link_email
    assert len(google_user.demat_accounts[0].holdings) == prov_holdings_count

    # Check auth methods linked
    auth_methods = {am.provider for am in google_user.auth_methods}
    assert "internal" in auth_methods
    assert "google" in auth_methods

# 8. Outbox Event Enqueueing and Non-blocking Retries
def test_outbox_events_and_retry(db):
    test_email = "outbox.test@example.com"

    # Enqueue event
    evt = enqueue_outbox_event(db, email=test_email, event="HOLDINGS_CHANGED", provider="b")
    assert evt is not None
    assert evt.email == test_email
    assert evt.event == "HOLDINGS_CHANGED"
    assert evt.status == "PENDING"
    assert evt.attempts == 0
    assert evt.max_attempts == 10

    # Dispatch when TRADEONE_URL is not set (gracefully skips without failing)
    dispatched = dispatch_pending_outbox(db)
    assert dispatched == 0
    assert evt.status == "PENDING"
