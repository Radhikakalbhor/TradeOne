import io
import os
import pytest
from pypdf import PdfReader
from app.database import SessionLocal, Base, engine
from app.config import settings
from app.models import User, DematAccount, Holding, Instrument, Transaction
from app.services.seed_service import seed_database
from app.services.depository_service import get_portfolio_summary, get_user_holdings
from app.services.cas_pdf_service import generate_cas_pdf

@pytest.fixture(scope="module", autouse=True)
def setup_seed():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    orig_demo = os.environ.get("DEMO_MODE")
    os.environ["DEMO_MODE"] = "true"
    try:
        user = db.query(User).filter(User.email == "aarav.mehta@example.com").first()
        if user:
            for acc in user.demat_accounts:
                db.query(Holding).filter(Holding.demat_account_id == acc.id).delete()
                db.query(Transaction).filter(Transaction.demat_account_id == acc.id).delete()
            db.query(DematAccount).filter(DematAccount.user_id == user.id).delete()
            db.query(User).filter(User.id == user.id).delete()
            db.commit()
        seed_database(db)
    finally:
        if orig_demo is not None:
            os.environ["DEMO_MODE"] = orig_demo
        else:
            os.environ.pop("DEMO_MODE", None)
        db.close()

def test_aarav_portfolio_and_holdings():
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == "aarav.mehta@example.com").first()
        assert user is not None
        assert len(user.demat_accounts) == 3

        summary = get_portfolio_summary(db, user)
        assert summary["total_value"] > 0
        assert summary["num_accounts"] == 3
        assert summary["num_dps"] == 3
        assert "EQUITY" in summary["asset_allocation"]
        assert "REIT" in summary["asset_allocation"]

        # Test unmerged holdings (RELIANCE appears in multiple accounts)
        unmerged = get_user_holdings(db, user, merge_by_isin=False)
        reliance_entries = [h for h in unmerged if h["isin"] == "INE002A01018"]
        assert len(reliance_entries) == 2

        # Test merged holdings by ISIN
        merged = get_user_holdings(db, user, merge_by_isin=True)
        merged_reliance = [h for h in merged if h["isin"] == "INE002A01018"]
        assert len(merged_reliance) == 1
        assert merged_reliance[0]["total_units"] >= 25.0
    finally:
        db.close()

def test_cas_pdf_generation_and_password():
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == "aarav.mehta@example.com").first()
        assert user is not None

        pdf_bytes = generate_cas_pdf(
            user=user,
            accounts=user.demat_accounts,
            transactions=[],
            from_date_str="01-Apr-2026",
            to_date_str="08-Oct-2026"
        )
        assert len(pdf_bytes) > 1000

        # Read encrypted PDF
        reader = PdfReader(io.BytesIO(pdf_bytes))
        assert reader.is_encrypted is True

        # Compute password: PAN last 4 digits (ABCXX1234X -> 1234) + DOB DDMM (15081992 -> 1508)
        password = "12341508"
        decrypt_result = reader.decrypt(password)
        assert decrypt_result != 0
        assert len(reader.pages) >= 1
    finally:
        db.close()
