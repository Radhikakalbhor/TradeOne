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

### SMTP Email Configuration (Optional)
```env
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your-email@gmail.com
SMTP_PASSWORD=your-app-password
SMTP_FROM=no-reply@nationaldepo.sim
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

## 🧪 Running Automated Tests

Run the full pytest suite:
```bash
.\.venv\Scripts\python -m pytest -v
```

All 9 test suites verify:
- OTP generation, single-use, expiry, and 5-attempt brute-force lockout.
- User matching and account linking by lowercase email.
- Allowed email and allowed domain restrictions.
- End-to-end AA Consent lifecycle (create, approve, poll, revoke, pause).
- Daily fetch frequency limit enforcement (`HTTP 429 FETCH_LIMIT_EXCEEDED`).
- Account scoping (session data only contains user-selected demat accounts).
- HMAC-SHA256 webhook signatures and headers (`X-ND-Signature`).
- Broker Ingestion endpoint validation and negative quantity protection.
- Password-protected CAS PDF generation and decryption validation via `pypdf`.

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
