import os
import json
import random
from typing import Optional
from datetime import datetime, timezone, timedelta
from app.config import settings
from app.models import (
    User, AuthMethod, DematAccount, Instrument, Holding, Transaction,
    CorporateAction, RegisteredApp, Nominee, AdminSetting
)
from app.security import hash_secret

DEFAULT_INSTRUMENTS = [
    {
        "isin": "INE002A01018",
        "symbol": "RELIANCE",
        "name": "Reliance Industries Limited",
        "isin_description": "EQUITY SHARES",
        "asset_class": "EQUITY",
        "fi_type": "EQUITIES",
        "last_price": 2842.50,
        "prev_close": 2810.00,
        "change_pct": 1.16
    },
    {
        "isin": "INE467B01029",
        "symbol": "TCS",
        "name": "Tata Consultancy Services Ltd",
        "isin_description": "EQUITY SHARES",
        "asset_class": "EQUITY",
        "fi_type": "EQUITIES",
        "last_price": 4120.80,
        "prev_close": 4150.00,
        "change_pct": -0.70
    },
    {
        "isin": "INE009A01021",
        "symbol": "INFY",
        "name": "Infosys Limited",
        "isin_description": "EQUITY SHARES",
        "asset_class": "EQUITY",
        "fi_type": "EQUITIES",
        "last_price": 1895.30,
        "prev_close": 1870.10,
        "change_pct": 1.35
    },
    {
        "isin": "INE040A01034",
        "symbol": "HDFCBANK",
        "name": "HDFC Bank Limited",
        "isin_description": "EQUITY SHARES",
        "asset_class": "EQUITY",
        "fi_type": "EQUITIES",
        "last_price": 1648.75,
        "prev_close": 1635.00,
        "change_pct": 0.84
    },
    {
        "isin": "INE155A01022",
        "symbol": "TATAMOTORS",
        "name": "Tata Motors Limited",
        "isin_description": "EQUITY SHARES",
        "asset_class": "EQUITY",
        "fi_type": "EQUITIES",
        "last_price": 932.40,
        "prev_close": 920.00,
        "change_pct": 1.35
    },
    {
        "isin": "INE041A01026",
        "symbol": "ITC",
        "name": "ITC Limited",
        "isin_description": "EQUITY SHARES",
        "asset_class": "EQUITY",
        "fi_type": "EQUITIES",
        "last_price": 502.15,
        "prev_close": 498.50,
        "change_pct": 0.73
    },
    # REITs
    {
        "isin": "INE041025011",
        "symbol": "EMBASSY",
        "name": "Embassy Office Parks REIT",
        "isin_description": "REIT UNITS",
        "asset_class": "REIT",
        "fi_type": "REIT",
        "last_price": 384.20,
        "prev_close": 381.00,
        "change_pct": 0.84
    },
    {
        "isin": "INE0BW225017",
        "symbol": "MINDSPACE",
        "name": "Mindspace Business Parks REIT",
        "isin_description": "REIT UNITS",
        "asset_class": "REIT",
        "fi_type": "REIT",
        "last_price": 342.50,
        "prev_close": 340.00,
        "change_pct": 0.74
    },
    # InvITs
    {
        "isin": "INE002S25010",
        "symbol": "PGINVIT",
        "name": "POWERGRID Infrastructure Investment Trust",
        "isin_description": "INVIT UNITS",
        "asset_class": "INVIT",
        "fi_type": "INVIT",
        "last_price": 98.40,
        "prev_close": 97.90,
        "change_pct": 0.51
    },
    # ETFs
    {
        "isin": "INF204KB14I2",
        "symbol": "NIFTYBEES",
        "name": "Nippon India Nifty 50 BeES ETF",
        "isin_description": "ETF UNITS",
        "asset_class": "ETF",
        "fi_type": "ETF",
        "last_price": 272.10,
        "prev_close": 270.80,
        "change_pct": 0.48
    },
    {
        "isin": "INF204KB17H7",
        "symbol": "GOLDBEES",
        "name": "Nippon India ETF Gold BeES",
        "isin_description": "ETF UNITS",
        "asset_class": "ETF",
        "fi_type": "ETF",
        "last_price": 68.90,
        "prev_close": 68.20,
        "change_pct": 1.03
    },
    # Bonds
    {
        "isin": "IN0020230085",
        "symbol": "GS2033-718",
        "name": "Government of India 7.18% 2033 GS",
        "isin_description": "GOVERNMENT BOND",
        "asset_class": "BOND",
        "fi_type": "BONDS",
        "last_price": 101.40,
        "prev_close": 101.20,
        "change_pct": 0.20
    },
    {
        "isin": "INE906H07788",
        "symbol": "NHAI-2035",
        "name": "NHAI 7.60% Tax Free Bond 2035",
        "isin_description": "TAX FREE INFRA BOND",
        "asset_class": "BOND",
        "fi_type": "BONDS",
        "last_price": 116.80,
        "prev_close": 116.50,
        "change_pct": 0.26
    },
    # Mutual Funds
    {
        "isin": "INF209K01168",
        "symbol": "PPFAS-FLEXI",
        "name": "Parag Parikh Flexi Cap Fund - Direct Growth",
        "isin_description": "MUTUAL FUND UNITS",
        "asset_class": "MUTUAL_FUND",
        "fi_type": "MUTUAL_FUNDS",
        "last_price": 78.45,
        "prev_close": 78.10,
        "change_pct": 0.45
    }
]

DEFAULT_USERS = [
    {
        "email": "aarav.mehta@example.com",
        "name": "Aarav Mehta",
        "bo_id": "1208160012345678",
        "masked_pan": "ABCXX1234X",
        "dob": "15081992",  # DDMM: 1508
        "mobile": "+91 9876543210"
    },
    {
        "email": "priya.nair@example.com",
        "name": "Priya Nair",
        "bo_id": "1208160087654321",
        "masked_pan": "XYZXX9876Y",
        "dob": "22041995",  # DDMM: 2204
        "mobile": "+91 9812345678"
    }
]

def ensure_shared_files():
    """Ensure instruments_shared.json and users_shared.json exist."""
    # Check current directory and parent
    paths_to_check = [
        "instruments_shared.json",
        os.path.join("..", "instruments_shared.json")
    ]
    instruments_file = None
    for p in paths_to_check:
        if os.path.exists(p):
            instruments_file = p
            break
            
    if not instruments_file:
        instruments_file = "instruments_shared.json"
        with open(instruments_file, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_INSTRUMENTS, f, indent=2)

    user_paths_to_check = [
        "users_shared.json",
        os.path.join("..", "users_shared.json")
    ]
    users_file = None
    for p in user_paths_to_check:
        if os.path.exists(p):
            users_file = p
            break
            
    if not users_file:
        users_file = "users_shared.json"
        with open(users_file, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_USERS, f, indent=2)

    return instruments_file, users_file

def load_or_create_shared_data():
    instr_path, user_path = ensure_shared_files()
    
    with open(instr_path, "r", encoding="utf-8") as f:
        instruments = json.load(f)
        
    with open(user_path, "r", encoding="utf-8") as f:
        users = json.load(f)

    return instruments, users

def seed_database(db):
    """Seed the database with instruments, demo users, accounts, holdings, and transactions."""
    instruments_data, users_data = load_or_create_shared_data()

    # 1. Seed Registered App
    app = db.query(RegisteredApp).filter(RegisteredApp.client_id == "portfolio-aggregator").first()
    if not app:
        app = RegisteredApp(
            client_id="portfolio-aggregator",
            client_secret_hash=hash_secret("nd-demo-secret"),
            name="Portfolio Aggregator Demo",
            allowed_redirect_uris="*",
            allowed_webhook_uris="*"
        )
        db.add(app)
        db.commit()

    # 2. Seed Admin Settings
    default_settings = {
        "simulate_outage": "false",
        "slow_mode": "false",
        "data_prep_delay": "4",
        "fail_next_session": "false"
    }
    for k, v in default_settings.items():
        if not db.query(AdminSetting).filter(AdminSetting.key == k).first():
            db.add(AdminSetting(key=k, value=v))
    db.commit()

    # 3. Seed Instruments
    for item in instruments_data:
        existing = db.query(Instrument).filter(Instrument.isin == item["isin"]).first()
        if not existing:
            instr = Instrument(**item)
            db.add(instr)
    db.commit()

    # 4. Seed Corporate Actions
    corp_actions = [
        {
            "isin": "INE002A01018",
            "action_type": "DIVIDEND",
            "record_date": "2026-08-19",
            "ratio_or_amount": "₹10.00 per share",
            "description": "Final Dividend for FY25-26"
        },
        {
            "isin": "INE002A01018",
            "action_type": "BONUS",
            "record_date": "2026-04-10",
            "ratio_or_amount": "1:1 Bonus Issue",
            "description": "1 Bonus share for every 1 share held"
        },
        {
            "isin": "INE467B01029",
            "action_type": "DIVIDEND",
            "record_date": "2026-07-16",
            "ratio_or_amount": "₹28.00 per share",
            "description": "Interim Dividend"
        },
        {
            "isin": "INE041025011",
            "action_type": "DIVIDEND",
            "record_date": "2026-06-30",
            "ratio_or_amount": "₹5.60 per unit",
            "description": "Quarterly Distribution (Income Tax Free)"
        },
        {
            "isin": "INE155A01022",
            "action_type": "SPLIT",
            "record_date": "2026-03-15",
            "ratio_or_amount": "1:2 Stock Split",
            "description": "Sub-division of equity shares from FV ₹2 to ₹1"
        }
    ]
    for ca in corp_actions:
        existing_ca = db.query(CorporateAction).filter(
            CorporateAction.isin == ca["isin"],
            CorporateAction.record_date == ca["record_date"],
            CorporateAction.action_type == ca["action_type"]
        ).first()
        if not existing_ca:
            db.add(CorporateAction(**ca))
    db.commit()

    # 5. Seed Users
    for u in users_data:
        email = u["email"].lower()
        existing_user = db.query(User).filter(User.email == email).first()
        if not existing_user:
            user = User(
                email=email,
                name=u["name"],
                bo_id=u.get("bo_id", f"12081600{random.randint(10000000, 99999999)}"),
                masked_pan=u.get("masked_pan", "ABCXX1234X"),
                dob=u.get("dob", "15081992"),
                mobile=u.get("mobile", "+91 9876543210")
            )
            db.add(user)
            db.commit()
            db.refresh(user)

            # Add default auth methods
            db.add(AuthMethod(user_id=user.id, provider="email", email=email))
            db.add(AuthMethod(user_id=user.id, provider="google", provider_sub=f"google_{user.id}", email=email))
            db.add(AuthMethod(user_id=user.id, provider="microsoft", provider_sub=f"ms_{user.id}", email=email))

            # Add default Nominee
            db.add(Nominee(
                user_id=user.id,
                name="Ananya Mehta" if "aarav" in email else "Karthik Nair",
                relationship_type="SPOUSE",
                percentage=100,
                dob="1994-06-15"
            ))
            db.commit()

            # Seed Accounts & Holdings
            if email == "aarav.mehta@example.com":
                seed_aarav_accounts(db, user)
            elif email == "priya.nair@example.com":
                seed_priya_accounts(db, user)

def seed_aarav_accounts(db, user: User):
    """Aarav Mehta: 3 demat accounts, overlapping stocks (RELIANCE in 2), REIT, Bond, 6mo history."""
    now = datetime.now(timezone.utc)
    
    # Account 1: NiftyTrade Securities
    acc1 = DematAccount(
        id="da_5521",
        user_id=user.id,
        dp_name="NiftyTrade Securities",
        dp_id="IN300001",
        account_number="1208160000015521",
        masked_account_number="XXXX5521",
        account_type="INDIVIDUAL",
        status="ACTIVE",
        opened_date="2021-04-12",
        nominee_status="REGISTERED"
    )
    db.add(acc1)

    # Account 2: BharatInvest Securities
    acc2 = DematAccount(
        id="da_8842",
        user_id=user.id,
        dp_name="BharatInvest Securities",
        dp_id="IN300002",
        account_number="1208160000028842",
        masked_account_number="XXXX8842",
        account_type="INDIVIDUAL",
        status="ACTIVE",
        opened_date="2022-09-18",
        nominee_status="REGISTERED"
    )
    db.add(acc2)

    # Account 3: BondBazaar Depository Services
    acc3 = DematAccount(
        id="da_1920",
        user_id=user.id,
        dp_name="BondBazaar Depository Services",
        dp_id="IN300003",
        account_number="1208160000031920",
        masked_account_number="XXXX1920",
        account_type="INDIVIDUAL",
        status="ACTIVE",
        opened_date="2023-01-05",
        nominee_status="REGISTERED"
    )
    db.add(acc3)
    db.commit()

    # Holdings for Acc 1 (NiftyTrade)
    # Reliance (15), TCS (10), Infosys (25), HDFC Bank (30)
    db.add(Holding(demat_account_id=acc1.id, isin="INE002A01018", free_units=15.0, avg_price=2450.50))
    db.add(Holding(demat_account_id=acc1.id, isin="INE467B01029", free_units=10.0, avg_price=3850.00))
    db.add(Holding(demat_account_id=acc1.id, isin="INE009A01021", free_units=25.0, avg_price=1620.00))
    db.add(Holding(demat_account_id=acc1.id, isin="INE040A01034", free_units=30.0, avg_price=1580.00))

    # Holdings for Acc 2 (BharatInvest)
    # Overlapping RELIANCE (10), Tata Motors (40), ITC (50), Embassy REIT (50)
    db.add(Holding(demat_account_id=acc2.id, isin="INE002A01018", free_units=10.0, avg_price=2520.00))
    db.add(Holding(demat_account_id=acc2.id, isin="INE155A01022", free_units=40.0, avg_price=840.00))
    db.add(Holding(demat_account_id=acc2.id, isin="INE041A01026", free_units=50.0, avg_price=460.00))
    db.add(Holding(demat_account_id=acc2.id, isin="INE041025011", free_units=50.0, avg_price=365.00))

    # Holdings for Acc 3 (BondBazaar)
    # GOI 7.18% 2033 Bond (200), NHAI 2035 Bond (100)
    db.add(Holding(demat_account_id=acc3.id, isin="IN0020230085", free_units=200.0, avg_price=100.50))
    db.add(Holding(demat_account_id=acc3.id, isin="INE906H07788", free_units=100.0, avg_price=115.00))
    db.commit()

    # 6 Months of Transaction Statement History
    transactions = [
        # 5 months ago: buy Reliance 15 in Acc 1
        Transaction(
            demat_account_id=acc1.id,
            isin="INE002A01018",
            trans_date=now - timedelta(days=150),
            quantity=15.0,
            price=2450.50,
            trans_type="BUY_SETTLEMENT",
            reference_id="TXN-SETTL-98124",
            description="NSE Settlement Buy 15 units RELIANCE"
        ),
        # 4 months ago: buy TCS 10 in Acc 1
        Transaction(
            demat_account_id=acc1.id,
            isin="INE467B01029",
            trans_date=now - timedelta(days=120),
            quantity=10.0,
            price=3850.00,
            trans_type="BUY_SETTLEMENT",
            reference_id="TXN-SETTL-99432",
            description="NSE Settlement Buy 10 units TCS"
        ),
        # 3.5 months ago: bonus Reliance in Acc 1 (1:1 simulated or corporate action)
        Transaction(
            demat_account_id=acc1.id,
            isin="INE002A01018",
            trans_date=now - timedelta(days=105),
            quantity=0.0,
            price=0.0,
            trans_type="CORPORATE_ACTION",
            reference_id="CA-DIV-44312",
            description="Corporate Action: Dividend credited to Bank"
        ),
        # 3 months ago: buy Reliance 10 in Acc 2 (overlap)
        Transaction(
            demat_account_id=acc2.id,
            isin="INE002A01018",
            trans_date=now - timedelta(days=90),
            quantity=10.0,
            price=2520.00,
            trans_type="BUY_SETTLEMENT",
            reference_id="TXN-SETTL-10142",
            description="BSE Settlement Buy 10 units RELIANCE"
        ),
        # 2.5 months ago: buy Embassy REIT 50 in Acc 2
        Transaction(
            demat_account_id=acc2.id,
            isin="INE041025011",
            trans_date=now - timedelta(days=75),
            quantity=50.0,
            price=365.00,
            trans_type="BUY_SETTLEMENT",
            reference_id="TXN-SETTL-10499",
            description="NSE Settlement Buy 50 units EMBASSY REIT"
        ),
        # 2 months ago: buy Bond 200 in Acc 3
        Transaction(
            demat_account_id=acc3.id,
            isin="IN0020230085",
            trans_date=now - timedelta(days=60),
            quantity=200.0,
            price=100.50,
            trans_type="BUY_SETTLEMENT",
            reference_id="TXN-SETTL-10901",
            description="CCIL Debt Settlement Buy 200 units GOI 7.18% 2033"
        ),
        # 1 month ago: buy NHAI 100 in Acc 3
        Transaction(
            demat_account_id=acc3.id,
            isin="INE906H07788",
            trans_date=now - timedelta(days=30),
            quantity=100.0,
            price=115.00,
            trans_type="BUY_SETTLEMENT",
            reference_id="TXN-SETTL-11204",
            description="BSE Settlement Buy 100 units NHAI 2035"
        ),
        # 10 days ago: off-market transfer of 5 units Infosys
        Transaction(
            demat_account_id=acc1.id,
            isin="INE009A01021",
            trans_date=now - timedelta(days=10),
            quantity=25.0,
            price=1620.00,
            trans_type="BUY_SETTLEMENT",
            reference_id="TXN-SETTL-12500",
            description="NSE Settlement Buy 25 units INFY"
        )
    ]
    for txn in transactions:
        db.add(txn)
    db.commit()

def seed_priya_accounts(db, user: User):
    """Priya Nair: 2 demat accounts, one InvIT and a gold ETF."""
    now = datetime.now(timezone.utc)

    # Account 1: NiftyTrade Securities
    acc1 = DematAccount(
        id="da_7714",
        user_id=user.id,
        dp_name="NiftyTrade Securities",
        dp_id="IN300001",
        account_number="1208160000047714",
        masked_account_number="XXXX7714",
        account_type="INDIVIDUAL",
        status="ACTIVE",
        opened_date="2022-03-10",
        nominee_status="REGISTERED"
    )
    db.add(acc1)

    # Account 2: BharatInvest Securities
    acc2 = DematAccount(
        id="da_3391",
        user_id=user.id,
        dp_name="BharatInvest Securities",
        dp_id="IN300002",
        account_number="1208160000053391",
        masked_account_number="XXXX3391",
        account_type="INDIVIDUAL",
        status="ACTIVE",
        opened_date="2023-08-20",
        nominee_status="REGISTERED"
    )
    db.add(acc2)
    db.commit()

    # Holdings
    # Acc 1: PGINVIT (150 units), NIFTYBEES (80 units)
    db.add(Holding(demat_account_id=acc1.id, isin="INE002S25010", free_units=150.0, avg_price=95.00))
    db.add(Holding(demat_account_id=acc1.id, isin="INF204KB14I2", free_units=80.0, avg_price=260.00))

    # Acc 2: GOLDBEES (200 units), PPFAS-FLEXI (100 units)
    db.add(Holding(demat_account_id=acc2.id, isin="INF204KB17H7", free_units=200.0, avg_price=64.50))
    db.add(Holding(demat_account_id=acc2.id, isin="INF209K01168", free_units=100.0, avg_price=72.00))
    db.commit()

    # Transactions
    txns = [
        Transaction(
            demat_account_id=acc1.id,
            isin="INE002S25010",
            trans_date=now - timedelta(days=90),
            quantity=150.0,
            price=95.00,
            trans_type="BUY_SETTLEMENT",
            reference_id="TXN-PRIYA-001",
            description="NSE Settlement Buy 150 units PGINVIT"
        ),
        Transaction(
            demat_account_id=acc2.id,
            isin="INF204KB17H7",
            trans_date=now - timedelta(days=45),
            quantity=200.0,
            price=64.50,
            trans_type="BUY_SETTLEMENT",
            reference_id="TXN-PRIYA-002",
            description="NSE Settlement Buy 200 units GOLDBEES"
        )
    ]
    for t in txns:
        db.add(t)
    db.commit()

def provision_new_user(db, email: str, name: Optional[str] = None, provider: str = "email") -> User:
    """Auto-provision a new user on first login."""
    email = email.lower().strip()
    user = db.query(User).filter(User.email == email).first()
    if user:
        return user

    # Generate 16-digit BO ID
    bo_id = f"12081600{random.randint(10000000, 99999999)}"
    # Generate fake masked PAN: e.g. "ABCXX4589X"
    letters = "ABCDEFGHJKLMNPQRSTUVWXYZ"
    prefix = "".join(random.choices(letters, k=3))
    digits = f"{random.randint(1000, 9999)}"
    suffix = random.choice(letters)
    masked_pan = f"{prefix}XX{digits}{suffix}"
    
    # Fake mobile
    mobile = f"+91 98{random.randint(10000000, 99999999)}"
    
    if not name:
        name = email.split("@")[0].replace(".", " ").title()

    user = User(
        email=email,
        name=name,
        bo_id=bo_id,
        masked_pan=masked_pan,
        dob="15081992",
        mobile=mobile
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    # Auth method
    db.add(AuthMethod(user_id=user.id, provider=provider, email=email))
    # Nominee
    db.add(Nominee(user_id=user.id, name="Family Nominee", relationship_type="SPOUSE", percentage=100))
    db.commit()

    if settings.SEED_STARTER_ACCOUNTS:
        # Give starter accounts at the same three DPs
        seed_starter_accounts_for_user(db, user)

    return user

def seed_starter_accounts_for_user(db, user: User):
    """Provide realistic starter accounts if SEED_STARTER_ACCOUNTS=true."""
    acc_id = f"da_{random.randint(1000, 9999)}"
    acc_num = f"12081600{random.randint(10000000, 99999999)}"
    masked = f"XXXX{acc_num[-4:]}"
    acc = DematAccount(
        id=acc_id,
        user_id=user.id,
        dp_name="NiftyTrade Securities",
        dp_id="IN300001",
        account_number=acc_num,
        masked_account_number=masked,
        account_type="INDIVIDUAL",
        status="ACTIVE",
        opened_date=datetime.now().strftime("%Y-%m-%d"),
        nominee_status="REGISTERED"
    )
    db.add(acc)
    db.commit()

    # Give a couple of starter holdings
    db.add(Holding(demat_account_id=acc.id, isin="INE002A01018", free_units=5.0, avg_price=2750.00))
    db.add(Holding(demat_account_id=acc.id, isin="INF204KB14I2", free_units=20.0, avg_price=270.00))
    db.commit()

    db.add(Transaction(
        demat_account_id=acc.id,
        isin="INE002A01018",
        trans_date=datetime.now(timezone.utc) - timedelta(days=14),
        quantity=5.0,
        price=2750.00,
        trans_type="BUY_SETTLEMENT",
        reference_id=f"TXN-START-{random.randint(1000, 9999)}",
        description="NSE Settlement Buy 5 units RELIANCE"
    ))
    db.commit()
