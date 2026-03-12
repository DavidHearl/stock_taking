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

YEAR_BAND_BG = colors.HexColor('#eef2fb')


def generate_outstanding_report_pdf(rows, location_label='All Locations'):
    """
    Generate an Outstanding Balance Report PDF.

    `rows` is a list of dicts as returned by dashboard_outstanding_report:
        pk, sale_number, customer, contract, date, sale_value, paid, outstanding, year
    Returns a BytesIO buffer.
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
        title='Outstanding Balance Report',
    )

    elements = []
    page_width = A4[0] - 30 * mm

    # ── HEADER ──────────────────────────────────────────────────
    logo_path = os.path.join(settings.BASE_DIR, 'static', 'images', 'logo-full-light.png')
    if os.path.exists(logo_path):
        logo = Image(logo_path, width=50 * mm, height=12 * mm)
        logo.hAlign = 'LEFT'
    else:
        logo = Paragraph('<b>Sliderobes</b>', styles['Normal'])

    title_para = Paragraph(
        '<font size="16" color="#1a1a2e"><b>Outstanding Balance Report</b></font>',
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
    elements.append(Spacer(1, 5 * mm))

    # ── SUMMARY BLOCK ───────────────────────────────────────────
    total_outstanding = sum(r['outstanding'] for r in rows)
    info_data = [
        ['Location', location_label,
         'Total Customers', f'{len(rows):,}'],
        ['Generated', datetime.now().strftime('%d %b %Y at %H:%M'),
         'Total Outstanding', f'£{total_outstanding:,.0f}'],
    ]
    info_table = Table(info_data, colWidths=[
        page_width * 0.15, page_width * 0.35,
        page_width * 0.20, page_width * 0.30,
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
    elements.append(Spacer(1, 6 * mm))

    # ── GROUP BY YEAR ────────────────────────────────────────────
    from collections import defaultdict
    by_year = defaultdict(list)
    for r in rows:
        by_year[r['year'] or 'Unknown'].append(r)
    years = sorted(by_year.keys(), key=lambda y: -(y if isinstance(y, int) else 0))

    col_widths = [
        page_width * 0.22,  # Customer
        page_width * 0.10,  # Sale #
        page_width * 0.16,  # Contract
        page_width * 0.10,  # Date
        page_width * 0.13,  # Sale Value
        page_width * 0.13,  # Paid
        page_width * 0.16,  # Outstanding
    ]
    headers = ['Customer', 'Sale #', 'Contract', 'Date', 'Sale Value', 'Paid', 'Outstanding']

    for yr in years:
        year_rows = by_year[yr]
        year_total = sum(r['outstanding'] for r in year_rows)

        # Year section header
        elements.append(_section_header(
            f'{yr} — {len(year_rows)} customer{"s" if len(year_rows) != 1 else ""}'  # noqa
            f' &mdash; <font color="#dc3545">£{year_total:,.0f} outstanding</font>',
            styles,
        ))

        table_data = [headers]
        for r in year_rows:
            table_data.append([
                Paragraph(r['customer'], styles['CellText']),
                r['sale_number'],
                r['contract'] or '—',
                r['date'],
                f'£{r["sale_value"]:,.0f}',
                f'£{r["paid"]:,.0f}',
                Paragraph(f'<font color="#dc3545"><b>£{r["outstanding"]:,.0f}</b></font>',
                          styles['CellText']),
            ])

        # Year totals row
        table_data.append([
            Paragraph(f'<b>{len(year_rows)} customers</b>', styles['CellText']),
            '', '', '',
            f'£{sum(r["sale_value"] for r in year_rows):,.0f}',
            f'£{sum(r["paid"] for r in year_rows):,.0f}',
            Paragraph(f'<font color="#dc3545"><b>£{year_total:,.0f}</b></font>',
                      styles['CellText']),
        ])

        tbl = _build_table(table_data, col_widths, has_total_row=True)
        elements.append(tbl)
        elements.append(Spacer(1, 5 * mm))

    # ── GRAND TOTAL ──────────────────────────────────────────────
    grand_data = [['', '', '', '', 'Total Sale Value', 'Total Paid', 'Total Outstanding']]
    grand_data.append([
        Paragraph(f'<b>All {len(rows)} customers</b>', styles['CellText']),
        '', '', '',
        f'£{sum(r["sale_value"] for r in rows):,.0f}',
        f'£{sum(r["paid"] for r in rows):,.0f}',
        Paragraph(f'<font color="#dc3545"><b>£{total_outstanding:,.0f}</b></font>',
                  styles['CellText']),
    ])
    elements.append(_build_table(grand_data, col_widths, has_total_row=True))

    doc.build(elements)
    buffer.seek(0)
    return buffer


def generate_stock_report_pdf(recent_changes, current_stock, as_of_date=None):
    """
    Generate a Stock Report PDF.

    `recent_changes` — list of dicts: date, sku, name, change_type, change_amount,
                       unit_cost, value_change, reference
    `current_stock`  — list of dicts: sku, name, category, location, unit_cost,
                       quantity, total_value  (ALL items; items <£500 are excluded
                       from the detail table but included in the total)
    `as_of_date`     — optional date object for historical stock reports
    Returns a BytesIO buffer.
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
        title='Stock Report',
    )

    elements = []
    page_width = A4[0] - 30 * mm

    all_items_total = sum(i['total_value'] for i in current_stock)
    displayed_stock = current_stock

    date_label = as_of_date.strftime('%d %b %Y') if as_of_date else 'Current'

    # ── HEADER ──────────────────────────────────────────────────
    logo_path = os.path.join(settings.BASE_DIR, 'static', 'images', 'logo-full-light.png')
    if os.path.exists(logo_path):
        logo = Image(logo_path, width=50 * mm, height=12 * mm)
        logo.hAlign = 'LEFT'
    else:
        logo = Paragraph('<b>Sliderobes</b>', styles['Normal'])

    title_para = Paragraph(
        f'<font size="16" color="#1a1a2e"><b>Stock Report</b></font><br/><font size="9" color="#6b7280">As of: {date_label}</font>',
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
    elements.append(Spacer(1, 5 * mm))

    # ── SUMMARY BLOCK ───────────────────────────────────────────
    total_stock_value = all_items_total
    info_data = [
        ['Items in Stock', f'{len(current_stock):,}', 'Total Stock Value', f'£{total_stock_value:,.0f}'],
        ['Items Shown', f'{len(displayed_stock):,}', 'Generated', datetime.now().strftime('%d %b %Y at %H:%M')],
    ]
    info_table = Table(info_data, colWidths=[
        page_width * 0.18, page_width * 0.32,
        page_width * 0.20, page_width * 0.30,
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
    elements.append(Spacer(1, 6 * mm))

    # ── SECTION 1: RECENT CHANGES ────────────────────────────────
    elements.append(_section_header('Stock Changes (past 3 days)', styles))

    if recent_changes:
        change_col_widths = [
            page_width * 0.34,  # Item (SKU + Name)
            page_width * 0.12,  # Type
            page_width * 0.09,  # Qty Δ
            page_width * 0.14,  # Value Δ
            page_width * 0.16,  # Date
            page_width * 0.15,  # Reference
        ]
        change_headers = ['Item', 'Type', 'Qty Δ', 'Value Δ', 'Date', 'Reference']
        change_data = [change_headers]
        for c in recent_changes:
            qty_sign = '+' if c['change_amount'] > 0 else ''
            val_sign = '+' if c['value_change'] > 0 else ''
            qty_color = '#28a745' if c['change_amount'] > 0 else '#dc3545'
            val_color = '#28a745' if c['value_change'] > 0 else '#dc3545'
            change_data.append([
                Paragraph(
                    f'<b>{c["sku"]}</b><br/>'
                    f'<font size="7" color="#888888">{c["name"]}</font>',
                    styles['CellText']
                ),
                Paragraph(
                    c['change_type'].replace('_', ' ').capitalize(),
                    styles['CellTextSmall']
                ),
                Paragraph(
                    f'<font color="{qty_color}"><b>{qty_sign}{c["change_amount"]:,}</b></font>',
                    styles['CellText']
                ),
                Paragraph(
                    f'<font color="{val_color}"><b>{val_sign}£{abs(c["value_change"]):,.0f}</b></font>',
                    styles['CellText']
                ),
                Paragraph(c['date'], styles['CellTextSmall']),
                Paragraph(c['reference'] or '—', styles['CellTextSmall']),
            ])
        elements.append(_build_table(change_data, change_col_widths))
    else:
        elements.append(Paragraph('<i>No stock changes recorded.</i>', styles['Normal']))

    elements.append(Spacer(1, 6 * mm))

    # ── SECTION 2: CURRENT STOCK ─────────────────────────────────
    section_title = f'Current Stock — {date_label} ({len(displayed_stock)} items)'
    elements.append(_section_header(section_title, styles))

    if displayed_stock:
        stock_col_widths = [
            page_width * 0.52,
            page_width * 0.24,
            page_width * 0.24,
        ]
        stock_headers = ['Item', 'Qty × Unit Cost', 'Total Value']
        stock_data = [stock_headers]
        for i in displayed_stock:
            meta_parts = [p for p in [i['category'], i['location']] if p]
            meta_str = ' · '.join(meta_parts)
            item_html = f'<b>{i["sku"]}</b><br/><font size="7" color="#888888">{i["name"]}'
            if meta_str:
                item_html += f' &nbsp;·&nbsp; {meta_str}'
            item_html += '</font>'
            stock_data.append([
                Paragraph(item_html, styles['CellText']),
                Paragraph(
                    f'{i["quantity"]:,} × £{i["unit_cost"]:,.2f}',
                    styles['CellTextSmall']
                ),
                Paragraph(
                    f'<b>£{i["total_value"]:,.0f}</b>',
                    styles['CellText']
                ),
            ])
        # Totals row
        grand_qty = sum(i['quantity'] for i in current_stock)
        stock_data.append([
            Paragraph(f'<b>{len(current_stock):,} items total &nbsp;·&nbsp; {grand_qty:,} units</b>', styles['CellText']),
            '',
            Paragraph(f'<b>£{all_items_total:,.0f}</b>', styles['CellText']),
        ])
        elements.append(_build_table(stock_data, stock_col_widths, has_total_row=True))
    else:
        elements.append(Paragraph('<i>No stock items found.</i>', styles['Normal']))

    doc.build(elements)
    buffer.seek(0)
    return buffer


def generate_sales_after_pdf(rows, cutoff_date=None):
    """
    Generate a Sales After Date PDF.

    `rows` is a list of dicts with keys: pk, customer, sale_number, order_date,
    fit_date, sale_value, designer.
    Returns a BytesIO buffer.
    """
    buffer = io.BytesIO()
    styles = _get_styles()

    cutoff_label = cutoff_date.strftime('%d %b %Y') if cutoff_date else '—'

    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=15 * mm,
        rightMargin=15 * mm,
        topMargin=15 * mm,
        bottomMargin=15 * mm,
        title=f'Sales After {cutoff_label}',
    )

    elements = []
    page_width = A4[0] - 30 * mm

    # ── HEADER ──────────────────────────────────────────────────
    logo_path = os.path.join(settings.BASE_DIR, 'static', 'images', 'logo-full-light.png')
    if os.path.exists(logo_path):
        logo = Image(logo_path, width=50 * mm, height=12 * mm)
        logo.hAlign = 'LEFT'
    else:
        logo = Paragraph('<b>Sliderobes</b>', styles['Normal'])

    title_para = Paragraph(
        f'<font size="16" color="#1a1a2e"><b>Sales After {cutoff_label}</b></font>',
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
    elements.append(Spacer(1, 5 * mm))

    # ── SUMMARY BLOCK ───────────────────────────────────────────
    total_value = sum(r['sale_value'] for r in rows)
    info_data = [
        ['Orders', f'{len(rows):,}', 'Total Value', f'£{total_value:,.0f}'],
        ['From Date', cutoff_label, 'Generated', datetime.now().strftime('%d %b %Y at %H:%M')],
    ]
    info_table = Table(info_data, colWidths=[
        page_width * 0.15, page_width * 0.35,
        page_width * 0.20, page_width * 0.30,
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
    elements.append(Spacer(1, 6 * mm))

    # ── ORDERS TABLE ─────────────────────────────────────────────
    col_widths = [
        page_width * 0.22,  # Customer
        page_width * 0.08,  # Sale #
        page_width * 0.10,  # Order Date
        page_width * 0.10,  # Fit Date
        page_width * 0.18,  # Designer
        page_width * 0.14,  # Sale Value
        page_width * 0.10,  # Paid
        page_width * 0.08,  # Remaining
    ]
    headers = ['Customer', 'Sale #', 'Order Date', 'Fit Date', 'Designer', 'Sale Value', 'Paid', 'Remaining']

    table_data = [headers]
    for r in rows:
        remaining = r.get('remaining', max(r['sale_value'] - r.get('paid', 0), 0))
        remaining_cell = (
            Paragraph(f'<font color="#dc3545"><b>£{remaining:,.0f}</b></font>', styles['CellText'])
            if remaining > 0 else f'£{remaining:,.0f}'
        )
        table_data.append([
            Paragraph(r['customer'], styles['CellText']),
            r['sale_number'] or '—',
            r['order_date'] or '—',
            r['fit_date'] or '—',
            r['designer'] or '—',
            f'£{r["sale_value"]:,.0f}',
            f'£{r.get("paid", 0):,.0f}',
            remaining_cell,
        ])

    total_paid = sum(r.get('paid', 0) for r in rows)
    total_remaining = sum(r.get('remaining', max(r['sale_value'] - r.get('paid', 0), 0)) for r in rows)
    # Totals row
    table_data.append([
        Paragraph(f'<b>{len(rows)} orders</b>', styles['CellText']),
        '', '', '', 'Total',
        f'£{total_value:,.0f}',
        f'£{total_paid:,.0f}',
        Paragraph(f'<font color="#dc3545"><b>£{total_remaining:,.0f}</b></font>', styles['CellText']),
    ])

    elements.append(_build_table(table_data, col_widths, has_total_row=True))

    doc.build(elements)
    buffer.seek(0)
    return buffer


def generate_fit_sales_pdf(rows, title='Fits Report'):
    """
    Generate a Fits report PDF (weekly or monthly).

    `rows` is a list of dicts with keys: pk, customer, sale_number, order_date,
    fit_date, sale_value, designer.
    Returns a BytesIO buffer.
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
        title=title,
    )

    elements = []
    page_width = A4[0] - 30 * mm

    # ── HEADER ─────────────────────────────────────────────────────────
    logo_path = os.path.join(settings.BASE_DIR, 'static', 'images', 'logo-full-light.png')
    if os.path.exists(logo_path):
        logo = Image(logo_path, width=50 * mm, height=12 * mm)
        logo.hAlign = 'LEFT'
    else:
        logo = Paragraph('<b>Sliderobes</b>', styles['Normal'])

    title_para = Paragraph(
        f'<font size="14" color="#1a1a2e"><b>{title}</b></font>',
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
    elements.append(Spacer(1, 5 * mm))

    # ── SUMMARY BLOCK ───────────────────────────────────────────────
    total_value = sum(r['sale_value'] for r in rows)
    info_data = [
        ['Fits', f'{len(rows):,}', 'Total Value', f'\u00a3{total_value:,.0f}'],
        ['Generated', datetime.now().strftime('%d %b %Y at %H:%M'), '', ''],
    ]
    info_table = Table(info_data, colWidths=[
        page_width * 0.15, page_width * 0.35,
        page_width * 0.20, page_width * 0.30,
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
    elements.append(Spacer(1, 6 * mm))

    # ── FITS TABLE ────────────────────────────────────────────────────
    col_widths = [
        page_width * 0.26,  # Customer
        page_width * 0.10,  # Sale #
        page_width * 0.12,  # Order Date
        page_width * 0.12,  # Fit Date
        page_width * 0.22,  # Designer
        page_width * 0.18,  # Sale Value
    ]
    headers = ['Customer', 'Sale #', 'Order Date', 'Fit Date', 'Designer', 'Sale Value']
    table_data = [headers]
    for r in rows:
        table_data.append([
            Paragraph(r['customer'], styles['CellText']),
            r['sale_number'] or '\u2014',
            r['order_date'] or '\u2014',
            r['fit_date'] or '\u2014',
            r['designer'] or '\u2014',
            f'\u00a3{r["sale_value"]:,.0f}',
        ])
    table_data.append([
        Paragraph(f'<b>{len(rows)} fits</b>', styles['CellText']),
        '', '', '', 'Total',
        f'\u00a3{total_value:,.0f}',
    ])

    elements.append(_build_table(table_data, col_widths, has_total_row=True))
    doc.build(elements)
    buffer.seek(0)
    return buffer


def generate_avg_sales_pdf(rows, grand_total=0, grand_count=0, period=''):
    """
    Generate a 12-Month Average Sale Value breakdown PDF.

    `rows` is a list of dicts: month, count, total, avg.
    Returns a BytesIO buffer.
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
        title='Average Sale Value \u2014 Last 12 Months',
    )

    elements = []
    page_width = A4[0] - 30 * mm

    # ── HEADER ─────────────────────────────────────────────────────────
    logo_path = os.path.join(settings.BASE_DIR, 'static', 'images', 'logo-full-light.png')
    if os.path.exists(logo_path):
        logo = Image(logo_path, width=50 * mm, height=12 * mm)
        logo.hAlign = 'LEFT'
    else:
        logo = Paragraph('<b>Sliderobes</b>', styles['Normal'])

    title_para = Paragraph(
        '<font size="14" color="#1a1a2e"><b>Average Sale Value \u2014 Last 12 Months</b></font>',
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
    elements.append(Spacer(1, 5 * mm))

    # ── SUMMARY BLOCK ───────────────────────────────────────────────
    daily_avg = round(grand_total / 365, 0) if grand_total else 0
    info_data = [
        ['Period', period, 'Total Value', f'\u00a3{grand_total:,.0f}'],
        ['Total Fits', f'{grand_count:,}', 'Daily Avg Revenue', f'\u00a3{daily_avg:,.0f}'],
    ]
    info_table = Table(info_data, colWidths=[
        page_width * 0.15, page_width * 0.35,
        page_width * 0.20, page_width * 0.30,
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
    elements.append(Spacer(1, 6 * mm))

    # ── MONTHLY TABLE ──────────────────────────────────────────────────
    col_widths = [
        page_width * 0.30,  # Month
        page_width * 0.15,  # Fits
        page_width * 0.30,  # Total Value
        page_width * 0.25,  # Avg Sale
    ]
    headers = ['Month', 'Fits', 'Total Value', 'Avg Sale Value']
    table_data = [headers]
    for r in rows:
        table_data.append([
            r['month'],
            f'{r["count"]:,}',
            f'\u00a3{r["total"]:,.0f}',
            f'\u00a3{r["avg"]:,.0f}',
        ])
    avg_per_sale = round(grand_total / grand_count, 0) if grand_count else 0
    table_data.append([
        Paragraph('<b>Total</b>', styles['CellText']),
        Paragraph(f'<b>{grand_count:,}</b>', styles['CellText']),
        Paragraph(f'<b>\u00a3{grand_total:,.0f}</b>', styles['CellText']),
        Paragraph(f'<b>\u00a3{avg_per_sale:,.0f}</b>', styles['CellText']),
    ])

    elements.append(_build_table(table_data, col_widths, has_total_row=True))
    doc.build(elements)
    buffer.seek(0)
    return buffer