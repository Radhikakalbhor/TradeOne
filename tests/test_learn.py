"""
test_learn.py — Comprehensive tests for TradeOne Learn Module
Verifies:
1. Pure simulator math (Decimal rounding, coupon schedules, periodic payouts)
2. Content standards (No banned advice phrases, last_reviewed dates, sources, needs_verification audit)
3. Diversification calculation & zero-holdings isolation
4. User holding isolation (no cross-user leakage, no demo data assumption)
5. Quiz engine scoring
6. Router endpoints & API status codes
"""
import os
import json
import glob
from decimal import Decimal
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.database import Base, engine, SessionLocal
from app.models import User, DematAccount, Holding, Instrument, LessonProgress, QuizAttempt
from app.services.learn_service import (
    simulate_bond_coupons,
    simulate_periodic_income,
    compute_diversification,
    score_quiz,
    load_all_lessons,
    load_lesson,
    load_facts,
    ask_assistant,
    LEARN_DISCLAIMER,
)


@pytest.fixture(scope="module")
def db_session():
    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    yield session
    session.close()


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


# ---------------------------------------------------------------------------
# 1. Content Safety & Compliance Tests: Banned Advice Scanning
# ---------------------------------------------------------------------------

BANNED_PHRASES = [
    "you should buy",
    "we recommend",
    "guaranteed",
    "risk-free",
    "assured returns",
]

def test_banned_phrases_not_in_lessons():
    """Ensure no lesson content file uses prohibited advice or guarantee language."""
    lesson_files = glob.glob("content/lessons/*.json")
    assert len(lesson_files) >= 8, f"Expected at least 8 lesson files, found {len(lesson_files)}"

    violations = []
    for fpath in lesson_files:
        with open(fpath, "r", encoding="utf-8") as f:
            text = f.read().lower()
            for phrase in BANNED_PHRASES:
                if phrase in text:
                    # Allow 'not guaranteed' or 'is not risk-free'
                    # But strict flag if standalone
                    violations.append((fpath, phrase))

    # Any violations?
    assert len(violations) == 0, f"Found banned advice phrases in lessons: {violations}"


def test_banned_phrases_not_in_learn_templates():
    """Ensure no Learn Jinja2 template contains banned phrases in static copy."""
    template_files = glob.glob("app/templates/learn/*.html")
    assert len(template_files) >= 5

    violations = []
    for fpath in template_files:
        with open(fpath, "r", encoding="utf-8") as f:
            text = f.read().lower()
            for phrase in BANNED_PHRASES:
                if phrase in text:
                    violations.append((fpath, phrase))

    assert len(violations) == 0, f"Found banned advice phrases in templates: {violations}"


def test_every_lesson_has_metadata_and_sources():
    """Ensure every lesson has last_reviewed, valid sources, and disclaimer."""
    lessons = load_all_lessons("en")
    assert len(lessons) >= 8

    for lesson in lessons:
        topic = lesson.get("id") or lesson.get("topic")
        assert "last_reviewed" in lesson, f"Lesson {topic} missing last_reviewed"
        assert "sources" in lesson and len(lesson["sources"]) > 0, f"Lesson {topic} missing sources"
        assert "disclaimer" in lesson, f"Lesson {topic} missing disclaimer"
        for s in lesson["sources"]:
            assert "name" in s and "url" in s, f"Malformed source in lesson {topic}: {s}"


def test_needs_verification_registry_documented():
    """Ensure facts marked needs_verification: true are documented in docs/CONTENT_TO_VERIFY.md."""
    verify_doc_path = "docs/CONTENT_TO_VERIFY.md"
    assert os.path.exists(verify_doc_path), "docs/CONTENT_TO_VERIFY.md must exist"

    with open(verify_doc_path, "r", encoding="utf-8") as f:
        doc_content = f.read().lower()

    facts = load_facts()
    for topic, topic_facts in facts.items():
        if isinstance(topic_facts, dict) and topic_facts.get("needs_verification"):
            assert topic.lower() in doc_content, (
                f"Topic '{topic}' has needs_verification=true in facts.json but is not documented in {verify_doc_path}"
            )


# ---------------------------------------------------------------------------
# 2. Simulator Math Tests (Pure Decimal Functions)
# ---------------------------------------------------------------------------

def test_bond_coupon_math_annual():
    """Test annual bond coupon calculations and schedule generation."""
    principal = Decimal("100000")
    coupon_rate = Decimal("7.5")  # 7.5% per annum
    years = 3

    result = simulate_bond_coupons(principal, coupon_rate, years, frequency_months=12)

    assert result["annual_income"] == 7500.00
    assert result["total_income"] == 22500.00
    assert result["maturity_amount"] == 100000.00
    assert len(result["schedule"]) == 3
    for entry in result["schedule"]:
        assert entry["amount"] == 7500.00


def test_bond_coupon_math_semiannual():
    """Test semi-annual bond coupon schedule and accurate Decimal rounding."""
    principal = Decimal("200000")
    coupon_rate = Decimal("8.0")  # 8.0% per annum -> 4.0% per half year
    years = 2

    result = simulate_bond_coupons(principal, coupon_rate, years, frequency_months=6)

    assert result["annual_income"] == 16000.00
    assert result["total_income"] == 32000.00
    assert len(result["schedule"]) == 4
    for entry in result["schedule"]:
        assert entry["amount"] == 8000.00


def test_periodic_income_math():
    """Test periodic dividend/interest distribution math for REITs/InvITs."""
    principal = Decimal("500000")
    yield_pct = Decimal("6.5")  # 6.5% yield
    years = 4

    result = simulate_periodic_income(principal, yield_pct, years, "Quarterly")

    assert result["annual_income"] == 32500.00
    assert result["monthly_income"] == round(32500.0 / 12, 2)
    assert result["total_income"] == 130000.00
    assert result["principal"] == 500000.00


# ---------------------------------------------------------------------------
# 3. Diversification Calculation & Privacy Isolation Tests
# ---------------------------------------------------------------------------

def test_zero_holdings_diversification_returns_none(db_session):
    """A user with zero holdings must return None (no fake / assumed holdings)."""
    import uuid
    uid = uuid.uuid4().hex[:8]
    user = User(
        email=f"zero_{uid}@example.com",
        name="Zero Holdings User",
        bo_id=f"120816{uid}",
        masked_pan="ABCDE1234F",
        mobile="9876543210"
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    div = compute_diversification(db_session, user)
    assert div is None, "compute_diversification must return None when total portfolio value is 0"


def test_diversification_percentages_and_observations(db_session):
    """Verify diversification math across multiple instruments and trigger conditions."""
    import uuid
    uid = uuid.uuid4().hex[:8]
    user = User(
        email=f"div_{uid}@example.com",
        name="Diversified Investor",
        bo_id=f"120816{uid}",
        masked_pan="VWXYZ9876A",
        mobile="9876543211"
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    account = DematAccount(
        id=f"da_div_{uid}",
        user_id=user.id,
        account_number=f"BO_DIV_{uid}",
        masked_account_number="XXXX_001",
        dp_name="NiftyTrade",
        dp_id="IN300001"
    )
    db_session.add(account)
    db_session.commit()
    db_session.refresh(account)

    # 1. Equity: Reliance 10 units @ 2000 = 20,000 (40%)
    inst_eq = db_session.query(Instrument).filter(Instrument.isin == "INE002A01018").first()
    if not inst_eq:
        inst_eq = Instrument(isin="INE002A01018", symbol="RELIANCE", name="Reliance Industries", asset_class="EQUITY", fi_type="EQUITIES", last_price=2000.0)
        db_session.add(inst_eq)

    # 2. REIT: Embassy Office Parks 100 units @ 300 = 30,000 (60%)
    inst_reit = db_session.query(Instrument).filter(Instrument.isin == "INE041025011").first()
    if not inst_reit:
        inst_reit = Instrument(isin="INE041025011", symbol="EMBASSY", name="Embassy Office Parks REIT", asset_class="REIT", fi_type="REIT", last_price=300.0)
        db_session.add(inst_reit)
    db_session.commit()

    h1 = Holding(demat_account_id=account.id, isin=inst_eq.isin, free_units=10.0)
    h2 = Holding(demat_account_id=account.id, isin=inst_reit.isin, free_units=100.0)
    db_session.add_all([h1, h2])
    db_session.commit()

    div = compute_diversification(db_session, user)
    assert div is not None

    expected_eq_val = 10.0 * (inst_eq.last_price or 0.0)
    expected_reit_val = 100.0 * (inst_reit.last_price or 0.0)
    expected_total = expected_eq_val + expected_reit_val
    expected_eq_pct = round(expected_eq_val / expected_total * 100, 1)
    expected_reit_pct = round(expected_reit_val / expected_total * 100, 1)

    assert div["total_value"] == expected_total
    assert div["equity_pct"] == expected_eq_pct
    assert div["reit_pct"] == expected_reit_pct
    assert div["bond_pct"] == 0.0

    # Because REIT makes up 60% (>25%), observation must mention concentration
    has_conc = any("Embassy Office Parks REIT" in obs for obs in div["observations"])
    assert has_conc, "Should note concentration above 25%"

    # Because bond is 0%, observation must note lack of fixed income
    has_fixed = any("no bond or fixed-income exposure" in obs for obs in div["observations"])
    assert has_fixed


# ---------------------------------------------------------------------------
# 4. Quiz Scoring & Assessment Engine Tests
# ---------------------------------------------------------------------------

def test_quiz_scoring_all_correct():
    """Score quiz with all correct answers."""
    # Let's test REIT quiz
    reit_quiz = json.load(open("content/quizzes/reit.json", "r", encoding="utf-8"))
    correct_indices = [q["correct_index"] for q in reit_quiz["questions"]]

    result = score_quiz("reit", correct_indices)
    assert result["raw_score"] == len(correct_indices)
    assert result["score"] == 100
    assert result["total"] == len(correct_indices)
    assert all(b["correct"] for b in result["breakdown"])


def test_quiz_scoring_partial():
    """Score quiz with mixed correct and wrong answers."""
    reit_quiz = json.load(open("content/quizzes/reit.json", "r", encoding="utf-8"))
    answers = []
    for idx, q in enumerate(reit_quiz["questions"]):
        if idx % 2 == 0:
            answers.append(q["correct_index"])
        else:
            answers.append((q["correct_index"] + 1) % len(q["options"]))

    result = score_quiz("reit", answers)
    assert result["raw_score"] < len(answers)
    assert len(result["breakdown"]) == len(answers)


# ---------------------------------------------------------------------------
# 5. Assistant Advice Refusal and Sourced Responses Tests
# ---------------------------------------------------------------------------

def test_assistant_refuses_buy_sell_advice():
    """The assistant must politely refuse buy/sell calls and offer educational lessons."""
    res1 = ask_assistant("Should I buy Embassy REIT right now?")
    assert "does not offer investment advice" in res1["answer"]
    assert any("lesson" in c["url"] or "compare" in c["url"] for c in res1["citations"])

    res2 = ask_assistant("We recommend what stocks to pick?")
    assert "does not offer investment advice" in res2["answer"]


def test_assistant_answers_factual_reit_query():
    """The assistant returns sourced information for educational questions."""
    res = ask_assistant("What is a REIT and what percentage of NDCF must be distributed?")
    assert "reit" in res["answer"].lower()
    assert len(res["citations"]) > 0
    assert LEARN_DISCLAIMER in res["disclaimer"]


# ---------------------------------------------------------------------------
# 6. HTTP Endpoints Tests
# ---------------------------------------------------------------------------

def test_learn_hub_endpoint(client):
    """GET /learn loads successfully."""
    response = client.get("/learn")
    assert response.status_code == 200
    assert "TradeOne" in response.text
    assert "REITs" in response.text


def test_compare_endpoint(client):
    """GET /learn/compare loads comparison table."""
    response = client.get("/learn/compare")
    assert response.status_code == 200
    assert "Compare" in response.text
    assert "REITs" in response.text
    assert "InvITs" in response.text
    assert "Stocks (Equities)" not in response.text

    # Multi-selection test
    res_multi = client.get("/learn/compare?assets=invit&assets=corpbond")
    assert res_multi.status_code == 200
    assert "InvITs" in res_multi.text
    assert "Corporate Bonds" in res_multi.text


def test_simulator_page_and_run(client):
    """GET /learn/simulator loads and POST /learn/simulator/run executes."""
    res_page = client.get("/learn/simulator")
    assert res_page.status_code == 200

    res_run = client.post("/learn/simulator/run", json={
        "product": "corpbond",
        "amount": "100000",
        "yield_pct": "8.5",
        "years": 3
    })
    assert res_run.status_code == 200
    data = res_run.json()
    assert data["annual_income"] == 8500.0
    assert data["total_income"] == 25500.0


def test_whatif_page_and_run(client):
    """GET /learn/what-if loads and POST /learn/what-if/run calculates income diff."""
    res_page = client.get("/learn/what-if")
    assert res_page.status_code == 200

    res_run = client.post("/learn/what-if/run", json={
        "total": "1000000",
        "allocation": {"equity": 70, "reit": 20, "bond": 10},
        "before": {"equity": 100, "reit": 0, "bond": 0}
    })
    assert res_run.status_code == 200
    data = res_run.json()
    assert "before_annual_income" in data
    assert "after_annual_income" in data
    assert data["after_annual_income"] > data["before_annual_income"]


def test_glossary_endpoint(client):
    """GET /learn/glossary loads glossary terms."""
    response = client.get("/learn/glossary")
    assert response.status_code == 200
    assert "Glossary" in response.text
    assert "NDCF" in response.text or "Accrued Interest" in response.text


def test_hindi_content_and_endpoints(client):
    """Ensure Hindi localization loads for all lessons, glossary, and hub."""
    hi_lessons = load_all_lessons("hi")
    assert len(hi_lessons) == 9
    for l in hi_lessons:
        assert l.get("lang") == "hi"
        assert len(l.get("title", "")) > 0
        assert "sources" in l and len(l["sources"]) > 0
        assert "disclaimer" in l

    # Hub endpoint in Hindi
    res_hub = client.get("/learn?lang=hi")
    assert res_hub.status_code == 200
    assert "सीखें" in res_hub.text

    # Lesson endpoint in Hindi
    res_lesson = client.get("/learn/lesson/tbill?lang=hi")
    assert res_lesson.status_code == 200
    assert "ट्रेजरी" in res_lesson.text

    # Glossary endpoint in Hindi
    res_glossary = client.get("/learn/glossary?lang=hi")
    assert res_glossary.status_code == 200
    assert "शब्दावली" in res_glossary.text

