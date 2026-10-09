# TradeOne 🏦
> **Simulated Central Depository & Account Aggregator Sandbox**
> *"One view of every demat account"*

TradeOne is a simulated Indian securities depository platform (in the role NSDL and CDSL play in India). It acts as a sandbox data provider and centralized holdings registry for portfolio aggregators, wealth management services, and mock broker integrations.

*Disclaimer: Simulated data - not a real depository. For development, integration, and sandbox testing only.*

---

## 🌟 Key Features

- **Consolidated Demat Portfolio**: Unified multi-broker registry tracking securities across multiple Depository Participants (DPs) such as NiftyTrade Securities, BharatInvest Securities, and BondBazaar.
- **Unified Holdings Ledger**: Interactive filtering by DP, asset class (Equity, REIT, InvIT, ETF, Bond, Mutual Fund), search, and a toggle to **"Merge by ISIN"** across demat accounts with weighted average costs.
- **Consolidated Account Statement (CAS)**: Generates genuine NSDL/CDSL-style PDF statements password-protected using **Last 4 Digits of PAN + Date of Birth (DDMM)**.
- **Account Aggregator (AA) Consent API**: Full consent-driven financial data sharing architecture (`/aa/v1/consents`, `/aa/v1/sessions`, `/aa/v1/sessions/{id}/data`) with cryptographic HMAC-SHA256 signatures and webhook notifications (`CONSENT_APPROVED`, `DATA_READY`, `HOLDINGS_CHANGED`, etc.).
- **Multi-Method Secure Authentication**:
  - Email One-Time Code (OTP) with 6-digit auto-advancing boxes, 30s resend timer, 5-attempt rate limit & 15-minute lockout.
  - OpenID Connect (OIDC) with **Google**.
  - One-click Dev Demo accounts (**Aarav Mehta** and **Priya Nair**).
- **Internal Broker Ingestion API**: Real-time webhook and settlement sync endpoint (`POST /internal/v1/ingest/holdings`) enabling mock brokers to update depository holdings after trades.
- **Administrator Console**: Full testbed controls at `/admin` (simulate 503 outages, +5s latency, data prep delay, fail next session, and simulate corporate actions like bonuses, splits, and dividends).

---

## 🚀 Quickstart & One-Command Run

### 1. Prerequisites
- Python 3.10+ (Tested on Python 3.14)
- Git

### 2. Setup Virtual Environment
```bash
python -m venv .venv
# On Windows PowerShell:
.\.venv\Scripts\Activate.ps1
# On macOS/Linux:
source .venv/bin/activate
```

### 3. Install Dependencies
```bash
pip install -r requirements.txt
```

### 4. Configure Environment
```bash
copy .env.example .env
```

### 5. Launch Application
```bash
uvicorn app.main:app --reload
```
Access the application:
- **Web App**: [http://localhost:8000](http://localhost:8000)
- **Admin Console**: [http://localhost:8000/admin](http://localhost:8000/admin) (Default password: `adminsecret123`)
- **Interactive API Docs (Swagger)**: [http://localhost:8000/docs](http://localhost:8000/docs)
- **System Health**: [http://localhost:8000/health](http://localhost:8000/health)

---

## 🔑 Demo Credentials & Accounts

When `OTP_DEV_MODE=true` (enabled by default), you can sign in instantly with one click:

| Name | Email | Demat Accounts | Portfolio Profile |
| :--- | :--- | :--- | :--- |
| **Aarav Mehta** | `aarav.mehta@example.com` | 3 Accounts (NiftyTrade, BharatInvest, BondBazaar) | Equity-heavy, Overlapping RELIANCE stock, REIT, GOI Bond, 6mo history |
| **Priya Nair** | `priya.nair@example.com` | 2 Accounts (NiftyTrade, BharatInvest) | PowerGrid InvIT, Gold ETF, Mutual Fund Units |
| **Admin** | `/admin` | N/A | Password: `adminsecret123` |

---

## ⚙️ Environment Variables & OAuth Setup

Create a `.env` file in the project root:

| Variable | Default | Purpose |
| :--- | :--- | :--- |
| `SECRET_KEY` | `nationaldepo-super-secure...` | HMAC-SHA256 signing secret for consent artefacts and webhooks |
| `SESSION_SECRET` | `nationaldepo-session-encryption...` | Session cookie encryption secret |
| `ADMIN_PASSWORD` | `adminsecret123` | Admin dashboard unlock password |
| `INGEST_API_KEY` | `nd-ingest-secret-key-2026` | API key for mock brokers to sync trade settlements |
| `SHARED_IDENTITY_SALT`| `tradeone-shared-identity-salt-2026`| Shared secret across all 4 sibling projects to guarantee deterministic identity |
| `INTERNAL_API_KEY` | `tradeone-internal-key-2026` | Trusted server-to-server internal API key (`x-internal-key`) |
| `INTERNAL_API_ENABLED`| `true` | Enable/disable trusted internal server endpoints |
| `SEED_STARTER_PORTFOLIO`| `true` | Generate deterministic starter holdings for new users |
| `STARTING_FUNDS` | `1000000.0` | Initial simulated wallet balance (₹10,00,000) |
| `TRADEONE_URL` | `""` | Hub URL for broker apps to dispatch `HOLDINGS_CHANGED` events |
| `PROVIDER_CODE` | `tradeone` | Provider code (`tradeone`, `a`, `b`, or `c`) |
| `DP_NAME` | `TradeOne Depository` | Depository participant name |
| `DP_ID` | `IN300000` | Depository participant identifier |
| `OTP_DEV_MODE` | `true` | When true, logs OTP to server console, shows dev UI hint, enables demo logins |
| `OTP_EXPIRY_MINUTES`| `10` | One-time code validity window |
| `CONSENT_DEFAULT_DAYS`| `90` | Default validity for Account Aggregator consents |
| `SEED_STARTER_ACCOUNTS`| `true` | Automatically provisions starter demat accounts for new users |
| `ALLOWED_EMAILS` | `""` | Comma-separated list of allowed emails (optional allowlist) |
| `ALLOWED_EMAIL_DOMAINS`| `""` | Comma-separated list of allowed email domains (e.g., `company.com`) |

### Google OAuth Configuration
1. Go to Google Cloud Console &rarr; **APIs & Services** &rarr; **Credentials**.
2. Create an **OAuth 2.0 Client ID** (Web application).
3. Set **Authorized redirect URIs**:
   - Local: `http://localhost:8000/auth/google/callback`
   - Production / Render: `https://<your-render-domain>/auth/google/callback`
4. Set in `.env`:
   ```env
   GOOGLE_CLIENT_ID=your-google-client-id.apps.googleusercontent.com
   GOOGLE_CLIENT_SECRET=your-google-client-secret
   GOOGLE_REDIRECT_URI=http://localhost:8000/auth/google/callback
   ```

### Email Delivery Configuration (OTP Verification)
For cloud platforms such as Render where outbound SMTP ports (25, 465, 587) are restricted or block TCP sockets with `[Errno 101] Network is unreachable`, use an HTTPS Email API provider (Resend):

```env
# Resend HTTPS API (Recommended for Render & cloud platforms — port 443 HTTPS)
RESEND_API_KEY=your-resend-api-key
RESEND_FROM=TradeOne <onboarding@resend.dev>
```

#### SMTP Email Configuration (Optional fallback for local / non-restricted environments)
```env
SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_USER=your-email@example.com
SMTP_PASSWORD=your-app-password
SMTP_FROM=no-reply@tradeone.example.com
```

---

## 🔄 Account Aggregator (AA) Consent Flow via cURL

TradeOne comes pre-seeded with registered client application credentials:
- **Client ID**: `portfolio-aggregator`
- **Client Secret**: `nd-demo-secret`

### Step 1: Initiate Consent Request
```bash
curl -X POST "http://localhost:8000/aa/v1/consents" \
  -H "Content-Type: application/json" \
  -H "x-client-id: portfolio-aggregator" \
  -H "x-client-secret: nd-demo-secret" \
  -d '{
    "purpose": {"code": "101", "text": "Comprehensive Portfolio Wealth Analysis"},
    "fiTypes": ["EQUITIES", "REIT", "INVIT", "ETF", "BONDS", "MUTUAL_FUNDS"],
    "dataRange": {"from": "2026-01-01", "to": "2026-10-08"},
    "consentDurationDays": 90,
    "fetchFrequency": {"unit": "DAY", "value": 4},
    "customerEmail": "aarav.mehta@example.com",
    "redirectUrl": "http://localhost:8000/aa/callback",
    "webhookUrl": "http://localhost:8000/webhooks/nationaldepo"
  }'
```
**Response:**
```json
{
  "consentHandle": "ch_529bbafb6b15e0a9",
  "status": "PENDING",
  "approvalUrl": "http://localhost:8000/consent/approve?handle=ch_529bbafb6b15e0a9"
}
```

### Step 2: User Approves in Browser
1. Open the `approvalUrl` in your browser.
2. Sign in as Aarav Mehta (or using Email OTP).
3. Select which Demat Accounts to share with the external app.
4. Click **"Approve & Share Data"**. TradeOne signs the consent artefact and sends a `CONSENT_APPROVED` webhook.

### Step 3: Check Consent Status & Signed Artefact
```bash
curl -X GET "http://localhost:8000/aa/v1/consents/ch_529bbafb6b15e0a9" \
  -H "x-client-id: portfolio-aggregator" \
  -H "x-client-secret: nd-demo-secret"
```
**Response:**
```json
{
  "consentHandle": "ch_529bbafb6b15e0a9",
  "consentId": "cns_35e80dc9a76472f1",
  "status": "ACTIVE",
  "artefact": { ... },
  "signature": "321356...d83a"
}
```

### Step 4: Create Data Session
```bash
curl -X POST "http://localhost:8000/aa/v1/sessions" \
  -H "Content-Type: application/json" \
  -H "x-client-id: portfolio-aggregator" \
  -H "x-client-secret: nd-demo-secret" \
  -d '{
    "consentId": "cns_35e80dc9a76472f1"
  }'
```
**Response:**
```json
{
  "sessionId": "ses_7f21",
  "status": "PENDING"
}
```

### Step 5: Fetch Financial Information (FI) Data
```bash
curl -X GET "http://localhost:8000/aa/v1/sessions/ses_7f21/data" \
  -H "x-client-id: portfolio-aggregator" \
  -H "x-client-secret: nd-demo-secret"
```
- Returns `202 Accepted` with `{"status": "PENDING"}` while preparation simulated delay runs.
- Returns `200 OK` when ready with nested AA structure:
```json
{
  "sessionId": "ses_7f21",
  "consentId": "cns_35e80dc9a76472f1",
  "generatedAt": "2026-10-08T18:30:00+00:00",
  "customer": {
    "email": "aarav.mehta@example.com",
    "name": "Aarav Mehta",
    "maskedPan": "ABCXX1234X"
  },
  "accounts": [
    {
      "fiType": "EQUITIES",
      "linkedAccRef": "da_5521",
      "maskedAccNumber": "XXXX5521",
      "dp": {
        "name": "NiftyTrade Securities",
        "dpId": "IN300001",
        "depository": "TradeOne"
      },
      "profile": {
        "holders": [{"name": "Aarav Mehta", "type": "PRIMARY"}],
        "nomineeStatus": "REGISTERED"
      },
      "summary": {
        "currentValue": "177435.50",
        "investmentValue": "163200.00",
        "holdingCount": 4
      },
      "holdings": [
        {
          "isin": "INE002A01018",
          "issuerName": "Reliance Industries Limited",
          "isinDescription": "EQUITY SHARES",
          "units": "15.000",
          "lastTradedPrice": "2842.50",
          "avgPrice": "2450.50",
          "lockInUnits": "0.000",
          "pledgedUnits": "0.000",
          "assetClass": "EQUITY"
        }
      ]
    }
  ]
}
```

### Step 6: Revoke Consent
```bash
curl -X POST "http://localhost:8000/aa/v1/consents/cns_35e80dc9a76472f1/revoke" \
  -H "x-client-id: portfolio-aggregator" \
  -H "x-client-secret: nd-demo-secret"
```

---

## 📡 Internal Broker Ingestion Endpoint

Mock brokers call this endpoint after trade executions to sync settlement into the investor's demat account at TradeOne:

```bash
curl -X POST "http://localhost:8000/internal/v1/ingest/holdings" \
  -H "Content-Type: application/json" \
  -H "x-api-key: nd-ingest-secret-key-2026" \
  -d '{
    "email": "aarav.mehta@example.com",
    "dpName": "NiftyTrade Securities",
    "dpId": "IN300001",
    "maskedAccNumber": "XXXX5521",
    "isin": "INE002A01018",
    "quantityDelta": 5.0,
    "avgPrice": 2840.00,
    "reason": "BUY_SETTLEMENT"
  }'
```
**Behaviour:**
1. Auto-provisions user and demat account if not already present.
2. Updates holdings quantity (strictly rejects transactions that would drop free units below 0).
3. Records transaction debit/credit in the statement ledger.
4. Dispatches `HOLDINGS_CHANGED` webhooks signed with HMAC-SHA256 to all active consents for that user.

---

---

## 🤝 Shared Identity & Deterministic Profiles (`app/shared_identity.py`)

All 4 sibling sandbox sites (**NiftyTrade**, **BharatInvest**, **BondBazaar**, and the hub **TradeOne**) use an identical, deterministic identity generation algorithm derived from `HMAC-SHA256(SHARED_IDENTITY_SALT, normalize_email(email))`. When an investor logs in with the same Google or email credentials on any platform, their profile, PAN, mobile, DOB, and client code suffix match consistently across the entire ecosystem.

### Deterministic Generation Algorithm (Copy verbatim across projects)

```python
import os
import hmac
import hashlib
from typing import Optional, Dict

def normalize_email(email: Optional[str]) -> str:
    """Normalize email: strip leading/trailing whitespace and lowercase."""
    if not email:
        return ""
    return email.strip().lower()

def generate_identity(email: str, salt: Optional[str] = None) -> Dict[str, str]:
    """Deterministically derive identity fields from an email address."""
    norm_email = normalize_email(email)
    secret_salt = salt or os.getenv("SHARED_IDENTITY_SALT", "tradeone-shared-identity-salt-2026")

    # 64-char hex digest
    h = hmac.new(secret_salt.encode("utf-8"), norm_email.encode("utf-8"), hashlib.sha256).hexdigest()

    # 1. Full name fallback
    local_part = norm_email.split("@")[0] if "@" in norm_email else norm_email
    clean_local = local_part.replace(".", " ").replace("_", " ").replace("-", " ")
    full_name_fallback = " ".join([w.capitalize() for w in clean_local.split() if w]) or "Investor User"

    # 2. Fake masked PAN: ABCXX1234X (never looks like real PAN/Aadhaar)
    letters = "ABCDEFGHJKLMNPQRSTUVWXYZ"
    c1, c2, c3 = letters[int(h[0:2], 16) % len(letters)], letters[int(h[2:4], 16) % len(letters)], letters[int(h[4:6], 16) % len(letters)]
    d1, d2, d3, d4 = str(int(h[6:8], 16) % 10), str(int(h[8:10], 16) % 10), str(int(h[10:12], 16) % 10), str(int(h[12:14], 16) % 10)
    masked_pan = f"{c1}{c2}{c3}XX{d1}{d2}{d3}{d4}X"

    # 3. Mobile: 10-digit number starting with 9
    mobile_val = int(h[14:24], 16) % 1_000_000_000
    mobile = f"9{mobile_val:09d}"

    # 4. DOB: DDMM only for CAS PDF passwords
    day = (int(h[24:26], 16) % 28) + 1
    month = (int(h[26:28], 16) % 12) + 1
    dob = f"{day:02d}{month:02d}"

    # 5. Address city & nominee
    cities = ["Mumbai", "Bengaluru", "Delhi", "Pune", "Hyderabad", "Chennai", "Ahmedabad", "Kolkata", "Jaipur", "Surat"]
    address_city = cities[int(h[28:30], 16) % len(cities)]
    nominees = ["Ananya Sharma", "Karthik Verma", "Rohan Patel", "Sneha Iyer", "Aditya Joshi", "Pooja Reddy", "Vikram Malhotra", "Neha Gupta"]
    nominee_name = nominees[int(h[30:32], 16) % len(nominees)]

    # 6. Client code suffix (6 hex characters)
    client_code_suffix = h[32:38].upper()

    return {
        "full_name_fallback": full_name_fallback,
        "masked_pan": masked_pan,
        "mobile": mobile,
        "dob": dob,
        "address_city": address_city,
        "nominee_name": nominee_name,
        "client_code_suffix": client_code_suffix
    }
```

---

## 🛡️ Trusted Server-to-Server Internal Endpoints (`/internal/v1/*`)

When `INTERNAL_API_ENABLED=true`, trusted sibling servers can communicate securely over server-to-server endpoints.

### Authentication & Security
- **Header**: `x-internal-key: <INTERNAL_API_KEY>`
- **Constant-Time Verification**: Uses `secrets.compare_digest` to prevent timing attacks.
- **Key Safety**: The internal key is never printed or written to application logs.
- **Rate Limit**: 60 requests/minute per client IP (returns `HTTP 429 RATE_LIMIT_EXCEEDED` if exceeded).
- **Unauthorized Requests**: Reject with `HTTP 401 UNAUTHORIZED` JSON error.

### 1. Provision User (Idempotent)
Finds existing user by normalized email or creates them with deterministic identity, starter portfolio, and ₹10,00,000 wallet balance.
```bash
curl -X POST "http://localhost:8000/internal/v1/users/provision" \
  -H "Content-Type: application/json" \
  -H "x-internal-key: tradeone-internal-key-2026" \
  -d '{
    "email": "kavita.deshmukh@example.com",
    "full_name": "Kavita Deshmukh"
  }'
```
**Response (`HTTP 200`):**
```json
{
  "status": "CREATED",
  "client_code": "1208160012345678",
  "email": "kavita.deshmukh@example.com"
}
```

### 2. Get User Profile
Returns investor KYC profile with normalized email, client code (BO ID), masked PAN, demat number, and DP info.
```bash
curl -X GET "http://localhost:8000/internal/v1/users/kavita.deshmukh@example.com/profile" \
  -H "x-internal-key: tradeone-internal-key-2026"
```
**Response (`HTTP 200`):**
```json
{
  "name": "Kavita Deshmukh",
  "email": "kavita.deshmukh@example.com",
  "client_code": "1208160012345678",
  "masked_pan": "ABCXX1234X",
  "masked_demat_number": "XXXX5521",
  "dp_name": "BharatInvest Securities",
  "dp_id": "IN300002",
  "mobile": "9876543210"
}
```

### 3. Get User Holdings
Returns the EXACT same JSON structure, field names, number formats, and pagination as the public holdings endpoint (`/api/v1/holdings`), allowing a single adapter to parse both.
```bash
curl -X GET "http://localhost:8000/internal/v1/users/kavita.deshmukh@example.com/holdings?page=1&page_size=50" \
  -H "x-internal-key: tradeone-internal-key-2026"
```
**Response (`HTTP 200`):**
```json
{
  "total": 7,
  "page": 1,
  "page_size": 50,
  "total_pages": 1,
  "holdings": [
    {
      "id": 101,
      "demat_account_id": "da_b_a1b2c3",
      "dp_name": "BharatInvest Securities",
      "masked_account_number": "XXXX8842",
      "isin": "INE002A01018",
      "symbol": "RELIANCE",
      "security_name": "Reliance Industries Limited",
      "asset_class": "EQUITY",
      "free_units": 24.0,
      "pledged_units": 0.0,
      "locked_units": 0.0,
      "total_units": 24.0,
      "last_price": 2842.5,
      "avg_price": 2728.8,
      "current_value": 68220.0,
      "investment_value": 65491.2,
      "pnl": 2728.8,
      "pnl_pct": 4.17
    }
  ]
}
```

### 4. Get User Summary
Returns portfolio totals (invested, current value, day change, total PnL) for fast cross-broker reconciliation.
```bash
curl -X GET "http://localhost:8000/internal/v1/users/kavita.deshmukh@example.com/summary" \
  -H "x-internal-key: tradeone-internal-key-2026"
```
**Response (`HTTP 200`):**
```json
{
  "email": "kavita.deshmukh@example.com",
  "invested": 145230.50,
  "current_value": 152840.00,
  "day_change": 1280.20,
  "day_change_pct": 0.84,
  "total_pnl": 7609.50,
  "total_pnl_pct": 5.24
}
```

### 5. Cross-Broker Event Notification (`POST /internal/v1/events`)
Receives real-time "please re-pull" notifications from sibling brokers when investor holdings change.
```bash
curl -X POST "http://localhost:8000/internal/v1/events" \
  -H "Content-Type: application/json" \
  -H "x-internal-key: tradeone-internal-key-2026" \
  -d '{
    "provider": "b",
    "email": "kavita.deshmukh@example.com",
    "event": "HOLDINGS_CHANGED",
    "occurredAt": "2026-10-09T01:15:00Z"
  }'
```

---

## 📬 Outbox Pattern & Background Event Dispatcher

To notify TradeOne or sibling nodes whenever holdings change (executed trade, SIP, allotment, settlement, admin edit), TradeOne implements a transactional **Outbox Pattern**:
1. Every holdings modification enqueues a lightweight event into the `outbox_events` table within the database transaction.
2. The transaction never blocks or fails if notification dispatch is delayed or unavailable.
3. The background dispatcher delivers events to `{TRADEONE_URL}/internal/v1/events` with header `x-internal-key: <INTERNAL_API_KEY>`.
4. Failed deliveries are retried using **exponential backoff** (`2^attempts` seconds) up to **10 attempts**.
5. If `TRADEONE_URL` is unset, dispatch operations safely skip without error.

---

## 🧪 Running Automated Tests

Run the full pytest suite:
```bash
.\.venv\Scripts\python -m pytest -v
```

All **17 test suites** verify:
- **Shared Identity**: Identical output for identical email, distinct output for different emails, valid PAN, mobile, and DDMM DOB formatting.
- **Email Normalization**: Casing and leading/trailing whitespace normalization across storage, comparisons, and outputs.
- **Provisioning Idempotency**: `POST /internal/v1/users/provision` returns `CREATED` on first invocation and `EXISTS` on subsequent calls without duplicating records or reset wallet funds.
- **Deterministic Starter Portfolio**: Same email generates identical holdings even after database reset; quantities and prices deviate within +/-15% of market price; cross-broker overlap verified on RELIANCE, TCS, and HDFCBANK.
- **Internal Authentication**: Rejects missing or invalid `x-internal-key` with `HTTP 401 UNAUTHORIZED`; handles missing users with `HTTP 404 USER_NOT_FOUND`.
- **Public & Internal Format Parity**: `GET /internal/v1/users/{email}/holdings` returns identical field names, pagination, and JSON structure as `GET /api/v1/holdings`.
- **Same-Account Google Sign-in**: Google OAuth links seamlessly to pre-provisioned user accounts via normalized email, preserving demat accounts, starter portfolios, and holdings.
- **Outbox Worker & Retries**: Lightweight event enqueueing, exponential backoff retries, and non-blocking execution.
- **AA Consent Lifecycle**: Creation, approval, polling, session token generation, daily fetch limits, and cryptographic HMAC-SHA256 signatures.
- **CAS PDF Generation**: Password-protected consolidated account statement generation and PDF decryption validation.

---

## 🚢 Deployment on Render

This repository is ready to deploy directly on Render:
1. Push this repository to GitHub.
2. In Render Dashboard, click **New Web Service** &rarr; Connect repository.
3. Configure settings:
   - **Environment**: Python (or Docker)
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
4. Add environment variables from `.env.example`.
5. TradeOne will be live with full HTTPS support, automatic reverse proxy detection, and persistent SQLite database.
