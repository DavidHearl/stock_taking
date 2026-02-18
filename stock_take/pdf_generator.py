"""
Summary Document PDF generator for orders.

Generates a professional PDF listing all materials required for a job:
  - Board items (from PNX)
  - Accessories
  - OS Doors
  - Glass items
"""

import io
import os
from decimal import Decimal
from datetime import datetime

from django.conf import settings

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer,
    Image, HRFlowable, KeepTogether
)


# Brand colours
BRAND_DARK = colors.HexColor('#1a1a2e')
BRAND_ACCENT = colors.HexColor('#4f8cff')
HEADER_BG = colors.HexColor('#f0f4ff')
ROW_ALT = colors.HexColor('#f8f9fc')
BORDER_COLOR = colors.HexColor('#dfe3ec')
TEXT_PRIMARY = colors.HexColor('#1a1a2e')
TEXT_SECONDARY = colors.HexColor('#6b7280')


def _get_styles():
    """Build custom paragraph styles."""
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(
        'SectionTitle',
        parent=styles['Heading2'],
        fontSize=12,
        leading=16,
        textColor=TEXT_PRIMARY,
        spaceAfter=6,
        spaceBefore=14,
    ))
    styles.add(ParagraphStyle(
        'CellText',
        parent=styles['Normal'],
        fontSize=8,
        leading=10,
        textColor=TEXT_PRIMARY,
    ))
    styles.add(ParagraphStyle(
        'CellTextSmall',
        parent=styles['Normal'],
        fontSize=7,
        leading=9,
        textColor=TEXT_SECONDARY,
    ))
    styles.add(ParagraphStyle(
        'TotalText',
        parent=styles['Normal'],
        fontSize=9,
        leading=12,
        textColor=TEXT_PRIMARY,
        alignment=TA_RIGHT,
    ))
    return styles


def _section_header(text, styles):
    """Return a styled section header paragraph."""
    return Paragraph(
        f'<font color="#4f8cff"><b>■</b></font>&nbsp;&nbsp;{text}',
        styles['SectionTitle'],
    )


def _build_table(data, col_widths, has_total_row=False):
    """Build a consistently styled table."""
    table = Table(data, colWidths=col_widths, repeatRows=1)

    style_commands = [
        # Header row
        ('BACKGROUND', (0, 0), (-1, 0), HEADER_BG),
        ('TEXTCOLOR', (0, 0), (-1, 0), TEXT_PRIMARY),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 8),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 6),
        ('TOPPADDING', (0, 0), (-1, 0), 6),

        # Body
        ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
        ('FONTSIZE', (0, 1), (-1, -1), 8),
        ('TOPPADDING', (0, 1), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 1), (-1, -1), 4),

        # Grid
        ('LINEBELOW', (0, 0), (-1, 0), 0.8, BRAND_ACCENT),
        ('LINEBELOW', (0, 1), (-1, -1), 0.4, BORDER_COLOR),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
        ('RIGHTPADDING', (0, 0), (-1, -1), 6),
    ]

    # Alternating row colours
    for i in range(1, len(data)):
        if i % 2 == 0:
            style_commands.append(('BACKGROUND', (0, i), (-1, i), ROW_ALT))

    # Total row styling
    if has_total_row and len(data) > 1:
        last = len(data) - 1
        style_commands += [
            ('LINEABOVE', (0, last), (-1, last), 1, BRAND_ACCENT),
            ('FONTNAME', (0, last), (-1, last), 'Helvetica-Bold'),
            ('BACKGROUND', (0, last), (-1, last), HEADER_BG),
        ]

    table.setStyle(TableStyle(style_commands))
    return table


def generate_summary_pdf(order, price_per_sqm=12):
    """
    Generate a Summary Document PDF for an order.

    Returns a BytesIO buffer containing the PDF.
    """
    buffer = io.BytesIO()
    styles = _get_styles()

    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=15 * mm,
        rightMargin=15 * mm,
        topMargin=15 * mm,
        bottomMargin=15 * mm,
        title=f'Summary Document - {order.sale_number}',
    )

    elements = []
    page_width = A4[0] - 30 * mm  # usable width

    # ─── HEADER ───────────────────────────────────────────────
    logo_path = os.path.join(settings.BASE_DIR, 'static', 'images', 'logo-full-light.png')
    header_data = []

    if os.path.exists(logo_path):
        logo = Image(logo_path, width=50 * mm, height=12 * mm)
        logo.hAlign = 'LEFT'
    else:
        logo = Paragraph('<b>Atlas</b>', styles['Title'])

    title_para = Paragraph(
        '<font size="16" color="#1a1a2e"><b>Summary Document</b></font>',
        styles['Normal'],
    )

    header_table = Table(
        [[logo, title_para]],
        colWidths=[page_width * 0.5, page_width * 0.5],
    )
    header_table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('ALIGN', (1, 0), (1, 0), 'RIGHT'),
    ]))
    elements.append(header_table)
    elements.append(Spacer(1, 6 * mm))

    # ─── ORDER INFO ───────────────────────────────────────────
    customer_name = f'{order.first_name} {order.last_name}'.strip() or '—'
    info_data = [
        ['Customer', customer_name, 'Sale No.', order.sale_number or '—'],
        ['Address', order.address or '—', 'Customer No.', order.customer_number or '—'],
        ['Postcode', order.postcode or '—', 'Order Type', order.get_order_type_display()],
        ['Order Date', order.order_date.strftime('%d %b %Y') if order.order_date else '—',
         'Fit Date', order.fit_date.strftime('%d %b %Y') if order.fit_date else '—'],
        ['Boards PO', order.boards_po.po_number if order.boards_po else '—',
         'Designer', order.designer.name if order.designer else '—'],
    ]

    info_table = Table(info_data, colWidths=[
        page_width * 0.15, page_width * 0.35,
        page_width * 0.15, page_width * 0.35,
    ])
    info_table.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
        ('FONTNAME', (2, 0), (2, -1), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 8),
        ('TEXTCOLOR', (0, 0), (0, -1), TEXT_SECONDARY),
        ('TEXTCOLOR', (2, 0), (2, -1), TEXT_SECONDARY),
        ('TEXTCOLOR', (1, 0), (1, -1), TEXT_PRIMARY),
        ('TEXTCOLOR', (3, 0), (3, -1), TEXT_PRIMARY),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ('LINEBELOW', (0, -1), (-1, -1), 0.8, BORDER_COLOR),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
    ]))
    elements.append(info_table)
    elements.append(Spacer(1, 4 * mm))

    # Generated date
    elements.append(Paragraph(
        f'<font size="7" color="#6b7280">Generated: {datetime.now().strftime("%d %b %Y at %H:%M")}</font>',
        styles['Normal'],
    ))
    elements.append(Spacer(1, 6 * mm))

    # ─── 1. BOARD ITEMS ──────────────────────────────────────
    order_pnx_items = []
    if order.boards_po:
        order_pnx_items = list(
            order.boards_po.pnx_items
            .filter(customer__icontains=order.sale_number)
            .order_by('matname', 'partdesc')
        )

    if order_pnx_items:
        elements.append(_section_header('Board Items', styles))

        board_data = [['Material', 'Description', 'L (mm)', 'W (mm)', 'Qty', 'Grain']]
        for item in order_pnx_items:
            board_data.append([
                str(item.matname),
                str(item.partdesc) if item.partdesc else '—',
                f'{item.cleng:,.0f}',
                f'{item.cwidth:,.0f}',
                f'{item.cnt:,.0f}',
                str(item.grain) if item.grain else '—',
            ])

        # Summary row
        total_boards = sum(item.cnt for item in order_pnx_items)
        board_data.append(['', '', '', '', f'{total_boards:,.0f}', f'{len(order_pnx_items)} items'])

        board_widths = [
            page_width * 0.25, page_width * 0.30,
            page_width * 0.12, page_width * 0.12,
            page_width * 0.09, page_width * 0.12,
        ]
        elements.append(_build_table(board_data, board_widths, has_total_row=True))
        elements.append(Spacer(1, 4 * mm))

    # ─── 2. ACCESSORIES ──────────────────────────────────────
    accessories = list(
        order.accessories
        .exclude(sku__istartswith='GLS')
        .exclude(sku__icontains='_RAU_')
        .exclude(is_os_door=True)
        .order_by('name')
    )

    if accessories:
        elements.append(_section_header('Accessories', styles))

        acc_data = [['SKU', 'Name', 'Qty']]
        for acc in accessories:
            acc_data.append([
                str(acc.sku),
                str(acc.name),
                f'{acc.quantity:,.0f}',
            ])

        total_acc_qty = sum(acc.quantity for acc in accessories)
        acc_data.append(['', f'{len(accessories)} items', f'{total_acc_qty:,.0f}'])

        acc_widths = [
            page_width * 0.25, page_width * 0.60, page_width * 0.15,
        ]
        elements.append(_build_table(acc_data, acc_widths, has_total_row=True))
        elements.append(Spacer(1, 4 * mm))

    # ─── 3. RAUMPLUS ─────────────────────────────────────────
    raumplus_items = list(
        order.accessories
        .filter(sku__icontains='_RAU_')
        .order_by('name')
    )

    if raumplus_items:
        elements.append(_section_header('Raumplus', styles))

        rau_data = [['SKU', 'Name', 'Qty']]
        for rau in raumplus_items:
            rau_data.append([
                str(rau.sku),
                str(rau.name),
                f'{rau.quantity:,.0f}',
            ])

        total_rau_qty = sum(rau.quantity for rau in raumplus_items)
        rau_data.append(['', f'{len(raumplus_items)} items', f'{total_rau_qty:,.0f}'])

        rau_widths = [
            page_width * 0.25, page_width * 0.60, page_width * 0.15,
        ]
        elements.append(_build_table(rau_data, rau_widths, has_total_row=True))
        elements.append(Spacer(1, 4 * mm))

    # ─── 4. GLASS ITEMS ──────────────────────────────────────
    glass_items = list(
        order.accessories
        .filter(sku__istartswith='GLS')
        .order_by('name')
    )

    if glass_items:
        elements.append(_section_header('Glass Items', styles))

        glass_data = [['SKU', 'Name', 'Qty']]
        for gi in glass_items:
            glass_data.append([
                str(gi.sku),
                str(gi.name),
                f'{gi.quantity:,.0f}',
            ])

        total_glass_qty = sum(gi.quantity for gi in glass_items)
        glass_data.append(['', f'{len(glass_items)} items', f'{total_glass_qty:,.0f}'])

        glass_widths = [
            page_width * 0.25, page_width * 0.60, page_width * 0.15,
        ]
        elements.append(_build_table(glass_data, glass_widths, has_total_row=True))
        elements.append(Spacer(1, 4 * mm))

    # ─── 4. OS DOORS ─────────────────────────────────────────
    os_doors = list(order.os_doors.all().order_by('door_style', 'colour'))

    if os_doors:
        elements.append(_section_header('OS Doors', styles))

        door_data = [['Style', 'Colour', 'Description', 'H (mm)', 'W (mm)', 'Qty']]
        for door in os_doors:
            door_data.append([
                str(door.door_style),
                str(door.colour),
                str(door.item_description)[:60] if door.item_description else '—',
                f'{door.height:,.0f}',
                f'{door.width:,.0f}',
                str(door.quantity),
            ])

        total_doors = sum(door.quantity for door in os_doors)
        door_data.append(['', '', '', '', '', f'{total_doors:,.0f}'])

        door_widths = [
            page_width * 0.18, page_width * 0.14, page_width * 0.30,
            page_width * 0.12, page_width * 0.12, page_width * 0.14,
        ]
        elements.append(_build_table(door_data, door_widths, has_total_row=True))
        elements.append(Spacer(1, 4 * mm))

    # ─── MATERIAL COUNTS SUMMARY ─────────────────────────────
    elements.append(_section_header('Materials Summary', styles))

    summary_data = [['Category', 'Line Items', 'Total Qty']]
    if order_pnx_items:
        summary_data.append([
            'Board Items',
            str(len(order_pnx_items)),
            f'{sum(item.cnt for item in order_pnx_items):,.0f}',
        ])
    if accessories:
        summary_data.append([
            'Accessories',
            str(len(accessories)),
            f'{sum(a.quantity for a in accessories):,.0f}',
        ])
    if raumplus_items:
        summary_data.append([
            'Raumplus',
            str(len(raumplus_items)),
            f'{sum(r.quantity for r in raumplus_items):,.0f}',
        ])
    if glass_items:
        summary_data.append([
            'Glass Items',
            str(len(glass_items)),
            f'{sum(g.quantity for g in glass_items):,.0f}',
        ])
    if os_doors:
        summary_data.append([
            'OS Doors',
            str(len(os_doors)),
            f'{sum(d.quantity for d in os_doors):,.0f}',
        ])

    if len(summary_data) == 1:
        summary_data.append(['No materials found', '0', '0'])

    summary_widths = [page_width * 0.50, page_width * 0.25, page_width * 0.25]
    elements.append(_build_table(summary_data, summary_widths))

    # ─── BUILD PDF ────────────────────────────────────────────
    doc.build(elements)
    buffer.seek(0)
    return buffer
