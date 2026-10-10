"""
learn.py  —  Router for the TradeOne Learn module
Routes:
  GET  /learn                    - Learning hub landing page
  GET  /learn/lesson/{topic}     - Individual lesson page
  POST /learn/lesson/{topic}/complete - Mark lesson complete
  GET  /learn/compare            - Side-by-side compare tool
  GET  /learn/simulator          - Income simulator
  POST /learn/simulator/run      - Run simulation (JSON API)
  GET  /learn/what-if            - What-if allocation tool
  POST /learn/what-if/run        - Run what-if (JSON API)
  GET  /learn/glossary           - Full glossary
  GET  /learn/income-calendar    - Income calendar
  GET  /learn/quiz/{topic}       - Quiz page
  POST /learn/quiz/{topic}/submit - Submit quiz answers
  POST /learn/analytic           - Record anonymous analytic event
  POST /learn/rating/{topic}     - Submit thumbs rating
  GET  /learn/admin              - Admin analytics dashboard
  GET  /api/learn/divcheck       - JSON diversification data for dashboard card
  GET  /api/learn/explain/{isin} - JSON explain-my-holding data
"""
import json
from decimal import Decimal
from datetime import datetime, timezone
from typing import Optional, List

from fastapi import APIRouter, Depends, Request, Form, Query, HTTPException, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from sqlalchemy.orm import Session

from app.database import get_db
from app.models import LessonProgress, QuizAttempt, LessonAnalytic, DematAccount
from app.services.auth_service import get_current_user_from_session
from app.services.depository_service import format_inr
from app.config import settings
from app.services.learn_service import (
    load_lesson, load_all_lessons, load_glossary, load_facts,
    load_sample_assumptions, load_quiz, get_needs_verification_items,
    COMPARE_DATA, compute_diversification, compute_income_calendar,
    simulate_bond_coupons, simulate_periodic_income, score_quiz,
    get_or_create_progress, mark_lesson_complete, record_analytic,
    ask_assistant, LEARN_DISCLAIMER,
)

router = APIRouter(prefix="/learn", tags=["Learn"])
templates = Jinja2Templates(directory="app/templates")

# Mapping from topic to display label for the nav
LESSON_LABELS = {
    "reit": "REITs",
    "invit": "InvITs",
    "gsec": "Government Securities",
    "tbill": "Treasury Bills",
    "sgb": "Sovereign Gold Bonds",
    "corpbond": "Corporate Bonds",
    "stocks_vs_alternatives": "Stocks vs Alternatives",
    "taxes": "Taxes in Simple Words",
    "how_to_buy": "How to Buy",
}

LESSON_LABELS_HI = {
    "reit": "रीट्स (REITs)",
    "invit": "इनविट्स (InvITs)",
    "gsec": "सरकारी प्रतिभूतियां (G-Secs)",
    "tbill": "ट्रेजरी बिल्स (T-Bills)",
    "sgb": "सॉवरेन गोल्ड बॉन्ड्स (SGB)",
    "corpbond": "कॉरपोरेट बॉन्ड्स (Corporate Bonds)",
    "stocks_vs_alternatives": "स्टॉक्स बनाम अन्य विकल्प",
    "taxes": "सरल शब्दों में टैक्स",
    "how_to_buy": "कैसे खरीदें",
}


def get_user_optional(request: Request, db: Session = Depends(get_db)):
    token = request.cookies.get("nd_session")
    return get_current_user_from_session(db, token)


def require_user(request: Request, db: Session = Depends(get_db)):
    token = request.cookies.get("nd_session")
    user = get_current_user_from_session(db, token)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_307_TEMPORARY_REDIRECT,
            headers={"Location": f"/auth/login?next={request.url.path}"},
        )
    return user


# ---------------------------------------------------------------------------
# Learning Hub Landing Page
# ---------------------------------------------------------------------------

@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def learn_hub(
    request: Request,
    lang: str = Query("en"),
    user=Depends(get_user_optional),
    db: Session = Depends(get_db),
):
    lessons = load_all_lessons(lang)
    facts = load_facts()
    assumptions = load_sample_assumptions()

    # Build progress map for logged-in users
    progress_map = {}
    if user:
        progs = db.query(LessonProgress).filter(LessonProgress.user_id == user.id).all()
        for p in progs:
            progress_map[p.lesson_id] = {
                "started": True,
                "completed": p.completed_at is not None,
                "best_score": p.best_quiz_score,
            }

    # Diversification card data
    div_data = compute_diversification(db, user) if user else None

    return templates.TemplateResponse(
        request=request,
        name="learn/hub.html",
        context={
            "user": user,
            "lessons": lessons,
            "lesson_labels": LESSON_LABELS_HI if lang == "hi" else LESSON_LABELS,
            "progress_map": progress_map,
            "div_data": div_data,
            "facts": facts,
            "assumptions": assumptions,
            "lang": lang,
            "format_inr": format_inr,
            "disclaimer": LEARN_DISCLAIMER,
            "enable_assistant": settings.ENABLE_ASSISTANT,
        },
    )


# ---------------------------------------------------------------------------
# Individual Lesson Page
# ---------------------------------------------------------------------------

@router.get("/lesson/{topic}", response_class=HTMLResponse)
def lesson_page(
    topic: str,
    request: Request,
    lang: str = Query("en"),
    source: Optional[str] = Query(None),
    user=Depends(get_user_optional),
    db: Session = Depends(get_db),
):
    lesson = load_lesson(topic, lang)
    if not lesson:
        raise HTTPException(status_code=404, detail=f"Lesson '{topic}' not found")

    facts = load_facts()
    quiz = load_quiz(topic)

    progress = None
    pre_quiz_attempts = []
    post_quiz_attempts = []

    if user:
        progress = get_or_create_progress(db, user.id, topic, lang, source_card=source)
        # Record page view
        record_analytic(db, topic, "view", source_card=source)

        pre_quiz_attempts = (
            db.query(QuizAttempt)
            .filter(
                QuizAttempt.user_id == user.id,
                QuizAttempt.lesson_id == topic,
                QuizAttempt.attempt_type == "pre",
            )
            .order_by(QuizAttempt.attempted_at.desc())
            .all()
        )
        post_quiz_attempts = (
            db.query(QuizAttempt)
            .filter(
                QuizAttempt.user_id == user.id,
                QuizAttempt.lesson_id == topic,
                QuizAttempt.attempt_type == "post",
            )
            .order_by(QuizAttempt.attempted_at.desc())
            .all()
        )

    # Per-holding personalisation: find relevant holdings of this asset class
    asset_class_map = {
        "reit": "REIT", "invit": "INVIT", "gsec": "BOND",
        "tbill": "BOND", "sgb": "BOND", "corpbond": "BOND",
    }
    relevant_holdings = []
    if user and topic in asset_class_map:
        target_class = asset_class_map[topic]
        accounts = db.query(DematAccount).filter(DematAccount.user_id == user.id).all()
        for acct in accounts:
            for h in acct.holdings:
                inst = h.instrument
                if not inst or inst.asset_class != target_class:
                    continue
                meta = {}
                if h.metadata_json:
                    try:
                        meta = json.loads(h.metadata_json)
                    except Exception:
                        pass
                relevant_holdings.append({
                    "name": inst.name,
                    "isin": inst.isin,
                    "quantity": h.total_units,
                    "value": h.total_units * (inst.last_price or 0),
                    "asset_class": inst.asset_class,
                    "coupon": meta.get("coupon"),
                    "ytm": meta.get("ytm"),
                    "maturity_date": meta.get("maturity_date"),
                    "next_coupon_date": meta.get("next_coupon_date"),
                })

    # Resolve fact references in lesson
    topic_facts = facts.get(topic, {})

    return templates.TemplateResponse(
        request=request,
        name="learn/lesson.html",
        context={
            "user": user,
            "lesson": lesson,
            "topic": topic,
            "lang": lang,
            "facts": topic_facts,
            "quiz": quiz,
            "progress": progress,
            "pre_quiz_attempts": pre_quiz_attempts,
            "post_quiz_attempts": post_quiz_attempts,
            "relevant_holdings": relevant_holdings,
            "format_inr": format_inr,
            "disclaimer": LEARN_DISCLAIMER,
            "lesson_labels": LESSON_LABELS_HI if lang == "hi" else LESSON_LABELS,
        },
    )


# ---------------------------------------------------------------------------
# Mark Lesson Complete
# ---------------------------------------------------------------------------

@router.post("/lesson/{topic}/complete")
def mark_complete(
    topic: str,
    request: Request,
    user=Depends(require_user),
    db: Session = Depends(get_db),
):
    mark_lesson_complete(db, user.id, topic)
    record_analytic(db, topic, "complete")
    return JSONResponse({"status": "ok"})


# ---------------------------------------------------------------------------
# Quiz
# ---------------------------------------------------------------------------

@router.get("/quiz/{topic}", response_class=HTMLResponse)
def quiz_page(
    topic: str,
    request: Request,
    mode: str = Query("post"),
    lang: str = Query("en"),
    user=Depends(require_user),
    db: Session = Depends(get_db),
):
    quiz = load_quiz(topic)
    if not quiz or not quiz.get("questions"):
        raise HTTPException(status_code=404, detail="Quiz not found")

    current_labels = LESSON_LABELS_HI if lang == "hi" else LESSON_LABELS
    return templates.TemplateResponse(
        request=request,
        name="learn/quiz.html",
        context={
            "user": user,
            "quiz": quiz,
            "topic": topic,
            "mode": mode,
            "lang": lang,
            "lesson_label": current_labels.get(topic, topic.title()),
            "disclaimer": LEARN_DISCLAIMER,
        },
    )


@router.post("/quiz/{topic}/submit")
async def submit_quiz(
    topic: str,
    request: Request,
    mode: str = Query("post"),
    user=Depends(require_user),
    db: Session = Depends(get_db),
):
    body = await request.json()
    selected = body.get("answers", [])

    result = score_quiz(topic, selected)
    score = result["score"]

    # Record attempt
    attempt = QuizAttempt(
        user_id=user.id,
        lesson_id=topic,
        attempt_type=mode,
        score=score,
        answers_json=json.dumps(selected),
    )
    db.add(attempt)

    # Update best score in progress
    prog = get_or_create_progress(db, user.id, topic)
    if prog.best_quiz_score is None or score > prog.best_quiz_score:
        prog.best_quiz_score = score
    prog.last_score = score
    db.commit()

    return JSONResponse(result)


# ---------------------------------------------------------------------------
# Compare Tool
# ---------------------------------------------------------------------------

@router.get("/compare", response_class=HTMLResponse)
def compare_tool(
    request: Request,
    lang: str = Query("en"),
    assets: Optional[List[str]] = Query(None),
    user=Depends(get_user_optional),
    db: Session = Depends(get_db),
):
    default_keys = ["reit", "invit", "gsec", "corpbond"]
    raw_list = request.query_params.getlist("assets")
    if raw_list:
        custom_keys = []
        for item in raw_list:
            for k in item.split(","):
                k = k.strip()
                if k in COMPARE_DATA and k != "equity" and k not in custom_keys:
                    custom_keys.append(k)
        selected_keys = custom_keys if custom_keys else default_keys
    else:
        selected_keys = default_keys

    compare_rows = {k: COMPARE_DATA[k] for k in selected_keys if k in COMPARE_DATA}
    facts = load_facts()

    return templates.TemplateResponse(
        request=request,
        name="learn/compare.html",
        context={
            "user": user,
            "compare_rows": compare_rows,
            "all_assets": {k: v for k, v in COMPARE_DATA.items() if k != "equity"},
            "selected_keys": selected_keys,
            "facts": facts,
            "lang": lang,
            "disclaimer": LEARN_DISCLAIMER,
        },
    )


# ---------------------------------------------------------------------------
# Income Simulator
# ---------------------------------------------------------------------------

@router.get("/simulator", response_class=HTMLResponse)
def simulator_page(
    request: Request,
    lang: str = Query("en"),
    user=Depends(get_user_optional),
    db: Session = Depends(get_db),
):
    assumptions = load_sample_assumptions()
    return templates.TemplateResponse(
        request=request,
        name="learn/simulator.html",
        context={
            "user": user,
            "assumptions": assumptions,
            "lang": lang,
            "disclaimer": LEARN_DISCLAIMER,
            "format_inr": format_inr,
        },
    )


@router.post("/simulator/run")
async def simulator_run(request: Request, db: Session = Depends(get_db)):
    body = await request.json()
    product = body.get("product", "reit")
    amount_str = str(body.get("amount", "100000")).replace(",", "")
    yield_str = str(body.get("yield_pct", "7.0"))
    years = max(1, min(int(body.get("years", 5)), 30))

    try:
        principal = Decimal(amount_str)
        yield_pct = Decimal(yield_str)
    except Exception:
        return JSONResponse({"error": "Invalid amount or yield"}, status_code=400)

    assumptions = load_sample_assumptions()
    asset_data = assumptions.get("asset_classes", {}).get(product, {})
    frequency_label = asset_data.get("income_frequency", "Quarterly")

    if product == "corpbond" or product == "gsec":
        result = simulate_bond_coupons(principal, yield_pct, years)
    else:
        result = simulate_periodic_income(principal, yield_pct, years, frequency_label)

    result["disclaimer"] = LEARN_DISCLAIMER
    result["notice"] = assumptions.get("notice", "Sample assumptions only.")
    result["product"] = product
    result["frequency_label"] = frequency_label
    return JSONResponse(result)


# ---------------------------------------------------------------------------
# What-If Allocation Tool
# ---------------------------------------------------------------------------

@router.get("/what-if", response_class=HTMLResponse)
def whatif_page(
    request: Request,
    lang: str = Query("en"),
    user=Depends(get_user_optional),
    db: Session = Depends(get_db),
):
    assumptions = load_sample_assumptions()
    div_data = compute_diversification(db, user) if user else None

    return templates.TemplateResponse(
        request=request,
        name="learn/whatif.html",
        context={
            "user": user,
            "assumptions": assumptions,
            "div_data": div_data,
            "lang": lang,
            "disclaimer": LEARN_DISCLAIMER,
            "format_inr": format_inr,
        },
    )


@router.post("/what-if/run")
async def whatif_run(request: Request, db: Session = Depends(get_db)):
    body = await request.json()
    total_str = str(body.get("total", "1000000")).replace(",", "")
    allocation = body.get("allocation", {})  # {"equity": 100, "reit": 0, ...}

    try:
        total = Decimal(total_str)
    except Exception:
        return JSONResponse({"error": "Invalid total"}, status_code=400)

    assumptions = load_sample_assumptions()
    asset_classes = assumptions.get("asset_classes", {})

    before = body.get("before", {})  # same structure as allocation
    results = {"before": {}, "after": {}}

    def calc_income(alloc_pct_map):
        annual = Decimal("0")
        income_share = Decimal("0")
        details = {}
        for ac, pct in alloc_pct_map.items():
            ac_data = asset_classes.get(ac, {})
            yield_pct = Decimal(str(ac_data.get("sample_yield_pct", 0)))
            portion = total * Decimal(str(pct)) / 100
            income = (portion * yield_pct / 100).quantize(Decimal("0.01"))
            growth_pct = Decimal(str(ac_data.get("sample_growth_pct", 0)))
            vol_pct = Decimal(str(ac_data.get("sample_annual_volatility_pct", 10)))
            annual += income
            details[ac] = {
                "pct": float(pct),
                "amount": float(portion),
                "yield_pct": float(yield_pct),
                "income": float(income),
                "vol_pct": float(vol_pct),
                "growth_pct": float(growth_pct),
            }
        return float(annual), details

    before_income, before_details = calc_income(before if before else allocation)
    after_income, after_details = calc_income(allocation)

    return JSONResponse({
        "total": float(total),
        "before_annual_income": before_income,
        "after_annual_income": after_income,
        "before_monthly_income": round(before_income / 12, 2),
        "after_monthly_income": round(after_income / 12, 2),
        "before_details": before_details,
        "after_details": after_details,
        "disclaimer": LEARN_DISCLAIMER,
        "notice": assumptions.get("notice", "Sample assumptions only."),
    })


# ---------------------------------------------------------------------------
# Glossary
# ---------------------------------------------------------------------------

@router.get("/glossary", response_class=HTMLResponse)
def glossary_page(
    request: Request,
    lang: str = Query("en"),
    user=Depends(get_user_optional),
    db: Session = Depends(get_db),
):
    glossary = load_glossary(lang)
    return templates.TemplateResponse(
        request=request,
        name="learn/glossary.html",
        context={
            "user": user,
            "glossary": glossary,
            "lang": lang,
            "disclaimer": LEARN_DISCLAIMER,
        },
    )


# ---------------------------------------------------------------------------
# Income Calendar
# ---------------------------------------------------------------------------

@router.get("/income-calendar", response_class=HTMLResponse)
def income_calendar_page(
    request: Request,
    lang: str = Query("en"),
    user=Depends(require_user),
    db: Session = Depends(get_db),
):
    calendar_data = compute_income_calendar(db, user)
    return templates.TemplateResponse(
        request=request,
        name="learn/income_calendar.html",
        context={
            "user": user,
            "calendar": calendar_data,
            "format_inr": format_inr,
            "lang": lang,
            "disclaimer": LEARN_DISCLAIMER,
        },
    )


# ---------------------------------------------------------------------------
# Admin Analytics
# ---------------------------------------------------------------------------

@router.get("/admin", response_class=HTMLResponse)
def learn_admin(
    request: Request,
    user=Depends(require_user),
    db: Session = Depends(get_db),
):
    # Aggregate analytics
    from sqlalchemy import func

    event_counts = (
        db.query(LessonAnalytic.lesson_id, LessonAnalytic.event, func.count().label("cnt"))
        .group_by(LessonAnalytic.lesson_id, LessonAnalytic.event)
        .all()
    )
    analytics: dict = {}
    for row in event_counts:
        if row.lesson_id not in analytics:
            analytics[row.lesson_id] = {}
        analytics[row.lesson_id][row.event] = row.cnt

    # Avg quiz score improvement per topic
    quiz_stats = []
    for topic in LESSON_LABELS:
        pre_avg = (
            db.query(func.avg(QuizAttempt.score))
            .filter(QuizAttempt.lesson_id == topic, QuizAttempt.attempt_type == "pre")
            .scalar()
        )
        post_avg = (
            db.query(func.avg(QuizAttempt.score))
            .filter(QuizAttempt.lesson_id == topic, QuizAttempt.attempt_type == "post")
            .scalar()
        )
        quiz_stats.append({
            "topic": topic,
            "label": LESSON_LABELS.get(topic, topic),
            "pre_avg": round(pre_avg or 0, 1),
            "post_avg": round(post_avg or 0, 1),
            "improvement": round((post_avg or 0) - (pre_avg or 0), 1),
        })

    # Completion counts
    completion_counts = (
        db.query(LessonProgress.lesson_id, func.count().label("started"),
                 func.sum(
                     func.case((LessonProgress.completed_at != None, 1), else_=0)
                 ).label("completed"))
        .group_by(LessonProgress.lesson_id)
        .all()
    )

    return templates.TemplateResponse(
        request=request,
        name="learn/admin.html",
        context={
            "user": user,
            "analytics": analytics,
            "quiz_stats": quiz_stats,
            "completion_counts": {
                r.lesson_id: {"started": r.started, "completed": int(r.completed or 0)}
                for r in completion_counts
            },
            "lesson_labels": LESSON_LABELS,
            "disclaimer": LEARN_DISCLAIMER,
        },
    )


# ---------------------------------------------------------------------------
# Anonymous Analytic Event
# ---------------------------------------------------------------------------

@router.post("/analytic")
async def record_analytic_event(
    request: Request,
    db: Session = Depends(get_db),
):
    body = await request.json()
    lesson_id = body.get("lesson_id", "")
    event = body.get("event", "view")
    source_card = body.get("source_card")
    if lesson_id:
        record_analytic(db, lesson_id, event, source_card)
    return JSONResponse({"status": "ok"})


@router.post("/rating/{topic}")
async def submit_rating(
    topic: str,
    request: Request,
    db: Session = Depends(get_db),
):
    body = await request.json()
    thumbs = body.get("thumbs", "up")
    event = "thumbs_up" if thumbs == "up" else "thumbs_down"
    record_analytic(db, topic, event)
    return JSONResponse({"status": "ok"})


# ---------------------------------------------------------------------------
# JSON API: Diversification data for dashboard card
# ---------------------------------------------------------------------------

@router.get("/api/divcheck")
def api_divcheck(
    request: Request,
    user=Depends(require_user),
    db: Session = Depends(get_db),
):
    data = compute_diversification(db, user)
    if data is None:
        return JSONResponse({"empty": True, "disclaimer": LEARN_DISCLAIMER})
    return JSONResponse(data)


# ---------------------------------------------------------------------------
# JSON API: Explain-my-holding
# ---------------------------------------------------------------------------

@router.get("/api/explain/{isin}")
def explain_holding(
    isin: str,
    request: Request,
    user=Depends(require_user),
    db: Session = Depends(get_db),
):
    """Return plain-language explanation for a REIT/InvIT/Bond holding."""
    accounts = db.query(DematAccount).filter(DematAccount.user_id == user.id).all()
    holding = None
    for acct in accounts:
        for h in acct.holdings:
            if h.isin == isin:
                holding = h
                break

    if not holding:
        raise HTTPException(status_code=404, detail="Holding not found")

    inst = holding.instrument
    if not inst:
        raise HTTPException(status_code=404, detail="Instrument not found")

    asset_class = inst.asset_class
    meta = {}
    if holding.metadata_json:
        try:
            meta = json.loads(holding.metadata_json)
        except Exception:
            pass

    topic_map = {
        "REIT": "reit", "INVIT": "invit",
        "BOND": "corpbond",  # Default; refine by name if needed
    }
    # Better topic detection for bonds
    if asset_class == "BOND":
        name_upper = inst.name.upper()
        if "GOVT" in name_upper or "SOVEREIGN" in name_upper or "RBI" in name_upper:
            topic = "gsec"
        elif "TBILL" in name_upper or "T-BILL" in name_upper or "91D" in name_upper or "182D" in name_upper:
            topic = "tbill"
        elif "SGB" in name_upper or "GOLD BOND" in name_upper:
            topic = "sgb"
        else:
            topic = "corpbond"
    else:
        topic = topic_map.get(asset_class, "")

    lesson = load_lesson(topic) if topic else {}
    what_is_it = ""
    if lesson:
        for section in lesson.get("sections", []):
            if section.get("id") == "what_is_it":
                what_is_it = section.get("content", "")
                break

    explanation_map = {
        "REIT": (
            "This is a Real Estate Investment Trust (REIT). You own fractional units "
            "in a portfolio of commercial properties. The trust collects rental income "
            "and is required by SEBI to distribute at least 90% of its Net Distributable "
            "Cash Flows to unitholders, typically quarterly."
        ),
        "INVIT": (
            "This is an Infrastructure Investment Trust (InvIT). You own fractional units "
            "in a portfolio of infrastructure assets such as highways, power lines, or pipelines. "
            "The trust distributes revenue from tolls and tariffs to unitholders."
        ),
        "BOND": (
            "This is a bond — a debt instrument where you have effectively lent money to the issuer. "
            "The issuer pays you a fixed coupon (interest) at regular intervals and returns your "
            "principal at maturity. Your position carries credit risk depending on the issuer."
        ),
    }

    return JSONResponse({
        "isin": isin,
        "name": inst.name,
        "asset_class": asset_class,
        "quantity": holding.total_units,
        "value": holding.total_units * (inst.last_price or 0),
        "last_price": inst.last_price,
        "coupon": meta.get("coupon"),
        "ytm": meta.get("ytm"),
        "maturity_date": meta.get("maturity_date"),
        "next_coupon_date": meta.get("next_coupon_date"),
        "explanation": explanation_map.get(asset_class, ""),
        "what_is_it_full": what_is_it,
        "lesson_url": f"/learn/lesson/{topic}" if topic else None,
        "risk_note": (
            "Past distributions and prices are not a guide to future performance. "
            "Values can go up or down. Please read the relevant lesson and verify with official sources."
        ),
        "disclaimer": LEARN_DISCLAIMER,
    })


api_router = APIRouter(prefix="/api/learn", tags=["Learn API"])
api_router.add_api_route("/divcheck", api_divcheck, methods=["GET"])
api_router.add_api_route("/explain/{isin}", explain_holding, methods=["GET"])


@router.post("/assistant/ask")
async def assistant_endpoint(
    request: Request,
    user=Depends(get_user_optional),
    db: Session = Depends(get_db),
):
    body = await request.json()
    question = body.get("question", "").strip()
    if not question:
        return JSONResponse({"error": "Empty question"}, status_code=400)

    user_pcts = None
    if user:
        div = compute_diversification(db, user)
        if div:
            user_pcts = {
                "equity_pct": div["equity_pct"],
                "reit_pct": div["reit_pct"],
                "invit_pct": div["invit_pct"],
                "bond_pct": div["bond_pct"],
            }

    client_id = f"user_{user.id}" if user else (request.client.host if request.client else "anon")
    result = ask_assistant(question, client_id=client_id, user_pcts=user_pcts)
    return JSONResponse(result)
