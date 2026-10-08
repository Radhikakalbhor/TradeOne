import io
import csv
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
from sqlalchemy.orm import joinedload
from app.models import User, DematAccount, Holding, Instrument, Transaction

def format_inr(amount: float) -> str:
    """Format a number into Indian currency representation (e.g., 12,34,567.89)."""
    if amount is None:
        amount = 0.0
    
    is_negative = amount < 0
    amount = abs(amount)
    
    parts = f"{amount:.2f}".split(".")
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

def get_portfolio_summary(db, user: User) -> Dict[str, Any]:
    """Calculates overall depository portfolio statistics for a user."""
    accounts = db.query(DematAccount).filter(DematAccount.user_id == user.id).all()
    account_ids = [acc.id for acc in accounts]

    if not account_ids:
        return {
            "total_value": 0.0,
            "total_invested": 0.0,
            "total_pnl": 0.0,
            "total_pnl_pct": 0.0,
            "day_change": 0.0,
            "day_change_pct": 0.0,
            "num_accounts": 0,
            "num_dps": 0,
            "num_securities": 0,
            "asset_allocation": {},
            "dp_allocation": {},
            "accounts": []
        }

    holdings = db.query(Holding).options(
        joinedload(Holding.instrument),
        joinedload(Holding.demat_account)
    ).filter(Holding.demat_account_id.in_(account_ids)).all()

    total_value = 0.0
    total_invested = 0.0
    day_change = 0.0
    distinct_isins = set()
    distinct_dps = {acc.dp_name for acc in accounts}
    asset_allocation: Dict[str, float] = {}
    dp_allocation: Dict[str, float] = {acc.dp_name: 0.0 for acc in accounts}

    for h in holdings:
        instr = h.instrument
        if not instr:
            continue
        units = h.total_units
        current_val = units * instr.last_price
        invest_val = units * h.avg_price
        
        total_value += current_val
        total_invested += invest_val
        day_change += units * (instr.last_price - instr.prev_close)
        distinct_isins.add(h.isin)

        # Asset class grouping
        ac = instr.asset_class or "OTHER"
        asset_allocation[ac] = asset_allocation.get(ac, 0.0) + current_val

        # DP grouping
        dp_name = h.demat_account.dp_name if h.demat_account else "Unknown DP"
        dp_allocation[dp_name] = dp_allocation.get(dp_name, 0.0) + current_val

    total_pnl = total_value - total_invested
    total_pnl_pct = (total_pnl / total_invested * 100) if total_invested > 0 else 0.0
    prev_total = total_value - day_change
    day_change_pct = (day_change / prev_total * 100) if prev_total > 0 else 0.0

    return {
        "total_value": total_value,
        "total_invested": total_invested,
        "total_pnl": total_pnl,
        "total_pnl_pct": total_pnl_pct,
        "day_change": day_change,
        "day_change_pct": day_change_pct,
        "num_accounts": len(accounts),
        "num_dps": len(distinct_dps),
        "num_securities": len(distinct_isins),
        "asset_allocation": asset_allocation,
        "dp_allocation": dp_allocation,
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
    """Retrieve holdings with filtering, search, and optional ISIN merge."""
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

        current_val = h.total_units * instr.last_price
        invest_val = h.total_units * h.avg_price
        pnl = current_val - invest_val
        pnl_pct = (pnl / invest_val * 100) if invest_val > 0 else 0.0

        item = {
            "id": h.id,
            "demat_account_id": h.demat_account_id,
            "dp_name": h.demat_account.dp_name if h.demat_account else "",
            "masked_account_number": h.demat_account.masked_account_number if h.demat_account else "",
            "isin": instr.isin,
            "symbol": instr.symbol,
            "security_name": instr.name,
            "asset_class": instr.asset_class,
            "free_units": h.free_units,
            "pledged_units": h.pledged_units,
            "locked_units": h.locked_units,
            "total_units": h.total_units,
            "last_price": instr.last_price,
            "avg_price": h.avg_price,
            "current_value": current_val,
            "investment_value": invest_val,
            "pnl": pnl,
            "pnl_pct": pnl_pct
        }
        result.append(item)

    if not merge_by_isin:
        return result

    # Merge by ISIN across accounts
    merged: Dict[str, Dict[str, Any]] = {}
    for item in result:
        isin = item["isin"]
        if isin not in merged:
            merged[isin] = {
                "id": f"merged_{isin}",
                "demat_account_id": "MULTIPLE",
                "dp_name": "Across Accounts",
                "masked_account_number": "Multiple Accounts",
                "isin": isin,
                "symbol": item["symbol"],
                "security_name": item["security_name"],
                "asset_class": item["asset_class"],
                "free_units": 0.0,
                "pledged_units": 0.0,
                "locked_units": 0.0,
                "total_units": 0.0,
                "last_price": item["last_price"],
                "avg_price": 0.0,
                "current_value": 0.0,
                "investment_value": 0.0,
                "pnl": 0.0,
                "pnl_pct": 0.0
            }

        m = merged[isin]
        m["free_units"] += item["free_units"]
        m["pledged_units"] += item["pledged_units"]
        m["locked_units"] += item["locked_units"]
        m["total_units"] += item["total_units"]
        m["current_value"] += item["current_value"]
        m["investment_value"] += item["investment_value"]

    # Calculate weighted avg price and PnL for merged
    for m in merged.values():
        if m["total_units"] > 0:
            m["avg_price"] = m["investment_value"] / m["total_units"]
        m["pnl"] = m["current_value"] - m["investment_value"]
        m["pnl_pct"] = (m["pnl"] / m["investment_value"] * 100) if m["investment_value"] > 0 else 0.0

    return list(merged.values())

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
