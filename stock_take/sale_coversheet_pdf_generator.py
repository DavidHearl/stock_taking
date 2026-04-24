"""
Sale coversheet PDF generator.

Generates a PDF from the same persisted SaleCoverSheet data shown in the sale detail page.
"""

from io import BytesIO
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle


def _fmt_date(value):
    if not value:
        return "-"
    return value.strftime("%d %b %Y")


def _safe(value):
    return str(value).strip() if value else "-"


def _yes_no(value):
    return "Yes" if value else "No"


def generate_sale_coversheet_pdf(sale, coversheet):
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=36,
        rightMargin=36,
        topMargin=36,
        bottomMargin=36,
        title=f"Sale Coversheet - {sale.anthill_activity_id}",
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "CoverTitle",
        parent=styles["Title"],
        fontSize=18,
        textColor=colors.HexColor("#0d6efd"),
        spaceAfter=8,
    )
    section_title = ParagraphStyle(
        "SectionTitle",
        parent=styles["Heading4"],
        fontSize=11,
        textColor=colors.HexColor("#1f2937"),
        spaceAfter=4,
    )
    body_style = ParagraphStyle(
        "BodyStyle",
        parent=styles["BodyText"],
        fontSize=10,
        leading=14,
    )

    elements = [
        Paragraph("Installation Coversheet", title_style),
        Paragraph(
            f"Sale: {_safe(sale.contract_number) if sale.contract_number else _safe(sale.anthill_activity_id)}",
            body_style,
        ),
        Spacer(1, 10),
    ]

    summary_rows = [
        ["Prepared By", _safe(coversheet.prepared_by), "Final", "Yes" if coversheet.is_final else "No"],
        ["CAD Number", _safe(coversheet.cad_number), "Revision", _safe(coversheet.revision_number)],
        ["Customer On Site", _safe(coversheet.customer_on_site_name), "Phone", _safe(coversheet.customer_on_site_phone)],
        ["Survey Date", _fmt_date(coversheet.survey_date), "Fit Date", _fmt_date(coversheet.fit_date)],
        ["Contract", _safe(sale.contract_number), "Activity ID", _safe(sale.anthill_activity_id)],
        ["Design Check", _fmt_date(coversheet.design_check_passed_date), "PFP", _fmt_date(coversheet.pfp_passed_date)],
        ["Ordering Passed", _fmt_date(coversheet.ordering_passed_date), "Goods Due In", _fmt_date(coversheet.goods_due_in_date)],
        ["Fit Days Decided By", _safe(coversheet.fit_days_decided_by), "", ""],
        ["Remeasure Date", _fmt_date(coversheet.remeasure_date), "Parking", _safe(coversheet.parking_situation)],
        ["Fit Days", _safe(coversheet.fit_days), "Remeasure Required", _yes_no(coversheet.remeasure_required)],
        ["Door Type", _safe(coversheet.door_type), "Tracks", _safe(coversheet.track_type)],
        ["Track Colour", _safe(coversheet.track_colour), "Handles", _safe(coversheet.handle_details)],
        ["Products Included", _safe(coversheet.installation_products_included), "Design Type", _safe(coversheet.installation_design_type)],
        ["Measured On", _safe(coversheet.measured_on), "Fit On", _safe(coversheet.fit_on)],
    ]

    summary = Table(summary_rows, colWidths=[95, 170, 80, 165])
    summary.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
                ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f8fafc")),
                ("BACKGROUND", (2, 0), (2, -1), colors.HexColor("#f8fafc")),
                ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                ("FONTNAME", (2, 0), (2, -1), "Helvetica-Bold"),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    elements.append(summary)
    elements.append(Spacer(1, 14))

    sections = [
        ("Installation Address", coversheet.installation_address),
        ("Products / Scope", coversheet.products_scope),
        (
            "Property Flags",
            "2 Man Lift: {}<br/>Access Check Required: {}<br/>Rip Out Required: {}<br/>Remeasure Required: {}<br/>New Build Property: {}".format(
                _yes_no(coversheet.two_man_lift_required),
                _yes_no(coversheet.access_check_required),
                _yes_no(coversheet.rip_out_required),
                _yes_no(coversheet.remeasure_required),
                _yes_no(coversheet.new_build_property),
            ),
        ),
        ("Door Details", coversheet.door_details),
        ("Lighting Details", coversheet.lighting_details),
        (
            "Installation Checks",
            "Electrics / Utilities Required: {}<br/>Electrics Notes: {}<br/>Under Floor Heating: {}".format(
                _yes_no(coversheet.electrics_utilities_required),
                _safe(coversheet.electrics_utilities_notes),
                _yes_no(coversheet.underfloor_heating),
            ),
        ),
        (
            "Board Colours",
            "Exterior: {}<br/>Interior: {}<br/>Backs: {}<br/>Fronts (Drawers / Hinges): {}".format(
                _safe(coversheet.board_colour_exterior),
                _safe(coversheet.board_colour_interior),
                _safe(coversheet.board_colour_backs),
                _safe(coversheet.board_colour_fronts),
            ),
        ),
        ("Measurements Notes", coversheet.measurements_notes),
        ("Access Notes", coversheet.access_notes),
        ("Health and Safety Notes", coversheet.health_safety_notes),
        ("Special Instructions", coversheet.special_instructions),
    ]

    for heading, text in sections:
        elements.append(Paragraph(heading, section_title))
        elements.append(Paragraph(_safe(text).replace("\n", "<br/>"), body_style))
        elements.append(Spacer(1, 10))

    elements.append(Spacer(1, 12))
    elements.append(
        Paragraph(
            f"Generated from digital coversheet on {_fmt_date(coversheet.updated_at.date() if coversheet.updated_at else None)}",
            ParagraphStyle("Foot", parent=body_style, fontSize=8, textColor=colors.HexColor("#6b7280")),
        )
    )

    doc.build(elements)
    buffer.seek(0)
    return buffer
