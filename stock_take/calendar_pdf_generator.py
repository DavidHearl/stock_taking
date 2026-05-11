"""
Calendar Fit Schedule PDF generator.

Generates an A4 landscape PDF covering five consecutive weeks of fit
appointments (typically: 2 weeks ago, last week, this week, next week,
the week after).
"""

import io
import sys
from datetime import date


def _fmt_day(d):
    """Format a date as 'Mon 5 Apr' (no leading zero) on all platforms."""
    # %#d on Windows, %-d on Linux/macOS
    flag = '#' if sys.platform == 'win32' else '-'
    return d.strftime(f'%a %{flag}d %b')


def _fmt_day_year(d):
    """Format a date as 'Sat 10 May 2026' (no leading zero) on all platforms."""
    flag = '#' if sys.platform == 'win32' else '-'
    return d.strftime(f'%a %{flag}d %b %Y')

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_RIGHT
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer,
)

# ── Brand colours ─────────────────────────────────────────────────────────────
BRAND_DARK    = colors.HexColor('#1a2332')
BRAND_ACCENT  = colors.HexColor('#2c5f7c')
HEADER_BG     = colors.HexColor('#f5f7fa')
ROW_ALT       = colors.HexColor('#f9fafb')
BORDER_COLOR  = colors.HexColor('#e2e6ea')
TEXT_PRIMARY  = colors.HexColor('#1a1a2e')
TEXT_SECONDARY = colors.HexColor('#6b7280')
SUCCESS_COLOR = colors.HexColor('#16a34a')
WARNING_COLOR = colors.HexColor('#d97706')

PAGE   = A4
W, _H  = PAGE
MARGIN = 14 * mm

# ── Column layout (portrait A4 ≈ 182mm usable width) ────────────────────────
COL_HEADERS = ['Date', 'Fitter', 'Customer', 'Sale No.', 'Days', 'Value', 'Outstanding']
COL_WIDTHS  = [24*mm, 28*mm, 62*mm, 22*mm, 12*mm, 17*mm, 17*mm]
# Total: 182mm  (182mm usable)


def _styles():
    ss = getSampleStyleSheet()

    def add(name, **kw):
        ss.add(ParagraphStyle(name, parent=ss['Normal'], **kw))

    add('CalTitle',   fontSize=15, leading=19, textColor=BRAND_DARK,
        fontName='Helvetica-Bold', spaceAfter=2)
    add('CalSub',     fontSize=8,  leading=11, textColor=TEXT_SECONDARY)
    add('WeekHdr',    fontSize=9,  leading=12, textColor=colors.white,
        fontName='Helvetica-Bold')
    add('ColHdr',     fontSize=7.5, leading=10, textColor=TEXT_SECONDARY,
        fontName='Helvetica-Bold')
    add('Cell',       fontSize=8,  leading=11, textColor=TEXT_PRIMARY)
    add('CellMuted',  fontSize=8,  leading=11, textColor=TEXT_SECONDARY)
    add('CellRight',  fontSize=8,  leading=11, textColor=TEXT_PRIMARY,
        alignment=TA_RIGHT)
    add('CellWarn',   fontSize=8,  leading=11, textColor=WARNING_COLOR,
        fontName='Helvetica-Bold', alignment=TA_RIGHT)
    return ss


def generate_calendar_pdf(weeks_data):
    """
    Build and return a BytesIO containing the PDF.

    weeks_data  — list of dicts, one per week:
        {
            'week_start': date,
            'week_end':   date,
            'appointments': [
                {
                    'date':          date,
                    'fitter_name':   str,
                    'customer_name': str,
                    'sale_number':   str,
                    'postcode':      str,
                    'range_name':    str,
                    'duration':      int,
                    'sale_value':    Decimal or None,
                    'outstanding':   Decimal or None,
                    'is_remedial':   bool,
                }
            ]
        }
    """
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=PAGE,
        leftMargin=MARGIN, rightMargin=MARGIN,
        topMargin=14 * mm, bottomMargin=14 * mm,
    )
    ss = _styles()
    story = []

    # ── Document title ────────────────────────────────────────────────────────
    if weeks_data:
        r_start = weeks_data[0]['week_start']
        r_end   = weeks_data[-1]['week_end']
        title   = f"Fit Schedule  ·  {r_start.strftime('%d %b')} – {r_end.strftime('%d %b %Y')}"
    else:
        title = "Fit Schedule"

    story.append(Paragraph(title, ss['CalTitle']))
    story.append(Paragraph(f"Generated {date.today().strftime('%d %b %Y')}", ss['CalSub']))
    story.append(Spacer(1, 6 * mm))

    total_w = sum(COL_WIDTHS)

    for week in weeks_data:
        ws    = week['week_start']
        we    = week['week_end']
        appts = week['appointments']

        # ── Week header bar ───────────────────────────────────────────────────
        week_label = f"Week of  {_fmt_day(ws)} – {_fmt_day_year(we)}"
        hdr_tbl = Table(
            [[Paragraph(week_label, ss['WeekHdr'])] + [''] * (len(COL_HEADERS) - 1)],
            colWidths=COL_WIDTHS,
        )
        hdr_tbl.setStyle(TableStyle([
            ('SPAN',            (0, 0), (-1, 0)),
            ('BACKGROUND',      (0, 0), (-1, 0), BRAND_ACCENT),
            ('TOPPADDING',      (0, 0), (-1, 0), 5),
            ('BOTTOMPADDING',   (0, 0), (-1, 0), 5),
            ('LEFTPADDING',     (0, 0), (-1, 0), 8),
            ('RIGHTPADDING',    (0, 0), (-1, 0), 8),
        ]))
        story.append(hdr_tbl)

        if not appts:
            empty_tbl = Table(
                [['No fit appointments this week']],
                colWidths=[total_w],
            )
            empty_tbl.setStyle(TableStyle([
                ('FONTSIZE',      (0, 0), (-1, -1), 8),
                ('TEXTCOLOR',     (0, 0), (-1, -1), TEXT_SECONDARY),
                ('LEFTPADDING',   (0, 0), (-1, -1), 8),
                ('TOPPADDING',    (0, 0), (-1, -1), 6),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
                ('BACKGROUND',    (0, 0), (-1, -1), HEADER_BG),
                ('LINEBELOW',     (0, 0), (-1, -1), 0.3, BORDER_COLOR),
            ]))
            story.append(empty_tbl)
            story.append(Spacer(1, 5 * mm))
            continue

        # ── Column header row ─────────────────────────────────────────────────
        table_data = [[Paragraph(h, ss['ColHdr']) for h in COL_HEADERS]]

        # ── Data rows ─────────────────────────────────────────────────────────
        prev_date = None
        for appt in appts:
            d = appt['date']
            date_str = _fmt_day(d) if d != prev_date else ''
            prev_date = d

            name = appt.get('customer_name') or ''
            if appt.get('is_remedial'):
                name = f"[R] {name}"

            sv  = appt.get('sale_value')
            ost = appt.get('outstanding')
            sv_str  = f"£{sv:,.0f}"  if sv  is not None else '–'
            ost_str = f"£{ost:,.0f}" if ost is not None else '–'

            ost_style = ss['CellWarn'] if (ost and ost > 0) else ss['CellRight']

            row = [
                Paragraph(date_str,                        ss['Cell']),
                Paragraph(appt.get('fitter_name') or '',   ss['Cell']),
                Paragraph(name,                             ss['Cell']),
                Paragraph(appt.get('sale_number') or '',   ss['Cell']),
                Paragraph(str(appt.get('duration') or 1),  ss['Cell']),
                Paragraph(sv_str,                           ss['CellRight']),
                Paragraph(ost_str,                          ost_style),
            ]
            table_data.append(row)

        tbl = Table(table_data, colWidths=COL_WIDTHS, repeatRows=1)
        tbl.setStyle(TableStyle([
            # Header row
            ('BACKGROUND',    (0, 0), (-1, 0), HEADER_BG),
            ('TOPPADDING',    (0, 0), (-1, 0), 4),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 4),
            ('LINEBELOW',     (0, 0), (-1, 0), 0.6, BORDER_COLOR),
            # All cells
            ('FONTSIZE',      (0, 0), (-1, -1), 8),
            ('LEFTPADDING',   (0, 0), (-1, -1), 6),
            ('RIGHTPADDING',  (0, 0), (-1, -1), 6),
            ('TOPPADDING',    (0, 1), (-1, -1), 4),
            ('BOTTOMPADDING', (0, 1), (-1, -1), 4),
            ('VALIGN',        (0, 0), (-1, -1), 'MIDDLE'),
            # Alternating rows
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, ROW_ALT]),
            # Row separators
            ('LINEBELOW',     (0, 1), (-1, -1), 0.3, BORDER_COLOR),
        ]))
        story.append(tbl)
        story.append(Spacer(1, 6 * mm))

    doc.build(story)
    buf.seek(0)
    return buf
