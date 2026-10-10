#!/usr/bin/env python3
"""
TradeOne Helper Utility: Acquire Gmail API Refresh Token
==========================================================
This script guides you through a one-time Google OAuth authorization for your
TradeOne sender mailbox (e.g., hacksmiths360@gmail.com) to obtain a long-lived
GMAIL_REFRESH_TOKEN.

Safety & Privacy:
- Never prints or exposes your Client Secret.
- Does not modify your files or commit secrets to Git.
- Requests only the minimum necessary scope: https://www.googleapis.com/auth/gmail.send

Usage:
    python scripts/get_gmail_refresh_token.py
"""

import sys
import os
import secrets
import urllib.parse
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading

# Add parent directory to path to load .env if available
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

try:
    import httpx
except ImportError:
    print("Error: 'httpx' library is required. Install it via: pip install httpx")
    sys.exit(1)

REDIRECT_PORT = 8080
REDIRECT_URI = f"http://127.0.0.1:{REDIRECT_PORT}/callback"
GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.send"

auth_code_holder = {"code": None, "error": None}
server_done = threading.Event()


class OAuthCallbackHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Suppress standard HTTP request logging
        return

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/callback":
            params = urllib.parse.parse_qs(parsed.query)
            if "code" in params:
                auth_code_holder["code"] = params["code"][0]
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                html = """
                <html>
                <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; text-align: center; padding: 48px; background: #0f172a; color: #f8fafc;">
                    <h1 style="color: #10b981;">&#10003; Authorization Successful</h1>
                    <p style="font-size: 16px; color: #cbd5e1;">TradeOne has received the authorization code. You can now close this tab and return to your terminal.</p>
                </body>
                </html>
                """
                self.wfile.write(html.encode("utf-8"))
            else:
                err = params.get("error", ["Unknown error"])[0]
                auth_code_holder["error"] = err
                self.send_response(400)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(f"<h1>Authorization Failed: {err}</h1>".encode("utf-8"))
            server_done.set()
        else:
            self.send_response(404)
            self.end_headers()


def get_client_credentials():
    client_id = os.getenv("GMAIL_CLIENT_ID") or os.getenv("GOOGLE_CLIENT_ID") or ""
    client_secret = os.getenv("GMAIL_CLIENT_SECRET") or os.getenv("GOOGLE_CLIENT_SECRET") or ""

    if not client_id:
        print("\nEnter your Google OAuth Client ID (from Google Cloud Console):")
        client_id = input("Client ID: ").strip()

    if not client_secret:
        import getpass
        print("Enter your Google OAuth Client Secret (input will be hidden):")
        client_secret = getpass.getpass("Client Secret: ").strip()

    if not client_id or not client_secret:
        print("Error: Both Client ID and Client Secret are required.")
        sys.exit(1)

    return client_id, client_secret


def main():
    print("=" * 70)
    print("  TradeOne: Gmail API Sender Authorization Helper")
    print("=" * 70)
    print("\nThis tool will acquire a GMAIL_REFRESH_TOKEN for sending OTP emails.")
    print("Prerequisites in Google Cloud Console:")
    print(f"  1. Ensure the Gmail API is enabled.")
    print(f"  2. In 'APIs & Services > Credentials', edit your OAuth 2.0 Web Client")
    print(f"     and add this Authorized Redirect URI:")
    print(f"       -> {REDIRECT_URI}")
    print("=" * 70)

    client_id, client_secret = get_client_credentials()

    state = secrets.token_urlsafe(16)
    auth_params = {
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": GMAIL_SCOPE,
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    auth_url = f"https://accounts.google.com/o/oauth2/v2/auth?{urllib.parse.urlencode(auth_params)}"

    server = HTTPServer(("127.0.0.1", REDIRECT_PORT), OAuthCallbackHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    print("\nOpening your default browser for authorization...")
    print("If it does not open automatically, visit this URL:\n")
    print(auth_url)
    print("\nWaiting for you to log in with your sender Gmail account (e.g. hacksmiths360@gmail.com)...")

    try:
        webbrowser.open(auth_url)
    except Exception:
        pass

    # Wait for callback
    server_done.wait(timeout=300)
    server.shutdown()

    if not auth_code_holder["code"]:
        print("\n[!] Error: Authorization failed or timed out.")
        if auth_code_holder["error"]:
            print(f"    Google Error: {auth_code_holder['error']}")
        sys.exit(1)

    code = auth_code_holder["code"]
    print("\nAuthorization code received! Exchanging for refresh token...")

    token_url = "https://oauth2.googleapis.com/token"
    token_payload = {
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": REDIRECT_URI,
        "grant_type": "authorization_code"
    }

    with httpx.Client(timeout=15.0) as client:
        resp = client.post(token_url, data=token_payload)

    if resp.status_code != 200:
        print(f"\n[!] Token exchange failed (HTTP {resp.status_code}):")
        print(resp.text)
        sys.exit(1)

    token_data = resp.json()
    refresh_token = token_data.get("refresh_token")

    if not refresh_token:
        print("\n[!] Notice: Google did not return a refresh token.")
        print("    This usually happens if authorization was already previously granted without prompt=consent.")
        print("    Try revoking permissions at https://myaccount.google.com/permissions and running this script again.")
        sys.exit(1)

    print("\n" + "=" * 70)
    print("  SUCCESS! Your Gmail API Refresh Token is ready:")
    print("=" * 70)
    print(f"\nGMAIL_REFRESH_TOKEN={refresh_token}")
    print("\n" + "=" * 70)
    print("  Next Steps for Render Deployment:")
    print("  1. In Render Dashboard > TradeOne Service > Environment, add:")
    print(f"       EMAIL_PROVIDER=gmail")
    print(f"       GMAIL_CLIENT_ID={client_id}")
    print(f"       GMAIL_CLIENT_SECRET=<your client secret>")
    print(f"       GMAIL_REFRESH_TOKEN={refresh_token}")
    print(f"       GMAIL_SENDER=hacksmiths360@gmail.com")
    print("  2. Save Changes (Render will automatically redeploy).")
    print("=" * 70 + "\n")


if __name__ == "__main__":
    main()
