import json
import logging
import urllib.parse
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Dict, Any, Optional, Tuple, List
import concurrent.futures
import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.models import User, DematAccount, Holding, Instrument
from app.shared_identity import normalize_email

logger = logging.getLogger(__name__)


def parse_decimal(val: Any, default: str = "0") -> Decimal:
    """Safely convert any numeric or string value to Decimal with precision.
    Handles commas, whitespace, strings, ints, floats, None.
    """
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


class BrokerAdapter:
    """HTTP client adapter for communicating with the three deployed mock broker APIs:
    - Provider A: NiftyTrade (DP ID: IN300001)
    - Provider B: BharatInvest (DP ID: IN300002)
    - Provider C: BondBazaar (DP ID: IN300003)
    """

    def __init__(self, timeout: float = 60.0, max_retries: int = 2):
        self.timeout = timeout
        self.max_retries = max_retries

    def get_provider_config(self, provider_code: str) -> Dict[str, Any]:
        """Get standard metadata and configuration for a given provider code."""
        code = (provider_code or "").lower().strip()
        config = settings.BROKER_PROVIDERS.get(code)
        if not config:
            raise ValueError(f"Unknown broker provider code: {provider_code}")
        return config

    def _headers(self, provider_code: str) -> Dict[str, str]:
        """Construct secure server-to-server headers. Never logs or leaks the key."""
        config = self.get_provider_config(provider_code)
        key = (config.get("internal_key") or "").strip().strip('"').strip("'")
        return {
            "x-internal-key": key,
            "Content-Type": "application/json",
            "Accept": "application/json"
        }

    def _build_url(self, provider_code: str, path: str) -> str:
        config = self.get_provider_config(provider_code)
        base = config["url"].rstrip("/")
        return f"{base}{path}"

    def _request_with_retry(
        self,
        method: str,
        url: str,
        headers: Dict[str, str],
        json_body: Optional[Dict[str, Any]] = None
    ) -> Tuple[bool, Dict[str, Any]]:
        """Execute HTTP request with backoff for cold starts and transient errors.
        Does not retry permanent client/auth errors (400, 401, 403, 404).
        Never logs API keys or sensitive secrets.
        """
        last_error = None
        for attempt in range(self.max_retries + 1):
            t0 = time.time()
            try:
                with httpx.Client(timeout=self.timeout) as client:
                    if method.upper() == "POST":
                        resp = client.post(url, json=json_body, headers=headers)
                    else:
                        resp = client.get(url, headers=headers)

                    elapsed_ms = round((time.time() - t0) * 1000, 1)

                    if 200 <= resp.status_code < 300:
                        try:
                            data = resp.json()
                        except Exception:
                            data = {}
                        if isinstance(data, dict):
                            data["_status_code"] = resp.status_code
                            data["_response_time_ms"] = elapsed_ms
                        return True, data

                    # Non-retryable permanent client errors
                    if resp.status_code == 404:
                        return False, {
                            "code": "USER_NOT_FOUND",
                            "status_code": 404,
                            "message": f"User not found (HTTP 404)",
                            "_status_code": 404,
                            "_response_time_ms": elapsed_ms
                        }

                    if resp.status_code in (400, 401, 403):
                        msg = f"Provider returned HTTP {resp.status_code}"
                        if resp.status_code == 401:
                            msg = "Provider returned HTTP 401 (Authentication failed - invalid internal key)"
                        return False, {
                            "code": f"HTTP_{resp.status_code}",
                            "status_code": resp.status_code,
                            "message": msg,
                            "_status_code": resp.status_code,
                            "_response_time_ms": elapsed_ms
                        }

                    last_error = {
                        "code": f"HTTP_{resp.status_code}",
                        "status_code": resp.status_code,
                        "message": f"Provider returned HTTP {resp.status_code}",
                        "_status_code": resp.status_code,
                        "_response_time_ms": elapsed_ms
                    }
            except (httpx.TimeoutException, httpx.ConnectTimeout, httpx.ReadTimeout):
                elapsed_ms = round((time.time() - t0) * 1000, 1)
                last_error = {
                    "code": "TIMEOUT",
                    "status_code": 504,
                    "message": "Connection timed out contacting provider",
                    "_status_code": 504,
                    "_response_time_ms": elapsed_ms
                }
            except (httpx.ConnectError, httpx.NetworkError):
                elapsed_ms = round((time.time() - t0) * 1000, 1)
                last_error = {
                    "code": "CONNECTION_ERROR",
                    "status_code": 502,
                    "message": "Failed to connect to provider",
                    "_status_code": 502,
                    "_response_time_ms": elapsed_ms
                }
            except Exception as exc:
                elapsed_ms = round((time.time() - t0) * 1000, 1)
                last_error = {
                    "code": "CONNECTION_ERROR",
                    "status_code": 500,
                    "message": f"Network exception: {type(exc).__name__}",
                    "_status_code": 500,
                    "_response_time_ms": elapsed_ms
                }

            if attempt < self.max_retries:
                time.sleep(0.3 * (2 ** attempt))

        return False, last_error or {"code": "UNKNOWN_ERROR", "status_code": 500, "message": "Request failed"}

    def provision_user(
        self,
        provider_code: str,
        email: str,
        full_name: Optional[str] = None
    ) -> Tuple[bool, Dict[str, Any]]:
        """POST /internal/v1/users/provision (Idempotent user provisioning)."""
        norm_email = normalize_email(email)
        url = self._build_url(provider_code, "/internal/v1/users/provision")
        headers = self._headers(provider_code)
        payload = {"email": norm_email, "full_name": full_name, "fullName": full_name}
        return self._request_with_retry("POST", url, headers, json_body=payload)

    def get_profile(self, provider_code: str, email: str) -> Tuple[bool, Dict[str, Any]]:
        """GET /internal/v1/users/{email}/profile."""
        norm_email = normalize_email(email)
        encoded_email = urllib.parse.quote(norm_email)
        url = self._build_url(provider_code, f"/internal/v1/users/{encoded_email}/profile")
        headers = self._headers(provider_code)
        return self._request_with_retry("GET", url, headers)

    def get_holdings(self, provider_code: str, email: str) -> Tuple[bool, Dict[str, Any]]:
        """GET /internal/v1/users/{email}/holdings."""
        norm_email = normalize_email(email)
        encoded_email = urllib.parse.quote(norm_email)
        url = self._build_url(provider_code, f"/internal/v1/users/{encoded_email}/holdings?page=1&page_size=100")
        headers = self._headers(provider_code)
        return self._request_with_retry("GET", url, headers)

    def get_summary(self, provider_code: str, email: str) -> Tuple[bool, Dict[str, Any]]:
        """GET /internal/v1/users/{email}/summary."""
        norm_email = normalize_email(email)
        encoded_email = urllib.parse.quote(norm_email)
        url = self._build_url(provider_code, f"/internal/v1/users/{encoded_email}/summary")
        headers = self._headers(provider_code)
        return self._request_with_retry("GET", url, headers)

    def fetch_provider_bundle(
        self,
        provider_code: str,
        email: str,
        full_name: Optional[str] = None
    ) -> Dict[str, Any]:
        """Orchestrate provision, profile, holdings, and summary fetch for a single provider.
        Enforces strict identity verification and accurate status mapping.
        """
        config = self.get_provider_config(provider_code)
        norm_email = normalize_email(email)

        key = (config.get("internal_key") or "").strip().strip('"').strip("'")
        if not key:
            return {
                "provider": provider_code,
                "broker_name": config["broker_name"],
                "dp_name": config["dp_name"],
                "dp_id": config["dp_id"],
                "status": "unavailable",
                "error": f"Internal API key not configured for {config['broker_name']}",
                "profile": None,
                "holdings": [],
                "summary": None,
                "http_status": 401,
                "response_time_ms": 0.0
            }

        # 1. Provision user (idempotent)
        prov_ok, prov_data = self.provision_user(provider_code, norm_email, full_name)
        if not prov_ok:
            if prov_data.get("code") == "USER_NOT_FOUND" or prov_data.get("status_code") == 404:
                return {
                    "provider": provider_code,
                    "broker_name": config["broker_name"],
                    "dp_name": config["dp_name"],
                    "dp_id": config["dp_id"],
                    "status": "no_account",
                    "error": f"No account on {config['broker_name']} for this email",
                    "profile": None,
                    "holdings": [],
                    "summary": None,
                    "http_status": 404,
                    "response_time_ms": prov_data.get("_response_time_ms", 0.0)
                }
            is_timeout = prov_data.get("code") == "TIMEOUT"
            return {
                "provider": provider_code,
                "broker_name": config["broker_name"],
                "dp_name": config["dp_name"],
                "dp_id": config["dp_id"],
                "status": "stale" if is_timeout else "unavailable",
                "error": prov_data.get("message", "Provisioning failed"),
                "profile": None,
                "holdings": [],
                "summary": None,
                "http_status": prov_data.get("_status_code", 504 if is_timeout else 500),
                "response_time_ms": prov_data.get("_response_time_ms", 0.0)
            }

        # 2. Fetch profile
        prof_ok, prof_data = self.get_profile(provider_code, norm_email)
        if not prof_ok:
            if prof_data.get("code") == "USER_NOT_FOUND" or prof_data.get("status_code") == 404:
                return {
                    "provider": provider_code,
                    "broker_name": config["broker_name"],
                    "dp_name": config["dp_name"],
                    "dp_id": config["dp_id"],
                    "status": "no_account",
                    "error": f"No account on {config['broker_name']} for this email",
                    "profile": None,
                    "holdings": [],
                    "summary": None,
                    "http_status": 404,
                    "response_time_ms": prof_data.get("_response_time_ms", 0.0)
                }

        # Strict identity check: Verify returned profile email matches logged-in email
        if prof_ok and isinstance(prof_data, dict):
            prof_email = (
                prof_data.get("email") or
                (prof_data.get("data") if isinstance(prof_data.get("data"), dict) else {}).get("email") or
                (prof_data.get("user") if isinstance(prof_data.get("user"), dict) else {}).get("email") or
                ""
            )
            if prof_email and normalize_email(prof_email) != norm_email:
                logger.warning(
                    f"[Identity Mismatch] Provider {provider_code} returned profile email {prof_email} for user {norm_email}"
                )
                return {
                    "provider": provider_code,
                    "broker_name": config["broker_name"],
                    "dp_name": config["dp_name"],
                    "dp_id": config["dp_id"],
                    "status": "error",
                    "error": f"Broker profile email mismatch: expected {norm_email}, got {normalize_email(prof_email)}",
                    "profile": None,
                    "holdings": [],
                    "summary": None,
                    "http_status": prof_data.get("_status_code", 200),
                    "response_time_ms": prof_data.get("_response_time_ms", 0.0)
                }

        # 3. Fetch holdings and summary
        hold_ok, hold_data = self.get_holdings(provider_code, norm_email)
        summ_ok, summ_data = self.get_summary(provider_code, norm_email)

        if not hold_ok:
            if hold_data.get("code") == "USER_NOT_FOUND" or hold_data.get("status_code") == 404:
                return {
                    "provider": provider_code,
                    "broker_name": config["broker_name"],
                    "dp_name": config["dp_name"],
                    "dp_id": config["dp_id"],
                    "status": "no_account",
                    "error": f"No account on {config['broker_name']} for this email",
                    "profile": prof_data if prof_ok else None,
                    "holdings": [],
                    "summary": None,
                    "http_status": 404,
                    "response_time_ms": hold_data.get("_response_time_ms", 0.0)
                }
            is_timeout = hold_data.get("code") == "TIMEOUT"
            return {
                "provider": provider_code,
                "broker_name": config["broker_name"],
                "dp_name": config["dp_name"],
                "dp_id": config["dp_id"],
                "status": "stale" if is_timeout else "unavailable",
                "error": hold_data.get("message", "Holdings fetch failed"),
                "profile": prof_data if prof_ok else None,
                "holdings": [],
                "summary": summ_data if summ_ok else None,
                "http_status": hold_data.get("_status_code", 504 if is_timeout else 500),
                "response_time_ms": hold_data.get("_response_time_ms", 0.0)
            }

        # Parse holdings list
        holdings_list = []
        if isinstance(hold_data, list):
            holdings_list = hold_data
        elif isinstance(hold_data, dict):
            if isinstance(hold_data.get("holdings"), list):
                holdings_list = hold_data["holdings"]
            elif isinstance(hold_data.get("data"), list):
                holdings_list = hold_data["data"]
            elif isinstance(hold_data.get("data"), dict) and isinstance(hold_data["data"].get("holdings"), list):
                holdings_list = hold_data["data"]["holdings"]

        return {
            "provider": provider_code,
            "broker_name": config["broker_name"],
            "dp_name": config["dp_name"],
            "dp_id": config["dp_id"],
            "status": "connected",
            "error": None,
            "profile": prof_data if prof_ok else None,
            "holdings": holdings_list,
            "summary": summ_data if summ_ok else None,
            "http_status": hold_data.get("_status_code", 200) if isinstance(hold_data, dict) else 200,
            "response_time_ms": hold_data.get("_response_time_ms", 0.0) if isinstance(hold_data, dict) else 0.0
        }

    def _apply_bundle_to_db(
        self,
        db: Session,
        user: User,
        provider_code: str,
        bundle: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Persist fetched bundle to SQLite with strict snapshot replacement in a single transaction."""
        config = self.get_provider_config(provider_code)
        dp_id = config["dp_id"]
        dp_name = config["dp_name"]
        code = config["provider_code"]
        now = datetime.now(timezone.utc)

        # Demo bypass strictly behind DEMO_MODE
        if settings.DEMO_MODE and user.email in ("hacksmiths360@gmail.com", "aarav.mehta@example.com", "priya.nair@example.com"):
            account = db.query(DematAccount).filter(
                DematAccount.user_id == user.id,
                DematAccount.dp_id == dp_id
            ).first()
            if account:
                account.provider_code = code
                account.sync_status = "connected"
                account.last_synced_at = now
                account.sync_error = None
                db.commit()
            return {
                "provider": code,
                "broker_name": config["broker_name"],
                "dp_id": dp_id,
                "status": "connected",
                "holdings_count": len(account.holdings) if account else 0
            }

        # Find or create account
        account = db.query(DematAccount).filter(
            DematAccount.user_id == user.id,
            DematAccount.dp_id == dp_id
        ).first()

        status_type = bundle.get("status")

        if status_type == "connected":
            if not account:
                acc_num = f"12081600{dp_id[-3:]}{user.id:05d}"
                masked_num = f"XXXX{acc_num[-4:]}"
                if bundle.get("profile") and isinstance(bundle["profile"], dict):
                    p_info = bundle["profile"].get("data", bundle["profile"]) if isinstance(bundle["profile"].get("data"), dict) else bundle["profile"]
                    if p_info.get("masked_demat_number"):
                        masked_num = p_info["masked_demat_number"]

                account = DematAccount(
                    id=f"da_{code}_{user.id}_{user.bo_id[-4:]}",
                    user_id=user.id,
                    dp_name=dp_name,
                    dp_id=dp_id,
                    account_number=acc_num,
                    masked_account_number=masked_num,
                    account_type="INDIVIDUAL",
                    status="ACTIVE",
                    opened_date=datetime.now().strftime("%Y-%m-%d"),
                    nominee_status="REGISTERED",
                    provider_code=code,
                    sync_status="connected",
                    last_synced_at=now,
                    sync_error=None
                )
                db.add(account)
                db.commit()
                db.refresh(account)
            else:
                account.provider_code = code
                account.sync_status = "connected"
                account.last_synced_at = now
                account.sync_error = None

            # Process holdings with Decimal parsing, paise conversion, and bps conversion
            raw_holdings = bundle.get("holdings", [])
            received_holdings = []
            for raw_h in raw_holdings:
                scrip = raw_h.get("scrip", {}) if isinstance(raw_h.get("scrip"), dict) else {}
                h_isin = raw_h.get("isin") or raw_h.get("ISIN") or scrip.get("ISIN") or scrip.get("isin") or ""
                if not h_isin:
                    continue
                h_sym = raw_h.get("symbol") or raw_h.get("tradingsymbol") or scrip.get("symbol") or h_isin
                h_name = raw_h.get("security_name") or raw_h.get("name") or scrip.get("name") or h_sym

                # Parse prices with paise detection (BondBazaar)
                if "last_price_paise" in raw_h and raw_h["last_price_paise"] is not None:
                    h_price = parse_decimal(raw_h["last_price_paise"]) / Decimal("100")
                elif "last_price" in raw_h and raw_h["last_price"] is not None:
                    h_price = parse_decimal(raw_h["last_price"])
                elif "ltp" in raw_h and raw_h["ltp"] is not None:
                    h_price = parse_decimal(raw_h["ltp"])
                else:
                    h_price = Decimal("0")

                if "avg_price_paise" in raw_h and raw_h["avg_price_paise"] is not None:
                    h_avg = parse_decimal(raw_h["avg_price_paise"]) / Decimal("100")
                elif "avg_price" in raw_h and raw_h["avg_price"] is not None:
                    h_avg = parse_decimal(raw_h["avg_price"])
                elif "avg_cost" in raw_h and raw_h["avg_cost"] is not None:
                    h_avg = parse_decimal(raw_h["avg_cost"])
                elif "average_price" in raw_h and raw_h["average_price"] is not None:
                    h_avg = parse_decimal(raw_h["average_price"])
                else:
                    h_avg = h_price

                # Quantity (supports string numbers from BharatInvest)
                raw_qty = raw_h.get("free_units") if raw_h.get("free_units") is not None else (
                    raw_h.get("quantity") if raw_h.get("quantity") is not None else (
                        raw_h.get("qty") if raw_h.get("qty") is not None else raw_h.get("total_units")
                    )
                )
                h_qty = parse_decimal(raw_qty, default="0")

                # Bond metadata & bps conversion (BondBazaar)
                bond_meta = {}
                if "coupon_bps" in raw_h and raw_h["coupon_bps"] is not None:
                    bond_meta["coupon"] = f"{parse_decimal(raw_h['coupon_bps']) / Decimal('100')}%"
                elif "coupon" in raw_h and raw_h["coupon"] is not None:
                    bond_meta["coupon"] = str(raw_h["coupon"])

                if "ytm_bps" in raw_h and raw_h["ytm_bps"] is not None:
                    bond_meta["ytm"] = f"{parse_decimal(raw_h['ytm_bps']) / Decimal('100')}%"
                elif "ytm" in raw_h and raw_h["ytm"] is not None:
                    bond_meta["ytm"] = str(raw_h["ytm"])

                for k in ("maturity_date", "security_type"):
                    if k in raw_h and raw_h[k] is not None:
                        bond_meta[k] = str(raw_h[k])
                if "accrued_interest_paise" in raw_h and raw_h["accrued_interest_paise"] is not None:
                    bond_meta["accrued_interest"] = str(parse_decimal(raw_h["accrued_interest_paise"]) / Decimal("100"))
                elif "accrued_interest" in raw_h and raw_h["accrued_interest"] is not None:
                    bond_meta["accrued_interest"] = str(raw_h["accrued_interest"])

                if "face_value_paise" in raw_h and raw_h["face_value_paise"] is not None:
                    bond_meta["face_value"] = str(parse_decimal(raw_h["face_value_paise"]) / Decimal("100"))

                received_holdings.append({
                    "isin": h_isin,
                    "symbol": h_sym,
                    "name": h_name,
                    "free_units": h_qty,
                    "last_price": h_price,
                    "avg_price": h_avg,
                    "bond_meta": bond_meta,
                    "asset_class": raw_h.get("asset_class", "BOND" if code == "c" else "EQUITY"),
                    "isin_description": raw_h.get("isin_description", "GOVERNMENT BOND" if code == "c" else "EQUITY SHARES")
                })

            # Ensure master instruments exist
            for h_data in received_holdings:
                isin = h_data["isin"]
                instr = db.query(Instrument).filter(Instrument.isin == isin).first()
                if not instr:
                    instr = Instrument(
                        isin=isin,
                        symbol=h_data["symbol"],
                        name=h_data["name"],
                        isin_description=h_data["isin_description"],
                        asset_class=h_data["asset_class"],
                        fi_type="BONDS" if h_data["asset_class"] == "BOND" else "EQUITIES",
                        last_price=float(h_data["last_price"]),
                        prev_close=float(h_data["last_price"]),
                        change_pct=0.0
                    )
                    db.add(instr)
                    db.commit()
                elif h_data["last_price"] > Decimal("0"):
                    instr.last_price = float(h_data["last_price"])

            # Snapshot replacement: delete old rows and insert fresh rows in a single transaction
            db.query(Holding).filter(Holding.demat_account_id == account.id).delete()

            for h_data in received_holdings:
                meta_json = json.dumps(h_data["bond_meta"]) if h_data["bond_meta"] else None
                h_rec = Holding(
                    demat_account_id=account.id,
                    isin=h_data["isin"],
                    free_units=float(h_data["free_units"]),
                    pledged_units=0.0,
                    locked_units=0.0,
                    avg_price=float(h_data["avg_price"]),
                    metadata_json=meta_json,
                    updated_at=now
                )
                db.add(h_rec)

            account.sync_status = "connected"
            account.last_synced_at = now
            account.sync_error = None
            db.commit()

            return {
                "provider": code,
                "broker_name": config["broker_name"],
                "dp_id": dp_id,
                "status": "connected",
                "holdings_count": len(received_holdings)
            }

        elif status_type == "no_account":
            # Broker returned 404 or USER_NOT_FOUND -> No account on broker, show zero holdings
            if account:
                account.sync_status = "no_account"
                account.sync_error = bundle.get("error") or f"No account on {config['broker_name']} for this email"
                account.last_synced_at = now
                db.query(Holding).filter(Holding.demat_account_id == account.id).delete()
                db.commit()
            else:
                acc_num = f"12081600{dp_id[-3:]}{user.id:05d}"
                account = DematAccount(
                    id=f"da_{code}_{user.id}_{user.bo_id[-4:]}",
                    user_id=user.id,
                    dp_name=dp_name,
                    dp_id=dp_id,
                    account_number=acc_num,
                    masked_account_number=f"XXXX{acc_num[-4:]}",
                    account_type="INDIVIDUAL",
                    status="ACTIVE",
                    opened_date=datetime.now().strftime("%Y-%m-%d"),
                    nominee_status="REGISTERED",
                    provider_code=code,
                    sync_status="no_account",
                    last_synced_at=now,
                    sync_error=bundle.get("error") or f"No account on {config['broker_name']} for this email"
                )
                db.add(account)
                db.commit()
            return {
                "provider": code,
                "broker_name": config["broker_name"],
                "dp_id": dp_id,
                "status": "no_account",
                "error": bundle.get("error"),
                "holdings_count": 0
            }

        elif status_type == "error":
            # Profile mismatch or rejected -> Flag link as ERROR, leave existing holdings untouched
            err_msg = bundle.get("error", "Broker identity verification error")
            if account:
                account.sync_status = "error"
                account.sync_error = err_msg
                account.last_synced_at = now
                db.commit()
            else:
                acc_num = f"12081600{dp_id[-3:]}{user.id:05d}"
                account = DematAccount(
                    id=f"da_{code}_{user.id}_{user.bo_id[-4:]}",
                    user_id=user.id,
                    dp_name=dp_name,
                    dp_id=dp_id,
                    account_number=acc_num,
                    masked_account_number=f"XXXX{acc_num[-4:]}",
                    account_type="INDIVIDUAL",
                    status="ACTIVE",
                    opened_date=datetime.now().strftime("%Y-%m-%d"),
                    nominee_status="REGISTERED",
                    provider_code=code,
                    sync_status="error",
                    last_synced_at=now,
                    sync_error=err_msg
                )
                db.add(account)
                db.commit()
            return {
                "provider": code,
                "broker_name": config["broker_name"],
                "dp_id": dp_id,
                "status": "error",
                "error": err_msg,
                "holdings_count": len(account.holdings) if account else 0
            }

        else:
            # Stale / unavailable -> Non-destructive: keep last good rows untouched, mark stale
            err_msg = bundle.get("error", "Sync failed")
            if account:
                account.sync_status = "stale"
                account.sync_error = err_msg
                account.last_synced_at = now
                db.commit()
                return {
                    "provider": code,
                    "broker_name": config["broker_name"],
                    "dp_id": dp_id,
                    "status": "stale",
                    "error": err_msg,
                    "holdings_count": len(account.holdings)
                }
            else:
                acc_num = f"12081600{dp_id[-3:]}{user.id:05d}"
                account = DematAccount(
                    id=f"da_{code}_{user.id}_{user.bo_id[-4:]}",
                    user_id=user.id,
                    dp_name=dp_name,
                    dp_id=dp_id,
                    account_number=acc_num,
                    masked_account_number=f"XXXX{acc_num[-4:]}",
                    account_type="INDIVIDUAL",
                    status="ACTIVE",
                    opened_date=datetime.now().strftime("%Y-%m-%d"),
                    nominee_status="REGISTERED",
                    provider_code=code,
                    sync_status="unavailable",
                    last_synced_at=now,
                    sync_error=err_msg
                )
                db.add(account)
                db.commit()
                return {
                    "provider": code,
                    "broker_name": config["broker_name"],
                    "dp_id": dp_id,
                    "status": "unavailable",
                    "error": err_msg,
                    "holdings_count": 0
                }

    def _handle_sync_failure(self, db: Session, user: User, provider_code: str, error_msg: str) -> Dict[str, Any]:
        """Mark provider as stale on unexpected exception without corrupting existing data."""
        config = self.get_provider_config(provider_code)
        dp_id = config["dp_id"]
        dp_name = config["dp_name"]
        code = config["provider_code"]
        now = datetime.now(timezone.utc)
        account = db.query(DematAccount).filter(
            DematAccount.user_id == user.id,
            DematAccount.dp_id == dp_id
        ).first()
        if account:
            account.sync_status = "stale"
            account.sync_error = error_msg
            account.last_synced_at = now
            db.commit()
            return {
                "provider": code,
                "broker_name": config["broker_name"],
                "dp_id": dp_id,
                "status": "stale",
                "error": error_msg,
                "holdings_count": len(account.holdings)
            }
        else:
            acc_num = f"12081600{dp_id[-3:]}{user.id:05d}"
            account = DematAccount(
                id=f"da_{code}_{user.id}_{user.bo_id[-4:]}",
                user_id=user.id,
                dp_name=dp_name,
                dp_id=dp_id,
                account_number=acc_num,
                masked_account_number=f"XXXX{acc_num[-4:]}",
                account_type="INDIVIDUAL",
                status="ACTIVE",
                opened_date=datetime.now().strftime("%Y-%m-%d"),
                nominee_status="REGISTERED",
                provider_code=code,
                sync_status="unavailable",
                last_synced_at=now,
                sync_error=error_msg
            )
            db.add(account)
            db.commit()
            return {
                "provider": code,
                "broker_name": config["broker_name"],
                "dp_id": dp_id,
                "status": "unavailable",
                "error": error_msg,
                "holdings_count": 0
            }

    def sync_user_from_broker(
        self,
        db: Session,
        user: User,
        provider_code: str
    ) -> Dict[str, Any]:
        """Fetch broker data and persist it into TradeOne's DematAccount and Holding tables."""
        bundle = self.fetch_provider_bundle(provider_code, user.email, user.name)
        return self._apply_bundle_to_db(db, user, provider_code, bundle)

    def is_provider_stale(
        self,
        account: Optional[DematAccount],
        max_age_seconds: Optional[int] = None,
        max_age_minutes: Optional[int] = None
    ) -> bool:
        """Check if provider data is older than specified max age or not yet synced."""
        if not account or not account.last_synced_at:
            return True
        if max_age_seconds is None:
            max_age_seconds = (max_age_minutes * 60) if max_age_minutes is not None else 60
        now = datetime.now(timezone.utc)
        last_sync = account.last_synced_at
        if last_sync.tzinfo is None:
            last_sync = last_sync.replace(tzinfo=timezone.utc)
        return (now - last_sync).total_seconds() > max_age_seconds

    def sync_all_brokers(
        self,
        db: Session,
        user: User,
        only_stale: bool = False,
        max_age_minutes: int = 1
    ) -> Dict[str, Any]:
        """Fetch and synchronize portfolios across all three providers (A, B, C) in parallel.
        Uses 60-second timeouts with backoff retries.
        Failure of one broker never blocks or corrupts data from the others.
        """
        providers_to_sync = []
        results = {}
        for code in ("a", "b", "c"):
            try:
                config = self.get_provider_config(code)
                dp_id = config["dp_id"]
                account = db.query(DematAccount).filter(
                    DematAccount.user_id == user.id,
                    DematAccount.dp_id == dp_id
                ).first()

                if only_stale and account and not self.is_provider_stale(account, max_age_seconds=max_age_minutes * 60):
                    results[code] = {
                        "provider": code,
                        "broker_name": config["broker_name"],
                        "dp_id": dp_id,
                        "status": account.sync_status or "connected",
                        "holdings_count": len(account.holdings),
                        "cached": True
                    }
                else:
                    providers_to_sync.append(code)
            except Exception as e:
                logger.error(f"[BrokerAdapter] Error checking status for provider {code}: {e}")
                providers_to_sync.append(code)

        if not providers_to_sync:
            return results

        # Parallel network fetching across providers
        def _fetch_one(p_code: str):
            try:
                b = self.fetch_provider_bundle(p_code, user.email, user.name)
                return p_code, b, None
            except Exception as exc:
                return p_code, None, exc

        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
            future_map = {executor.submit(_fetch_one, c): c for c in providers_to_sync}
            for future in concurrent.futures.as_completed(future_map):
                c = future_map[future]
                try:
                    p_code, bundle, exc = future.result()
                    if exc:
                        results[p_code] = self._handle_sync_failure(db, user, p_code, str(exc))
                    else:
                        results[p_code] = self._apply_bundle_to_db(db, user, p_code, bundle)
                except Exception as e:
                    logger.error(f"[BrokerAdapter] Exception syncing provider {c}: {e}")
                    results[c] = self._handle_sync_failure(db, user, c, str(e))

        return results


broker_adapter = BrokerAdapter()
