"""
Raumplus Order PDF generator.

Produces a professional A4 order report matching the Sliderobes PO PDF style:
  - Company logo header
  - Generated date / prepared by
  - Single order table, sections indicated by full-width header rows
  - Totals footer
Uses ReportLab, consistent with po_pdf_generator.py.
"""

import io
import os
import datetime
from decimal import Decimal

from django.conf import settings

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer,
    Image, HRFlowable,
)

# â”€â”€ Brand colours (match po_pdf_generator) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
BRAND_DARK    = colors.HexColor('#1a2332')
BRAND_ACCENT  = colors.HexColor('#2c5f7c')
HEADER_BG     = colors.HexColor('#f5f7fa')
ROW_ALT       = colors.HexColor('#f9fafb')
BORDER_COLOR  = colors.HexColor('#e2e6ea')
TEXT_PRIMARY  = colors.HexColor('#1a1a2e')
TEXT_SECONDARY = colors.HexColor('#6b7280')

SECTION_COLORS = {
    'critical':  colors.HexColor('#c0392b'),
    'predicted': colors.HexColor('#b7770d'),
    'suggested': colors.HexColor('#1e7e34'),
    'manual':    colors.HexColor('#1a56db'),
}
SECTION_BG = {
    'critical':  colors.HexColor('#fdf0ef'),
    'predicted': colors.HexColor('#fdf7ee'),
    'suggested': colors.HexColor('#edfaf3'),
    'manual':    colors.HexColor('#eef4fe'),
}
SECTION_LABELS = {
    'critical':  'Critical Items',
    'predicted': 'Predicted Items',
    'suggested': 'Suggested Items',
    'manual':    'Manually Added',
}


def _styles():
    base = getSampleStyleSheet()
    s = {}
    s['title'] = ParagraphStyle('RauTitle', parent=base['Normal'],
                                fontSize=22, leading=26, fontName='Helvetica-Bold',
                                textColor=TEXT_PRIMARY, spaceAfter=2)
    s['sub'] = ParagraphStyle('RauSub', parent=base['Normal'],
                              fontSize=9, leading=12, textColor=TEXT_SECONDARY)
    s['label'] = ParagraphStyle('RauLabel', parent=base['Normal'],
                                fontSize=9, leading=12, fontName='Helvetica-Bold',
                                textColor=TEXT_SECONDARY)
    s['col_hdr'] = ParagraphStyle('RauColHdr', parent=base['Normal'],
                                  fontSize=8, leading=10, fontName='Helvetica-Bold',
                                  textColor=TEXT_SECONDARY)
    s['col_hdr_r'] = ParagraphStyle('RauColHdrR', parent=base['Normal'],
                                    fontSize=8, leading=10, fontName='Helvetica-Bold',
                                    textColor=TEXT_SECONDARY, alignment=TA_RIGHT)
    s['cell'] = ParagraphStyle('RauCell', parent=base['Normal'],
                               fontSize=8, leading=11, textColor=TEXT_PRIMARY)
    s['cell_r'] = ParagraphStyle('RauCellR', parent=base['Normal'],
                                 fontSize=8, leading=11, textColor=TEXT_PRIMARY,
                                 alignment=TA_RIGHT)
    s['cell_bold_r'] = ParagraphStyle('RauCellBoldR', parent=base['Normal'],
                                      fontSize=8, leading=11, fontName='Helvetica-Bold',
                                      textColor=TEXT_PRIMARY, alignment=TA_RIGHT)
    s['cell_mono'] = ParagraphStyle('RauMono', parent=base['Normal'],
                                    fontSize=7.5, leading=10, fontName='Courier',
                                    textColor=BRAND_ACCENT)
    s['sec_hdr'] = ParagraphStyle('RauSecHdr', parent=base['Normal'],
                                  fontSize=8, leading=10, fontName='Helvetica-Bold')
    s['total_lbl'] = ParagraphStyle('RauTotLbl', parent=base['Normal'],
                                    fontSize=9, leading=13, fontName='Helvetica-Bold',
                                    textColor=TEXT_PRIMARY, alignment=TA_RIGHT)
    s['total_val'] = ParagraphStyle('RauTotVal', parent=base['Normal'],
                                    fontSize=9, leading=13, textColor=TEXT_PRIMARY,
                                    alignment=TA_RIGHT)
    s['grand_lbl'] = ParagraphStyle('RauGrandLbl', parent=base['Normal'],
                                    fontSize=10, leading=14, fontName='Helvetica-Bold',
                                    textColor=TEXT_PRIMARY, alignment=TA_RIGHT)
    s['grand_val'] = ParagraphStyle('RauGrandVal', parent=base['Normal'],
                                    fontSize=10, leading=14, fontName='Helvetica-Bold',
                                    textColor=TEXT_PRIMARY, alignment=TA_RIGHT)
    return s


def _strip_sku(sku: str) -> str:
    """Return the supplier art number (after RAU_)."""
    idx = sku.upper().find('RAU_')
    return sku[idx + 4:] if idx != -1 else sku


def generate_raumplus_order_pdf(items: list, user=None) -> io.BytesIO:
    """
    Build the PDF and return a BytesIO buffer ready for HttpResponse.

    ``items`` is the serialised RAU_ORDER_ROWS list from the modal:
        [{sku, name, order_qty, cost, suggested_qty, min_order_qty, section, included}, ...]
    Only rows with included=True and order_qty > 0 are rendered.
    """
    buf = io.BytesIO()
    page_width = A4[0] - 40 * mm   # usable width: 257 mm

    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=20 * mm,
        rightMargin=20 * mm,
        topMargin=20 * mm,
        bottomMargin=20 * mm,
        title='Raumplus Order',
    )

    st = _styles()
    story = []
    today = datetime.date.today().strftime('%d %B %Y')
    generated_by = user.get_full_name() or user.username if user else 'System'

    # â”€â”€ Logo â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    logo_path = os.path.join(settings.BASE_DIR, 'static', 'images', 'logo-full-light.png')
    if os.path.exists(logo_path):
        logo = Image(logo_path, width=50 * mm, height=14 * mm, kind='proportional')
        logo.hAlign = 'CENTER'
        story.append(logo)
        story.append(Spacer(1, 6 * mm))

    # â”€â”€ Header: title left, date/prepared right â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    left_cell = [
        Paragraph('<b>Raumplus Order</b>', st['title']),
        Paragraph('Raumplus GmbH &amp; Co. KG', st['sub']),
    ]
    right_data = [
        [Paragraph('<b>Date:</b>', st['label']), Paragraph(today, st['sub'])],
        [Paragraph('<b>Prepared by:</b>', st['label']), Paragraph(generated_by, st['sub'])],
    ]
    right_tbl = Table(right_data, colWidths=[28 * mm, 50 * mm])
    right_tbl.setStyle(TableStyle([
        ('VALIGN',        (0, 0), (-1, -1), 'TOP'),
        ('TOPPADDING',    (0, 0), (-1, -1), 1),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 1),
        ('LEFTPADDING',   (0, 0), (-1, -1), 0),
        ('RIGHTPADDING',  (0, 0), (-1, -1), 4),
    ]))
    header_tbl = Table(
        [[left_cell, right_tbl]],
        colWidths=[page_width * 0.6, page_width * 0.4],
    )
    header_tbl.setStyle(TableStyle([
        ('VALIGN',       (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING',  (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
    ]))
    story.append(header_tbl)
    story.append(Spacer(1, 6 * mm))
    story.append(HRFlowable(width='100%', thickness=1.5, color=BRAND_DARK, spaceAfter=6 * mm))

    # â”€â”€ Column widths (total = 240 mm, well within 257 mm usable) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    # Art No. | Description | Sugg. | Order Qty | Change | Unit Cost | Line Cost
    col_widths = [22 * mm, 68 * mm, 14 * mm, 18 * mm, 14 * mm, 17 * mm, 17 * mm]  # sums to 170 mm (A4 - margins)
    NUM_COLS = len(col_widths)

    # â”€â”€ Group items by section â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    section_order = ['critical', 'predicted', 'suggested', 'manual']
    grouped: dict[str, list] = {s: [] for s in section_order}
    for row in items:
        if not row.get('included', True):
            continue
        qty = int(row.get('order_qty', 0) or 0)
        if qty <= 0:
            continue
        sec = row.get('section', 'manual')
        if sec not in grouped:
            sec = 'manual'
        grouped[sec].append(row)

    # â”€â”€ Build single order table â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    tbl_data = [[
        Paragraph('Art No.', st['col_hdr']),
        Paragraph('Description', st['col_hdr']),
        Paragraph('Sugg.', st['col_hdr_r']),
        Paragraph('Order Qty', st['col_hdr_r']),
        Paragraph('Change', st['col_hdr_r']),
        Paragraph('Unit Cost', st['col_hdr_r']),
        Paragraph('Line Cost', st['col_hdr_r']),
    ]]

    # Row-level TableStyle commands accumulated
    tbl_styles = [
        # Column header row
        ('BACKGROUND',    (0, 0), (-1, 0), HEADER_BG),
        ('LINEBELOW',     (0, 0), (-1, 0), 1.2, BRAND_DARK),
        ('TOPPADDING',    (0, 0), (-1, 0), 6),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 6),
        ('ALIGN',         (2, 0), (-1, 0), 'RIGHT'),
        # Global defaults
        ('FONTNAME',      (0, 0), (-1, -1), 'Helvetica'),
        ('FONTSIZE',      (0, 0), (-1, -1), 8),
        ('LEFTPADDING',   (0, 0), (-1, -1), 6),
        ('RIGHTPADDING',  (0, 0), (-1, -1), 6),
        ('VALIGN',        (0, 0), (-1, -1), 'MIDDLE'),
        ('BOX',           (0, 0), (-1, -1), 0.5, BORDER_COLOR),
    ]

    grand_total_ex = Decimal('0.00')
    included_count = 0
    data_row_idx = 1   # rows[0] = column header

    for sec in section_order:
        rows = grouped.get(sec, [])
        if not rows:
            continue

        sec_color = SECTION_COLORS[sec]
        sec_bg    = SECTION_BG[sec]
        sec_label = SECTION_LABELS[sec]

        # Section header row (spans all columns)
        tbl_data.append([
            Paragraph(
                f'{sec_label}  ({len(rows)} item{"s" if len(rows) != 1 else ""})',
                st['sec_hdr'],
            ),
            '', '', '', '', '', '',
        ])
        r = data_row_idx
        tbl_styles += [
            ('SPAN',           (0, r), (-1, r)),
            ('BACKGROUND',     (0, r), (-1, r), sec_bg),
            ('TEXTCOLOR',      (0, r), (-1, r), sec_color),
            ('LINEABOVE',      (0, r), (-1, r), 0.8, sec_color),
            ('LINEBELOW',      (0, r), (-1, r), 0.4, sec_color),
            ('TOPPADDING',     (0, r), (-1, r), 5),
            ('BOTTOMPADDING',  (0, r), (-1, r), 5),
        ]
        data_row_idx += 1

        for i, row in enumerate(rows):
            order_qty = int(row.get('order_qty', 0) or 0)
            suggested = int(row.get('suggested_qty', 0) or 0)
            unit_cost = Decimal(str(row.get('cost', 0) or 0))
            line_cost = unit_cost * order_qty
            grand_total_ex += line_cost
            included_count += 1

            delta = order_qty - suggested
            if delta > 0:
                delta_str = f'+{delta}'
                delta_color = colors.HexColor('#1e7e34')
            elif delta < 0:
                delta_str = str(delta)
                delta_color = colors.HexColor('#c0392b')
            else:
                delta_str = u'\u2014'
                delta_color = TEXT_SECONDARY

            art_no = _strip_sku(row.get('sku', ''))
            bg = ROW_ALT if i % 2 == 0 else colors.white

            tbl_data.append([
                Paragraph(art_no, st['cell_mono']),
                Paragraph(row.get('name', ''), st['cell']),
                Paragraph(str(suggested), st['cell_r']),
                Paragraph(f'<b>{order_qty}</b>', st['cell_bold_r']),
                Paragraph(delta_str, st['cell_r']),
                Paragraph(f'\u00a3{unit_cost:.2f}', st['cell_r']),
                Paragraph(f'<b>\u00a3{line_cost:.2f}</b>', st['cell_bold_r']),
            ])
            r = data_row_idx
            tbl_styles += [
                ('BACKGROUND',    (0, r), (-1, r), bg),
                ('TEXTCOLOR',     (4, r), (4, r),  delta_color),
                ('ALIGN',         (2, r), (-1, r), 'RIGHT'),
                ('LINEBELOW',     (0, r), (-1, r), 0.3, BORDER_COLOR),
                ('TOPPADDING',    (0, r), (-1, r), 4),
                ('BOTTOMPADDING', (0, r), (-1, r), 4),
            ]
            data_row_idx += 1

    order_tbl = Table(tbl_data, colWidths=col_widths, repeatRows=1)
    order_tbl.setStyle(TableStyle(tbl_styles))
    story.append(order_tbl)
    story.append(Spacer(1, 6 * mm))

    # â”€â”€ Totals â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    vat_rate = Decimal('1.19')
    total_inc = grand_total_ex * vat_rate
    lbl_w = sum(col_widths) - 52 * mm

    totals_data = [
        ['', Paragraph('Items:', st['total_lbl']),
              Paragraph(str(included_count), st['total_val'])],
        ['', Paragraph('Total (exc VAT):', st['total_lbl']),
              Paragraph(f'\u00a3{grand_total_ex:.2f}', st['total_val'])],
        ['', Paragraph('Total (inc 19% VAT):', st['grand_lbl']),
              Paragraph(f'\u00a3{total_inc:.2f}', st['grand_val'])],
    ]
    totals_tbl = Table(totals_data, colWidths=[lbl_w, 36 * mm, 20 * mm])
    totals_tbl.setStyle(TableStyle([
        ('ALIGN',         (1, 0), (-1, -1), 'RIGHT'),
        ('TOPPADDING',    (0, 0), (-1, -1), 2),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
        ('LINEABOVE',     (1, 2), (-1, 2), 1.0, BRAND_DARK),
    ]))
    story.append(totals_tbl)
    story.append(Spacer(1, 4 * mm))
    story.append(Paragraph(
        'Note: All unit prices are pre-VAT. VAT rate 19% (German supplier).',
        st['sub'],
    ))

    doc.build(story)
    buf.seek(0)
    return buf

