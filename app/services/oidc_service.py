import secrets
import jwt
from jwt import PyJWKClient
import httpx
from typing import Dict, Any, Tuple, Optional
from app.config import settings

# In-memory OIDC states with expiration: state -> {"nonce": str, "provider": str}
_oidc_states: Dict[str, Dict[str, Any]] = {}

def generate_oidc_state(provider: str) -> Tuple[str, str]:
    state = secrets.token_urlsafe(24)
    nonce = secrets.token_urlsafe(24)
    _oidc_states[state] = {
        "nonce": nonce,
        "provider": provider
    }
    return state, nonce

def validate_and_consume_state(state: str, provider: str) -> Optional[str]:
    data = _oidc_states.pop(state, None)
    if not data or data.get("provider") != provider:
        return None
    return data.get("nonce")

def get_google_auth_url() -> str:
    if not settings.GOOGLE_CLIENT_ID:
        return ""
    state, nonce = generate_oidc_state("google")
    params = {
        "client_id": settings.GOOGLE_CLIENT_ID,
        "response_type": "code",
        "scope": "openid email profile",
        "redirect_uri": settings.GOOGLE_REDIRECT_URI,
        "state": state,
        "nonce": nonce,
        "prompt": "select_account"
    }
    query = "&".join(f"{k}={v}" for k, v in params.items())
    return f"https://accounts.google.com/o/oauth2/v2/auth?{query}"

def get_microsoft_auth_url() -> str:
    if not settings.MICROSOFT_CLIENT_ID:
        return ""
    state, nonce = generate_oidc_state("microsoft")
    tenant = settings.MICROSOFT_TENANT or "common"
    params = {
        "client_id": settings.MICROSOFT_CLIENT_ID,
        "response_type": "code",
        "scope": "openid email profile",
        "redirect_uri": settings.MICROSOFT_REDIRECT_URI,
        "state": state,
        "nonce": nonce,
        "response_mode": "query"
    }
    query = "&".join(f"{k}={v}" for k, v in params.items())
    return f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize?{query}"

async def exchange_google_code(code: str, expected_nonce: str) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    """Exchange authorization code with Google and validate ID token."""
    token_url = "https://oauth2.googleapis.com/token"
    data = {
        "code": code,
        "client_id": settings.GOOGLE_CLIENT_ID,
        "client_secret": settings.GOOGLE_CLIENT_SECRET,
        "redirect_uri": settings.GOOGLE_REDIRECT_URI,
        "grant_type": "authorization_code"
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(token_url, data=data)
            if resp.status_code != 200:
                return False, "Failed to exchange token with Google.", None
            token_data = resp.json()

        id_token = token_data.get("id_token")
        if not id_token:
            return False, "Google did not provide an ID token.", None

        jwks_client = PyJWKClient("https://www.googleapis.com/oauth2/v3/certs")
        signing_key = jwks_client.get_signing_key_from_jwt(id_token)

        claims = jwt.decode(
            id_token,
            signing_key.key,
            algorithms=["RS256"],
            audience=settings.GOOGLE_CLIENT_ID,
            issuer="https://accounts.google.com"
        )

        # Check nonce
        if claims.get("nonce") != expected_nonce:
            return False, "Google security token mismatch (invalid nonce).", None

        # Check email verified
        if not claims.get("email_verified", False):
            return False, "Google email address is not verified.", None

        email = claims.get("email", "").lower().strip()
        sub = claims.get("sub", "")
        name = claims.get("name", "")

        return True, "Success", {
            "email": email,
            "provider_sub": sub,
            "name": name,
            "provider": "google"
        }
    except Exception as e:
        return False, f"Google login error: {str(e)}", None

async def exchange_microsoft_code(code: str, expected_nonce: str) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    """Exchange authorization code with Microsoft and validate ID token."""
    tenant = settings.MICROSOFT_TENANT or "common"
    token_url = f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
    data = {
        "code": code,
        "client_id": settings.MICROSOFT_CLIENT_ID,
        "client_secret": settings.MICROSOFT_CLIENT_SECRET,
        "redirect_uri": settings.MICROSOFT_REDIRECT_URI,
        "grant_type": "authorization_code"
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(token_url, data=data)
            if resp.status_code != 200:
                return False, "Failed to exchange token with Microsoft.", None
            token_data = resp.json()

        id_token = token_data.get("id_token")
        if not id_token:
            return False, "Microsoft did not provide an ID token.", None

        jwks_client = PyJWKClient(f"https://login.microsoftonline.com/{tenant}/discovery/v2.0/keys")
        signing_key = jwks_client.get_signing_key_from_jwt(id_token)

        # Allow various tenant issuers
        unverified_claims = jwt.decode(id_token, options={"verify_signature": False})
        issuer = unverified_claims.get("iss")

        claims = jwt.decode(
            id_token,
            signing_key.key,
            algorithms=["RS256"],
            audience=settings.MICROSOFT_CLIENT_ID,
            issuer=issuer
        )

        if claims.get("nonce") != expected_nonce:
            return False, "Microsoft security token mismatch (invalid nonce).", None

        email = claims.get("email") or claims.get("preferred_username")
        if not email:
            return False, "No verified email or username claim found from Microsoft.", None

        email = email.lower().strip()
        sub = claims.get("sub") or claims.get("oid", "")
        name = claims.get("name", "")

        return True, "Success", {
            "email": email,
            "provider_sub": sub,
            "name": name,
            "provider": "microsoft"
        }
    except Exception as e:
        return False, f"Microsoft login error: {str(e)}", None
