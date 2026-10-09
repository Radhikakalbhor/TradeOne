import os
import json
import hmac
import hashlib
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone, timedelta
from app.config import settings
from app.shared_identity import normalize_email

INSTRUMENTS_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "instruments_shared.json")
if not os.path.exists(INSTRUMENTS_FILE):
    INSTRUMENTS_FILE = "instruments_shared.json"

def load_shared_instruments() -> List[Dict[str, Any]]:
    """Load instruments from instruments_shared.json."""
    if os.path.exists(INSTRUMENTS_FILE):
        with open(INSTRUMENTS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []

def generate_starter_portfolio(
    email: str,
    provider_code: str = "b",
    salt: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Deterministically generate 6-10 starter holdings for a user and broker theme.
    
    Derived from HMAC-SHA256(SHARED_IDENTITY_SALT, email + provider_code).
    Theme A (provider 'a', e.g. NiftyTrade): stocks + 1 REIT
    Theme B (provider 'b', e.g. BharatInvest): stocks + ETFs + 1 InvIT
    Theme C (provider 'c', e.g. BondBazaar): bonds/G-Secs/SGBs + overlapping blue chips
    
    Quantities and average prices (+/-15% of last price) are derived deterministically.
    Same email always gives the exact same starter holdings.
    """
    norm_email = normalize_email(email)
    p_code = (provider_code or "b").lower().strip()
    secret_salt = salt or settings.SHARED_IDENTITY_SALT
    
    seed = hmac.new(
        secret_salt.encode("utf-8"),
        f"{norm_email}{p_code}".encode("utf-8"),
        hashlib.sha256
    ).hexdigest()

    instruments = load_shared_instruments()
    by_isin = {inst["isin"]: inst for inst in instruments}

    # ISIN candidate sets
    # Stocks:
    # RELIANCE (INE002A01018), TCS (INE467B01029), INFY (INE009A01021),
    # HDFCBANK (INE040A01034), TATAMOTORS (INE155A01022), ITC (INE041A01026)
    # REITs: EMBASSY (INE041025011), MINDSPACE (INE0BW225017)
    # InvITs: PGINVIT (INE002S25010)
    # ETFs: NIFTYBEES (INF204KB14I2), GOLDBEES (INF204KB17H7)
    # Bonds: GS2033-718 (IN0020230085), NHAI-2035 (INE906H07788)

    selected_isins: List[str] = []

    if p_code in ("a", "niftytrade"):
        # Stocks + 1 REIT (overlap on RELIANCE, TCS, HDFCBANK)
        selected_isins = [
            "INE002A01018",  # RELIANCE
            "INE467B01029",  # TCS
            "INE040A01034",  # HDFCBANK
            "INE009A01021",  # INFY
            "INE155A01022",  # TATAMOTORS
            "INE041A01026",  # ITC
            "INE041025011"   # EMBASSY (REIT)
        ]
    elif p_code in ("b", "bharatinvest", "tradeone"):
        # Stocks + ETFs + 1 InvIT (overlap on RELIANCE, TCS, HDFCBANK)
        selected_isins = [
            "INE002A01018",  # RELIANCE
            "INE467B01029",  # TCS
            "INE040A01034",  # HDFCBANK
            "INE155A01022",  # TATAMOTORS
            "INF204KB14I2",  # NIFTYBEES (ETF)
            "INF204KB17H7",  # GOLDBEES (ETF)
            "INE002S25010"   # PGINVIT (InvIT)
        ]
    else:
        # Theme C (bonds / G-Secs / SGBs + overlapping blue chips)
        selected_isins = [
            "IN0020230085",  # GS2033-718 (Govt Bond)
            "INE906H07788",  # NHAI-2035 (Tax Free Bond)
            "INF209K01168",  # PPFAS-FLEXI (Mutual Fund)
            "INE002A01018",  # RELIANCE (Overlap)
            "INE467B01029",  # TCS (Overlap)
            "INE040A01034"   # HDFCBANK (Overlap)
        ]

    holdings = []
    for isin in selected_isins:
        instr = by_isin.get(isin)
        if not instr:
            continue

        # Deterministic hash for this specific instrument
        inst_hash = hmac.new(
            seed.encode("utf-8"),
            isin.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()

        # Quantity: between 5 and 45 units (or for bonds: between 10 and 100)
        base_qty = (int(inst_hash[0:4], 16) % 40) + 5
        if instr["asset_class"] == "BOND":
            base_qty = base_qty * 2
        qty = float(base_qty)

        # Average price within +/- 15% of last price
        # factor ranges from 0.850 to 1.150
        offset_pct = ((int(inst_hash[4:8], 16) % 301) - 150) / 1000.0  # -0.150 to +0.150
        avg_price = round(instr["last_price"] * (1.0 + offset_pct), 2)

        holdings.append({
            "isin": instr["isin"],
            "symbol": instr["symbol"],
            "name": instr["name"],
            "asset_class": instr["asset_class"],
            "fi_type": instr.get("fi_type", instr["asset_class"]),
            "free_units": qty,
            "pledged_units": 0.0,
            "locked_units": 0.0,
            "total_units": qty,
            "avg_price": avg_price,
            "last_price": instr["last_price"]
        })

    return holdings
