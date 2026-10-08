import httpx
import time
import urllib.parse

BASE = "http://localhost:8000"
client = httpx.Client(base_url=BASE, timeout=15.0)

print("[1] Creating new Consent via External App API...")
headers = {
    "x-client-id": "portfolio-aggregator",
    "x-client-secret": "nd-demo-secret"
}
consent_payload = {
    "purpose": {"code": "101", "text": "Wealth management portfolio aggregator"},
    "fiTypes": ["EQUITIES", "REIT", "INVIT", "ETF", "BONDS", "MUTUAL_FUNDS"],
    "dataRange": {"from": "2026-01-01", "to": "2026-10-08"},
    "consentDurationDays": 90,
    "fetchFrequency": {"unit": "DAY", "value": 4},
    "customerEmail": "aarav.mehta@example.com",
    "redirectUrl": "http://localhost:8000/consents",
    "webhookUrl": "http://localhost:8000/health"
}
res_c = client.post("/aa/v1/consents", json=consent_payload, headers=headers)
assert res_c.status_code == 200, f"Failed creating consent: {res_c.text}"
c_json = res_c.json()
handle = c_json["consentHandle"]
approval_url = c_json["approvalUrl"]
print(f"Consent Created: Handle={handle}, Status={c_json['status']}")

print("\n[2] Requesting Email OTP for Login...")
res_otp = client.post("/auth/email/request-otp", data={"email": "aarav.mehta@example.com"})
assert res_otp.status_code == 303, f"Expected 303 redirect, got {res_otp.status_code}"
verify_url = res_otp.headers["location"]
query = urllib.parse.urlparse(verify_url).query
params = urllib.parse.parse_qs(query)
otp_code = params["dev_hint"][0]
print(f"Extracted Verification Code: {otp_code}")

print("\n[3] Verifying OTP & Establishing Session...")
res_ver = client.post("/auth/email/verify-otp", data={
    "email": "aarav.mehta@example.com",
    "otp_code": otp_code
})
assert res_ver.status_code == 303
assert "nd_session" in client.cookies, "Missing nd_session cookie"
print("Logged in successfully! Session cookie acquired.")

print("\n[4] User Approves Consent Request (selecting 2 specific Demat Accounts)...")
res_app = client.post("/consent/approve", data={
    "handle": handle,
    "action": "APPROVE",
    "account_ids": ["da_5521", "da_8842"]
})
assert res_app.status_code == 303
print("Consent approved by user! Redirected.")

print("\n[5] Checking Consent Status from External App...")
res_status = client.get(f"/aa/v1/consents/{handle}", headers=headers)
assert res_status.status_code == 200
status_json = res_status.json()
assert status_json["status"] == "ACTIVE"
consent_id = status_json["consentId"]
print(f"Status is ACTIVE! Consent ID: {consent_id}")
print(f"Artefact Signature: {status_json['signature'][:20]}...")

print("\n[6] Initiating Data Session...")
res_ses = client.post("/aa/v1/sessions", json={"consentId": consent_id}, headers=headers)
assert res_ses.status_code == 200
session_id = res_ses.json()["sessionId"]
print(f"Data session created: {session_id} (Status: PENDING)")

print("\n[7] Polling for Data Readiness...")
data_ready = False
for attempt in range(12):
    res_data = client.get(f"/aa/v1/sessions/{session_id}/data", headers=headers)
    if res_data.status_code == 200:
        fi_data = res_data.json()
        print(f"SUCCESS! FI Data Ready (Status 200):")
        print(f"  Customer: {fi_data['customer']['name']} ({fi_data['customer']['maskedPan']})")
        print(f"  Scoped Accounts Count: {len(fi_data['accounts'])}")
        for acc in fi_data['accounts']:
            print(f"    - DP: {acc['dp']['name']} ({acc['maskedAccNumber']}) -> {acc['summary']['holdingCount']} holdings, Value: Rs. {acc['summary']['currentValue']}")
        data_ready = True
        break
    elif res_data.status_code == 202:
        print(f"  Attempt {attempt+1}: 202 Accepted (preparation in progress)...")
        time.sleep(1)
    else:
        print(f"  Unexpected status: {res_data.status_code} - {res_data.text}")
        break

assert data_ready, "Data session did not become ready!"

print("\n[8] Testing Internal Ingest Endpoint (Broker Settlement Sync)...")
ingest_headers = {"x-api-key": "nd-ingest-secret-key-2026"}
res_ingest = client.post("/internal/v1/ingest/holdings", json={
    "email": "aarav.mehta@example.com",
    "dpName": "NiftyTrade Securities",
    "dpId": "IN300001",
    "maskedAccNumber": "XXXX5521",
    "isin": "INE002A01018",
    "quantityDelta": 5.0,
    "avgPrice": 2840.0,
    "reason": "BUY_SETTLEMENT"
}, headers=ingest_headers)
assert res_ingest.status_code == 200
print(f"Ingest successful: {res_ingest.json()}")

print("\n[9] Revoking Consent...")
res_revoke = client.post(f"/aa/v1/consents/{consent_id}/revoke", headers=headers)
assert res_revoke.status_code == 200
print(f"Consent revoked: {res_revoke.json()}")

print("\n[10] Verifying Error after Revocation...")
res_denied = client.post("/aa/v1/sessions", json={"consentId": consent_id}, headers=headers)
assert res_denied.status_code == 403
error_detail = res_denied.json()["detail"]
assert error_detail["code"] == "CONSENT_REVOKED"
print(f"Confirmed 403 Forbidden with code: {error_detail['code']} ({error_detail['message']})")

print("\n=======================================================")
print("ALL 10 END-TO-END WORKFLOW VERIFICATIONS PASSED 100%!")
print("=======================================================")
