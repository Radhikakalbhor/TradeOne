import io
from datetime import datetime, timezone
from typing import List, Optional
from reportlab.lib.pagesizes import letter, A4
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from pypdf import PdfReader, PdfWriter
from app.models import User, DematAccount, Holding, Transaction
from app.services.depository_service import format_inr

def generate_cas_pdf(
    user: User, 
    accounts: List[DematAccount], 
    transactions: List[Transaction], 
    from_date_str: str, 
    to_date_str: str
) -> bytes:
    """Generate a password-protected Consolidated Account Statement (CAS) PDF."""
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=36,
        leftMargin=36,
        topMargin=36,
        bottomMargin=36
    )

    styles = getSampleStyleSheet()
    
    # Custom Styles
    title_style = ParagraphStyle(
        "CASTitle",
        parent=styles["Heading1"],
        fontName="Helvetica-Bold",
        fontSize=18,
        leading=22,
        textColor=colors.HexColor("#1e293b")
    )
    subtitle_style = ParagraphStyle(
        "CASSubtitle",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=10,
        leading=14,
        textColor=colors.HexColor("#d97706")
    )
    section_heading = ParagraphStyle(
        "CASSection",
        parent=styles["Heading2"],
        fontName="Helvetica-Bold",
        fontSize=12,
        leading=16,
        textColor=colors.HexColor("#0f172a"),
        spaceBefore=12,
        spaceAfter=6
    )
    normal_style = ParagraphStyle(
        "CASNormal",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=9,
        leading=12,
        textColor=colors.HexColor("#334155")
    )
    small_style = ParagraphStyle(
        "CASSmall",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=8,
        leading=10,
        textColor=colors.HexColor("#64748b")
    )
    table_text = ParagraphStyle(
        "CASTableText",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=8,
        leading=10,
        textColor=colors.HexColor("#1e293b")
    )
    table_head = ParagraphStyle(
        "CASTableHead",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=8,
        leading=10,
        textColor=colors.HexColor("#ffffff")
    )

    story = []

    # 1. Header
    story.append(Paragraph("TRADEONE", title_style))
    story.append(Paragraph("SIMULATED CONSOLIDATED ACCOUNT STATEMENT (CAS)", subtitle_style))
    story.append(Paragraph("Single View of Demat Holdings across Depositories & Depository Participants", small_style))
    story.append(Spacer(1, 10))
    story.append(HRFlowable(width="100%", thickness=1.5, color=colors.HexColor("#cbd5e1"), spaceAfter=12))

    # 2. Investor Demographics & Statement Meta
    meta_data = [
        [
            Paragraph(f"<b>Investor Name:</b> {user.name}", normal_style),
            Paragraph(f"<b>Statement Period:</b> {from_date_str} to {to_date_str}", normal_style)
        ],
        [
            Paragraph(f"<b>Depository BO ID:</b> {user.bo_id}", normal_style),
            Paragraph(f"<b>Generation Date:</b> {datetime.now(timezone.utc).strftime('%d-%b-%Y')}", normal_style)
        ],
        [
            Paragraph(f"<b>Permanent Account No (PAN):</b> {user.masked_pan}", normal_style),
            Paragraph(f"<b>Registered Email:</b> {user.email}", normal_style)
        ]
    ]
    meta_table = Table(meta_data, colWidths=[260, 260])
    meta_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f8fafc")),
        ("PADDING", (0, 0), (-1, -1), 6),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, -1), 0.5, colors.HexColor("#e2e8f0")),
    ]))
    story.append(meta_table)
    story.append(Spacer(1, 15))

    # 3. Overall Portfolio Summary
    total_portfolio_val = 0.0
    for acc in accounts:
        for h in acc.holdings:
            if h.instrument:
                total_portfolio_val += h.total_units * h.instrument.last_price

    story.append(Paragraph("PORTFOLIO SUMMARY AT A GLANCE", section_heading))
    summary_data = [
        [
            Paragraph("Total Portfolio Valuation", normal_style),
            Paragraph(f"<b>{format_inr(total_portfolio_val)}</b>", normal_style)
        ],
        [
            Paragraph("Active Demat Accounts", normal_style),
            Paragraph(f"<b>{len(accounts)}</b>", normal_style)
        ]
    ]
    sum_table = Table(summary_data, colWidths=[260, 260])
    sum_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f1f5f9")),
        ("PADDING", (0, 0), (-1, -1), 5),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
    ]))
    story.append(sum_table)
    story.append(Spacer(1, 15))

    # 4. Demat Account Details & Holdings
    story.append(Paragraph("DEMAT ACCOUNT HOLDINGS DETAILS", section_heading))

    for acc in accounts:
        acc_header = f"DP: {acc.dp_name} (DP ID: {acc.dp_id}) &bull; Demat Acc No: {acc.masked_account_number} &bull; Type: {acc.account_type}"
        story.append(Paragraph(acc_header, ParagraphStyle("AccH", parent=normal_style, fontName="Helvetica-Bold", fontSize=9, textColor=colors.HexColor("#0f172a"))))
        story.append(Spacer(1, 4))

        holdings_data = [[
            Paragraph("ISIN", table_head),
            Paragraph("Security Name", table_head),
            Paragraph("Asset Class", table_head),
            Paragraph("Free Qty", table_head),
            Paragraph("Lock/Pledge", table_head),
            Paragraph("Price (₹)", table_head),
            Paragraph("Value (₹)", table_head),
        ]]

        acc_total = 0.0
        for h in acc.holdings:
            instr = h.instrument
            if not instr:
                continue
            val = h.total_units * instr.last_price
            acc_total += val
            lock_pledge = h.pledged_units + h.locked_units
            holdings_data.append([
                Paragraph(h.isin, table_text),
                Paragraph(instr.name[:25], table_text),
                Paragraph(instr.asset_class, table_text),
                Paragraph(f"{h.free_units:.3f}", table_text),
                Paragraph(f"{lock_pledge:.3f}", table_text),
                Paragraph(f"{instr.last_price:.2f}", table_text),
                Paragraph(format_inr(val), table_text),
            ])

        holdings_data.append([
            Paragraph("<b>Total Account Value</b>", table_text),
            Paragraph("", table_text),
            Paragraph("", table_text),
            Paragraph("", table_text),
            Paragraph("", table_text),
            Paragraph("", table_text),
            Paragraph(f"<b>{format_inr(acc_total)}</b>", table_text),
        ])

        h_table = Table(holdings_data, colWidths=[75, 135, 65, 55, 55, 60, 75])
        h_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1e293b")),
            ("PADDING", (0, 0), (-1, -1), 4),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#e2e8f0")),
            ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#f8fafc")),
        ]))
        story.append(h_table)
        story.append(Spacer(1, 12))

    # 5. Transactions Statement
    if transactions:
        story.append(Paragraph("TRANSACTION STATEMENT FOR THE PERIOD", section_heading))
        tx_data = [[
            Paragraph("Date", table_head),
            Paragraph("Demat Acc", table_head),
            Paragraph("ISIN / Security", table_head),
            Paragraph("Type", table_head),
            Paragraph("Qty", table_head),
            Paragraph("Settlement Price", table_head),
        ]]
        for t in transactions[:30]:  # Cap at 30 recent
            s_name = t.instrument.name if t.instrument else t.isin
            tx_data.append([
                Paragraph(t.trans_date.strftime("%d-%b-%Y") if t.trans_date else "", table_text),
                Paragraph(t.demat_account.masked_account_number if t.demat_account else "", table_text),
                Paragraph(f"{t.isin}<br/>{s_name[:20]}", table_text),
                Paragraph(t.trans_type, table_text),
                Paragraph(f"{t.quantity:.3f}", table_text),
                Paragraph(f"₹{t.price:.2f}", table_text),
            ])
        tx_table = Table(tx_data, colWidths=[65, 75, 170, 90, 50, 70])
        tx_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0f172a")),
            ("PADDING", (0, 0), (-1, -1), 4),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#e2e8f0")),
        ]))
        story.append(tx_table)
        story.append(Spacer(1, 15))

    # 6. Footer Disclaimer
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#cbd5e1"), spaceAfter=8))
    story.append(Paragraph(
        "<b>Important Notice:</b> Simulated data - not a real depository. This document is automatically generated by TradeOne simulated sandbox platform for integration and developer testing purposes only. It carries no legal validity.",
        small_style
    ))

    doc.build(story)
    pdf_bytes = buffer.getvalue()

    # Password Encryption: User's PAN last 4 digits + date of birth in DDMM format
    # Example: PAN ABCXX1234X -> last 4 digits "1234" + DOB "15081992" -> DDMM "1508" => "12341508"
    pan = user.masked_pan or "ABCXX1234X"
    # extract 4 digits from pan
    pan_digits = "".join([c for c in pan if c.isdigit()])[-4:]
    if len(pan_digits) < 4:
        pan_digits = "1234"
        
    dob = user.dob or "15081992"
    ddmm = dob[:4] if len(dob) >= 4 else "1508"
    cas_password = f"{pan_digits}{ddmm}".upper()

    # Encrypt PDF using pypdf
    reader = PdfReader(io.BytesIO(pdf_bytes))
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)

    writer.encrypt(cas_password)

    encrypted_buffer = io.BytesIO()
    writer.write(encrypted_buffer)
    return encrypted_buffer.getvalue()
