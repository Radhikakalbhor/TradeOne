import io
import csv
import json
from decimal import Decimal, InvalidOperation
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional, Union
from sqlalchemy.orm import joinedload
from app.config import settings
from app.models import User, DematAccount, Holding, Instrument, Transaction


def parse_decimal(val: Any, default: str = "0") -> Decimal:
    """Safely convert any value to Decimal."""
    if val is None:
        return Decimal(default)
    if isinstance(val, Decimal):
        return val
    if isinstance(val, (int, float)):
        return Decimal(str(val))
    s = str(val).replace(",", "").strip()
    if not s:
        return Decimal(default)
    try:
        return Decimal(s)
    except (InvalidOperation, ValueError):
        return Decimal(default)


def format_inr(amount: Union[float, Decimal, int, str, None]) -> str:
    """Format a number into Indian currency representation (e.g., ₹12,34,567.89)."""
    if amount is None:
        dec = Decimal("0.00")
    elif isinstance(amount, Decimal):
        dec = amount
    else:
        try:
            dec = Decimal(str(amount))
        except Exception:
            dec = Decimal("0.00")

    is_negative = dec < Decimal("0")
    dec_abs = abs(dec)

    parts = f"{dec_abs:.2f}".split(".")
    integer_part = parts[0]
    decimal_part = parts[1]

    if len(integer_part) <= 3:
        formatted = integer_part
    else:
        last_three = integer_part[-3:]
        remaining = integer_part[:-3]
        groups = []
        while len(remaining) > 2:
            groups.insert(0, remaining[-2:])
            remaining = remaining[:-2]
        if remaining:
            groups.insert(0, remaining)
        groups.append(last_three)
        formatted = ",".join(groups)

    prefix = "-" if is_negative else ""
    return f"₹{prefix}{formatted}.{decimal_part}"


SECTOR_MAP = {
    "INE002A01018": "Energy & Petrochemicals",
    "INE467B01029": "Information Technology",
    "INE009A01021": "Information Technology",
    "INE040A01034": "Financial Services",
    "INE155A01022": "Automotive",
    "INE041A01026": "Consumer Goods (FMCG)",
    "INE041025011": "Real Estate (REIT)",
    "INE0BW225017": "Real Estate (REIT)",
    "INE002S25010": "Power & Infrastructure (InvIT)",
    "INF204KB14I2": "Index Funds / ETFs",
    "INF204KB17H7": "Commodities / Gold",
    "IN0020230085": "Government Bonds (Sovereign)",
    "INE001A07SB2": "Banking & Financial Debt",
    "INE296A07RQ9": "Non-Banking Financial Debt",
    "INE906H07788": "Infrastructure Bonds"
}


def generate_portfolio_insights(
    total_value: float,
    asset_allocation: Dict[str, float],
    dp_allocation: Dict[str, float],
    holdings_items: List[Dict[str, Any]]
) -> List[Dict[str, str]]:
    """Calculates factual portfolio statistics and absence insights based solely on active holdings."""
    insights = []
    if total_value <= 0 or not holdings_items:
        return insights

    # 1. Single Security Concentration
    sorted_holdings = sorted(holdings_items, key=lambda x: x.get("current_value", 0.0), reverse=True)
    if sorted_holdings:
        top_h = sorted_holdings[0]
        top_pct = (top_h["current_value"] / total_value * 100) if total_value > 0 else 0.0
        if top_pct >= 25.0:
            insights.append({
                "type": "risk",
                "title": "High Position Concentration",
                "message": f"Largest holding ({top_h.get('security_name', top_h.get('symbol'))}) accounts for {top_pct:.1f}% of overall portfolio value."
            })
        else:
            insights.append({
                "type": "info",
                "title": "Diversified Position Sizing",
                "message": f"Largest position ({top_h.get('security_name', top_h.get('symbol'))}) accounts for {top_pct:.1f}% of total portfolio value."
            })

    # 2. Asset Class Allocation
    if asset_allocation:
        top_ac, top_ac_val = max(asset_allocation.items(), key=lambda x: x[1])
        top_ac_pct = (top_ac_val / total_value * 100)
        insights.append({
            "type": "allocation",
            "title": f"Primary Class: {top_ac}",
            "message": f"{top_ac} represents {top_ac_pct:.1f}% of total invested assets."
        })

    # 3. Provider Concentration
    if dp_allocation:
        top_dp, top_dp_val = max(dp_allocation.items(), key=lambda x: x[1])
        top_dp_pct = (top_dp_val / total_value * 100)
        insights.append({
            "type": "provider",
            "title": f"Primary Broker: {top_dp}",
            "message": f"{top_dp} holds {top_dp_pct:.1f}% of your consolidated depository assets."
        })

    # 4. Absence Insights (factual portfolio statistics)
    if asset_allocation.get("REIT", 0.0) == 0.0:
        insights.append({
            "type": "absence",
            "title": "No REIT Exposure",
            "message": "Portfolio currently contains 0.0% allocation to Real Estate Investment Trusts."
        })
    if asset_allocation.get("INVIT", 0.0) == 0.0:
        insights.append({
            "type": "absence",
            "title": "No InvIT Exposure",
            "message": "Portfolio currently contains 0.0% allocation to Infrastructure Investment Trusts."
        })
    if asset_allocation.get("BOND", 0.0) == 0.0:
        insights.append({
            "type": "absence",
            "title": "No Bond Exposure",
            "message": "Portfolio currently contains 0.0% allocation to fixed-income / debt securities."
        })
    if asset_allocation.get("ETF", 0.0) == 0.0:
        insights.append({
            "type": "absence",
            "title": "No ETF Exposure",
            "message": "Portfolio currently contains 0.0% allocation to exchange-traded index funds."
        })

    return insights


def get_portfolio_summary(db, user: User) -> Dict[str, Any]:
    """Calculates consolidated depository portfolio statistics for a user using exact Decimal arithmetic."""
    accounts = db.query(DematAccount).filter(DematAccount.user_id == user.id).all()
    account_ids = [acc.id for acc in accounts]

    now = datetime.now(timezone.utc)
    provider_statuses = {}
    broker_subtotals = {}
    stale_providers = []

    for p_code, p_meta in settings.BROKER_PROVIDERS.items():
        acc = next((a for a in accounts if a.dp_id == p_meta["dp_id"] or getattr(a, "provider_code", None) == p_code), None)
        sync_status = "unavailable"
        time_human = "Never"
        last_sync_dt = None
        sync_error = None
        conn_method = "Google Identity"

        if acc:
            sync_status = getattr(acc, "sync_status", "connected") or "connected"
            sync_error = getattr(acc, "sync_error", None)
            last_sync_dt = getattr(acc, "last_synced_at", None)
            if last_sync_dt:
                tz_sync = last_sync_dt if last_sync_dt.tzinfo else last_sync_dt.replace(tzinfo=timezone.utc)
                diff_sec = (now - tz_sync).total_seconds()
                if diff_sec < 60:
                    time_human = "Just now"
                elif diff_sec < 3600:
                    time_human = f"{int(diff_sec // 60)}m ago"
                elif diff_sec < 86400:
                    time_human = f"{int(diff_sec // 3600)}h ago"
                else:
                    time_human = last_sync_dt.strftime("%d %b %H:%M")

        if sync_status in ("stale", "unavailable"):
            stale_providers.append(p_meta["broker_name"])

        note_text = ""
        if sync_status == "no_account":
            note_text = f"No account on {p_meta['broker_name']} for this email"
        elif sync_error:
            note_text = sync_error

        provider_statuses[p_code] = {
            "provider": p_code,
            "broker_name": p_meta["broker_name"],
            "dp_id": p_meta["dp_id"],
            "status": sync_status,
            "last_synced_at": last_sync_dt.isoformat() if last_sync_dt else None,
            "last_synced_human": time_human,
            "connection_method": conn_method,
            "error": sync_error,
            "note": note_text
        }

        broker_subtotals[p_code] = {
            "provider": p_code,
            "broker_name": p_meta["broker_name"],
            "dp_id": p_meta["dp_id"],
            "quantity": Decimal("0"),
            "invested": Decimal("0"),
            "current_value": Decimal("0"),
            "pnl": Decimal("0"),
            "pnl_pct": Decimal("0"),
            "pct": Decimal("0"),
            "holdings_count": 0,
            "status": sync_status,
            "note": note_text
        }

    holdings = []
    if account_ids:
        holdings = db.query(Holding).options(
            joinedload(Holding.instrument),
            joinedload(Holding.demat_account)
        ).filter(Holding.demat_account_id.in_(account_ids)).all()

    total_value_dec = Decimal("0")
    total_invested_dec = Decimal("0")
    day_change_dec = Decimal("0")
    distinct_isins = set()
    asset_allocation: Dict[str, float] = {}
    dp_allocation: Dict[str, float] = {}
    sector_allocation: Dict[str, float] = {}
    raw_holdings_summary_list = []

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

        current_val = qty * last_price
        invest_val = qty * avg_price
        day_chg = qty * (last_price - prev_close)

        total_value_dec += current_val
        total_invested_dec += invest_val
        day_change_dec += day_chg
        distinct_isins.add(h.isin)

        # Attribute to per-broker subtotal
        dp_id = h.demat_account.dp_id if h.demat_account else ""
        p_code = {"IN300001": "a", "IN300002": "b", "IN300003": "c"}.get(
            dp_id, getattr(h.demat_account, "provider_code", "a")
        )
        if p_code in broker_subtotals:
            broker_subtotals[p_code]["quantity"] += qty
            broker_subtotals[p_code]["invested"] += invest_val
            broker_subtotals[p_code]["current_value"] += current_val
            broker_subtotals[p_code]["holdings_count"] += 1

        # Allocations (only if current_val > 0)
        if current_val > Decimal("0"):
            ac = instr.asset_class or "OTHER"
            asset_allocation[ac] = float(parse_decimal(asset_allocation.get(ac, 0.0)) + current_val)

            dp_name = h.demat_account.dp_name if h.demat_account else "Unknown DP"
            dp_allocation[dp_name] = float(parse_decimal(dp_allocation.get(dp_name, 0.0)) + current_val)

            sector = SECTOR_MAP.get(h.isin, f"{ac.title()} Sector" if ac != "EQUITY" else "General Diversified")
            sector_allocation[sector] = float(parse_decimal(sector_allocation.get(sector, 0.0)) + current_val)

        raw_holdings_summary_list.append({
            "isin": h.isin,
            "symbol": instr.symbol,
            "security_name": instr.name,
            "current_value": float(current_val)
        })

    # Finalize broker subtotals: PnL, PnL %, and portfolio %
    for b_sub in broker_subtotals.values():
        b_sub["pnl"] = b_sub["current_value"] - b_sub["invested"]
        if b_sub["invested"] > Decimal("0"):
            b_sub["pnl_pct"] = (b_sub["pnl"] / b_sub["invested"] * Decimal("100"))
        else:
            b_sub["pnl_pct"] = Decimal("0")

        if total_value_dec > Decimal("0"):
            b_sub["pct"] = (b_sub["current_value"] / total_value_dec * Decimal("100"))
        else:
            b_sub["pct"] = Decimal("0")

    total_pnl_dec = total_value_dec - total_invested_dec
    total_pnl_pct_dec = (total_pnl_dec / total_invested_dec * Decimal("100")) if total_invested_dec > Decimal("0") else Decimal("0")
    prev_total_dec = total_value_dec - day_change_dec
    day_change_pct_dec = (day_change_dec / prev_total_dec * Decimal("100")) if prev_total_dec > Decimal("0") else Decimal("0")

    # Zero state enforcement: if user has no holdings anywhere, show 0 and NO concentration/allocation insights
    if total_value_dec <= Decimal("0") or len(raw_holdings_summary_list) == 0:
        insights = []
        asset_allocation = {}
        dp_allocation = {}
        sector_allocation = {}
    else:
        insights = generate_portfolio_insights(float(total_value_dec), asset_allocation, dp_allocation, raw_holdings_summary_list)

    # Distinct DPs across user's accounts
    distinct_dps = {acc.dp_name for acc in accounts}

    return {
        "total_value": total_value_dec,
        "total_value_dec": total_value_dec,
        "total_value_float": float(total_value_dec),
        "total_invested": total_invested_dec,
        "total_invested_dec": total_invested_dec,
        "total_invested_float": float(total_invested_dec),
        "total_pnl": total_pnl_dec,
        "total_pnl_dec": total_pnl_dec,
        "total_pnl_pct": total_pnl_pct_dec,
        "day_change": day_change_dec,
        "day_change_pct": day_change_pct_dec,
        "num_accounts": len(accounts),
        "num_dps": len(distinct_dps),
        "num_securities": len(distinct_isins),
        "asset_allocation": asset_allocation,
        "dp_allocation": dp_allocation,
        "sector_allocation": sector_allocation,
        "insights": insights,
        "provider_statuses": provider_statuses,
        "broker_subtotals": broker_subtotals,
        "stale_providers": stale_providers,
        "has_stale_data": len(stale_providers) > 0,
        "accounts": accounts
    }


def get_user_holdings(
    db,
    user: User,
    dp_filter: Optional[str] = None,
    asset_filter: Optional[str] = None,
    search: Optional[str] = None,
    merge_by_isin: bool = False
) -> List[Dict[str, Any]]:
    """Retrieve holdings with filtering, search, and optional ISIN merge using Decimal calculations."""
    accounts = db.query(DematAccount).filter(DematAccount.user_id == user.id).all()
    account_ids = [acc.id for acc in accounts]

    if not account_ids:
        return []

    query = db.query(Holding).options(
        joinedload(Holding.instrument),
        joinedload(Holding.demat_account)
    ).filter(Holding.demat_account_id.in_(account_ids))

    if dp_filter:
        query = query.join(DematAccount).filter(DematAccount.dp_name == dp_filter)

    raw_holdings = query.all()
    result = []

    for h in raw_holdings:
        instr = h.instrument
        if not instr:
            continue

        if asset_filter and instr.asset_class != asset_filter:
            continue

        if search:
            s = search.lower()
            if s not in instr.name.lower() and s not in instr.symbol.lower() and s not in instr.isin.lower():
                continue

        qty_dec = parse_decimal(h.total_units)
        if qty_dec <= Decimal("0"):
            continue

        price_dec = parse_decimal(instr.last_price)
        avg_dec = parse_decimal(h.avg_price)

        current_val_dec = qty_dec * price_dec
        invest_val_dec = qty_dec * avg_dec
        pnl_dec = current_val_dec - invest_val_dec
        pnl_pct_dec = (pnl_dec / invest_val_dec * Decimal("100")) if invest_val_dec > Decimal("0") else Decimal("0")

        dp_id = h.demat_account.dp_id if h.demat_account else ""
        provider_code = {"IN300001": "a", "IN300002": "b", "IN300003": "c"}.get(
            dp_id, getattr(h.demat_account, "provider_code", "") or ""
        )
        broker_name = {"IN300001": "NiftyTrade", "IN300002": "BharatInvest", "IN300003": "BondBazaar"}.get(
            dp_id, h.demat_account.dp_name if h.demat_account else ""
        )
        sync_status = getattr(h.demat_account, "sync_status", "connected") or "connected"

        item = {
            "id": h.id,
            "demat_account_id": h.demat_account_id,
            "dp_name": h.demat_account.dp_name if h.demat_account else "",
            "dp_id": dp_id,
            "provider": provider_code,
            "broker_name": broker_name,
            "masked_account_number": h.demat_account.masked_account_number if h.demat_account else "",
            "isin": instr.isin,
            "symbol": instr.symbol,
            "security_name": instr.name,
            "asset_class": instr.asset_class,
            "free_units": float(qty_dec),
            "pledged_units": float(parse_decimal(h.pledged_units)),
            "locked_units": float(parse_decimal(h.locked_units)),
            "total_units": float(qty_dec),
            "total_units_dec": qty_dec,
            "last_price": float(price_dec),
            "last_price_dec": price_dec,
            "avg_price": float(avg_dec),
            "avg_price_dec": avg_dec,
            "current_value": float(current_val_dec),
            "current_value_dec": current_val_dec,
            "investment_value": float(invest_val_dec),
            "investment_value_dec": invest_val_dec,
            "pnl": float(pnl_dec),
            "pnl_pct": float(pnl_pct_dec),
            "sync_status": sync_status
        }

        # Bond metadata
        bond_details = {
            "coupon": "N/A",
            "ytm": "N/A",
            "maturity_date": "N/A",
            "accrued_interest": "N/A",
            "security_type": "N/A"
        }
        if getattr(h, "metadata_json", None):
            try:
                meta = json.loads(h.metadata_json)
                if isinstance(meta, dict):
                    for k in bond_details:
                        if k in meta and meta[k] is not None and str(meta[k]).strip():
                            bond_details[k] = str(meta[k])
            except Exception:
                pass

        item["bond_details"] = bond_details
        item["is_bond"] = (instr.asset_class == "BOND" or dp_id == "IN300003")

        result.append(item)

    if not merge_by_isin:
        return result

    # Merge by ISIN across accounts with quantity-weighted average price
    merged: Dict[str, Dict[str, Any]] = {}
    for item in result:
        isin = item["isin"]
        if isin not in merged:
            merged[isin] = {
                "id": f"merged_{isin}",
                "demat_account_id": "MULTIPLE",
                "dp_name": "Across Accounts",
                "dp_id": "MULTIPLE",
                "provider": "multiple",
                "broker_name": "Multiple Brokers",
                "masked_account_number": "Multiple Accounts",
                "isin": isin,
                "symbol": item["symbol"],
                "security_name": item["security_name"],
                "asset_class": item["asset_class"],
                "free_units": Decimal("0"),
                "pledged_units": Decimal("0"),
                "locked_units": Decimal("0"),
                "total_units": Decimal("0"),
                "last_price": item["last_price_dec"],
                "avg_price": Decimal("0"),
                "current_value": Decimal("0"),
                "investment_value": Decimal("0"),
                "pnl": Decimal("0"),
                "pnl_pct": Decimal("0"),
                "bond_details": item["bond_details"],
                "is_bond": item["is_bond"],
                "broker_breakdown": []
            }

        m = merged[isin]
        qty_d = item["total_units_dec"]
        cur_d = item["current_value_dec"]
        inv_d = item["investment_value_dec"]

        m["free_units"] += qty_d
        m["total_units"] += qty_d
        m["current_value"] += cur_d
        m["investment_value"] += inv_d

        m["broker_breakdown"].append({
            "provider": item["provider"],
            "broker_name": item["broker_name"],
            "dp_name": item["dp_name"],
            "dp_id": item["dp_id"],
            "masked_account_number": item["masked_account_number"],
            "quantity": item["total_units"],
            "avg_price": item["avg_price"],
            "current_value": item["current_value"],
            "investment_value": item["investment_value"],
            "pnl": item["pnl"],
            "pnl_pct": item["pnl_pct"],
            "sync_status": item.get("sync_status", "connected")
        })

    # Weighted average price: sum(quantity * avg_price) / sum(quantity)
    final_merged = []
    for m in merged.values():
        total_u = m["total_units"]
        inv_v = m["investment_value"]
        cur_v = m["current_value"]

        if total_u > Decimal("0"):
            avg_p = inv_v / total_u
        else:
            avg_p = Decimal("0")

        pnl = cur_v - inv_v
        pnl_pct = (pnl / inv_v * Decimal("100")) if inv_v > Decimal("0") else Decimal("0")

        final_merged.append({
            "id": m["id"],
            "demat_account_id": m["demat_account_id"],
            "dp_name": m["dp_name"],
            "dp_id": m["dp_id"],
            "provider": m["provider"],
            "broker_name": m["broker_name"],
            "masked_account_number": m["masked_account_number"],
            "isin": m["isin"],
            "symbol": m["symbol"],
            "security_name": m["security_name"],
            "asset_class": m["asset_class"],
            "free_units": float(total_u),
            "pledged_units": float(m["pledged_units"]),
            "locked_units": float(m["locked_units"]),
            "total_units": float(total_u),
            "total_units_dec": total_u,
            "last_price": float(m["last_price"]),
            "last_price_dec": m["last_price"],
            "avg_price": float(avg_p),
            "avg_price_dec": avg_p,
            "current_value": float(cur_v),
            "current_value_dec": cur_v,
            "investment_value": float(inv_v),
            "investment_value_dec": inv_v,
            "pnl": float(pnl),
            "pnl_pct": float(pnl_pct),
            "bond_details": m["bond_details"],
            "is_bond": m["is_bond"],
            "broker_breakdown": m["broker_breakdown"]
        })

    return final_merged


def export_transactions_csv(transactions: List[Transaction]) -> str:
    """Generate CSV string of statement transactions."""
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Transaction Date",
        "DP Name",
        "Demat Account",
        "Security Name",
        "ISIN",
        "Type",
        "Quantity",
        "Settlement Price (₹)",
        "Reference ID",
        "Description"
    ])

    for t in transactions:
        writer.writerow([
            t.trans_date.strftime("%Y-%m-%d %H:%M:%S") if t.trans_date else "",
            t.demat_account.dp_name if t.demat_account else "",
            t.demat_account.masked_account_number if t.demat_account else "",
            t.instrument.name if t.instrument else "",
            t.isin,
            t.trans_type,
            f"{t.quantity:.3f}",
            f"{t.price:.2f}",
            t.reference_id,
            t.description
        ])

    return output.getvalue()
