import json
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
from app.models import Consent, DataSession, User, DematAccount, Holding

def serialize_fi_data(
    session: DataSession,
    consent: Consent,
    user: User,
    accounts: List[DematAccount],
    allowed_fi_types: List[str]
) -> Dict[str, Any]:
    """Serialize customer demat accounts and holdings in the strict nested AA-style format."""
    now_iso = datetime.now(timezone.utc).isoformat()
    serialized_accounts = []

    for acc in accounts:
        # Filter holdings belonging to allowed FI types
        acc_holdings = []
        acc_current_val = 0.0
        acc_invest_val = 0.0

        for h in acc.holdings:
            instr = h.instrument
            if not instr:
                continue

            # Check if instrument's FI type is requested in consent
            fi_type = instr.fi_type or "EQUITIES"
            if allowed_fi_types and fi_type not in allowed_fi_types:
                continue

            current_val = h.total_units * instr.last_price
            invest_val = h.total_units * h.avg_price
            acc_current_val += current_val
            acc_invest_val += invest_val

            acc_holdings.append({
                "isin": instr.isin,
                "issuerName": instr.name,
                "isinDescription": instr.isin_description,
                "units": f"{h.total_units:.3f}",
                "lastTradedPrice": f"{instr.last_price:.2f}",
                "avgPrice": f"{h.avg_price:.2f}",
                "lockInUnits": f"{h.locked_units:.3f}",
                "pledgedUnits": f"{h.pledged_units:.3f}",
                "assetClass": instr.asset_class
            })

        # Determine top-level primary fiType for this account based on its first holding or default EQUITIES
        primary_fi_type = acc_holdings[0]["assetClass"] if acc_holdings else "EQUITIES"
        if primary_fi_type == "EQUITY":
            primary_fi_type = "EQUITIES"

        serialized_accounts.append({
            "fiType": primary_fi_type,
            "linkedAccRef": acc.id,
            "maskedAccNumber": acc.masked_account_number,
            "dp": {
                "name": acc.dp_name,
                "dpId": acc.dp_id,
                "depository": "TradeOne"
            },
            "profile": {
                "holders": [
                    {"name": user.name, "type": "PRIMARY"}
                ],
                "nomineeStatus": acc.nominee_status
            },
            "summary": {
                "currentValue": f"{acc_current_val:.2f}",
                "investmentValue": f"{acc_invest_val:.2f}",
                "holdingCount": len(acc_holdings)
            },
            "holdings": acc_holdings
        })

    return {
        "sessionId": session.session_id,
        "consentId": consent.consent_id,
        "generatedAt": now_iso,
        "customer": {
            "email": user.email,
            "name": user.name,
            "maskedPan": user.masked_pan
        },
        "accounts": serialized_accounts
    }
