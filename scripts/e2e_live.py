#!/usr/bin/env python3
"""scripts/e2e_live.py — Live End-to-End Verification Script for TradeOne Sibling Brokers.

Verifies connectivity and integration with the three deployed external mock broker services:
- Provider A: NiftyTrade
- Provider B: BharatInvest
- Provider C: BondBazaar

Usage:
    python scripts/e2e_live.py
Requirements:
    Environment variables:
      E2E_TEST_EMAIL: Email to provision and inspect (e.g. test.e2e@example.com)
      NIFTYTRADE_URL, NIFTYTRADE_INTERNAL_KEY
      BHARATINVEST_URL, BHARATINVEST_INTERNAL_KEY
      BONDBAZAAR_URL, BONDBAZAAR_INTERNAL_KEY
"""

import os
import sys
import json
import urllib.parse
from typing import Dict, Any, List
from pathlib import Path
from dotenv import load_dotenv

# Load .env if present
env_file = Path(__file__).resolve().parent.parent / ".env"
if env_file.exists():
    load_dotenv(env_file)

import httpx

def main():
    print("=" * 70)
    print("TradeOne Hub — Live Deployed Broker Services E2E Verification")
    print("=" * 70)

    # 1. Read and validate environment variables
    test_email = os.getenv("E2E_TEST_EMAIL", "").strip().lower()
    if not test_email:
        print("\n[ERROR] E2E_TEST_EMAIL environment variable is not set.")
        print("Please provide a test email via E2E_TEST_EMAIL=test.user@example.com")
        sys.exit(1)

    providers = [
        {
            "code": "a",
            "name": "NiftyTrade",
            "dp_id": "IN300001",
            "url": os.getenv("NIFTYTRADE_URL", "https://nifty-50-5nzz.onrender.com").rstrip("/"),
            "key": os.getenv("NIFTYTRADE_INTERNAL_KEY", "").strip()
        },
        {
            "code": "b",
            "name": "BharatInvest",
            "dp_id": "IN300002",
            "url": os.getenv("BHARATINVEST_URL", "https://bharatinvest.onrender.com").rstrip("/"),
            "key": os.getenv("BHARATINVEST_INTERNAL_KEY", "").strip()
        },
        {
            "code": "c",
            "name": "BondBazaar",
            "dp_id": "IN300003",
            "url": os.getenv("BONDBAZAAR_URL", "https://bondbazaar-1.onrender.com").rstrip("/"),
            "key": os.getenv("BONDBAZAAR_INTERNAL_KEY", "").strip()
        }
    ]

    # Check for CLI or environment options
    selected_brokers = []
    for arg in sys.argv[1:]:
        if arg.startswith("--broker="):
            selected_brokers.extend([b.strip().lower() for b in arg.split("=", 1)[1].split(",")])
        elif arg.startswith("--brokers="):
            selected_brokers.extend([b.strip().lower() for b in arg.split("=", 1)[1].split(",")])
        elif not arg.startswith("-"):
            selected_brokers.append(arg.strip().lower())

    bharatinvest_pending = os.getenv("BHARATINVEST_PENDING", "false").lower() in ("true", "1") or "--pending-bharatinvest" in sys.argv
    if bharatinvest_pending:
        for p in providers:
            if p["code"] == "b":
                p["key"] = ""

    if selected_brokers:
        for p in providers:
            if p["code"] not in selected_brokers and p["name"].lower() not in selected_brokers:
                p["key"] = ""

    configured_providers = [p for p in providers if p["key"]]
    if not configured_providers:
        print("\n[ERROR] Missing internal secret keys for all providers.")
        print("Please configure at least one of NIFTYTRADE_INTERNAL_KEY, BHARATINVEST_INTERNAL_KEY, or BONDBAZAAR_INTERNAL_KEY.")
        sys.exit(1)

    print(f"\nTarget User Email: {test_email}")
    print(f"Testing configured providers ({len(configured_providers)} of 3)...\n")

    encoded_email = urllib.parse.quote(test_email)
    results = []
    all_holdings = []
    has_failure = False

    timeout = httpx.Timeout(25.0, connect=15.0)

    for p in providers:
        p_code = p["code"]
        name = p["name"]
        url = p["url"]

        if not p["key"]:
            print(f"[{name} - Provider {p_code.upper()}] SKIPPED (Key not configured — KEY PENDING)")
            results.append({
                "provider": p_code.upper(),
                "broker": name,
                "status": "NOT TESTED — KEY PENDING",
                "identity": "N/A",
                "holding_count": 0,
                "portfolio_value": 0.0
            })
            continue

        headers = {
            "x-internal-key": p["key"],
            "Content-Type": "application/json",
            "Accept": "application/json"
        }

        print(f"[{name} - Provider {p_code.upper()}] Contacting {url}...")

        # A. Provisioning
        prov_status = "UNKNOWN"
        try:
            with httpx.Client(timeout=timeout) as client:
                resp = client.post(
                    f"{url}/internal/v1/users/provision",
                    json={"email": test_email, "full_name": "E2E Test User"},
                    headers=headers
                )
                if 200 <= resp.status_code < 300:
                    prov_status = "OK"
                else:
                    prov_status = f"HTTP_{resp.status_code}"
                    has_failure = True
        except Exception as exc:
            prov_status = f"ERROR: {type(exc).__name__}"
            has_failure = True

        # B. Profile
        prof_name = "N/A"
        try:
            with httpx.Client(timeout=timeout) as client:
                resp = client.get(f"{url}/internal/v1/users/{encoded_email}/profile", headers=headers)
                if resp.status_code == 200:
                    pjson = resp.json()
                    pdata = pjson.get("data", pjson) if isinstance(pjson, dict) else {}
                    prof_name = pdata.get("user_name") or pdata.get("name") or pdata.get("full_name") or "User"
                else:
                    has_failure = True
        except Exception as exc:
            has_failure = True

        # C. Holdings
        holdings_list = []
        try:
            with httpx.Client(timeout=timeout) as client:
                resp = client.get(f"{url}/internal/v1/users/{encoded_email}/holdings?page=1&page_size=100", headers=headers)
                if resp.status_code == 200:
                    hjson = resp.json()
                    if isinstance(hjson, list):
                        holdings_list = hjson
                    elif isinstance(hjson, dict):
                        if isinstance(hjson.get("holdings"), list):
                            holdings_list = hjson["holdings"]
                        elif isinstance(hjson.get("data"), list):
                            holdings_list = hjson["data"]
                        elif isinstance(hjson.get("data"), dict) and isinstance(hjson["data"].get("holdings"), list):
                            holdings_list = hjson["data"]["holdings"]
                        else:
                            holdings_list = []
                    else:
                        holdings_list = []
                else:
                    has_failure = True
        except Exception as exc:
            has_failure = True

        # D. Summary
        total_val = 0.0
        try:
            with httpx.Client(timeout=timeout) as client:
                resp = client.get(f"{url}/internal/v1/users/{encoded_email}/summary", headers=headers)
                if resp.status_code == 200:
                    sjson = resp.json()
                    sdata = sjson.get("data", sjson) if isinstance(sjson, dict) else {}
                    total_val = float(sdata.get("current_value") or sdata.get("net_worth") or sdata.get("total_investment") or 0.0)
                else:
                    # Fallback compute from holdings
                    total_val = sum(
                        float(h.get("free_units", h.get("quantity", 0.0))) * float(h.get("last_price", 0.0))
                        for h in holdings_list
                    )
        except Exception:
            total_val = sum(
                float(h.get("free_units", h.get("quantity", 0.0))) * float(h.get("last_price", 0.0))
                for h in holdings_list
            )

        status_label = "CONNECTED" if prov_status == "OK" and holdings_list is not None else "FAILED"
        results.append({
            "provider": p_code.upper(),
            "broker": name,
            "status": status_label,
            "identity": prof_name,
            "holding_count": len(holdings_list),
            "portfolio_value": total_val
        })

        for h in holdings_list:
            scrip = h.get("scrip", {}) if isinstance(h.get("scrip"), dict) else {}
            h_isin = h.get("isin") or h.get("ISIN") or scrip.get("ISIN") or scrip.get("isin") or ""
            if not h_isin:
                continue
            sym = h.get("symbol") or h.get("tradingsymbol") or scrip.get("symbol") or ""
            sec_name = h.get("security_name") or h.get("name") or scrip.get("name") or sym
            qty = float(h.get("free_units") or h.get("quantity") or h.get("qty") or h.get("total_units") or 0.0)
            price = float(h.get("last_price") or h.get("ltp") or 0.0)
            all_holdings.append({
                "provider": name,
                "isin": h_isin,
                "symbol": sym,
                "name": sec_name,
                "quantity": qty,
                "last_price": price,
                "value": qty * price
            })

    # Print Provider Summary Table
    print("\n" + "=" * 70)
    print(f"{'Provider':<12} {'Broker Name':<16} {'Status':<12} {'Identity':<15} {'Holdings':<10} {'Valuation (INR)':<15}")
    print("-" * 70)
    for r in results:
        print(f"{r['provider']:<12} {r['broker']:<16} {r['status']:<12} {r['identity']:<15} {r['holding_count']:<10} {r['portfolio_value']:<15,.2f}")
    print("=" * 70)

    # Print Merged Holdings Grouped by ISIN
    merged: Dict[str, Dict[str, Any]] = {}
    for h in all_holdings:
        isin = h["isin"]
        if not isin:
            continue
        if isin not in merged:
            merged[isin] = {
                "isin": isin,
                "symbol": h["symbol"],
                "name": h["name"],
                "total_qty": 0.0,
                "last_price": h["last_price"],
                "total_val": 0.0,
                "breakdown": []
            }
        m = merged[isin]
        m["total_qty"] += h["quantity"]
        m["total_val"] += h["value"]
        m["breakdown"].append(f"{h['provider']}: {h['quantity']:.2f}")

    print("\n" + "=" * 70)
    print("CONSOLIDATED PORTFOLIO (MERGED BY ISIN)")
    print("=" * 70)
    print(f"{'ISIN':<14} {'Symbol':<12} {'Total Qty':<12} {'Valuation (INR)':<16} {'Broker Breakdown'}")
    print("-" * 70)
    for isin, m in sorted(merged.items()):
        breakdown_str = ", ".join(m["breakdown"])
        print(f"{isin:<14} {m['symbol']:<12} {m['total_qty']:<12.3f} {m['total_val']:<16,.2f} {breakdown_str}")
    print("=" * 70)

    if has_failure:
        print("\n[RESULT] One or more tested providers failed to respond successfully.")
        sys.exit(2)
    else:
        print("\n[SUCCESS] Tested broker services verified and consolidated successfully!")
        sys.exit(0)

if __name__ == "__main__":
    main()
