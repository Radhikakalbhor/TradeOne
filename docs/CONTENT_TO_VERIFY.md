# Content Verification Registry (`docs/CONTENT_TO_VERIFY.md`)

This document tracks factual regulatory parameters, tax clauses, and issuance statuses marked with `needs_verification: true` across TradeOne's educational content.

---

## Items Flagged for Official Verification

### 1. Sovereign Gold Bond (SGB) Fresh Tranche Issuance
- **Item**: Fresh primary subscription window for Sovereign Gold Bonds.
- **Current Observation**: The Government of India and RBI did not announce new tranches in the latest fiscal borrowing calendar, leading investors to buy existing tranches exclusively via secondary exchange markets.
- **Reference in App**: `content/facts.json` under `sgb.new_issuance_status`
- **Verification Action**: Review upcoming RBI press releases regarding SGB issuance calendar for FY 2026-27.
- **Official Source**: [RBI SGB Notification Center](https://www.rbi.org.in)

### 2. REIT Repayment of Capital Taxation (Section 56(2)(xii))
- **Item**: Calculation mechanism for taxation of "repayment of debt / return of capital" component in REIT distributions.
- **Current Observation**: Finance Act provisions specify that distribution amounts exceeding issue price are taxed as income from other sources, with specified cost adjustment rules.
- **Reference in App**: `content/facts.json` under `reit.tax_repayment_of_capital`
- **Verification Action**: Verify latest CBDT circular on computation of acquisition cost for units acquired in secondary market.
- **Official Source**: [Income Tax Department circulars](https://incometaxindia.gov.in)

### 3. Corporate Bond TDS Exemption Thresholds
- **Item**: TDS applicability on interest paid on listed Demat debentures.
- **Current Observation**: Section 193 provides for 10% TDS on corporate bond interest; earlier exemption for listed demat debentures was modified.
- **Reference in App**: `content/facts.json` under `corpbond.tax_interest`
- **Verification Action**: Confirm active TDS rate and Form 15G/15H filing mechanisms with depository clearing corporations.
- **Official Source**: [Income Tax Act, Section 193](https://incometaxindia.gov.in)

---

> [!NOTE]
> All user-facing UI elements referencing items above display a **"Check current rules"** flag to maintain strict regulatory accuracy and avoid outdated guidance.
