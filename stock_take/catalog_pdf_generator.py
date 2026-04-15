"""
Product Catalog PDF generator.

Generates a professional catalog grouped by category then supplier,
showing SKU, supplier code, cost, dimensions, etc.
"""

import io
import os
import tempfile
from datetime import datetime
from urllib.request import urlopen
from urllib.error import URLError

from django.conf import settings

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import mm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer,
    Image, KeepTogether, PageBreak
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
CATEGORY_BG = colors.HexColor('#1a2332')
SUPPLIER_BG = colors.HexColor('#2c5f7c')


def _get_styles():
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(
        'CatalogTitle',
        parent=styles['Heading1'],
        fontSize=20,
        leading=24,
        textColor=TEXT_PRIMARY,
        fontName='Helvetica-Bold',
        spaceAfter=2,
    ))
    styles.add(ParagraphStyle(
        'CatalogSubtitle',
        parent=styles['Normal'],
        fontSize=9,
        leading=12,
        textColor=TEXT_SECONDARY,
    ))
    styles.add(ParagraphStyle(
        'CategoryHeading',
        parent=styles['Heading2'],
        fontSize=13,
        leading=16,
        textColor=colors.white,
        fontName='Helvetica-Bold',
        spaceBefore=8,
        spaceAfter=4,
    ))
    styles.add(ParagraphStyle(
        'SupplierHeading',
        parent=styles['Heading3'],
        fontSize=10,
        leading=13,
        textColor=colors.white,
        fontName='Helvetica-Bold',
        spaceBefore=4,
        spaceAfter=2,
    ))
    styles.add(ParagraphStyle(
        'CellText',
        parent=styles['Normal'],
        fontSize=7,
        leading=9,
        textColor=TEXT_PRIMARY,
    ))
    styles.add(ParagraphStyle(
        'CellTextRight',
        parent=styles['Normal'],
        fontSize=7,
        leading=9,
        textColor=TEXT_PRIMARY,
        alignment=TA_RIGHT,
    ))
    styles.add(ParagraphStyle(
        'CellTextCenter',
        parent=styles['Normal'],
        fontSize=7,
        leading=9,
        textColor=TEXT_PRIMARY,
        alignment=TA_CENTER,
    ))
    styles.add(ParagraphStyle(
        'FooterText',
        parent=styles['Normal'],
        fontSize=7,
        leading=9,
        textColor=TEXT_MUTED,
        alignment=TA_CENTER,
    ))
    return styles


def _format_dimension(val):
    """Format a dimension value, stripping trailing zeros."""
    if val is None:
        return ''
    v = float(val)
    if v == int(v):
        return str(int(v))
    return f'{v:.1f}'


def _build_dimensions_str(item):
    """Build a dimensions string like 2400 x 600 x 21."""
    parts = []
    for field in ('length', 'width', 'height'):
        val = getattr(item, field, None)
        if val is not None:
            parts.append(_format_dimension(val))
    if parts:
        return ' × '.join(parts)
    return '-'


def _build_box_dimensions_str(item):
    """Build box dimensions string."""
    parts = []
    for field in ('box_length', 'box_width', 'box_height'):
        val = getattr(item, field, None)
        if val is not None:
            parts.append(_format_dimension(val))
    if parts:
        text = ' × '.join(parts)
        if item.box_quantity:
            text += f' (×{item.box_quantity})'
        return text
    return '-'


def generate_product_catalog_pdf(stock_items, include_images=False):
    """
    Generate a product catalog PDF grouped by category then supplier.

    Args:
        stock_items: QuerySet of StockItem objects (with supplier and category prefetched)
        include_images: If True, include product images in the catalog

    Returns:
        BytesIO buffer containing the PDF
    """
    buffer = io.BytesIO()
    styles = _get_styles()

    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        leftMargin=15 * mm,
        rightMargin=15 * mm,
        topMargin=15 * mm,
        bottomMargin=15 * mm,
        title='Sliderobes Product Catalog',
    )

    elements = []
    page_width = landscape(A4)[0] - 30 * mm

    # ─── LOGO ──────────────────────────────────────────────────
    logo_path = os.path.join(settings.BASE_DIR, 'static', 'images', 'sliderobes_logo.png')
    if os.path.exists(logo_path):
        logo = Image(logo_path, width=50 * mm, height=14 * mm, kind='proportional')
        logo.hAlign = 'CENTER'
        elements.append(logo)
        elements.append(Spacer(1, 4 * mm))

    # ─── TITLE ─────────────────────────────────────────────────
    elements.append(Paragraph('Product Catalog', styles['CatalogTitle']))
    elements.append(Paragraph(
        f'Generated {datetime.now().strftime("%d/%m/%Y")}  •  {stock_items.count()} products',
        styles['CatalogSubtitle']
    ))
    elements.append(Spacer(1, 6 * mm))

    # ─── Group items by category then supplier ─────────────────
    grouped = {}
    for item in stock_items:
        cat_name = item.category.name if item.category else 'Uncategorised'
        sup_name = item.supplier.name if item.supplier else 'No Supplier'
        grouped.setdefault(cat_name, {}).setdefault(sup_name, []).append(item)

    if include_images:
        col_widths = [
            15 * mm,   # Image
            40 * mm,   # SKU
            62 * mm,   # Name
            28 * mm,   # Supplier Code
            18 * mm,   # Cost
            34 * mm,   # Dimensions (mm)
            16 * mm,   # Weight
            28 * mm,   # Box Dims
            page_width - (15+40+62+28+18+34+16+28) * mm,  # Stock (remainder)
        ]
    else:
        col_widths = [
            48 * mm,   # SKU
            72 * mm,   # Name
            30 * mm,   # Supplier Code
            20 * mm,   # Cost
            36 * mm,   # Dimensions (mm)
            18 * mm,   # Weight
            28 * mm,   # Box Dims
            page_width - (48+72+30+20+36+18+28) * mm,  # Stock (remainder)
        ]

    # Build pages per category
    for cat_name in sorted(grouped.keys()):
        suppliers = grouped[cat_name]

        # Category header bar
        cat_header = Table(
            [[Paragraph(cat_name, styles['CategoryHeading'])]],
            colWidths=[page_width],
        )
        cat_header.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), CATEGORY_BG),
            ('TOPPADDING', (0, 0), (-1, -1), 4),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
            ('LEFTPADDING', (0, 0), (-1, -1), 8),
            ('ROUNDEDCORNERS', [4, 4, 0, 0]),
        ]))
        elements.append(cat_header)

        for sup_name in sorted(suppliers.keys()):
            items = suppliers[sup_name]

            # Supplier sub-header
            sup_header = Table(
                [[Paragraph(f'{sup_name}  ({len(items)} items)', styles['SupplierHeading'])]],
                colWidths=[page_width],
            )
            sup_header.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, -1), SUPPLIER_BG),
                ('TOPPADDING', (0, 0), (-1, -1), 3),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
                ('LEFTPADDING', (0, 0), (-1, -1), 8),
            ]))
            elements.append(sup_header)

            # Table header row
            if include_images:
                header_row = [
                    Paragraph('<b>Image</b>', styles['CellTextCenter']),
                    Paragraph('<b>SKU</b>', styles['CellText']),
                    Paragraph('<b>Name</b>', styles['CellText']),
                    Paragraph('<b>Supplier Code</b>', styles['CellText']),
                    Paragraph('<b>Cost</b>', styles['CellTextRight']),
                    Paragraph('<b>Dimensions (mm)</b>', styles['CellTextCenter']),
                    Paragraph('<b>Weight</b>', styles['CellTextRight']),
                    Paragraph('<b>Box Dims</b>', styles['CellTextCenter']),
                    Paragraph('<b>Stock</b>', styles['CellTextRight']),
                ]
            else:
                header_row = [
                    Paragraph('<b>SKU</b>', styles['CellText']),
                    Paragraph('<b>Name</b>', styles['CellText']),
                    Paragraph('<b>Supplier Code</b>', styles['CellText']),
                    Paragraph('<b>Cost</b>', styles['CellTextRight']),
                    Paragraph('<b>Dimensions (mm)</b>', styles['CellTextCenter']),
                    Paragraph('<b>Weight</b>', styles['CellTextRight']),
                    Paragraph('<b>Box Dims</b>', styles['CellTextCenter']),
                    Paragraph('<b>Stock</b>', styles['CellTextRight']),
                ]

            table_data = [header_row]
            row_height = 12 * mm if include_images else None

            for item in sorted(items, key=lambda x: x.sku):
                base_cells = [
                    Paragraph(item.sku or '', styles['CellText']),
                    Paragraph(item.name or '', styles['CellText']),
                    Paragraph(item.supplier_code or '-', styles['CellText']),
                    Paragraph(f'£{item.cost:.2f}' if item.cost else '-', styles['CellTextRight']),
                    Paragraph(_build_dimensions_str(item), styles['CellTextCenter']),
                    Paragraph(f'{_format_dimension(item.weight)} kg' if item.weight else '-', styles['CellTextRight']),
                    Paragraph(_build_box_dimensions_str(item), styles['CellTextCenter']),
                    Paragraph(str(item.quantity), styles['CellTextRight']),
                ]

                if include_images:
                    img_cell = ''
                    if item.image:
                        try:
                            img_url = item.image.url
                            img_data = io.BytesIO(urlopen(img_url, timeout=10).read())
                            img_cell = Image(img_data, width=10 * mm, height=10 * mm, kind='proportional')
                        except Exception:
                            img_cell = Paragraph('-', styles['CellTextCenter'])
                    else:
                        img_cell = Paragraph('-', styles['CellTextCenter'])
                    row = [img_cell] + base_cells
                else:
                    row = base_cells

                table_data.append(row)

            row_heights = [None] + ([row_height] * (len(table_data) - 1)) if include_images else None
            tbl = Table(table_data, colWidths=col_widths, repeatRows=1, rowHeights=row_heights)
            tbl_style = [
                # Header row
                ('BACKGROUND', (0, 0), (-1, 0), HEADER_BG),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, 0), 7),
                ('BOTTOMPADDING', (0, 0), (-1, 0), 4),
                ('TOPPADDING', (0, 0), (-1, 0), 4),
                # All cells
                ('LEFTPADDING', (0, 0), (-1, -1), 4),
                ('RIGHTPADDING', (0, 0), (-1, -1), 4),
                ('TOPPADDING', (0, 1), (-1, -1), 2),
                ('BOTTOMPADDING', (0, 1), (-1, -1), 2),
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                # Grid
                ('LINEBELOW', (0, 0), (-1, 0), 0.5, BORDER_COLOR),
                ('LINEBELOW', (0, 1), (-1, -2), 0.25, BORDER_COLOR),
                ('LINEBELOW', (0, -1), (-1, -1), 0.5, BORDER_COLOR),
            ]
            # Alternate row shading
            for i in range(1, len(table_data)):
                if i % 2 == 0:
                    tbl_style.append(('BACKGROUND', (0, i), (-1, i), ROW_ALT))

            tbl.setStyle(TableStyle(tbl_style))
            elements.append(tbl)
            elements.append(Spacer(1, 4 * mm))

        elements.append(Spacer(1, 2 * mm))

    # ─── Footer ────────────────────────────────────────────────
    elements.append(Spacer(1, 6 * mm))
    elements.append(Paragraph(
        f'Sliderobes Product Catalog  •  Confidential  •  Generated {datetime.now().strftime("%d/%m/%Y %H:%M")}',
        styles['FooterText'],
    ))

    doc.build(elements)
    buffer.seek(0)
    return buffer
