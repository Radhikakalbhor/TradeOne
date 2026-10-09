#!/usr/bin/env python3
"""scripts/purge_stale_data.py — Purge Stale, Seeded, and Mock Data and Rebuild from Live Brokers.

Requirement 8:
Deletes all provider_holdings, snapshots and cached data for users and rebuilds them
from a fresh sync, plus removes any seeded/mock rows (Aarav Mehta, Priya Nair, demo demat accounts).
"""

import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# Ensure root directory is in sys.path
root_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root_dir))

# Load .env
env_file = root_dir / ".env"
if env_file.exists():
    load_dotenv(dotenv_path=env_file, override=True)

from sqlalchemy.orm import Session
from app.database import SessionLocal, engine
from app.config import settings
from app.models import User, DematAccount, Holding, Transaction, AuthMethod, Nominee, Consent, ConsentAccessLog
from app.services.broker_adapter import broker_adapter


def purge_and_rebuild(db: Session = None) -> dict:
    close_at_end = False
    if db is None:
        db = SessionLocal()
        close_at_end = True

    purged_stats = {
        "demo_users_purged": 0,
        "holdings_deleted": 0,
        "transactions_deleted": 0,
        "accounts_cleared": 0,
        "users_resynced": 0
    }

    try:
        print("[Purge] Starting stale and mock data cleanup...")

        # 1. Purge seeded demo users if DEMO_MODE is False
        if not settings.DEMO_MODE:
            demo_emails = ["aarav.mehta@example.com", "priya.nair@example.com"]
            demo_users = db.query(User).filter(User.email.in_(demo_emails)).all()
            for du in demo_users:
                print(f"[Purge] Removing demo user {du.email} (ID: {du.id})...")
                accs = db.query(DematAccount).filter(DematAccount.user_id == du.id).all()
                for a in accs:
                    db.query(Holding).filter(Holding.demat_account_id == a.id).delete()
                    db.query(Transaction).filter(Transaction.demat_account_id == a.id).delete()
                    db.delete(a)
                db.query(AuthMethod).filter(AuthMethod.user_id == du.id).delete()
                db.query(Nominee).filter(Nominee.user_id == du.id).delete()
                db.query(ConsentAccessLog).filter(ConsentAccessLog.consent_id.in_(
                    [c.consent_id for c in db.query(Consent).filter(Consent.user_id == du.id).all()]
                )).delete(synchronize_session=False)
                db.query(Consent).filter(Consent.user_id == du.id).delete()
                db.delete(du)
                purged_stats["demo_users_purged"] += 1
            db.commit()

        # 2. For all remaining registered users: delete old/stale holdings and rebuild
        remaining_users = db.query(User).all()
        for u in remaining_users:
            print(f"[Purge] Clearing old stored holdings for user: {u.email}...")
            acc_ids = [a.id for a in u.demat_accounts]
            if acc_ids:
                h_del = db.query(Holding).filter(Holding.demat_account_id.in_(acc_ids)).delete(synchronize_session=False)
                purged_stats["holdings_deleted"] += h_del
                purged_stats["accounts_cleared"] += len(acc_ids)

            # Clear any fake/starter transactions
            if acc_ids:
                t_del = db.query(Transaction).filter(Transaction.demat_account_id.in_(acc_ids)).delete(synchronize_session=False)
                purged_stats["transactions_deleted"] += t_del
            db.commit()

            # 3. Trigger fresh live snapshot sync from deployed brokers
            print(f"[Sync] Running fresh snapshot sync for {u.email} across all 3 brokers...")
            sync_results = broker_adapter.sync_all_brokers(db, u, only_stale=False)
            purged_stats["users_resynced"] += 1

            for code, res in sync_results.items():
                b_name = res.get("broker_name", code.upper())
                b_status = res.get("status", "unknown")
                b_count = res.get("holdings_count", 0)
                print(f"       -> {b_name} ({code}): status={b_status}, holdings={b_count}")

        db.commit()
        print("\n[Purge] Successfully completed! Stats:")
        for k, v in purged_stats.items():
            print(f"       {k}: {v}")

        return purged_stats

    finally:
        if close_at_end:
            db.close()


if __name__ == "__main__":
    purge_and_rebuild()
