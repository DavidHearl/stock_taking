"""
Purchase Order PDF generator.

Generates a professional PDF matching the Sliderobes PO layout:
  - Company logo header
  - Supplier & delivery details
  - PO number, date, ABN
  - Line items table with Quantity, Rate, Amount
  - Subtotal, VAT, Total
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
BRAND_DARK = colors.HexColor('#1a2332')
BRAND_ACCENT = colors.HexColor('#2c5f7c')
HEADER_BG = colors.HexColor('#f5f7fa')
ROW_ALT = colors.HexColor('#f9fafb')
BORDER_COLOR = colors.HexColor('#e2e6ea')
TEXT_PRIMARY = colors.HexColor('#1a1a2e')
TEXT_SECONDARY = colors.HexColor('#6b7280')
TEXT_MUTED = colors.HexColor('#9ca3af')


def _get_styles():
    """Build custom paragraph styles for PO document."""
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(
        'POTitle',
        parent=styles['Heading1'],
        fontSize=22,
        leading=26,
        textColor=TEXT_PRIMARY,
        spaceAfter=2,
        spaceBefore=0,
        fontName='Helvetica-Bold',
    ))
    styles.add(ParagraphStyle(
        'SupplierName',
        parent=styles['Normal'],
        fontSize=10,
        leading=14,
        textColor=TEXT_PRIMARY,
        fontName='Helvetica-Bold',
    ))
    styles.add(ParagraphStyle(
        'AddressText',
        parent=styles['Normal'],
        fontSize=9,
        leading=13,
        textColor=TEXT_PRIMARY,
    ))
    styles.add(ParagraphStyle(
        'LabelText',
        parent=styles['Normal'],
        fontSize=9,
        leading=12,
        textColor=TEXT_SECONDARY,
        fontName='Helvetica-Bold',
    ))
    styles.add(ParagraphStyle(
        'ValueText',
        parent=styles['Normal'],
        fontSize=9,
        leading=12,
        textColor=TEXT_PRIMARY,
    ))
    styles.add(ParagraphStyle(
        'CellText',
        parent=styles['Normal'],
        fontSize=8,
        leading=11,
        textColor=TEXT_PRIMARY,
    ))
    styles.add(ParagraphStyle(
        'CellTextRight',
        parent=styles['Normal'],
        fontSize=8,
        leading=11,
        textColor=TEXT_PRIMARY,
        alignment=TA_RIGHT,
    ))
    styles.add(ParagraphStyle(
        'TotalLabel',
        parent=styles['Normal'],
        fontSize=9,
        leading=13,
        textColor=TEXT_PRIMARY,
        fontName='Helvetica-Bold',
        alignment=TA_RIGHT,
    ))
    styles.add(ParagraphStyle(
        'TotalValue',
        parent=styles['Normal'],
        fontSize=9,
        leading=13,
        textColor=TEXT_PRIMARY,
        alignment=TA_RIGHT,
    ))
    styles.add(ParagraphStyle(
        'GrandTotalLabel',
        parent=styles['Normal'],
        fontSize=10,
        leading=14,
        textColor=TEXT_PRIMARY,
        fontName='Helvetica-Bold',
        alignment=TA_RIGHT,
    ))
    styles.add(ParagraphStyle(
        'GrandTotalValue',
        parent=styles['Normal'],
        fontSize=10,
        leading=14,
        textColor=TEXT_PRIMARY,
        fontName='Helvetica-Bold',
        alignment=TA_RIGHT,
    ))
    return styles


def _format_date(date_str):
    """Try to format a date string to DD/MM/YYYY."""
    if not date_str:
        return ''
    # Try various formats
    for fmt in ('%Y-%m-%dT%H:%M:%S', '%Y-%m-%dT%H:%M:%S.%f', '%Y-%m-%d', '%d/%m/%Y'):
        try:
            return datetime.strptime(date_str[:19], fmt).strftime('%d/%m/%Y')
        except (ValueError, TypeError):
            continue
    return str(date_str)


def _currency_symbol(currency):
    """Return the currency symbol for a given currency code."""
    symbols = {
        'EUR': '€',
        'GBP': '£',
        'USD': '$',
    }
    return symbols.get(currency, currency + ' ')


def generate_purchase_order_pdf(purchase_order, products):
    """
    Generate a Purchase Order PDF.

    Args:
        purchase_order: PurchaseOrder model instance
        products: QuerySet of PurchaseOrderProduct items

    Returns:
        BytesIO buffer containing the PDF
    """
    buffer = io.BytesIO()
    styles = _get_styles()

    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=20 * mm,
        rightMargin=20 * mm,
        topMargin=20 * mm,
        bottomMargin=20 * mm,
        title=f'Purchase Order - {purchase_order.display_number}',
    )

    elements = []
    page_width = A4[0] - 40 * mm  # usable width

    # ─── LOGO ──────────────────────────────────────────────────
    logo_path = os.path.join(settings.BASE_DIR, 'static', 'images', 'logo-icon-dark.png')
    if os.path.exists(logo_path):
        logo = Image(logo_path, width=18 * mm, height=18 * mm)
        logo.hAlign = 'CENTER'
        elements.append(logo)
        elements.append(Spacer(1, 6 * mm))

    # ─── HEADER: Title + Date/PO info side by side ─────────────
    currency_sym = _currency_symbol(purchase_order.currency or 'EUR')

    # Left side: Purchase Order title + supplier info
    left_parts = []
    left_parts.append(Paragraph('<b>Purchase Order</b>', styles['POTitle']))

    # Supplier details
    supplier = None
    if purchase_order.supplier_id:
        from .models import Supplier
        supplier = Supplier.objects.filter(workguru_id=purchase_order.supplier_id).first()

    supplier_name = purchase_order.supplier_name or ''
    if supplier_name:
        left_parts.append(Paragraph(f'<b>{supplier_name}</b>', styles['SupplierName']))

    # Supplier address
    if supplier:
        addr_lines = []
        if supplier.address_1:
            addr_lines.append(supplier.address_1)
        if supplier.address_2:
            addr_lines.append(supplier.address_2)
        city_parts = []
        if supplier.city:
            city_parts.append(supplier.city)
        if supplier.state:
            city_parts.append(supplier.state)
        if supplier.postcode:
            city_parts.append(supplier.postcode)
        if city_parts:
            addr_lines.append(' '.join(city_parts))
        if supplier.country:
            addr_lines.append(supplier.country)
        if addr_lines:
            left_parts.append(Paragraph('<br/>'.join(addr_lines), styles['AddressText']))

    left_content = []
    for p in left_parts:
        left_content.append(p)

    # Right side: Date, PO Number, ABN
    right_data = []
    date_str = _format_date(purchase_order.issue_date)
    if date_str:
        right_data.append([
            Paragraph('<b>Date:</b>', styles['LabelText']),
            Paragraph(date_str, styles['ValueText']),
        ])
    right_data.append([
        Paragraph('<b>PO Number:</b>', styles['LabelText']),
        Paragraph(purchase_order.display_number or '', styles['ValueText']),
    ])
    # ABN from supplier
    abn = ''
    if supplier and supplier.abn:
        abn = supplier.abn
    right_data.append([
        Paragraph('<b>ABN:</b>', styles['LabelText']),
        Paragraph(abn, styles['ValueText']),
    ])

    right_table = Table(right_data, colWidths=[22 * mm, 40 * mm])
    right_table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('TOPPADDING', (0, 0), (-1, -1), 1),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 1),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 4),
    ]))

    # Combine left and right into a header row
    left_cell = []
    for p in left_parts:
        left_cell.append(p)

    header_table = Table(
        [[left_cell, right_table]],
        colWidths=[page_width * 0.55, page_width * 0.45],
    )
    header_table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
    ]))
    elements.append(header_table)
    elements.append(Spacer(1, 8 * mm))

    # ─── DELIVER TO / DELIVERY INSTRUCTIONS ───────────────────
    deliver_lines = []
    deliver_lines.append(Paragraph('<b>Deliver to:</b>', styles['LabelText']))
    deliver_lines.append(Paragraph('Reddington Jordan Ltd t/a Sliderobes', styles['AddressText']))
    if purchase_order.delivery_address_1:
        deliver_lines.append(Paragraph(purchase_order.delivery_address_1, styles['AddressText']))
    else:
        deliver_lines.append(Paragraph('61-63 Boucher Crescent', styles['AddressText']))
    if purchase_order.delivery_address_2:
        deliver_lines.append(Paragraph(purchase_order.delivery_address_2, styles['AddressText']))
    else:
        deliver_lines.append(Paragraph('Belfast  BT12 6HU', styles['AddressText']))

    delivery_instr = []
    delivery_instr.append(Paragraph('<b>Delivery Instructions</b>', styles['LabelText']))
    if purchase_order.delivery_instructions:
        delivery_instr.append(Paragraph(purchase_order.delivery_instructions, styles['AddressText']))

    deliver_table = Table(
        [[deliver_lines, delivery_instr]],
        colWidths=[page_width * 0.5, page_width * 0.5],
    )
    deliver_table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
    ]))
    elements.append(deliver_table)
    elements.append(Spacer(1, 8 * mm))

    # ─── ORDER TABLE ──────────────────────────────────────────
    elements.append(Paragraph('<b>Order</b>', styles['LabelText']))
    elements.append(Spacer(1, 2 * mm))

    # Table header
    col_widths = [
        page_width * 0.15,   # SKU / Code
        page_width * 0.40,   # Description
        page_width * 0.12,   # Quantity
        page_width * 0.15,   # Rate
        page_width * 0.18,   # Amount
    ]

    table_data = [[
        Paragraph('<b>Code</b>', styles['CellText']),
        Paragraph('', styles['CellText']),
        Paragraph('<b>Quantity</b>', styles['CellTextRight']),
        Paragraph('<b>Rate</b>', styles['CellTextRight']),
        Paragraph('<b>Amount</b>', styles['CellTextRight']),
    ]]

    subtotal = Decimal('0')
    for product in products:
        qty = product.order_quantity or product.quantity or Decimal('0')
        rate = product.order_price or Decimal('0')
        line_total = product.line_total or (qty * rate)
        subtotal += Decimal(str(line_total))

        table_data.append([
            Paragraph(str(product.sku or product.supplier_code or ''), styles['CellText']),
            Paragraph(str(product.name or product.description or ''), styles['CellText']),
            Paragraph(f'{qty:,.0f}' if qty == int(qty) else f'{qty:,.2f}', styles['CellTextRight']),
            Paragraph(f'{rate:,.2f}', styles['CellTextRight']),
            Paragraph(f'{line_total:,.2f}', styles['CellTextRight']),
        ])

    order_table = Table(table_data, colWidths=col_widths, repeatRows=1)

    style_commands = [
        # Header
        ('LINEBELOW', (0, 0), (-1, 0), 0.8, BRAND_DARK),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 8),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 6),
        ('TOPPADDING', (0, 0), (-1, 0), 6),
        # Body
        ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
        ('FONTSIZE', (0, 1), (-1, -1), 8),
        ('TOPPADDING', (0, 1), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 1), (-1, -1), 5),
        ('LINEBELOW', (0, 1), (-1, -1), 0.3, BORDER_COLOR),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (-1, -1), 4),
        ('RIGHTPADDING', (0, 0), (-1, -1), 4),
        # Right-align numeric columns
        ('ALIGN', (2, 0), (-1, -1), 'RIGHT'),
    ]

    order_table.setStyle(TableStyle(style_commands))
    elements.append(order_table)
    elements.append(Spacer(1, 4 * mm))

    # ─── TOTALS ───────────────────────────────────────────────
    # Calculate VAT (use tax_total from PO if available, else estimate at 23%)
    tax_total = purchase_order.tax_total or Decimal('0')
    if not tax_total and subtotal:
        tax_total = subtotal * Decimal('0.23')

    grand_total = subtotal + tax_total

    # Use actual PO total if it differs (trust the PO data)
    if purchase_order.total and abs(purchase_order.total - subtotal) > Decimal('0.01'):
        subtotal = purchase_order.total
        if purchase_order.tax_total:
            tax_total = purchase_order.tax_total
        else:
            tax_total = subtotal * Decimal('0.23')
        grand_total = subtotal + tax_total

    totals_data = [
        [
            '', '',
            Paragraph('Subtotal', styles['TotalLabel']),
            Paragraph(f'{currency_sym} {subtotal:,.2f}', styles['TotalValue']),
        ],
        [
            '', '',
            Paragraph('VAT', styles['TotalLabel']),
            Paragraph(f'{currency_sym} {tax_total:,.2f}', styles['TotalValue']),
        ],
        [
            '', '',
            Paragraph('<b>Total</b>', styles['GrandTotalLabel']),
            Paragraph(f'<b>{currency_sym} {grand_total:,.2f}</b>', styles['GrandTotalValue']),
        ],
    ]

    totals_table = Table(
        totals_data,
        colWidths=[page_width * 0.35, page_width * 0.25, page_width * 0.20, page_width * 0.20],
    )
    totals_table.setStyle(TableStyle([
        ('ALIGN', (2, 0), (-1, -1), 'RIGHT'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ('LINEABOVE', (2, 0), (-1, 0), 0.8, BRAND_DARK),
        ('LINEABOVE', (2, 2), (-1, 2), 0.8, BRAND_DARK),
        ('LEFTPADDING', (0, 0), (-1, -1), 4),
        ('RIGHTPADDING', (0, 0), (-1, -1), 4),
    ]))
    elements.append(totals_table)

    # ─── BUILD PDF ────────────────────────────────────────────
    doc.build(elements)
    buffer.seek(0)
    return buffer
