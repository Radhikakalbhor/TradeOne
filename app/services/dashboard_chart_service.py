"""Dashboard chart computation service.

Computes unified portfolio chart data strictly from active user holdings.
Ensures:
- Decimal arithmetic on server, plain numbers (floats) sent to client.
- Every chart is computed from the same query result so slice totals match exactly.
- Consistent color palette across asset classes and brokers.
- Never invents history or demo data.
"""

from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime, timezone, timedelta, date
from typing import Dict, Any, List, Optional
from sqlalchemy.orm import Session, joinedload

from app.config import settings
from app.models import User, DematAccount, Holding, Instrument, HoldingSnapshot
from app.services.depository_service import parse_decimal, format_inr
from app.services.snapshot_service import get_holding_snapshots


# Consistent color palette
ASSET_CLASS_COLORS = {
    "Equity": "#3b82f6",
    "EQUITY": "#3b82f6",
    "ETF": "#06b6d4",
    "REIT": "#8b5cf6",
    "InvIT": "#ec4899",
    "INVIT": "#ec4899",
    "Bond": "#10b981",
    "BOND": "#10b981",
    "MF": "#f59e0b",
    "Mutual Fund": "#f59e0b",
    "Other": "#64748b",
    "OTHER": "#64748b"
}

BROKER_COLORS = {
    "a": "#f59e0b",
    "b": "#2563eb",
    "c": "#059669",
    "IN300001": "#f59e0b",
    "IN300002": "#2563eb",
    "IN300003": "#059669",
    "NiftyTrade": "#f59e0b",
    "BharatInvest": "#2563eb",
    "BondBazaar": "#059669",
    "Other": "#8b5cf6"
}

ASSET_CLASS_NORM = {
    "EQUITY": "Equity",
    "ETF": "ETF",
    "REIT": "REIT",
    "INVIT": "InvIT",
    "BOND": "Bond",
    "MF": "MF",
    "MUTUAL_FUND": "MF",
}


def normalize_asset_class(raw_class: Optional[str]) -> str:
    if not raw_class:
        return "Other"
    upper = str(raw_class).upper().strip()
    return ASSET_CLASS_NORM.get(upper, upper.title())


def get_broker_display_name(dp_id: str, provider_code: Optional[str], default_name: str) -> str:
    for code, meta in settings.BROKER_PROVIDERS.items():
        if meta.get("dp_id") == dp_id or code == provider_code:
            return meta.get("broker_name", default_name)
    return default_name or "Unknown Broker"


def get_dashboard_charts_data(db: Session, user: User) -> Dict[str, Any]:
    """Compute all chart and metric payloads for the logged-in user."""
    accounts = db.query(DematAccount).filter(DematAccount.user_id == user.id).all()
    account_ids = [acc.id for acc in accounts]

    now = datetime.now(timezone.utc)

    # 1. Broker sync status computation
    broker_statuses = {}
    stale_providers = []
    connected_count = 0
    total_configured_brokers = len(settings.BROKER_PROVIDERS)
    latest_sync_dt: Optional[datetime] = None

    for p_code, p_meta in settings.BROKER_PROVIDERS.items():
        acc = next(
            (a for a in accounts if a.dp_id == p_meta["dp_id"] or getattr(a, "provider_code", None) == p_code),
            None
        )
        sync_status = getattr(acc, "sync_status", "unavailable") if acc else "unavailable"
        last_sync_dt = getattr(acc, "last_synced_at", None) if acc else None

        if last_sync_dt:
            tz_dt = last_sync_dt if last_sync_dt.tzinfo else last_sync_dt.replace(tzinfo=timezone.utc)
            if not latest_sync_dt or tz_dt > latest_sync_dt:
                latest_sync_dt = tz_dt

        if sync_status == "connected":
            connected_count += 1
        elif sync_status in ("stale", "unavailable", "error"):
            stale_providers.append(p_meta["broker_name"])

        time_human = "Never"
        if last_sync_dt:
            tz_dt = last_sync_dt if last_sync_dt.tzinfo else last_sync_dt.replace(tzinfo=timezone.utc)
            diff_sec = (now - tz_dt).total_seconds()
            if diff_sec < 60:
                time_human = "Just now"
            elif diff_sec < 3600:
                time_human = f"{int(diff_sec // 60)}m ago"
            elif diff_sec < 86400:
                time_human = f"{int(diff_sec // 3600)}h ago"
            else:
                time_human = tz_dt.strftime("%d %b %H:%M")

        broker_statuses[p_code] = {
            "code": p_code,
            "broker_name": p_meta["broker_name"],
            "dp_id": p_meta["dp_id"],
            "status": sync_status,
            "last_synced_human": time_human
        }

    last_synced_text = "Never"
    if latest_sync_dt:
        diff_sec = (now - latest_sync_dt).total_seconds()
        if diff_sec < 60:
            last_synced_text = "Just now"
        elif diff_sec < 3600:
            last_synced_text = f"{int(diff_sec // 60)}m ago"
        elif diff_sec < 86400:
            last_synced_text = f"{int(diff_sec // 3600)}h ago"
        else:
            last_synced_text = latest_sync_dt.strftime("%d %b %H:%M")

    # 2. Fetch all user holdings in a single query
    holdings: List[Holding] = []
    if account_ids:
        holdings = (
            db.query(Holding)
            .options(joinedload(Holding.instrument), joinedload(Holding.demat_account))
            .filter(Holding.demat_account_id.in_(account_ids))
            .all()
        )

    # Decimal aggregators to ensure exact precision
    total_val_dec = Decimal("0")
    total_inv_dec = Decimal("0")
    day_chg_dec = Decimal("0")

    asset_totals_dec: Dict[str, Decimal] = {}
    broker_totals_dec: Dict[str, Decimal] = {}
    broker_x_asset_dec: Dict[str, Dict[str, Decimal]] = {}
    merged_holdings_map: Dict[str, Dict[str, Any]] = {}

    has_bond_holdings = False

    for h in holdings:
        instr = h.instrument
        if not instr:
            continue

        qty = parse_decimal(h.total_units)
        if qty <= Decimal("0"):
            continue

        last_price = parse_decimal(instr.last_price)
        avg_price = parse_decimal(h.avg_price)
        prev_close = parse_decimal(instr.prev_close or instr.last_price)

        cur_val = qty * last_price
        inv_val = qty * avg_price
        day_chg = qty * (last_price - prev_close)

        total_val_dec += cur_val
        total_inv_dec += inv_val
        day_chg_dec += day_chg

        # Normalized asset class
        norm_ac = normalize_asset_class(instr.asset_class)
        asset_totals_dec[norm_ac] = asset_totals_dec.get(norm_ac, Decimal("0")) + cur_val

        # Broker identification
        acc = h.demat_account
        dp_id = acc.dp_id if acc else ""
        p_code = getattr(acc, "provider_code", None)
        broker_name = get_broker_display_name(dp_id, p_code, acc.dp_name if acc else "Unknown")

        broker_totals_dec[broker_name] = broker_totals_dec.get(broker_name, Decimal("0")) + cur_val

        if broker_name not in broker_x_asset_dec:
            broker_x_asset_dec[broker_name] = {}
        broker_x_asset_dec[broker_name][norm_ac] = (
            broker_x_asset_dec[broker_name].get(norm_ac, Decimal("0")) + cur_val
        )

        # Merged by ISIN
        isin = instr.isin
        if isin not in merged_holdings_map:
            merged_holdings_map[isin] = {
                "isin": isin,
                "symbol": instr.symbol,
                "name": instr.name,
                "asset_class": norm_ac,
                "quantity_dec": Decimal("0"),
                "current_val_dec": Decimal("0"),
                "invest_val_dec": Decimal("0"),
                "brokers": {},
                "bond_meta": {}
            }

        m_item = merged_holdings_map[isin]
        m_item["quantity_dec"] += qty
        m_item["current_val_dec"] += cur_val
        m_item["invest_val_dec"] += inv_val

        # Broker breakdown for tooltip
        if broker_name not in m_item["brokers"]:
            m_item["brokers"][broker_name] = Decimal("0")
        m_item["brokers"][broker_name] += qty

        # Bond metadata
        if norm_ac == "Bond":
            has_bond_holdings = True
            if getattr(h, "metadata_json", None):
                try:
                    import json
                    meta = json.loads(h.metadata_json)
                    if isinstance(meta, dict):
                        m_item["bond_meta"].update(meta)
                except Exception:
                    pass

    total_pnl_dec = total_val_dec - total_inv_dec
    total_pnl_pct_dec = (
        (total_pnl_dec / total_inv_dec * Decimal("100")) if total_inv_dec > Decimal("0") else Decimal("0")
    )
    prev_total_dec = total_val_dec - day_chg_dec
    day_chg_pct_dec = (
        (day_chg_dec / prev_total_dec * Decimal("100")) if prev_total_dec > Decimal("0") else Decimal("0")
    )

    # 3. Chart 1: Asset class donut (max 6 slices, group small into 'Other')
    asset_donut_slices: List[Dict[str, Any]] = []
    sorted_assets = sorted(asset_totals_dec.items(), key=lambda x: x[1], reverse=True)
    if len(sorted_assets) > 6:
        top_5 = sorted_assets[:5]
        other_sum = sum((v for _, v in sorted_assets[5:]), Decimal("0"))
        final_assets = list(top_5)
        if other_sum > Decimal("0"):
            final_assets.append(("Other", other_sum))
    else:
        final_assets = sorted_assets

    for ac_name, ac_val in final_assets:
        pct = (ac_val / total_val_dec * Decimal("100")) if total_val_dec > Decimal("0") else Decimal("0")
        asset_donut_slices.append({
            "label": ac_name,
            "value": float(ac_val),
            "percentage": float(pct.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)),
            "color": ASSET_CLASS_COLORS.get(ac_name, "#64748b")
        })

    # 4. Chart 2: Broker allocation donut
    broker_donut_slices: List[Dict[str, Any]] = []
    sorted_brokers = sorted(broker_totals_dec.items(), key=lambda x: x[1], reverse=True)
    for b_name, b_val in sorted_brokers:
        pct = (b_val / total_val_dec * Decimal("100")) if total_val_dec > Decimal("0") else Decimal("0")
        broker_donut_slices.append({
            "label": b_name,
            "value": float(b_val),
            "percentage": float(pct.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)),
            "color": BROKER_COLORS.get(b_name, "#8b5cf6")
        })

    # 5. Chart 3: Portfolio value over time (holding_snapshots)
    snapshots_raw = get_holding_snapshots(db, user, range_filter="ALL")
    
    def format_snapshots_for_range(cut_days: Optional[int]) -> List[Dict[str, Any]]:
        if not snapshots_raw:
            return []
        if cut_days is not None:
            cutoff = (now - timedelta(days=cut_days)).replace(tzinfo=None)
            filtered = [s for s in snapshots_raw if s.snapshot_date >= cutoff]
        else:
            filtered = snapshots_raw
        return [
            {
                "date": s.snapshot_date.strftime("%Y-%m-%d"),
                "label": s.snapshot_date.strftime("%d %b"),
                "total_value": float(s.total_value),
                "invested_value": float(s.invested_value)
            }
            for s in filtered
        ]

    snapshots_by_range = {
        "1W": format_snapshots_for_range(7),
        "1M": format_snapshots_for_range(30),
        "3M": format_snapshots_for_range(90),
        "ALL": format_snapshots_for_range(None)
    }

    # 6. Chart 4: Broker x asset class stacked bar
    all_asset_classes = ["Equity", "ETF", "REIT", "InvIT", "Bond", "MF"]
    # Include any other asset classes present
    for ac in asset_totals_dec.keys():
        if ac not in all_asset_classes:
            all_asset_classes.append(ac)

    broker_labels = [b for b, _ in sorted_brokers]
    broker_x_asset_datasets = []
    for ac in all_asset_classes:
        values = []
        has_any_nonzero = False
        for b in broker_labels:
            val = float(broker_x_asset_dec.get(b, {}).get(ac, Decimal("0")))
            values.append(val)
            if val > 0:
                has_any_nonzero = True
        if has_any_nonzero:
            broker_x_asset_datasets.append({
                "label": ac,
                "backgroundColor": ASSET_CLASS_COLORS.get(ac, "#64748b"),
                "data": values
            })

    # 7. Chart 5: Top 10 holdings merged by ISIN
    sorted_merged = sorted(merged_holdings_map.values(), key=lambda x: x["current_val_dec"], reverse=True)
    top_10_holdings: List[Dict[str, Any]] = []
    for item in sorted_merged[:10]:
        c_val = item["current_val_dec"]
        pct = (c_val / total_val_dec * Decimal("100")) if total_val_dec > Decimal("0") else Decimal("0")
        is_above_25 = pct >= Decimal("25.0")
        
        # Tooltip split string: "BrokerA: X units, BrokerB: Y units"
        split_items = [
            f"{b_name}: {float(q):g} units"
            for b_name, q in item["brokers"].items()
        ]
        split_tooltip = " | ".join(split_items)

        top_10_holdings.append({
            "isin": item["isin"],
            "symbol": item["symbol"],
            "name": item["name"],
            "asset_class": item["asset_class"],
            "quantity": float(item["quantity_dec"]),
            "current_value": float(c_val),
            "percentage": float(pct.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)),
            "is_above_25_pct": is_above_25,
            "highlight_color": "#f59e0b" if is_above_25 else "#3b82f6",
            "broker_split": [{"broker": b, "quantity": float(q)} for b, q in item["brokers"].items()],
            "broker_split_tooltip": split_tooltip
        })

    # 8. Chart 6: Profit/loss by holding (diverging horizontal bar)
    holdings_with_pnl = []
    for item in merged_holdings_map.values():
        c_val = item["current_val_dec"]
        i_val = item["invest_val_dec"]
        pnl = c_val - i_val
        pnl_pct = (pnl / i_val * Decimal("100")) if i_val > Decimal("0") else Decimal("0")
        holdings_with_pnl.append({
            "isin": item["isin"],
            "symbol": item["symbol"],
            "name": item["name"],
            "current_value": float(c_val),
            "invested_value": float(i_val),
            "pnl": float(pnl),
            "pnl_pct": float(pnl_pct.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)),
            "color": "#10b981" if pnl >= Decimal("0") else "#ef4444"
        })

    # Top 10 gains and Top 10 losses
    gains = [h for h in holdings_with_pnl if h["pnl"] >= 0]
    losses = [h for h in holdings_with_pnl if h["pnl"] < 0]
    gains.sort(key=lambda x: x["pnl"], reverse=True)
    losses.sort(key=lambda x: x["pnl"])  # most negative first

    pnl_diverging_items = gains[:10] + losses[:10]
    # Sort all for display: highest gains down to highest losses
    pnl_diverging_items.sort(key=lambda x: x["pnl"], reverse=True)

    # 9. Chart 7: Income calendar (bond coupons & maturities for next 12 months)
    today = date.today()
    end_date = today + timedelta(days=365)
    monthly_coupons: Dict[str, Decimal] = {}
    monthly_maturities: Dict[str, Decimal] = {}
    calendar_events: List[Dict[str, Any]] = []

    for item in merged_holdings_map.values():
        if item["asset_class"] != "Bond":
            continue

        meta = item.get("bond_meta", {})
        raw_coupon = meta.get("coupon") or meta.get("coupon_rate")
        maturity_str = meta.get("maturity_date")
        next_coupon_str = meta.get("next_coupon_date")
        raw_face = meta.get("face_value", "1000")

        if not raw_coupon:
            continue

        try:
            coupon_rate = parse_decimal(str(raw_coupon).replace("%", ""))
        except Exception:
            continue

        try:
            face_value = parse_decimal(raw_face, default="1000")
        except Exception:
            face_value = Decimal("1000")

        qty = item["quantity_dec"]
        if qty <= Decimal("0"):
            continue

        # Semi-annual coupon payout
        semi_annual = (coupon_rate / Decimal("100") * face_value * qty / Decimal("2")).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

        if next_coupon_str:
            try:
                nxt = datetime.strptime(str(next_coupon_str).strip(), "%Y-%m-%d").date()
            except Exception:
                nxt = today + timedelta(days=180)
        else:
            nxt = today + timedelta(days=180)

        maturity_date = None
        if maturity_str:
            try:
                maturity_date = datetime.strptime(str(maturity_str).strip(), "%Y-%m-%d").date()
            except Exception:
                pass

        coupon_dt = nxt
        while coupon_dt <= end_date:
            if coupon_dt >= today:
                if maturity_date and coupon_dt > maturity_date:
                    break
                m_key = coupon_dt.strftime("%Y-%m")
                monthly_coupons[m_key] = monthly_coupons.get(m_key, Decimal("0")) + semi_annual
                calendar_events.append({
                    "date": coupon_dt.isoformat(),
                    "month_label": coupon_dt.strftime("%b %Y"),
                    "type": "Coupon",
                    "symbol": item["symbol"],
                    "name": item["name"],
                    "amount": float(semi_annual)
                })
            coupon_dt += timedelta(days=182)

        if maturity_date and today <= maturity_date <= end_date:
            mat_amount = (face_value * qty).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            m_key = maturity_date.strftime("%Y-%m")
            monthly_maturities[m_key] = monthly_maturities.get(m_key, Decimal("0")) + mat_amount
            calendar_events.append({
                "date": maturity_date.isoformat(),
                "month_label": maturity_date.strftime("%b %Y"),
                "type": "Maturity",
                "symbol": item["symbol"],
                "name": item["name"],
                "amount": float(mat_amount)
            })

    calendar_events.sort(key=lambda x: x["date"])

    income_monthly_chart: List[Dict[str, Any]] = []
    cur_m = today.replace(day=1)
    for _ in range(12):
        key = cur_m.strftime("%Y-%m")
        c_amt = monthly_coupons.get(key, Decimal("0"))
        m_amt = monthly_maturities.get(key, Decimal("0"))
        tot_amt = c_amt + m_amt
        income_monthly_chart.append({
            "month": cur_m.strftime("%b %Y"),
            "key": key,
            "coupon_amount": float(c_amt),
            "maturity_amount": float(m_amt),
            "total_amount": float(tot_amt)
        })
        if cur_m.month == 12:
            cur_m = cur_m.replace(year=cur_m.year + 1, month=1)
        else:
            cur_m = cur_m.replace(month=cur_m.month + 1)

    # 10. Diversification card: 100% stacked bar of Equity vs REIT/InvIT vs Bond
    eq_val = asset_totals_dec.get("Equity", Decimal("0")) + asset_totals_dec.get("ETF", Decimal("0"))
    reit_invit_val = asset_totals_dec.get("REIT", Decimal("0")) + asset_totals_dec.get("InvIT", Decimal("0"))
    bond_val = asset_totals_dec.get("Bond", Decimal("0"))

    if total_val_dec > Decimal("0"):
        eq_pct = float((eq_val / total_val_dec * Decimal("100")).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))
        reit_invit_pct = float((reit_invit_val / total_val_dec * Decimal("100")).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))
        bond_pct = float((bond_val / total_val_dec * Decimal("100")).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))
    else:
        eq_pct = 0.0
        reit_invit_pct = 0.0
        bond_pct = 0.0

    return {
        "summary": {
            "total_value": float(total_val_dec),
            "total_invested": float(total_inv_dec),
            "total_pnl": float(total_pnl_dec),
            "total_pnl_pct": float(total_pnl_pct_dec.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
            "day_change": float(day_chg_dec),
            "day_change_pct": float(day_chg_pct_dec.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
            "connected_brokers": connected_count,
            "total_brokers": total_configured_brokers,
            "sync_status_text": f"{connected_count} of {total_configured_brokers} brokers synced",
            "last_synced_human": last_synced_text,
            "has_stale_data": len(stale_providers) > 0,
            "stale_providers": stale_providers,
            "broker_statuses": broker_statuses
        },
        "asset_class_donut": {
            "slices": asset_donut_slices,
            "total_value": float(total_val_dec)
        },
        "broker_donut": {
            "slices": broker_donut_slices,
            "total_value": float(total_val_dec)
        },
        "portfolio_history": {
            "ranges": snapshots_by_range,
            "point_count": len(snapshots_raw),
            "insufficient_history": len(snapshots_raw) < 2,
            "empty_message": "History builds up from the day you linked your accounts"
        },
        "broker_x_asset_class": {
            "brokers": broker_labels,
            "datasets": broker_x_asset_datasets
        },
        "top_holdings": {
            "holdings": top_10_holdings,
            "total_value": float(total_val_dec),
            "threshold_25_pct": float(total_val_dec * Decimal("0.25"))
        },
        "pnl_diverging": {
            "items": pnl_diverging_items,
            "top_gains": gains[:10],
            "top_losses": losses[:10]
        },
        "income_calendar": {
            "months": income_monthly_chart,
            "events": calendar_events,
            "has_bonds": has_bond_holdings and len(calendar_events) > 0,
            "empty_message": "No bond holdings with upcoming coupon payments recorded."
        },
        "diversification": {
            "equity_pct": eq_pct,
            "reit_invit_pct": reit_invit_pct,
            "bond_pct": bond_pct,
            "total_value": float(total_val_dec),
            "has_holdings": total_val_dec > Decimal("0")
        },
        "is_empty": total_val_dec <= Decimal("0")
    }
