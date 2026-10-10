"""Snapshot job and history tracking for user portfolios.

Rules:
- Store user's total value, invested value, and per-asset-class values after every successful sync.
- At most once per hour per user.
- Always keep the last of each day (1 row per user per day).
- Never backfill or invent history.
"""

import json
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any
from sqlalchemy.orm import Session
from app.models import User, HoldingSnapshot
from app.services.depository_service import get_portfolio_summary


def record_holding_snapshot(db: Session, user: User, force: bool = False) -> Optional[HoldingSnapshot]:
    """Store the user's total value, invested value and per-asset-class values.
    Throttled to at most once per hour per user, keeping the latest of each calendar day.
    Never backfills or fabricates history.
    """
    if not user or not user.id:
        return None

    now = datetime.now(timezone.utc)
    summary = get_portfolio_summary(db, user)
    total_val = float(summary["total_value"])
    invested_val = float(summary["total_invested"])
    asset_alloc = {str(k): float(v) for k, v in summary.get("asset_allocation", {}).items()}

    # Check for existing snapshot recorded on today's calendar date (UTC)
    start_of_day = datetime(now.year, now.month, now.day, 0, 0, 0)
    end_of_day = datetime(now.year, now.month, now.day, 23, 59, 59, 999999)

    existing_today = (
        db.query(HoldingSnapshot)
        .filter(
            HoldingSnapshot.user_id == user.id,
            HoldingSnapshot.snapshot_date >= start_of_day,
            HoldingSnapshot.snapshot_date <= end_of_day
        )
        .first()
    )

    if existing_today:
        dt = existing_today.created_at
        if dt:
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            seconds_since = (now - dt).total_seconds()
            if not force and seconds_since < 3600:
                # Less than 1 hour since creation, throttled
                return existing_today

        # Update today's record to preserve the last snapshot of the day (1 row per user per day)
        existing_today.total_value = total_val
        existing_today.invested_value = invested_val
        existing_today.asset_class_values_json = json.dumps(asset_alloc)
        existing_today.snapshot_date = now.replace(tzinfo=None)
        existing_today.created_at = now.replace(tzinfo=None)
        db.commit()
        db.refresh(existing_today)
        return existing_today

    # If no snapshot for today, check throttle against the most recent snapshot overall
    latest = (
        db.query(HoldingSnapshot)
        .filter(HoldingSnapshot.user_id == user.id)
        .order_by(HoldingSnapshot.snapshot_date.desc())
        .first()
    )
    if latest and not force:
        dt = latest.created_at
        if dt:
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if (now - dt).total_seconds() < 3600:
                return latest

    snapshot = HoldingSnapshot(
        user_id=user.id,
        snapshot_date=now.replace(tzinfo=None),
        total_value=total_val,
        invested_value=invested_val,
        asset_class_values_json=json.dumps(asset_alloc),
        created_at=now.replace(tzinfo=None)
    )
    db.add(snapshot)
    db.commit()
    db.refresh(snapshot)
    return snapshot


def get_holding_snapshots(db: Session, user: User, range_filter: str = "ALL") -> List[HoldingSnapshot]:
    """Retrieve actual historical snapshots for the specified range.
    Never invents history points.
    """
    if not user or not user.id:
        return []

    now = datetime.now(timezone.utc)
    query = db.query(HoldingSnapshot).filter(HoldingSnapshot.user_id == user.id)

    range_clean = (range_filter or "ALL").upper().strip()
    if range_clean == "1W":
        cutoff = (now - timedelta(days=7)).replace(tzinfo=None)
        query = query.filter(HoldingSnapshot.snapshot_date >= cutoff)
    elif range_clean == "1M":
        cutoff = (now - timedelta(days=30)).replace(tzinfo=None)
        query = query.filter(HoldingSnapshot.snapshot_date >= cutoff)
    elif range_clean == "3M":
        cutoff = (now - timedelta(days=90)).replace(tzinfo=None)
        query = query.filter(HoldingSnapshot.snapshot_date >= cutoff)

    return query.order_by(HoldingSnapshot.snapshot_date.asc()).all()
