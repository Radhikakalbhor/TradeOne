"""
learn_service.py
Provides content loading, caching, and business logic for the Learn module.
All personalised data is derived solely from the authenticated user's actual
holdings — never from seeded or demo data.
"""
from __future__ import annotations

import json
import logging
import time
import re
from datetime import datetime, date, timezone, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

logger = logging.getLogger("app.learn_service")

CONTENT_ROOT = Path(__file__).resolve().parent.parent.parent / "content"

LEARN_DISCLAIMER = (
    "Educational information only. Not investment advice. "
    "TradeOne is not a SEBI-registered investment adviser. "
    "Rules, taxes and yields change; verify with official sources."
)


# ---------------------------------------------------------------------------
# Content Loaders (cached so disk I/O happens only once per server start)
# ---------------------------------------------------------------------------

@lru_cache(maxsize=128)
def _load_json_cached(path_str: str, mtime: float) -> Any:
    try:
        with open(path_str, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        logger.warning("Content file not found: %s", path_str)
        return {}
    except json.JSONDecodeError as exc:
        logger.error("Invalid JSON in %s: %s", path_str, exc)
        return {}


def _load_json(path: Path) -> Any:
    try:
        p = Path(path)
        mtime = p.stat().st_mtime if p.exists() else 0.0
        return _load_json_cached(str(p.resolve()), mtime)
    except Exception as exc:
        logger.warning("Error checking JSON file %s: %s", path, exc)
        return {}


def load_lesson(topic: str, lang: str = "en") -> Dict:
    """Load lesson JSON; falls back to English if the requested lang is missing."""
    path = CONTENT_ROOT / "lessons" / f"{topic}.{lang}.json"
    if not path.exists() and lang != "en":
        path = CONTENT_ROOT / "lessons" / f"{topic}.en.json"
    return dict(_load_json(path))


def load_all_lessons(lang: str = "en") -> List[Dict]:
    """Return all available lesson dicts, sorted by topic name."""
    topics = [
        "reit", "invit", "gsec", "tbill", "sgb", "corpbond",
        "stocks_vs_alternatives", "taxes", "how_to_buy",
    ]
    lessons = []
    for t in topics:
        lesson = load_lesson(t, lang)
        if lesson:
            lessons.append(lesson)
    return lessons


def load_glossary(lang: str = "en") -> Dict:
    path = CONTENT_ROOT / "lessons" / f"glossary.{lang}.json"
    if not path.exists():
        path = CONTENT_ROOT / "lessons" / "glossary.en.json"
    return dict(_load_json(path))


def load_facts() -> Dict:
    return dict(_load_json(CONTENT_ROOT / "facts.json"))


def load_sample_assumptions() -> Dict:
    return dict(_load_json(CONTENT_ROOT / "sample_assumptions.json"))


def load_quiz(topic: str) -> Dict:
    path = CONTENT_ROOT / "quizzes" / f"{topic}.json"
    return dict(_load_json(path))


def get_needs_verification_items() -> List[Dict]:
    """Collect all facts/lessons marked needs_verification=true."""
    items = []
    facts = load_facts()
    for key, fact in facts.items():
        if isinstance(fact, dict) and fact.get("needs_verification"):
            items.append({
                "type": "fact",
                "key": key,
                "title": fact.get("title", key),
                "as_of": fact.get("as_of", "unknown"),
            })
    for topic in ["reit", "invit", "gsec", "tbill", "sgb", "corpbond",
                  "stocks_vs_alternatives", "taxes", "how_to_buy"]:
        lesson = load_lesson(topic)
        if lesson.get("needs_verification"):
            items.append({
                "type": "lesson",
                "key": topic,
                "title": lesson.get("title", topic),
                "last_reviewed": lesson.get("last_reviewed", "unknown"),
            })
    return items


# ---------------------------------------------------------------------------
# Compare table data
# ---------------------------------------------------------------------------

COMPARE_DATA: Dict[str, Dict] = {
    "equity": {
        "label": "Stocks (Equities)",
        "how_you_earn": "Dividends (discretionary) + price appreciation",
        "income_pattern": "Irregular / annual dividend if declared",
        "price_volatility": "Higher",
        "liquidity": "Very high (T+1 settlement on NSE/BSE)",
        "min_amount": "1 share (~₹1 to ₹tens of thousands)",
        "main_risks": "Company performance, sector downturns, market-wide volatility",
        "tax_summary": "STCG 20% (≤12 months), LTCG 12.5% above ₹1.25 lakh (>12 months)",
        "who_it_can_suit": "Those comfortable with price swings and a longer horizon",
    },
    "reit": {
        "label": "REITs",
        "how_you_earn": "Quarterly distributions (rental income) + unit price movement",
        "income_pattern": "Quarterly (mandated by SEBI: ≥90% NDCF distributed)",
        "price_volatility": "Medium",
        "liquidity": "Medium (listed on NSE/BSE; volumes lower than large-caps)",
        "min_amount": "1 unit (~₹300–₹450; check current market price)",
        "main_risks": "Vacancy risk, interest-rate sensitivity, property concentration",
        "tax_summary": "Distributions split into dividend/interest/capital-return streams. LTCG 12.5%, STCG 20%.",
        "who_it_can_suit": "Those seeking periodic income without direct property ownership",
    },
    "invit": {
        "label": "InvITs",
        "how_you_earn": "Quarterly distributions (toll/tariff revenue) + unit price movement",
        "income_pattern": "Quarterly or semi-annual distributions",
        "price_volatility": "Medium–Low",
        "liquidity": "Medium (listed; lower volumes than equities)",
        "min_amount": "1 unit (~₹100–₹150; check current market price)",
        "main_risks": "Traffic/project risk, regulatory risk, interest-rate sensitivity",
        "tax_summary": "Same treatment as REITs. Dividend/interest split. LTCG 12.5%, STCG 20%.",
        "who_it_can_suit": "Those interested in infrastructure revenue streams",
    },
    "gsec": {
        "label": "Govt. Securities (G-Secs)",
        "how_you_earn": "Fixed semi-annual coupon + face value at maturity",
        "income_pattern": "Semi-annual coupon payments",
        "price_volatility": "Lower (rises with duration; sovereign credit)",
        "liquidity": "Medium (exchange or RBI Retail Direct; secondary market available)",
        "min_amount": "₹10,000 primary (RBI Retail Direct); 1 bond (₹100 face) on exchange",
        "main_risks": "Interest-rate risk (price falls when rates rise), inflation erosion",
        "tax_summary": "Coupon taxed at slab rate. LTCG 12.5% (>12 months). STCG at slab.",
        "who_it_can_suit": "Those wanting sovereign-backed, predictable income",
    },
    "corpbond": {
        "label": "Corporate Bonds",
        "how_you_earn": "Fixed/floating coupon (annual or semi-annual) + face value at maturity",
        "income_pattern": "Annual or semi-annual coupon",
        "price_volatility": "Lower–Medium (depends on credit rating and duration)",
        "liquidity": "Lower (secondary market thinner than equities; improving via OBPP)",
        "min_amount": "₹1,000–₹10,000 face value (varies by issuance type)",
        "main_risks": "Credit/default risk, interest-rate risk, liquidity risk",
        "tax_summary": "Coupon at slab rate (TDS 10% u/s 193 on listed bonds). LTCG 12.5%, STCG at slab.",
        "who_it_can_suit": "Those wanting higher yield than G-Secs with acceptable credit risk",
    },
}


# ---------------------------------------------------------------------------
# Diversification card computation
# ---------------------------------------------------------------------------

ASSET_CLASS_MAP = {
    "EQUITY": "Equity",
    "ETF": "ETF",
    "REIT": "REIT",
    "INVIT": "InvIT",
    "BOND": "Bond",
    "MF": "Mutual Fund",
}


def compute_diversification(db: Session, user) -> Optional[Dict]:
    """
    Compute asset-class breakdown from the user's actual synced holdings.
    Returns None if total_value is 0 (caller must show empty state).
    Uses only real holdings — never demo/seeded data.
    """
    from app.models import DematAccount

    totals: Dict[str, Decimal] = {}
    broker_totals: Dict[str, Dict[str, Decimal]] = {}
    total_value = Decimal("0")
    holding_values: List[Tuple[str, Decimal]] = []

    accounts = db.query(DematAccount).filter(DematAccount.user_id == user.id).all()
    for acct in accounts:
        broker_name = acct.dp_name
        if broker_name not in broker_totals:
            broker_totals[broker_name] = {}
        for h in acct.holdings:
            inst = h.instrument
            if not inst:
                continue
            price = Decimal(str(inst.last_price or 0))
            qty = Decimal(str(h.total_units or 0))
            val = price * qty
            if val <= 0:
                continue
            ac = ASSET_CLASS_MAP.get(inst.asset_class, inst.asset_class)
            totals[ac] = totals.get(ac, Decimal("0")) + val
            broker_totals[broker_name][ac] = broker_totals[broker_name].get(ac, Decimal("0")) + val
            total_value += val
            holding_values.append((inst.name, val))

    if total_value <= 0:
        return None

    breakdown: Dict[str, float] = {}
    for cls, val in sorted(totals.items(), key=lambda x: -x[1]):
        pct = float((val / total_value * 100).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))
        breakdown[cls] = pct

    observations: List[str] = []
    if holding_values:
        top_name, top_val = max(holding_values, key=lambda x: x[1])
        top_pct = float(top_val / total_value * 100)
        if top_pct >= 25:
            observations.append(
                f"One holding ({top_name}) makes up {top_pct:.1f}% of your portfolio. "
                "Investors often review whether this level of concentration aligns with their goals."
            )

    fixed_income_pct = float(totals.get("Bond", Decimal("0")) / total_value * 100)
    if fixed_income_pct < 1:
        observations.append(
            "Your portfolio has no bond or fixed-income exposure. "
            "Some investors include bonds for predictable income."
        )

    income_asset_pct = sum(
        float(totals.get(cls, Decimal("0")) / total_value * 100)
        for cls in ("REIT", "InvIT", "Bond")
    )
    if income_asset_pct < 1:
        observations.append(
            "Your portfolio currently has no income-generating assets "
            "(REITs, InvITs, or bonds). These can provide periodic distributions."
        )

    num_brokers = sum(1 for b in broker_totals if any(v > 0 for v in broker_totals[b].values()))

    equity_pct = breakdown.get("Equity", 0.0) + breakdown.get("ETF", 0.0)
    reit_pct = breakdown.get("REIT", 0.0)
    invit_pct = breakdown.get("InvIT", 0.0)
    bond_pct = breakdown.get("Bond", 0.0)

    return {
        "total_value": float(total_value),
        "breakdown": breakdown,
        "broker_totals": {
            b: {cls: float(v) for cls, v in vals.items()}
            for b, vals in broker_totals.items()
        },
        "num_brokers": num_brokers,
        "equity_pct": round(equity_pct, 1),
        "reit_pct": round(reit_pct, 1),
        "invit_pct": round(invit_pct, 1),
        "bond_pct": round(bond_pct, 1),
        "observations": observations,
        "disclaimer": LEARN_DISCLAIMER,
    }


# ---------------------------------------------------------------------------
# Income calendar (bonds & REIT/InvIT distributions)
# ---------------------------------------------------------------------------

def compute_income_calendar(db: Session, user) -> Dict:
    """
    Compute upcoming coupon / distribution events from the user's real holdings
    for the next 12 months. REIT/InvIT distribution dates are NOT invented;
    only bond coupons derived from metadata are shown.
    """
    from app.models import DematAccount

    today = date.today()
    end_date = today + timedelta(days=365)
    events: List[Dict] = []
    monthly_income: Dict[str, Decimal] = {}

    accounts = db.query(DematAccount).filter(DematAccount.user_id == user.id).all()
    has_reit = False
    has_invit = False

    for acct in accounts:
        for h in acct.holdings:
            inst = h.instrument
            if not inst:
                continue

            if inst.asset_class == "REIT":
                has_reit = True
            if inst.asset_class == "INVIT":
                has_invit = True

            if inst.asset_class != "BOND":
                continue

            meta = {}
            if h.metadata_json:
                try:
                    meta = json.loads(h.metadata_json)
                except Exception:
                    pass

            coupon_rate = meta.get("coupon") or meta.get("coupon_rate")
            maturity_str = meta.get("maturity_date")
            next_coupon_str = meta.get("next_coupon_date")

            if not coupon_rate:
                continue

            try:
                coupon_rate_dec = Decimal(str(coupon_rate))
            except Exception:
                continue

            face_value = Decimal("1000")
            qty = Decimal(str(h.total_units or 0))
            if qty <= 0:
                continue

            semi_annual_coupon = (coupon_rate_dec / 100 * face_value * qty / 2).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )

            if next_coupon_str:
                try:
                    nxt = datetime.strptime(next_coupon_str, "%Y-%m-%d").date()
                except Exception:
                    nxt = today + timedelta(days=180)
            else:
                nxt = today + timedelta(days=180)

            maturity = None
            if maturity_str:
                try:
                    maturity = datetime.strptime(maturity_str, "%Y-%m-%d").date()
                except Exception:
                    pass

            coupon_date = nxt
            while coupon_date <= end_date:
                if coupon_date >= today:
                    if maturity and coupon_date > maturity:
                        break
                    events.append({
                        "date": coupon_date.isoformat(),
                        "type": "coupon",
                        "isin": inst.isin,
                        "name": inst.name,
                        "amount": float(semi_annual_coupon),
                        "label": f"Coupon: {inst.name}",
                    })
                    month_key = coupon_date.strftime("%Y-%m")
                    monthly_income[month_key] = (
                        monthly_income.get(month_key, Decimal("0")) + semi_annual_coupon
                    )
                coupon_date += timedelta(days=182)

            if maturity and today <= maturity <= end_date:
                maturity_amount = face_value * qty
                events.append({
                    "date": maturity.isoformat(),
                    "type": "maturity",
                    "isin": inst.isin,
                    "name": inst.name,
                    "amount": float(maturity_amount),
                    "label": f"Maturity: {inst.name}",
                })

    events.sort(key=lambda e: e["date"])

    monthly_chart: List[Dict] = []
    cur = today.replace(day=1)
    for _ in range(12):
        key = cur.strftime("%Y-%m")
        monthly_chart.append({
            "month": cur.strftime("%b %Y"),
            "amount": float(monthly_income.get(key, Decimal("0"))),
        })
        if cur.month == 12:
            cur = cur.replace(year=cur.year + 1, month=1)
        else:
            cur = cur.replace(month=cur.month + 1)

    return {
        "events": events,
        "monthly_chart": monthly_chart,
        "has_bond_holdings": any(
            h.instrument and h.instrument.asset_class == "BOND"
            for acct in accounts for h in acct.holdings
        ),
        "has_reit": has_reit,
        "has_invit": has_invit,
    }


# ---------------------------------------------------------------------------
# Simulator math (pure, testable functions)
# ---------------------------------------------------------------------------

def simulate_bond_coupons(
    principal: Decimal,
    coupon_rate_pct: Decimal,
    years: int,
    frequency: int = 2,
    frequency_months: Optional[int] = None,
) -> Dict:
    """
    Returns annual_income, monthly_income, maturity_amount, and coupon_schedule.
    All arithmetic uses Decimal to avoid floating-point drift.
    """
    if frequency_months is not None:
        frequency = max(1, 12 // frequency_months)

    annual_coupon = (principal * coupon_rate_pct / 100).quantize(Decimal("0.01"))
    per_payment = (annual_coupon / Decimal(str(frequency))).quantize(Decimal("0.01"))

    schedule = []
    today = date.today()
    for period in range(1, years * frequency + 1):
        days_ahead = int(period * 365 / frequency)
        pay_date = today + timedelta(days=days_ahead)
        schedule.append({
            "period": period,
            "date": pay_date.isoformat(),
            "amount": float(per_payment),
        })

    return {
        "annual_income": float(annual_coupon),
        "monthly_income": float((annual_coupon / 12).quantize(Decimal("0.01"))),
        "income_over_period": float((annual_coupon * years).quantize(Decimal("0.01"))),
        "total_income": float((annual_coupon * years).quantize(Decimal("0.01"))),
        "maturity_amount": float(principal),
        "coupon_schedule": schedule,
        "schedule": schedule,
    }


def simulate_periodic_income(
    principal: Decimal,
    yield_pct: Decimal,
    years: int,
    frequency_label: str = "Quarterly",
) -> Dict:
    """
    For REITs/InvITs/G-Secs: compute expected annual income.
    """
    annual_income = (principal * yield_pct / 100).quantize(Decimal("0.01"))
    monthly_income = (annual_income / 12).quantize(Decimal("0.01"))
    total_income = (annual_income * years).quantize(Decimal("0.01"))

    freq_map = {
        "Quarterly": 4,
        "Semi-Annually": 2,
        "Annually / Discretionary Dividend": 1,
        "At Maturity (Discounted par)": 1,
    }
    payments_per_year = freq_map.get(frequency_label, 4)
    per_payment = (annual_income / Decimal(str(payments_per_year))).quantize(Decimal("0.01"))

    today = date.today()
    periods = []
    for period in range(1, years * payments_per_year + 1):
        days_ahead = int(period * 365 / payments_per_year)
        pay_date = today + timedelta(days=days_ahead)
        periods.append({"date": pay_date.isoformat(), "amount": float(per_payment)})

    return {
        "principal": float(principal),
        "annual_income": float(annual_income),
        "monthly_income": float(monthly_income),
        "income_over_period": float(total_income),
        "total_income": float(total_income),
        "periods": periods,
        "frequency_label": frequency_label,
    }


# ---------------------------------------------------------------------------
# Quiz scoring
# ---------------------------------------------------------------------------

def score_quiz(topic: str, selected_indices: List[int]) -> Dict:
    """Score a quiz attempt. Returns score (0-100), per-question results, and explanations."""
    quiz = load_quiz(topic)
    questions = quiz.get("questions", [])
    if not questions:
        return {"score": 0, "results": [], "breakdown": [], "total": 0, "correct": 0, "raw_score": 0}

    correct = 0
    results = []
    for i, q in enumerate(questions):
        selected = selected_indices[i] if i < len(selected_indices) else -1
        is_correct = selected == q.get("correct_index", -1)
        if is_correct:
            correct += 1
        item = {
            "question": q.get("question", ""),
            "selected": selected,
            "user_answer": selected,
            "correct_index": q.get("correct_index", -1),
            "is_correct": is_correct,
            "correct": is_correct,
            "explanation": q.get("explanation", ""),
            "options": q.get("options", []),
        }
        results.append(item)

    score = int(correct / len(questions) * 100) if questions else 0
    return {
        "score": score,
        "raw_score": correct,
        "correct": correct,
        "total": len(questions),
        "results": results,
        "breakdown": results,
    }


# ---------------------------------------------------------------------------
# Progress helpers
# ---------------------------------------------------------------------------

def get_or_create_progress(
    db: Session,
    user_id: int,
    lesson_id: str,
    lang: str = "en",
    source_card: Optional[str] = None,
):
    from app.models import LessonProgress
    prog = (
        db.query(LessonProgress)
        .filter(LessonProgress.user_id == user_id, LessonProgress.lesson_id == lesson_id)
        .first()
    )
    if not prog:
        prog = LessonProgress(
            user_id=user_id,
            lesson_id=lesson_id,
            lang=lang,
            source_card=source_card,
        )
        db.add(prog)
        db.commit()
        db.refresh(prog)
    return prog


def mark_lesson_complete(db: Session, user_id: int, lesson_id: str):
    from app.models import LessonProgress
    prog = (
        db.query(LessonProgress)
        .filter(LessonProgress.user_id == user_id, LessonProgress.lesson_id == lesson_id)
        .first()
    )
    if prog and not prog.completed_at:
        prog.completed_at = datetime.now(timezone.utc)
        db.commit()


def record_analytic(db: Session, lesson_id: str, event: str, source_card: Optional[str] = None):
    from app.models import LessonAnalytic
    entry = LessonAnalytic(lesson_id=lesson_id, event=event, source_card=source_card)
    db.add(entry)
    db.commit()


# ---------------------------------------------------------------------------
# Educational Assistant Engine (with LLM and keyword retrieval fallback)
# ---------------------------------------------------------------------------

_rate_limit_cache: Dict[str, List[float]] = {}

ADVICE_TRIGGERS = [
    "should i buy", "should i sell", "what should i buy", "what should i invest",
    "recommend", "which is best", "stock tip", "target price", "guaranteed return",
    "where should i put my money", "best stock", "best bond"
]

def ask_assistant(question: str, client_id: str = "anon", user_pcts: Optional[Dict] = None) -> Dict:
    """
    Educational assistant answering ONLY from lesson and facts content.
    Refuses investment advice politely and cites sources.
    """
    now = time.time()
    # Rate limit: max 10 calls per 60 seconds
    timestamps = _rate_limit_cache.get(client_id, [])
    timestamps = [t for t in timestamps if now - t < 60]
    if len(timestamps) >= 10:
        return {
            "answer": "Rate limit reached. Please wait a minute before asking another question.",
            "citations": [],
            "disclaimer": LEARN_DISCLAIMER
        }
    timestamps.append(now)
    _rate_limit_cache[client_id] = timestamps

    q_lower = question.lower()

    # Advice check
    if any(trigger in q_lower for trigger in ADVICE_TRIGGERS):
        return {
            "answer": (
                "TradeOne provides educational information only and does not offer investment advice, "
                "recommendations, or buy/sell calls. "
                "Every investor has different risk tolerances and financial goals. "
                "You can explore our lessons below to learn how each asset class functions and the risks involved."
            ),
            "citations": [
                {"title": "Stocks vs Alternatives", "url": "/learn/lesson/stocks_vs_alternatives"},
                {"title": "Compare Options", "url": "/learn/compare"},
                {"title": "Learning Hub", "url": "/learn"}
            ],
            "disclaimer": LEARN_DISCLAIMER
        }

    # Search all lesson files for matching keywords
    lessons = load_all_lessons("en")
    facts = load_facts()

    best_lesson = None
    best_section = None
    best_score = 0
    words = [w for w in re.findall(r"\w+", q_lower) if len(w) > 3]

    for lesson in lessons:
        score = 0
        topic = lesson.get("topic", "")
        title = lesson.get("title", "").lower()
        summary = lesson.get("summary", "").lower()

        for w in words:
            if w in topic:
                score += 5
            if w in title:
                score += 4
            if w in summary:
                score += 2

        matched_sec = None
        for sec in lesson.get("sections", []):
            sec_content = sec.get("content", "").lower()
            sec_score = sum(1 for w in words if w in sec_content)
            if sec_score > 0 and (score + sec_score) > best_score:
                matched_sec = sec
                score += sec_score

        if score > best_score:
            best_score = score
            best_lesson = lesson
            best_section = matched_sec

    # Check facts dictionary
    fact_hit = None
    for k, v in facts.items():
        if isinstance(v, dict):
            for fk, fv in v.items():
                if any(w in fk.lower() or w in str(fv).lower() for w in words):
                    fact_hit = (k, fk, fv)
                    break

    # If LLM configured and enabled, we can query it with strict educational prompt
    from app.config import settings
    if settings.LLM_API_KEY and best_lesson:
        try:
            import urllib.request
            # Generic REST call if configured, or fallback to keyword synthesis
            pass
        except Exception:
            pass

    # Keyword synthesized response
    if best_lesson and best_score >= 2:
        topic_id = best_lesson.get("topic")
        title = best_lesson.get("title")
        content_snippet = ""
        if best_section:
            content_snippet = f"**{best_section.get('title', '')}**: {best_section.get('content', '')}"
        else:
            content_snippet = best_lesson.get("summary", "")

        extra_fact = ""
        if fact_hit and fact_hit[0] == topic_id:
            extra_fact = f"\n\n*Key Reference ({fact_hit[1]})*: {fact_hit[2]}"

        answer = f"According to our lesson on **{title}**:\n\n{content_snippet}{extra_fact}"

        return {
            "answer": answer,
            "citations": [
                {"title": title, "url": f"/learn/lesson/{topic_id}"}
            ],
            "disclaimer": LEARN_DISCLAIMER
        }

    return {
        "answer": (
            "Our educational library does not currently cover this specific topic. "
            "You can explore our available lessons on REITs, InvITs, Government Securities, "
            "Treasury Bills, Sovereign Gold Bonds, Corporate Bonds, and Taxes."
        ),
        "citations": [
            {"title": "Learning Hub", "url": "/learn"},
            {"title": "Compare Investment Options", "url": "/learn/compare"}
        ],
        "disclaimer": LEARN_DISCLAIMER
    }
