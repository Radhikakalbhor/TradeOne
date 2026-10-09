import os
import hmac
import hashlib
from typing import Optional, Dict, Any

def normalize_email(email: Optional[str]) -> str:
    """Normalize email: strip leading/trailing whitespace and convert to lowercase.
    
    Must be used EVERYWHERE an email is stored, compared, or returned.
    """
    if not email:
        return ""
    return email.strip().lower()

def generate_identity(email: str, salt: Optional[str] = None) -> Dict[str, str]:
    """Deterministically generate mock identity fields from an email address.
    
    Derives all fields from HMAC-SHA256(SHARED_IDENTITY_SALT, normalize_email(email)).
    The exact same code produces the identical output across all 4 sibling projects
    (NiftyTrade, BharatInvest, BondBazaar, and TradeOne).
    
    Returns:
        full_name_fallback: Name derived from email local part or deterministic fallback
        masked_pan: Deterministic PAN formatted as ABCXX1234X (never looks like real PAN/Aadhaar)
        mobile: 10-digit phone number starting with 9
        dob: DDMM string for fake CAS PDF passwords
        address_city: City from a fixed list of Indian cities
        nominee_name: Nominee name from a fixed list
        client_code_suffix: 6-character hex string for unique client/demat IDs
    """
    norm_email = normalize_email(email)
    secret_salt = salt or os.getenv("SHARED_IDENTITY_SALT", "tradeone-shared-salt-key-2026")
    
    # 32-byte (64 hex char) digest
    h = hmac.new(secret_salt.encode("utf-8"), norm_email.encode("utf-8"), hashlib.sha256).hexdigest()
    
    # 1. full_name_fallback
    local_part = norm_email.split("@")[0] if "@" in norm_email else norm_email
    clean_local = local_part.replace(".", " ").replace("_", " ").replace("-", " ")
    full_name_fallback = " ".join([word.capitalize() for word in clean_local.split() if word])
    if not full_name_fallback:
        full_name_fallback = "Investor User"
        
    # 2. masked_pan: ABCXX1234X
    # Letters pool (avoiding confusion, clean uppercase A-Z)
    letters = "ABCDEFGHJKLMNPQRSTUVWXYZ"
    c1 = letters[int(h[0:2], 16) % len(letters)]
    c2 = letters[int(h[2:4], 16) % len(letters)]
    c3 = letters[int(h[4:6], 16) % len(letters)]
    d1 = str(int(h[6:8], 16) % 10)
    d2 = str(int(h[8:10], 16) % 10)
    d3 = str(int(h[10:12], 16) % 10)
    d4 = str(int(h[12:14], 16) % 10)
    masked_pan = f"{c1}{c2}{c3}XX{d1}{d2}{d3}{d4}X"
    
    # 3. mobile: 10-digit number starting with 9
    mobile_val = int(h[14:24], 16) % 1_000_000_000
    mobile = f"9{mobile_val:09d}"
    
    # 4. dob: DDMM only (day 1-28, month 1-12)
    day = (int(h[24:26], 16) % 28) + 1
    month = (int(h[26:28], 16) % 12) + 1
    dob = f"{day:02d}{month:02d}"
    
    # 5. address_city
    cities = [
        "Mumbai", "Bengaluru", "Delhi", "Pune", "Hyderabad",
        "Chennai", "Ahmedabad", "Kolkata", "Jaipur", "Surat"
    ]
    address_city = cities[int(h[28:30], 16) % len(cities)]
    
    # 6. nominee_name
    nominees = [
        "Ananya Sharma", "Karthik Verma", "Rohan Patel", "Sneha Iyer",
        "Aditya Joshi", "Pooja Reddy", "Vikram Malhotra", "Neha Gupta"
    ]
    nominee_name = nominees[int(h[30:32], 16) % len(nominees)]
    
    # 7. client_code_suffix (6 hex chars)
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
