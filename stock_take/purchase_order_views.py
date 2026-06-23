from django.contrib.auth.decorators import login_required
from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse, StreamingHttpResponse, HttpResponse
from django.db.models import Count, Sum, Q
from django.core.mail import EmailMessage
from django.conf import settings
from .models import BoardsPO, Expense, Fitter, Order, OSDoor, PNXItem, PriceHistory, PurchaseInvoice, PurchaseInvoiceLineItem, PurchaseOrder, PurchaseOrderAttachment, PurchaseOrderInvoice, PurchaseOrderProduct, PurchaseOrderProject, ProductCustomerAllocation, RaumplusDraftOrder, StockItem, StockHistory, Supplier, SupplierContact, Timesheet, log_activity
from .po_pdf_generator import generate_purchase_order_pdf
from .pricing_utils import apply_invoice_price
import logging
import requests
import json
import time
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


def _generate_angled_board_preview(left_height, right_height, width, top_edge=0):
    """Generate a PNG preview image of an angled board shape using Pillow.
    Returns bytes of the PNG image."""
    from PIL import Image, ImageDraw, ImageFont
    import io

    lh = float(left_height or 0)
    rh = float(right_height or 0)
    bw = float(width or 0)
    te = float(top_edge or 0)

    img_w, img_h = 400, 280
    pad = 55
    img = Image.new('RGB', (img_w, img_h), '#1e2127')
    draw = ImageDraw.Draw(img)

    if lh <= 0 and rh <= 0 and bw <= 0:
        return None

    draw_w = img_w - pad * 2
    draw_h = img_h - pad * 2
    scale_x = draw_w / (bw or 1)
    scale_y = draw_h / (max(lh, rh) or 1)
    scale = min(scale_x, scale_y)

    shape_w = bw * scale
    shape_lh = lh * scale
    shape_rh = rh * scale
    shape_te = te * scale

    offset_x = (img_w - shape_w) / 2
    base_y = img_h - pad

    bl = (offset_x, base_y)
    br = (offset_x + shape_w, base_y)

    if lh >= rh and te > 0:
        tl = (offset_x, base_y - shape_lh)
        te_corner = (offset_x + shape_te, base_y - shape_lh)
        tr = (offset_x + shape_w, base_y - shape_rh)
        points = [bl, br, tr, te_corner, tl]
    elif rh > lh and te > 0:
        tl = (offset_x, base_y - shape_lh)
        te_corner = (offset_x + shape_w - shape_te, base_y - shape_rh)
        tr = (offset_x + shape_w, base_y - shape_rh)
        points = [bl, br, tr, te_corner, tl]
    else:
        tl = (offset_x, base_y - shape_lh)
        tr = (offset_x + shape_w, base_y - shape_rh)
        points = [bl, br, tr, tl]

    # Fill
    draw.polygon(points, fill='#1a2744', outline='#3b82f6')
    # Stroke (thicker outline)
    for i in range(len(points)):
        p1 = points[i]
        p2 = points[(i + 1) % len(points)]
        draw.line([p1, p2], fill='#3b82f6', width=2)

    # Dimension labels
    try:
        font = ImageFont.truetype("arial.ttf", 13)
    except (IOError, OSError):
        font = ImageFont.load_default()

    label_color = '#e0e0e0'

    # Left height
    if lh > 0:
        lx = bl[0] - 8
        ly = (bl[1] + tl[1]) / 2
        label = f'{lh:.0f}mm'
        # Draw rotated text using a temporary image
        txt_img = Image.new('RGBA', (80, 20), (0, 0, 0, 0))
        txt_draw = ImageDraw.Draw(txt_img)
        txt_draw.text((0, 0), label, fill=label_color, font=font)
        txt_img = txt_img.rotate(90, expand=True)
        tw, th = txt_img.size
        img.paste(txt_img, (int(lx - tw), int(ly - th / 2)), txt_img)

    # Right height
    if rh > 0:
        rx = br[0] + 6
        ry = (br[1] + tr[1]) / 2
        label = f'{rh:.0f}mm'
        txt_img = Image.new('RGBA', (80, 20), (0, 0, 0, 0))
        txt_draw = ImageDraw.Draw(txt_img)
        txt_draw.text((0, 0), label, fill=label_color, font=font)
        txt_img = txt_img.rotate(90, expand=True)
        tw, th = txt_img.size
        img.paste(txt_img, (int(rx), int(ry - th / 2)), txt_img)

    # Width (bottom)
    if bw > 0:
        label = f'{bw:.0f}mm'
        bbox = draw.textbbox((0, 0), label, font=font)
        tw = bbox[2] - bbox[0]
        cx = (bl[0] + br[0]) / 2
        draw.text((cx - tw / 2, bl[1] + 8), label, fill=label_color, font=font)

    # Top edge
    if te > 0:
        label = f'{te:.0f}mm'
        bbox = draw.textbbox((0, 0), label, font=font)
        tw = bbox[2] - bbox[0]
        if lh >= rh:
            cx = (tl[0] + tl[0] + shape_te) / 2
            draw.text((cx - tw / 2, tl[1] - 20), label, fill=label_color, font=font)
        else:
            cx = (tr[0] - shape_te + tr[0]) / 2
            draw.text((cx - tw / 2, tr[1] - 20), label, fill=label_color, font=font)

    buf = io.BytesIO()
    img.save(buf, format='PNG')
    return buf.getvalue()


def _ensure_angled_previews(purchase_order, boards_po):
    """Auto-generate missing angled board preview images for an existing PO.
    Called from the detail view so previews are available in the send modal."""
    from collections import defaultdict
    from django.core.files.base import ContentFile

    groups = defaultdict(list)
    for item in boards_po.pnx_items.all():
        if item.left_height is not None:
            key = (float(item.left_height or 0), float(item.right_height or 0),
                   float(item.cwidth), float(item.top_edge or 0))
            groups[key].append(item)

    if not groups:
        return

    for (lh, rh, w, te), items in sorted(groups.items()):
        fname = f'angled_preview_{int(lh)}x{int(rh)}x{int(w)}.png'
        if purchase_order.attachments.filter(filename=fname).exists():
            continue
        try:
            png_data = _generate_angled_board_preview(lh, rh, w, te)
            if png_data:
                desc = f'Angled board L:{int(lh)} R:{int(rh)} W:{int(w)}'
                if te > 0:
                    desc += f' TE:{int(te)}'
                desc += f'mm (x{sum(int(i.cnt) for i in items)})'
                att = PurchaseOrderAttachment(
                    purchase_order=purchase_order,
                    filename=fname,
                    description=desc,
                    uploaded_by='System',
                )
                att.file.save(fname, ContentFile(png_data), save=False)
                att.save()
        except Exception:
            pass


def _get_board_product_rows_for_pdf(po):
    """
    For Carnehill board POs, group PNX items by material + dimensions
    and return lightweight objects that the PDF generator can iterate.
    Returns a list of objects (or an empty list if this PO has no boards).
    """
    boards_po = BoardsPO.objects.filter(po_number=po.display_number).first()
    if not boards_po:
        return []

    pnx_items = list(boards_po.pnx_items.all())
    if not pnx_items:
        return []

    from collections import defaultdict
    from decimal import Decimal

    is_angled = boards_po.is_angled

    groups = defaultdict(lambda: {
        'matname': '', 'cleng': 0, 'cwidth': 0,
        'left_height': None, 'right_height': None, 'top_edge': 0,
        'total_qty': Decimal('0'), 'total_cost': Decimal('0'),
    })
    for item in pnx_items:
        if is_angled and item.left_height is not None:
            # For angled boards, group by all dimensions including left/right/top_edge
            key = (item.matname, float(item.left_height or 0), float(item.right_height or 0),
                   float(item.cwidth), float(item.top_edge or 0))
        else:
            key = (item.matname, float(item.cleng), float(item.cwidth))
        grp = groups[key]
        grp['matname'] = item.matname
        grp['cleng'] = float(item.cleng)
        grp['cwidth'] = float(item.cwidth)
        grp['left_height'] = float(item.left_height) if item.left_height is not None else None
        grp['right_height'] = float(item.right_height) if item.right_height is not None else None
        grp['top_edge'] = float(item.top_edge or 0)
        grp['total_qty'] += item.cnt
        grp['total_cost'] += item.get_cost()

    class _BoardRow:
        """Lightweight duck-type wrapper matching PurchaseOrderProduct interface."""
        def __init__(self, sku, name, description, qty, unit_price, line_total):
            self.sku = sku
            self.supplier_code = ''
            self.stock_item = None
            self.name = name
            self.description = description
            self.order_quantity = qty
            self.quantity = qty
            self.order_price = unit_price
            self.line_total = line_total

    rows = []
    for key, grp in sorted(groups.items(), key=lambda x: x[0]):
        sku = f"{grp['matname'].rstrip('_')}_{int(grp['cwidth'])}"
        qty = grp['total_qty']
        cost = grp['total_cost']
        unit_price = (cost / qty) if qty else Decimal('0')
        # Build dimension string
        if grp['left_height'] is not None:
            dims = f"L:{grp['left_height']:.0f} R:{grp['right_height']:.0f} W:{grp['cwidth']:.0f}"
            if grp['top_edge'] > 0:
                dims += f" TE:{grp['top_edge']:.0f}"
            name = f"{grp['matname']} — {dims}mm"
        else:
            name = f"{grp['matname']} — {grp['cleng']:.0f}×{grp['cwidth']:.0f}mm"
        rows.append(_BoardRow(
            sku=sku,
            name=name,
            description=name,
            qty=qty,
            unit_price=unit_price,
            line_total=cost,
        ))
    return rows


@login_required
def sync_purchase_orders_stream(request):
    """Stream sync progress — WorkGuru integration removed, returns immediately."""
    def event_stream():
        yield f"data: {json.dumps({'status': 'complete', 'synced': 0, 'total': 0, 'products': 0, 'suppliers': 0, 'message': 'WorkGuru sync has been removed. Purchase orders are now managed locally.'})}\n\n"

    response = StreamingHttpResponse(event_stream(), content_type='text/event-stream')
    response['Cache-Control'] = 'no-cache'
    response['X-Accel-Buffering'] = 'no'
    return response


@login_required
def purchase_orders_list(request):
    """Display list of all purchase orders from local database"""
    from decimal import Decimal
    status_filter = request.GET.get('status', 'all')
    search_query = request.GET.get('q', '').strip()
    excluded_suppliers = request.GET.getlist('exclude_supplier')
    
    # All unique supplier names for the filter dropdown
    all_suppliers = list(
        PurchaseOrder.objects.exclude(supplier_name__isnull=True)
        .exclude(supplier_name='')
        .values_list('supplier_name', flat=True)
        .distinct()
        .order_by('supplier_name')
    )
    
    # Special filters
    show_zero = request.GET.get('zero') == '1'
    
    # Base queryset
    queryset = PurchaseOrder.objects.all()
    
    # Exclude suppliers
    if excluded_suppliers:
        queryset = queryset.exclude(supplier_name__in=excluded_suppliers)
    
    # Zero total filter
    if show_zero:
        queryset = queryset.filter(total=0)
    
    # Search filter
    if search_query:
        queryset = queryset.filter(
            Q(display_number__icontains=search_query) |
            Q(number__icontains=search_query) |
            Q(supplier_name__icontains=search_query) |
            Q(project_name__icontains=search_query) |
            Q(project_number__icontains=search_query) |
            Q(description__icontains=search_query) |
            Q(creator_name__icontains=search_query)
        )
    
    # Status counts (after supplier/search filtering)
    status_counts = dict(
        queryset.values_list('status')
        .annotate(c=Count('id'))
        .values_list('status', 'c')
    )
    
    total_filtered = queryset.count()
    
    # Filter by status tab
    STATUS_TAB_MAP = {
        'draft': 'Draft',
        'approved': 'Approved',
        'received': 'Received',
        'partially received': 'Partially Received',
        'cancelled': 'Cancelled',
    }
    if status_filter != 'all':
        db_status = STATUS_TAB_MAP.get(status_filter.lower(), status_filter.capitalize())
        queryset = queryset.filter(status=db_status)
    
    total_count = PurchaseOrder.objects.count()
    
    # Count of zero-total POs (before status filter but after supplier/search filters)
    zero_base = PurchaseOrder.objects.all()
    if excluded_suppliers:
        zero_base = zero_base.exclude(supplier_name__in=excluded_suppliers)
    if search_query:
        zero_base = zero_base.filter(
            Q(display_number__icontains=search_query) |
            Q(number__icontains=search_query) |
            Q(supplier_name__icontains=search_query) |
            Q(project_name__icontains=search_query) |
            Q(project_number__icontains=search_query) |
            Q(description__icontains=search_query) |
            Q(creator_name__icontains=search_query)
        )
    zero_count = zero_base.filter(total=0).count()
    
    # Supplier objects for the create PO modal
    supplier_objects = Supplier.objects.filter(is_active=True).order_by('name')

    # Fitter objects for the create PO modal
    fitter_objects = Fitter.objects.filter(active=True).order_by('name')

    # Carnehill approved POs for the summary modal
    carnehill_pos = (
        PurchaseOrder.objects
        .filter(supplier_name__icontains='Carnehill', status='Approved')
        .order_by('-display_number')
    )

    # All approved POs for the Report modal (value + expected date)
    approved_report_qs = PurchaseOrder.objects.filter(status='Approved')
    approved_report_total = approved_report_qs.aggregate(t=Sum('total'))['t'] or 0

    def _expected_sort_key(po):
        """Parse expected_date (stored as a string in various formats).
        POs with no parseable date sort first, then oldest date first."""
        raw = (po.expected_date or '').strip()
        if not raw:
            return (0, datetime.min)
        for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%dT%H:%M:%S.%f'):
            try:
                return (1, datetime.strptime(raw[:19], fmt))
            except (ValueError, TypeError):
                continue
        return (0, datetime.min)

    approved_report_pos = sorted(approved_report_qs, key=_expected_sort_key)

    # Per-currency totals for the report modal (GBP first, then EUR, then others)
    _ccy_order = {'GBP': 0, 'EUR': 1, 'USD': 2}
    _ccy_symbols = {'GBP': '£', 'EUR': '€', 'USD': '$'}
    _ccy_totals = {}
    for _po in approved_report_pos:
        _code = _po.currency or 'GBP'
        _ccy_totals[_code] = _ccy_totals.get(_code, Decimal('0')) + (_po.total or Decimal('0'))
    approved_report_currency_totals = [
        {'symbol': _ccy_symbols.get(code.upper(), code), 'amount': amount}
        for code, amount in sorted(
            _ccy_totals.items(),
            key=lambda kv: (_ccy_order.get(kv[0].upper(), 99), kv[0])
        )
    ]

    # Batch-lookup linked orders (project_id -> Order.workguru_id)
    # Batch-lookup linked orders using multiple strategies
    linked_orders_map = {}  # keyed by PO pk

    # Strategy 1: project_id -> Order.workguru_id
    project_ids = list(
        queryset.exclude(project_id__isnull=True)
        .values_list('project_id', flat=True)
        .distinct()
    )
    wg_id_to_order = {}
    if project_ids:
        for o in Order.objects.filter(
            workguru_id__in=[str(pid) for pid in project_ids]
        ).values('id', 'workguru_id', 'sale_number'):
            wg_id_to_order[int(o['workguru_id'])] = o

    # Strategy 2: project_name -> Order.last_name (for unmatched POs)
    name_to_order = {}
    project_names = list(
        queryset.exclude(project_name__isnull=True)
        .exclude(project_name='')
        .values_list('project_name', flat=True)
        .distinct()
    )
    if project_names:
        for o in Order.objects.filter(
            last_name__in=project_names
        ).values('id', 'last_name', 'sale_number'):
            name_to_order[o['last_name']] = o

    # Strategy 3: project_number -> Order.sale_number (most reliable for customer POs)
    project_num_to_order = {}
    project_numbers = list(
        queryset.exclude(project_number__isnull=True)
        .exclude(project_number='')
        .values_list('project_number', flat=True)
        .distinct()
    )
    if project_numbers:
        for o in Order.objects.filter(
            sale_number__in=project_numbers
        ).values('id', 'sale_number'):
            project_num_to_order[o['sale_number']] = o

    # Strategy 4: PurchaseOrderProject rows already seeded with a resolved order FK
    proj_order_map = {}  # po pk -> {'id': order_id, 'sale_number': ...}
    for p in PurchaseOrderProject.objects.filter(
        purchase_order__in=queryset,
        project_type='customer',
        order__isnull=False,
    ).select_related('order').values('purchase_order_id', 'order__id', 'order__sale_number'):
        proj_order_map[p['purchase_order_id']] = {
            'id': p['order__id'],
            'sale_number': p['order__sale_number'],
        }

    # Strategy 5: product allocations
    alloc_map = {}
    alloc_qs = ProductCustomerAllocation.objects.filter(
        product__purchase_order__in=queryset
    ).select_related('order', 'product__purchase_order').values(
        'product__purchase_order_id', 'order__id', 'order__sale_number'
    ).distinct()
    for a in alloc_qs:
        alloc_map[a['product__purchase_order_id']] = {
            'id': a['order__id'],
            'sale_number': a['order__sale_number'],
        }

    # Pre-fetch POs that have a stock project entry (for display fallback)
    stock_po_ids = set(
        PurchaseOrderProject.objects.filter(
            purchase_order__in=queryset, project_type='stock'
        ).values_list('purchase_order_id', flat=True)
    )

    # Annotate POs with linked order info (try each strategy in order)
    po_list = list(queryset)

    # Batch-lookup linked purchase invoices (one query for all POs)
    po_invoice_map = {}  # po.pk -> list of {id, invoice_number, supplier_name}
    for inv in PurchaseInvoice.objects.filter(
        purchase_orders__in=po_list
    ).values('id', 'invoice_number', 'supplier_name', 'purchase_orders'):
        po_invoice_map.setdefault(inv['purchase_orders'], []).append(inv)

    for po in po_list:
        # If project_name is blank but a stock project is assigned, display "Stock"
        if not po.project_name and po.pk in stock_po_ids:
            po.project_name = 'Stock'
        info = wg_id_to_order.get(po.project_id)
        if not info and po.project_number:
            info = project_num_to_order.get(po.project_number)
        if not info and po.project_name:
            info = name_to_order.get(po.project_name)
        if not info:
            info = proj_order_map.get(po.pk)
        if not info:
            info = alloc_map.get(po.pk)
        po.linked_order_info = info
        po.linked_invoices_list = po_invoice_map.get(po.pk, [])

    context = {
        'purchase_orders': po_list,
        'total_count': total_count,
        'total_filtered': total_filtered,
        'filtered_count': queryset.count(),
        'status_filter': status_filter,
        'search_query': search_query,
        'excluded_suppliers': excluded_suppliers,
        'all_suppliers': all_suppliers,
        'supplier_objects': supplier_objects,
        'fitter_objects': fitter_objects,
        'show_zero': show_zero,
        'zero_count': zero_count,
        'draft_count': status_counts.get('Draft', 0),
        'approved_count': status_counts.get('Approved', 0),
        'received_count': status_counts.get('Received', 0),
        'partial_count': status_counts.get('Partially Received', 0),
        'cancelled_count': status_counts.get('Cancelled', 0),
        'carnehill_pos': carnehill_pos,
        'approved_report_pos': approved_report_pos,
        'approved_report_total': approved_report_total,
        'approved_report_currency_totals': approved_report_currency_totals,
    }
    
    return render(request, 'stock_take/purchase_orders_list.html', context)


@login_required
def purchase_order_detail(request, po_id):
    """Display detailed view of a single purchase order from local database"""
    purchase_order = get_object_or_404(PurchaseOrder, workguru_id=po_id)
    products = purchase_order.products.all()

    # ── Recalculate cut-to-size glass lines from current price per m² ──────────
    # CTS glass is priced as (width_m × height_m × price_per_sqm). The dimensions
    # are stored in the line description (e.g. "511mm x 2022mm | Polished Edges").
    # Recompute on load so a later change to the stock item's price_per_sqm is
    # reflected without re-adding the line.
    import re as _re_cts
    from decimal import Decimal as _Dec_cts
    _cts_changed = False
    for _p in products:
        if not (_p.stock_item and _p.stock_item.price_per_sqm):
            continue
        _m = _re_cts.search(
            r'(\d+(?:\.\d+)?)\s*mm\s*x\s*(\d+(?:\.\d+)?)\s*mm',
            _p.description or '', _re_cts.IGNORECASE)
        if not _m:
            continue
        _width_m = _Dec_cts(_m.group(1)) / _Dec_cts('1000')
        _height_m = _Dec_cts(_m.group(2)) / _Dec_cts('1000')
        _unit = (_width_m * _height_m * _p.stock_item.price_per_sqm).quantize(_Dec_cts('0.00001'))
        _line_total = (_unit * _p.order_quantity).quantize(_Dec_cts('0.01'))
        if _p.order_price != _unit or _p.line_total != _line_total:
            _p.order_price = _unit
            _p.line_total = _line_total
            _p.save(update_fields=['order_price', 'line_total'])
            _cts_changed = True
    if _cts_changed:
        _new_total = purchase_order.products.aggregate(total=Sum('line_total'))['total'] or 0
        if purchase_order.total != _new_total:
            purchase_order.total = _new_total
            purchase_order.save(update_fields=['total'])
        products = purchase_order.products.all()

    # All suppliers for the dropdown
    all_suppliers = list(
        Supplier.objects.order_by('name')
    )

    # Staff users for the received-by dropdown
    from django.contrib.auth.models import User as AuthUser
    staff_users = list(
        AuthUser.objects.filter(is_active=True)
        .order_by('first_name', 'last_name')
    )
    
    # Try to find a linked local Order via multiple strategies
    linked_order = None
    if purchase_order.project_id:
        # 1. Match project_id -> Order.workguru_id
        linked_order = Order.objects.filter(workguru_id=str(purchase_order.project_id)).first()
    if not linked_order and purchase_order.project_number:
        # 2. Match project_number -> Order.sale_number
        linked_order = Order.objects.filter(sale_number=purchase_order.project_number).first()
    if not linked_order and purchase_order.project_name:
        # 3. Match project_name -> Order customer name (last_name)
        linked_order = Order.objects.filter(last_name__iexact=purchase_order.project_name).first()
    if not linked_order:
        # 4. Match via product allocations on this PO
        allocation = ProductCustomerAllocation.objects.filter(
            product__purchase_order=purchase_order
        ).select_related('order').first()
        if allocation:
            linked_order = allocation.order
    
    # Find the BoardsPO that matches this PO's display_number (Carnehill board POs)
    boards_po = BoardsPO.objects.filter(po_number=purchase_order.display_number).first()
    pnx_items = []
    pnx_total_cost = 0
    board_product_rows = []  # grouped PNX items presented as product rows
    if boards_po:
        pnx_items = list(boards_po.pnx_items.all())
        for item in pnx_items:
            item.calculated_cost = item.get_cost()
        pnx_total_cost = sum(item.calculated_cost for item in pnx_items)
        # Update the PO total to reflect the real board cost
        if pnx_total_cost and purchase_order.total != pnx_total_cost:
            purchase_order.total = pnx_total_cost
            purchase_order.save(update_fields=['total'])

        # Group PNX items by matname + dimensions into product-like rows
        from collections import defaultdict
        from decimal import Decimal
        is_angled_po = boards_po.is_angled
        groups = defaultdict(lambda: {
            'matname': '', 'cleng': 0, 'cwidth': 0,
            'left_height': None, 'right_height': None, 'top_edge': 0,
            'total_qty': Decimal('0'), 'received_qty': Decimal('0'),
            'total_cost': Decimal('0'), 'items': [],
        })
        for item in pnx_items:
            if is_angled_po and item.left_height is not None:
                key = (item.matname, float(item.left_height or 0), float(item.right_height or 0),
                       float(item.cwidth), float(item.top_edge or 0))
            else:
                key = (item.matname, float(item.cleng), float(item.cwidth))
            grp = groups[key]
            grp['matname'] = item.matname
            grp['cleng'] = float(item.cleng)
            grp['cwidth'] = float(item.cwidth)
            grp['left_height'] = float(item.left_height) if item.left_height is not None else None
            grp['right_height'] = float(item.right_height) if item.right_height is not None else None
            grp['top_edge'] = float(item.top_edge or 0)
            grp['total_qty'] += item.cnt
            grp['received_qty'] += item.received_quantity
            grp['total_cost'] += item.calculated_cost
            grp['items'].append(item)

        for key, grp in sorted(groups.items(), key=lambda x: x[0]):
            sku = f"{grp['matname'].rstrip('_')}_{int(grp['cwidth'])}"
            qty = grp['total_qty']
            cost = grp['total_cost']
            unit_price = (cost / qty) if qty else Decimal('0')
            # Build dimension string
            if grp['left_height'] is not None:
                dims = f"L:{grp['left_height']:.0f} R:{grp['right_height']:.0f} W:{grp['cwidth']:.0f}"
                if grp['top_edge'] > 0:
                    dims += f" TE:{grp['top_edge']:.0f}"
                name = f"{grp['matname']} — {dims}mm"
            else:
                name = f"{grp['matname']} — {grp['cleng']:.0f}×{grp['cwidth']:.0f}mm"
            board_product_rows.append({
                'sku': sku,
                'name': name,
                'order_price': unit_price,
                'order_quantity': qty,
                'received_quantity': grp['received_qty'],
                'invoice_price': Decimal('0'),
                'line_total': cost,
                'is_board': True,
                'items': grp['items'],
                'item_ids': ','.join(str(i.id) for i in grp['items']),
                'left_height': grp['left_height'],
                'right_height': grp['right_height'],
                'top_edge': grp['top_edge'],
                'cwidth': grp['cwidth'],
            })

        # Auto-generate missing angled preview images
        if is_angled_po:
            _ensure_angled_previews(purchase_order, boards_po)
    
    # Compute total board counts for template display
    total_board_items = len(pnx_items)  # individual PNX rows (before grouping)
    total_board_qty = sum(item.cnt for item in pnx_items)  # sum of all cnt values

    # Find related POs on the same project (sibling POs from other suppliers)
    related_pos = []
    if purchase_order.project_id:
        related_pos = PurchaseOrder.objects.filter(
            project_id=purchase_order.project_id
        ).exclude(
            workguru_id=purchase_order.workguru_id
        ).order_by('display_number')
    
    # Find OS door items linked to this PO (via Order.os_doors_po)
    os_door_items = []
    if not products and not pnx_items:
        linked_orders = Order.objects.filter(os_doors_po=purchase_order.display_number)
        for order in linked_orders:
            os_door_items.extend(list(order.os_doors.all()))
    
    # Get supplier email for the send modal
    supplier_email = ''
    supplier_obj = None
    supplier_contacts = []
    if purchase_order.supplier_id:
        supplier_obj = Supplier.objects.filter(workguru_id=purchase_order.supplier_id).first()
        if supplier_obj and supplier_obj.email:
            supplier_email = supplier_obj.email
        if supplier_obj:
            supplier_contacts = list(supplier_obj.contacts.all())
    # Fall back to name match if no supplier_id set
    if not supplier_obj and purchase_order.supplier_name:
        supplier_obj = Supplier.objects.filter(name__iexact=purchase_order.supplier_name).first()
        if supplier_obj:
            supplier_contacts = list(supplier_obj.contacts.all())

    # ── Derive expected date from approved date + supplier lead time ───────────
    # If the PO is approved but has no expected date, calculate it as
    # approved_date + supplier lead time (weekends pushed to Monday) and persist it.
    if not purchase_order.expected_date and purchase_order.approved_date:
        approved_dt = None
        for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y',
                    '%Y-%m-%dT%H:%M:%S', '%Y-%m-%dT%H:%M:%S.%f'):
            try:
                approved_dt = datetime.strptime(purchase_order.approved_date, fmt)
                break
            except ValueError:
                continue
        if approved_dt:
            lead_days = (supplier_obj.estimate_lead_time
                         if supplier_obj and supplier_obj.estimate_lead_time else 10)
            exp_dt = approved_dt + timedelta(days=lead_days)
            # Push weekends to Monday
            if exp_dt.weekday() == 5:
                exp_dt += timedelta(days=2)
            elif exp_dt.weekday() == 6:
                exp_dt += timedelta(days=1)
            purchase_order.expected_date = exp_dt.strftime('%Y-%m-%d')
            purchase_order.save(update_fields=['expected_date'])

    # Determine original customer info (for stock/customer toggle)
    # If project is currently "Stock", try to find the original customer from the PO description
    original_customer_name = ''
    original_customer_number = ''
    if linked_order:
        original_customer_name = f'{linked_order.first_name} {linked_order.last_name}'.strip()
        original_customer_number = linked_order.sale_number
    elif purchase_order.project_name and purchase_order.project_name != 'Stock':
        original_customer_name = purchase_order.project_name
        original_customer_number = purchase_order.project_number or ''
    elif purchase_order.description:
        # Try to extract sale number from description like "Accessories for S12345"
        import re as re_mod
        desc_match = re_mod.search(r'for\s+(S\d+)', purchase_order.description or '')
        if desc_match:
            sale_num = desc_match.group(1)
            desc_order = Order.objects.filter(sale_number=sale_num).first()
            if desc_order:
                original_customer_name = f'{desc_order.first_name} {desc_order.last_name}'.strip()
                original_customer_number = desc_order.sale_number

    # ── Compute expected delivery date for email template ──────
    # If the PO already has one, use it; otherwise calculate from lead time
    expected_delivery_date = ''
    if purchase_order.expected_date:
        try:
            for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y'):
                try:
                    expected_delivery_date = datetime.strptime(purchase_order.expected_date, fmt).strftime('%d/%m/%Y')
                    break
                except ValueError:
                    continue
        except Exception:
            expected_delivery_date = purchase_order.expected_date
    if not expected_delivery_date:
        lead_days = (supplier_obj.estimate_lead_time if supplier_obj and supplier_obj.estimate_lead_time else 10)
        exp_dt = datetime.now() + timedelta(days=lead_days)
        # Push weekends to Monday
        if exp_dt.weekday() == 5:
            exp_dt += timedelta(days=2)
        elif exp_dt.weekday() == 6:
            exp_dt += timedelta(days=1)
        expected_delivery_date = exp_dt.strftime('%d/%m/%Y')

    # ── Fetch multi-project entries ──────────────────────────
    po_projects = list(purchase_order.projects.select_related('order', 'order__customer').all())

    # Auto-seed: if no PurchaseOrderProject rows exist yet, create them from
    # the legacy project_name / linked_order fields so the new UI has data.
    if not po_projects:
        if purchase_order.project_name == 'Stock':
            PurchaseOrderProject.objects.create(
                purchase_order=purchase_order, project_type='stock',
                label='Stock', sort_order=0)
        if linked_order:
            customer_name = f'{linked_order.first_name} {linked_order.last_name}'.strip()
            if linked_order.customer and linked_order.customer.name:
                customer_name = linked_order.customer.name
            PurchaseOrderProject.objects.create(
                purchase_order=purchase_order, project_type='customer',
                order=linked_order,
                label=f'{linked_order.sale_number} - {customer_name}' if customer_name else linked_order.sale_number,
                sort_order=1)
        elif purchase_order.project_name and purchase_order.project_name != 'Stock':
            # Try to find matching order for legacy customer name
            legacy_order = Order.objects.filter(
                Q(sale_number=purchase_order.project_number) |
                Q(last_name__iexact=purchase_order.project_name)
            ).first()
            PurchaseOrderProject.objects.create(
                purchase_order=purchase_order, project_type='customer',
                order=legacy_order,
                label=f'{purchase_order.project_number} - {purchase_order.project_name}'.strip(' -'),
                sort_order=1)
        po_projects = list(purchase_order.projects.select_related('order', 'order__customer').all())

    # Determine if any project is stock (for allocation UI visibility)
    has_stock_project = any(p.project_type == 'stock' for p in po_projects)

    # ── VAT breakdown ─────────────────────────────────────────────────────────
    po_vat_rate = None
    po_net_total = None
    po_vat_amount = None
    po_gross_total = None
    if supplier_obj and supplier_obj.vat_rate is not None:
        po_vat_rate = supplier_obj.vat_rate
    if po_vat_rate is not None:
        net = float(purchase_order.total or 0) + float(purchase_order.freight_cost or 0)
        # Always calculate VAT from the supplier's rate
        vat_amt = round(net * float(po_vat_rate) / 100, 2)
        po_net_total = net
        po_vat_amount = vat_amt
        po_gross_total = round(net + vat_amt, 2)

    # ── Linked purchase invoice totals ───────────────────────────────────────
    _pi_agg = purchase_order.linked_purchase_invoices.aggregate(
        t=Sum('total'), p=Sum('amount_paid'))
    _pi_agg_total = _pi_agg['t'] or 0
    _pi_agg_outstanding = (_pi_agg['t'] or 0) - (_pi_agg['p'] or 0)

    # ── Fitter PO: timesheets & expenses ─────────────────────────
    po_timesheets = []
    po_expenses = []
    po_timesheets_total = 0
    po_expenses_total = 0
    unlinked_timesheets = []
    if purchase_order.po_type == 'fitter':
        _linked_pi_ids = list(purchase_order.linked_purchase_invoices.values_list('id', flat=True))
        po_timesheets = list(
            Timesheet.objects.filter(
                Q(purchase_order=purchase_order) |
                (Q(purchase_invoice_line__isnull=False) & Q(purchase_invoice_line__invoice_id__in=_linked_pi_ids))
            )
            .select_related('order', 'fitter', 'purchase_invoice_line')
            .distinct()
            .order_by('order__sale_number', 'date')
        )
        for ts in po_timesheets:
            if ts.purchase_invoice_line_id:
                ts.line_total = float(ts.purchase_invoice_line.line_total)
            else:
                ts.line_total = round(float(ts.hours or 0) * float(ts.hourly_rate or 0), 2)
        po_timesheets_total = sum(ts.line_total for ts in po_timesheets)
        po_expenses = list(
            purchase_order.expenses.select_related('order', 'fitter')
            .order_by('-date')
        )
        po_expenses_total = sum(float(e.amount or 0) for e in po_expenses)

        # Fetch timesheets on linked orders that are NOT yet linked to this PO
        linked_order_ids = [
            p.order_id for p in po_projects if p.order_id
        ]
        if linked_order_ids:
            linked_ts_ids = set(ts.id for ts in po_timesheets)
            unlinked_timesheets = list(
                Timesheet.objects.filter(
                    order_id__in=linked_order_ids,
                    purchase_order__isnull=True,
                )
                .select_related('order', 'fitter')
                .order_by('order__sale_number', 'date')
            )
            for ts in unlinked_timesheets:
                ts.line_total = round(float(ts.hours or 0) * float(ts.hourly_rate or 0), 2)

    # ── Compute next split suffix for the Split PO modal ──────
    import re as _re
    _root_match = _re.match(r'^(PO\d+)(?:_\d+)?$', purchase_order.display_number or '')
    _split_root = _root_match.group(1) if _root_match else (purchase_order.display_number or '')
    _existing_splits = PurchaseOrder.objects.filter(
        display_number__startswith=_split_root + '_'
    ).values_list('display_number', flat=True)
    _max_suffix = 0
    for _dn in _existing_splits:
        _sm = _re.match(r'^' + _re.escape(_split_root) + r'_(\d+)$', _dn)
        if _sm:
            _max_suffix = max(_max_suffix, int(_sm.group(1)))
    next_split_label = f'{_split_root}_{_max_suffix + 1}'

    # ── Split family: parent / children ─────────────────────────
    parent_po = purchase_order.parent_po
    split_children = list(
        purchase_order.split_children.all().order_by('display_number')
    )

    # ── PO Activity history ─────────────────────────────────────
    # Collect activity for this PO and all its split family members
    from .models import ActivityLog
    _family_ids = [purchase_order.id]
    if parent_po:
        _family_ids.append(parent_po.id)
        _family_ids.extend(parent_po.split_children.values_list('id', flat=True))
    else:
        _family_ids.extend(c.id for c in split_children)
    po_activity_logs = ActivityLog.objects.filter(
        purchase_order_id__in=_family_ids,
    ).select_related('user', 'purchase_order').order_by('-timestamp')[:50]

    context = {
        'purchase_order': purchase_order,
        'products': products,
        'all_suppliers': all_suppliers,
        'supplier_obj': supplier_obj,
        'linked_order': linked_order,
        'boards_po': boards_po,
        'pnx_items': pnx_items,
        'pnx_total_cost': pnx_total_cost,
        'board_product_rows': board_product_rows,
        'total_board_items': total_board_items,
        'total_board_qty': total_board_qty,
        'related_pos': related_pos,
        'os_door_items': os_door_items,
        'supplier_email': supplier_email,
        'supplier_contacts': supplier_contacts,
        'attachments': purchase_order.attachments.all(),
        'po_invoices': purchase_order.invoices.all(),
        'linked_invoices': purchase_order.linked_invoices.all().order_by('-date'),
        'linked_purchase_invoices': purchase_order.linked_purchase_invoices.all().order_by('-date', '-created_at'),
        'linked_pi_total': _pi_agg_total,
        'linked_pi_outstanding': _pi_agg_outstanding,
        'original_customer_name': original_customer_name,
        'original_customer_number': original_customer_number,
        'expected_delivery_date': expected_delivery_date,
        'po_projects': po_projects,
        'has_stock_project': has_stock_project,
        'po_vat_rate': po_vat_rate,
        'po_net_total': po_net_total,
        'po_vat_amount': po_vat_amount,
        'po_gross_total': po_gross_total,
        'staff_users': staff_users,
        'po_timesheets': po_timesheets,
        'po_expenses': po_expenses,
        'po_timesheets_total': po_timesheets_total,
        'po_expenses_total': po_expenses_total,
        'po_fitter_total': po_timesheets_total + po_expenses_total,
        'unlinked_timesheets': unlinked_timesheets,
        'next_split_label': next_split_label,
        'parent_po': parent_po,
        'split_children': split_children,
        'po_activity_logs': po_activity_logs,
    }
    
    return render(request, 'stock_take/purchase_order_detail.html', context)


@login_required
def purchase_order_toggle_project(request, po_id):
    """Toggle PO project between customer link and Stock (AJAX) – legacy endpoint.
    Now also kept for backwards-compat but the new multi-project endpoints are preferred.
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    
    po = get_object_or_404(PurchaseOrder, workguru_id=po_id)
    
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)
    
    mode = data.get('mode')  # 'stock' or 'customer'
    
    if mode == 'stock':
        po.project_name = 'Stock'
        po.project_number = ''
        po.project_id = None
        po.save(update_fields=['project_name', 'project_number', 'project_id'])
        return JsonResponse({
            'success': True,
            'project_name': 'Stock',
            'project_number': '',
        })
    elif mode == 'customer':
        customer_name = data.get('customer_name', '')
        customer_number = data.get('customer_number', '')
        po.project_name = customer_name
        po.project_number = customer_number
        po.save(update_fields=['project_name', 'project_number'])
        return JsonResponse({
            'success': True,
            'project_name': customer_name,
            'project_number': customer_number,
        })
    else:
        return JsonResponse({'error': 'Invalid mode. Use "stock" or "customer".'}, status=400)


@login_required
def po_toggle_invoice_not_required(request, po_id):
    """Mark a PO as not requiring a supplier invoice (or clear that mark) (AJAX).

    Used by the Accounts Payable "Awaiting Invoice" sidebar so POs that will
    never be invoiced can be removed from the list instead of lingering.
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    po = get_object_or_404(PurchaseOrder, workguru_id=po_id)

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        data = {}

    po.invoice_not_required = bool(data.get('not_required', True))
    po.save(update_fields=['invoice_not_required'])

    return JsonResponse({
        'success': True,
        'invoice_not_required': po.invoice_not_required,
    })


@login_required
def po_add_project(request, po_id):
    """Add a project entry (Stock or Customer order) to a PO (AJAX)."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    po = get_object_or_404(PurchaseOrder, workguru_id=po_id)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    project_type = data.get('project_type', 'customer')  # 'stock' or 'customer'

    if project_type == 'stock':
        # Prevent duplicate stock entries
        if po.projects.filter(project_type='stock').exists():
            return JsonResponse({'error': 'Stock entry already exists'}, status=400)
        proj = PurchaseOrderProject.objects.create(
            purchase_order=po,
            project_type='stock',
            label='Stock',
            sort_order=0,
        )
        return JsonResponse({
            'success': True,
            'project': {
                'id': proj.id,
                'project_type': 'stock',
                'label': 'Stock',
                'order_id': None,
                'sale_number': '',
            }
        })
    elif project_type == 'customer':
        order_id = data.get('order_id')
        if not order_id:
            return JsonResponse({'error': 'order_id is required for customer projects'}, status=400)
        order = get_object_or_404(Order, id=order_id)

        # Prevent duplicate order entries
        if po.projects.filter(order=order).exists():
            return JsonResponse({'error': 'This order is already linked to this PO'}, status=400)

        customer_name = f'{order.first_name} {order.last_name}'.strip()
        if order.customer and order.customer.name:
            customer_name = order.customer.name
        label = f'{order.sale_number} - {customer_name}' if customer_name else order.sale_number

        max_sort = po.projects.count()
        proj = PurchaseOrderProject.objects.create(
            purchase_order=po,
            project_type='customer',
            order=order,
            label=label,
            sort_order=max_sort,
        )
        return JsonResponse({
            'success': True,
            'project': {
                'id': proj.id,
                'project_type': 'customer',
                'label': label,
                'order_id': order.id,
                'sale_number': order.sale_number,
            }
        })
    else:
        return JsonResponse({'error': 'Invalid project_type'}, status=400)


@login_required
def po_remove_project(request, po_id, project_id):
    """Remove a project entry from a PO (AJAX)."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    po = get_object_or_404(PurchaseOrder, workguru_id=po_id)
    proj = get_object_or_404(PurchaseOrderProject, id=project_id, purchase_order=po)
    proj.delete()
    return JsonResponse({'success': True})


@login_required
def purchase_order_save(request, po_id):
    """Save edits to a purchase order (AJAX)"""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    
    po = get_object_or_404(PurchaseOrder, workguru_id=po_id)
    
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)
    
    # Update PO header fields
    field_map = {
        'supplier_name': 'supplier_name',
        'supplier_invoice_number': 'supplier_invoice_number',
        'description': 'description',
        'project_name': 'project_name',
        'project_number': 'project_number',
        'issue_date': 'issue_date',
        'expected_date': 'expected_date',
        'received_date': 'received_date',
        'invoice_date': 'invoice_date',
        'received_by_name': 'received_by_name',
        'delivery_instructions': 'delivery_instructions',
        'delivery_address_1': 'delivery_address_1',
        'delivery_address_2': 'delivery_address_2',
        'status': 'status',
        'total': 'total',
        'currency': 'currency',
    }
    
    for json_key, model_field in field_map.items():
        if json_key in data:
            val = data[json_key]
            if json_key == 'total':
                try:
                    val = float(val) if val else 0
                except (ValueError, TypeError):
                    val = 0
            # Normalise date fields: convert DD/MM/YYYY -> YYYY-MM-DD for consistent storage
            if json_key in ('issue_date', 'expected_date', 'received_date', 'invoice_date') and val:
                for fmt in ('%d/%m/%Y', '%Y-%m-%d', '%Y-%m-%dT%H:%M:%S'):
                    try:
                        from datetime import datetime as _dt
                        val = _dt.strptime(str(val)[:19], fmt).strftime('%Y-%m-%d')
                        break
                    except (ValueError, TypeError):
                        continue
            if json_key == 'supplier_name' and val:
                # Also update supplier_id to match the selected supplier
                matched_supplier = Supplier.objects.filter(name__iexact=val).first()
                if matched_supplier:
                    po.supplier_id = matched_supplier.workguru_id
                    # Auto-sync supplier's currency to the PO if supplier has one set
                    if matched_supplier.currency and 'currency' not in data:
                        po.currency = matched_supplier.currency.strip().upper()
            setattr(po, model_field, val)
    
    if 'billable' in data:
        po.billable = data['billable'] in (True, 'true', '1', 'on')
    
    po.save()
    
    from decimal import Decimal
    # Update product lines if provided
    products_data = data.get('products')
    if products_data is not None:
        for prod_data in products_data:
            prod_id = prod_data.get('id')
            if not prod_id:
                continue
            try:
                product = PurchaseOrderProduct.objects.get(id=prod_id, purchase_order=po)
                if 'order_price' in prod_data:
                    try:
                        product.order_price = float(prod_data['order_price']) if prod_data['order_price'] else 0
                    except (ValueError, TypeError):
                        pass
                if 'order_quantity' in prod_data:
                    try:
                        product.order_quantity = float(prod_data['order_quantity']) if prod_data['order_quantity'] else 0
                    except (ValueError, TypeError):
                        pass
                if 'received_quantity' in prod_data:
                    try:
                        new_received = float(prod_data['received_quantity']) if prod_data['received_quantity'] else 0
                        old_received = float(product.received_quantity)
                        delta = new_received - old_received
                        product.received_quantity = new_received
                        # Update linked stock item quantity and create stock history
                        if delta != 0 and product.stock_item:
                            product.stock_item.quantity = max(0, product.stock_item.quantity + int(delta))
                            product.stock_item.save(update_fields=['quantity'])
                            StockHistory.objects.create(
                                stock_item=product.stock_item,
                                quantity=product.stock_item.quantity,
                                change_amount=int(delta),
                                change_type='purchase',
                                reference=po.display_number,
                                notes=f'Received {int(abs(delta))} via {po.display_number} ({product.sku})',
                                created_by=request.user,
                            )
                    except (ValueError, TypeError):
                        pass
                if 'invoice_price' in prod_data:
                    try:
                        new_invoice_price = float(prod_data['invoice_price']) if prod_data['invoice_price'] else 0
                        old_invoice_price = float(product.invoice_price or 0)
                        if new_invoice_price > 0 and new_invoice_price != old_invoice_price:
                            # The invoiced unit price becomes the source of truth: overwrite
                            # the order price, refresh the linked product cost and log the
                            # change in price history.
                            apply_invoice_price(product, new_invoice_price, po.display_number, user=request.user)
                        else:
                            product.invoice_price = new_invoice_price
                    except (ValueError, TypeError):
                        pass
                # Always recalculate line_total from order_price * order_quantity
                product.line_total = round(float(product.order_price) * float(product.order_quantity), 2)
                for field in ('sku', 'supplier_code', 'name', 'description'):
                    if field in prod_data:
                        setattr(product, field, prod_data[field])
                product.save()
            except PurchaseOrderProduct.DoesNotExist:
                continue
    
    # Recalculate total from product lines if products exist
    line_total_sum = po.products.aggregate(total=Sum('line_total'))['total']
    if line_total_sum is not None:
        po.total = line_total_sum
        po.save(update_fields=['total'])
    
    return JsonResponse({'success': True, 'total': str(po.total)})


@login_required
def purchase_order_recalculate_total(request, po_id):
    """Recalculate a PO's total.

    For OS Doors POs (detected by order.os_doors_po or door.po_number),
    rebuilds product lines from the linked order's door items then sums.
    For all other POs, sums existing product line totals.
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    from decimal import Decimal

    po = get_object_or_404(PurchaseOrder, workguru_id=po_id)

    # ── OS Doors detection ────────────────────────────────────────────────────
    linked_order = None
    doors_qs = None

    if po.display_number:
        # Primary OS doors PO: order.os_doors_po == this PO's display_number
        linked_order = Order.objects.filter(os_doors_po=po.display_number).first()
        if linked_order:
            doors_qs = linked_order.os_doors.filter(
                Q(po_number='') | Q(po_number__isnull=True) | Q(po_number=po.display_number)
            )
        else:
            # Additional OS doors PO: individual doors are tagged with this PO number
            first_door = OSDoor.objects.filter(po_number=po.display_number).select_related('customer').first()
            if first_door:
                linked_order = first_door.customer
                doors_qs = linked_order.os_doors.filter(po_number=po.display_number)

    if doors_qs is not None and doors_qs.exists():
        po.products.all().delete()
        total = Decimal('0')
        for idx, door in enumerate(doors_qs.order_by('door_style', 'colour')):
            unit_price = door.cost_price if door.cost_price else Decimal('0')
            qty = Decimal(str(door.quantity))
            line_total = unit_price * qty
            total += line_total
            PurchaseOrderProduct.objects.create(
                purchase_order=po,
                sku=door.door_style,
                name=f'{door.door_style} - {door.style_colour} ({float(door.height):.0f}x{float(door.width):.0f}mm) {door.colour}',
                description=door.item_description or '',
                order_price=unit_price,
                order_quantity=qty,
                quantity=qty,
                line_total=line_total,
                sort_order=idx,
            )
        po.total = total
        po.save(update_fields=['total'])
        return JsonResponse({'success': True, 'total': str(po.total)})

    # ── Standard: sum existing product lines ──────────────────────────────────
    # First fix any stale line_total values (order_price * order_quantity != line_total)
    products_to_update = []
    for product in po.products.all():
        computed = product.order_price * product.order_quantity
        if computed != product.line_total:
            product.line_total = computed
            products_to_update.append(product)
    if products_to_update:
        from django.db import transaction
        with transaction.atomic():
            for p in products_to_update:
                p.save(update_fields=['line_total'])

    line_total_sum = po.products.aggregate(total=Sum('line_total'))['total']
    if line_total_sum is not None:
        po.total = line_total_sum
        po.save(update_fields=['total'])

    return JsonResponse({'success': True, 'total': str(po.total)})


@login_required
def purchase_order_receive(request, po_id):
    """Receive a purchase order (full or partial).

    Accepts an optional ``product_ids`` list in the JSON body.  When supplied
    only those regular product lines are marked as received; when omitted all
    un-received lines are marked (original behaviour).  The PO status is set
    to ``'Received'`` when every product line is fully received, otherwise to
    ``'Partially Received'``.
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    po = get_object_or_404(PurchaseOrder, workguru_id=po_id)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    try:
        return _do_receive_po(request, po, data)
    except Exception as exc:
        import traceback
        logger.error('purchase_order_receive error for po_id=%s: %s', po_id, traceback.format_exc())
        return JsonResponse({'error': str(exc)}, status=500)


@login_required
def purchase_order_unreceive(request, po_id):
    """Reverse a received PO: reset all received quantities to 0 and revert status to Approved."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    po = get_object_or_404(PurchaseOrder, workguru_id=po_id)

    if po.status not in ('Received', 'Partially Received'):
        return JsonResponse({'error': 'PO is not in a received state'}, status=400)

    try:
        # ── Regular product lines — reverse stock and reset quantities ──────
        for product in po.products.select_related('stock_item').all():
            if product.received_quantity > 0:
                delta = int(product.received_quantity)
                if product.stock_item and delta > 0:
                    try:
                        product.stock_item.quantity = max(0, product.stock_item.quantity - delta)
                        product.stock_item.save(update_fields=['quantity'])
                        StockHistory.objects.create(
                            stock_item=product.stock_item,
                            quantity=product.stock_item.quantity,
                            change_amount=-delta,
                            change_type='adjustment',
                            reference=po.display_number or '',
                            notes=f'Unreceive: reversed {delta} via {po.display_number} ({product.sku})',
                            created_by=request.user,
                        )
                    except Exception as stock_exc:
                        logger.error(
                            'purchase_order_unreceive: stock update failed for product %s: %s',
                            product.id, stock_exc,
                        )
                product.received_quantity = 0
                product.save(update_fields=['received_quantity'])

        # ── Boards PO PNX items ─────────────────────────────────────────────
        boards_po = BoardsPO.objects.filter(po_number=po.display_number).first()
        if boards_po:
            boards_po.pnx_items.filter(received=True).update(received=False, received_quantity=0)

        # ── OS Doors ────────────────────────────────────────────────────────
        for order in Order.objects.filter(os_doors_po=po.display_number):
            order.os_doors.filter(received=True).update(received=False, received_quantity=0)

        # ── Revert PO status ────────────────────────────────────────────────
        po.status = 'Approved'
        po.received_date = None
        po.save(update_fields=['status', 'received_date'])

        from .models import ActivityLog
        ActivityLog.objects.create(
            user=request.user,
            action='po_status_change',
            description=(
                f'{request.user.get_full_name() or request.user.username} unreceived purchase order '
                f'{po.display_number}.'
            ),
        )

        return JsonResponse({'success': True, 'status': po.status})

    except Exception as exc:
        import traceback
        logger.error('purchase_order_unreceive error for po_id=%s: %s', po_id, traceback.format_exc())
        return JsonResponse({'error': str(exc)}, status=500)


def _do_receive_po(request, po, data):
    from django.utils import timezone

    # Parse the received date (defaults to today)
    received_date_str = data.get('received_date', '')
    if received_date_str:
        try:
            rd = datetime.strptime(received_date_str, '%Y-%m-%d')
            today = rd.strftime('%d/%m/%Y')
        except (ValueError, TypeError):
            today = timezone.now().strftime('%d/%m/%Y')
    else:
        today = timezone.now().strftime('%d/%m/%Y')

    # Optional selective product list (list of PurchaseOrderProduct.id values)
    selected_ids = data.get('product_ids', None)  # None = receive all
    selective = selected_ids is not None

    # Optional per-product quantities: {product_id -> qty_to_receive}
    # Keys may arrive as ints (from JSON) or strings; normalise to int.
    raw_quantities = data.get('quantities', None)
    if raw_quantities is not None:
        quantities: dict | None = {int(k): float(v) for k, v in raw_quantities.items() if v is not None}
    else:
        quantities = None

    received_items = 0
    is_carnehill = (po.supplier_name or '').lower().find('carnehill') >= 0

    # ── Carnehill (boards) POs ──────────────────────────────────────────────
    boards_po = BoardsPO.objects.filter(po_number=po.display_number).first()
    if boards_po:
        for pnx_item in boards_po.pnx_items.all():
            if not pnx_item.is_fully_received:
                pnx_item.received = True
                pnx_item.received_quantity = pnx_item.cnt
                pnx_item.save(update_fields=['received', 'received_quantity'])
                received_items += 1

    # ── OS Doors POs ────────────────────────────────────────────────────────
    linked_orders = Order.objects.filter(os_doors_po=po.display_number)
    for order in linked_orders:
        for door in order.os_doors.all():
            if not door.is_fully_received:
                door.received = True
                door.received_quantity = door.quantity
                door.save(update_fields=['received', 'received_quantity'])
                received_items += 1

    # ── Regular product lines ───────────────────────────────────────────────
    from decimal import Decimal
    if not is_carnehill:
        all_products = list(po.products.select_related('stock_item').all())
        logger.info(
            '_do_receive_po: po=%s quantities=%s products=%s',
            po.display_number,
            quantities,
            [(p.id, float(p.order_quantity), float(p.received_quantity)) for p in all_products],
        )
        for product in all_products:
            # Determine if this product is selected and how much to receive
            if quantities is not None:
                if product.id not in quantities:
                    logger.debug('_do_receive_po: skip product %s (not in quantities)', product.id)
                    continue  # not ticked in the receive modal
                qty_to_receive = quantities[product.id]
            elif selective and product.id not in selected_ids:
                continue
            else:
                qty_to_receive = None  # receive all remaining

            if product.received_quantity >= product.order_quantity:
                logger.debug(
                    '_do_receive_po: skip product %s (already received %s/%s)',
                    product.id, product.received_quantity, product.order_quantity,
                )
                continue  # already fully received

            remaining = float(product.order_quantity) - float(product.received_quantity)
            delta = min(float(qty_to_receive), remaining) if qty_to_receive is not None else remaining

            if delta <= 0:
                continue

            product.received_quantity = Decimal(str(float(product.received_quantity) + delta))
            product.save(update_fields=['received_quantity'])
            logger.info('_do_receive_po: received product %s delta=%s new_qty=%s', product.id, delta, product.received_quantity)

            # Update linked stock item — wrapped separately so a failure here
            # does not roll back the received_quantity save above.
            if product.stock_item:
                try:
                    product.stock_item.quantity = max(0, product.stock_item.quantity + int(delta))
                    product.stock_item.save(update_fields=['quantity'])
                    StockHistory.objects.create(
                        stock_item=product.stock_item,
                        quantity=product.stock_item.quantity,
                        change_amount=int(delta),
                        change_type='purchase',
                        reference=po.display_number or '',
                        notes=f'Received {int(delta)} via {po.display_number} ({product.sku})',
                        created_by=request.user,
                    )
                except Exception as stock_exc:
                    logger.error(
                        '_do_receive_po: stock update failed for product %s: %s',
                        product.id, stock_exc,
                    )
            received_items += 1

    # ── Determine new status ────────────────────────────────────────────────
    # Refresh products from DB to get updated received quantities
    all_products_fresh = list(po.products.select_related('stock_item').all())
    if all_products_fresh:
        all_received = all(
            p.received_quantity >= p.order_quantity
            for p in all_products_fresh
            if p.order_quantity > 0
        )
        new_status = 'Received' if all_received else 'Partially Received'
    else:
        new_status = 'Received'

    if new_status == 'Partially Received':
        # ── Auto-split: move un-received lines to a new child PO ─────────────
        from datetime import timedelta as _td
        import re as _re
        from decimal import Decimal as _Dec

        _today_date = timezone.now().date()
        _expected_date_new = (_today_date + _td(days=7)).strftime('%d/%m/%Y')

        # Work out the next _N suffix for the split child
        base_display = po.display_number or ''
        root_match = _re.match(r'^(PO\d+)(?:_\d+)?$', base_display)
        root_number = root_match.group(1) if root_match else base_display
        existing_suffixes = PurchaseOrder.objects.filter(
            display_number__startswith=root_number + '_'
        ).values_list('display_number', flat=True)
        max_suffix = 0
        for dn in existing_suffixes:
            m = _re.match(r'^' + _re.escape(root_number) + r'_(\d+)$', dn)
            if m:
                max_suffix = max(max_suffix, int(m.group(1)))
        new_display = f'{root_number}_{max_suffix + 1}'

        max_wg_id = PurchaseOrder.objects.order_by('-workguru_id').values_list('workguru_id', flat=True).first() or 0
        new_wg_id = max(max_wg_id + 1, 800000)

        # Create the child PO for remaining items
        child_po = PurchaseOrder.objects.create(
            workguru_id=new_wg_id,
            number=new_display,
            display_number=new_display,
            description=f'Remaining items from {po.display_number}',
            parent_po=po,
            po_type=po.po_type,
            supplier_id=po.supplier_id,
            supplier_name=po.supplier_name,
            fitter=po.fitter,
            project_id=po.project_id,
            project_number=po.project_number,
            project_name=po.project_name,
            currency=po.currency,
            exchange_rate=po.exchange_rate,
            warehouse_id=po.warehouse_id,
            warehouse_name=po.warehouse_name,
            delivery_address_1=po.delivery_address_1,
            delivery_address_2=po.delivery_address_2,
            delivery_instructions=po.delivery_instructions,
            suburb=po.suburb,
            state=po.state,
            postcode=po.postcode,
            client_id_wg=po.client_id_wg,
            client_name=po.client_name,
            contact_name=po.contact_name,
            creator_name=request.user.get_full_name() or request.user.username,
            status='Approved',
            expected_date=_expected_date_new,
        )

        # Copy project links
        for proj in po.projects.all():
            PurchaseOrderProject.objects.create(
                purchase_order=child_po,
                project_type=proj.project_type,
                order=proj.order,
                label=proj.label,
                sort_order=proj.sort_order,
            )

        # Move un-received lines to the child PO
        child_total = _Dec('0')
        for idx, product in enumerate(all_products_fresh):
            remaining = float(product.order_quantity) - float(product.received_quantity)
            if remaining <= 0:
                continue
            remaining_dec = _Dec(str(remaining))
            line_total = product.order_price * remaining_dec
            child_total += line_total
            PurchaseOrderProduct.objects.create(
                purchase_order=child_po,
                product_id=product.product_id,
                sku=product.sku,
                supplier_code=product.supplier_code,
                name=product.name,
                description=product.description,
                notes=product.notes,
                order_price=product.order_price,
                order_quantity=remaining_dec,
                quantity=remaining_dec,
                received_quantity=_Dec('0'),
                invoice_price=product.invoice_price,
                line_total=line_total,
                unit_cost=product.unit_cost,
                tax_type=product.tax_type,
                tax_name=product.tax_name,
                tax_rate=product.tax_rate,
                account_code=product.account_code,
                expense_account_code=product.expense_account_code,
                sort_order=idx,
                stock_item=product.stock_item,
            )
            # Trim the original line to only the received quantity and remove if nothing received
            if product.received_quantity > 0:
                product.order_quantity = product.received_quantity
                product.quantity = product.received_quantity
                product.line_total = product.order_price * product.received_quantity
                product.save(update_fields=['order_quantity', 'quantity', 'line_total'])
            else:
                product.delete()

        child_po.total = child_total
        child_po.save(update_fields=['total'])

        # Mark the original PO as fully received
        po.status = 'Received'
        po.received_date = today
        po.save(update_fields=['status', 'received_date'])

        split_display = new_display

    else:
        # Fully received — no split needed
        po.status = 'Received'
        po.received_date = today
        po.save(update_fields=['status', 'received_date'])
        split_display = None

    # Mark the linked BoardsPO as ordered (Approved or Received both mean it was ordered)
    if po.display_number:
        BoardsPO.objects.filter(po_number=po.display_number).update(boards_ordered=True)

    return JsonResponse({
        'success': True,
        'received_items': received_items,
        'status': po.status,
        'received_date': po.received_date,
        'split_po': split_display,
    })


@login_required
def purchase_order_create(request):
    """Create a new purchase order manually"""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    from django.contrib import messages
    from django.shortcuts import redirect

    po_type = request.POST.get('po_type', 'supplier').strip()
    supplier_id = request.POST.get('supplier_id', '').strip()
    fitter_id = request.POST.get('fitter_id', '').strip()
    description = request.POST.get('description', '').strip()
    order_ids = request.POST.getlist('order_ids')
    product_id = request.POST.get('product_id', '').strip()

    supplier = None
    fitter = None
    stock_item = None

    # If launched from a product page, derive the supplier from the product
    if product_id:
        try:
            stock_item = StockItem.objects.get(id=int(product_id))
        except (StockItem.DoesNotExist, ValueError):
            messages.error(request, 'Product not found.')
            return redirect('purchase_orders_list')
        if not stock_item.supplier:
            messages.error(request, f'{stock_item.sku} has no supplier set — add a supplier before creating a PO.')
            return redirect('product_detail', item_id=stock_item.id)
        po_type = 'supplier'
        supplier = stock_item.supplier

    if po_type == 'fitter':
        if not fitter_id:
            messages.error(request, 'Please select a fitter.')
            return redirect('purchase_orders_list')
        try:
            fitter = Fitter.objects.get(id=int(fitter_id))
        except (Fitter.DoesNotExist, ValueError):
            messages.error(request, 'Fitter not found.')
            return redirect('purchase_orders_list')
    elif supplier is None:
        if not supplier_id:
            messages.error(request, 'Please select a supplier.')
            return redirect('purchase_orders_list')
        try:
            supplier = Supplier.objects.get(workguru_id=int(supplier_id))
        except (Supplier.DoesNotExist, ValueError):
            messages.error(request, 'Supplier not found.')
            return redirect('purchase_orders_list')

    # Generate a unique workguru_id in the 800000+ range for manual POs
    max_id = PurchaseOrder.objects.order_by('-workguru_id').values_list('workguru_id', flat=True).first() or 0
    manual_id = max(max_id + 1, 800000)

    # Generate display number: POXXXX (continue from highest existing PO number)
    import re
    last_num = 0
    for po_obj in PurchaseOrder.objects.filter(display_number__startswith='PO').order_by('-display_number'):
        match = re.match(r'^PO(\d+)$', po_obj.display_number or '')
        if match:
            last_num = max(last_num, int(match.group(1)))
    display_number = f'PO{last_num + 1}'

    po = PurchaseOrder.objects.create(
        workguru_id=manual_id,
        number=display_number,
        display_number=display_number,
        description=description or None,
        po_type=po_type,
        supplier_id=supplier.workguru_id if supplier else None,
        supplier_name=supplier.name if supplier else fitter.name,
        fitter=fitter,
        status='Draft',
        currency=(supplier.currency.strip().upper() if supplier and supplier.currency else 'GBP'),
        creator_name=request.user.get_full_name() or request.user.username,
    )

    # If a fitter PO with orders assigned, create the project links
    if po_type == 'fitter' and order_ids:
        for oid in order_ids:
            try:
                order = Order.objects.get(id=int(oid))
                PurchaseOrderProject.objects.create(
                    purchase_order=po,
                    project_type='customer',
                    order=order,
                    label=f'{order.sale_number} - {order.first_name} {order.last_name}'.strip(),
                )
            except (Order.DoesNotExist, ValueError):
                pass  # Order link failed but PO still created

    # If launched from a product page, add that product as the first line item
    if stock_item is not None:
        unit_cost = float(stock_item.cost or 0)
        pack_size = int(stock_item.pack_size or 1)
        PurchaseOrderProduct.objects.create(
            purchase_order=po,
            sku=stock_item.sku,
            supplier_code=stock_item.supplier_code or stock_item.supplier_sku or '',
            name=stock_item.name,
            description=stock_item.description or '',
            order_price=unit_cost,
            order_quantity=pack_size,
            line_total=round(unit_cost * pack_size, 2),
            sort_order=1,
            stock_item_id=stock_item.id,
        )
        po.total = po.products.aggregate(total=Sum('line_total'))['total'] or 0
        po.save(update_fields=['total'])

    entity_name = fitter.name if fitter else supplier.name
    log_activity(
        user=request.user,
        event_type='po_created',
        description=f'{request.user.get_full_name() or request.user.username} created {po_type} purchase order {display_number} ({entity_name}).',
    )

    messages.success(request, f'Purchase order {display_number} created.')
    return redirect('purchase_order_detail', po_id=po.workguru_id)


@login_required
def po_save_freight(request, po_id):
    """Save freight cost on a purchase order (AJAX)"""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    po = get_object_or_404(PurchaseOrder, workguru_id=po_id)
    data = json.loads(request.body)
    freight = data.get('freight_cost', 0)
    try:
        freight = round(float(freight), 2)
    except (ValueError, TypeError):
        freight = 0
    if freight < 0:
        freight = 0
    po.freight_cost = freight
    po.save(update_fields=['freight_cost'])
    return JsonResponse({'ok': True, 'freight_cost': str(po.freight_cost)})


@login_required
def set_po_type(request, po_id):
    """Set po_type to 'supplier' or 'fitter' (AJAX POST)."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    po = get_object_or_404(PurchaseOrder, workguru_id=po_id)
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'error': 'Invalid JSON'}, status=400)
    new_type = data.get('po_type', '')
    if new_type not in ('supplier', 'fitter'):
        return JsonResponse({'error': 'Invalid type'}, status=400)
    po.po_type = new_type
    po.save(update_fields=['po_type'])
    label = po.get_po_type_display()
    return JsonResponse({'success': True, 'po_type': new_type, 'label': label})


@login_required
def purchase_order_add_product(request, po_id):
    """Add a product line item to a purchase order (AJAX)"""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    po = get_object_or_404(PurchaseOrder, workguru_id=po_id)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    name = data.get('name', '').strip()
    if not name:
        return JsonResponse({'error': 'Product name is required'}, status=400)

    try:
        order_price = float(data.get('order_price', 0) or 0)
        order_quantity = float(data.get('order_quantity', 0) or 0)
    except (ValueError, TypeError):
        order_price = 0
        order_quantity = 0

    line_total = round(order_price * order_quantity, 2)

    # Determine sort order
    max_sort = po.products.order_by('-sort_order').values_list('sort_order', flat=True).first() or 0

    product = PurchaseOrderProduct.objects.create(
        purchase_order=po,
        sku=data.get('sku', '').strip(),
        supplier_code=data.get('supplier_code', '').strip(),
        name=name,
        description=data.get('description', '').strip(),
        order_price=order_price,
        order_quantity=order_quantity,
        line_total=line_total,
        sort_order=max_sort + 1,
        stock_item_id=data.get('stock_item_id') or None,
    )

    # Recalculate PO total
    new_total = po.products.aggregate(total=Sum('line_total'))['total'] or 0
    po.total = new_total
    po.save(update_fields=['total'])

    return JsonResponse({
        'success': True,
        'product': {
            'id': product.id,
            'sku': product.sku,
            'supplier_code': product.supplier_code,
            'name': product.name,
            'description': product.description,
            'order_price': str(product.order_price),
            'order_quantity': str(product.order_quantity),
            'line_total': str(product.line_total),
        },
        'po_total': str(new_total),
    })


@login_required
def purchase_order_push_lines(request, po_id):
    """Push invoice line items onto a purchase order as product lines (AJAX).

    Accepts a JSON body with a ``lines`` list, each entry having ``name``,
    ``order_price`` and ``order_quantity``. Used by the create/edit purchase
    invoice modal to copy invoice lines back onto the linked PO.
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    po = get_object_or_404(PurchaseOrder, workguru_id=po_id)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    lines = data.get('lines') or []
    if not isinstance(lines, list) or not lines:
        return JsonResponse({'error': 'No lines provided'}, status=400)

    # Fitter POs hold timesheets/expenses rather than product lines, so push
    # each line into the matching record type (mirrors po_pull_from_invoice).
    if po.po_type == 'fitter':
        from django.utils import timezone as _tz

        EXPENSE_KEYWORDS = ('expense', 'expenses', 'petrol', 'materials', 'mileage', 'fuel', 'hotel', 'accommodation')
        today = _tz.now().date()

        added = 0
        for line in lines:
            name = (line.get('name') or '').strip()
            if not name:
                continue
            try:
                order_price = float(line.get('order_price', 0) or 0)
                order_quantity = float(line.get('order_quantity', 0) or 0)
            except (ValueError, TypeError):
                order_price = 0
                order_quantity = 0

            if any(kw in name.lower() for kw in EXPENSE_KEYWORDS):
                Expense.objects.create(
                    purchase_order=po,
                    fitter=po.fitter,
                    date=today,
                    amount=round(order_price * (order_quantity or 1), 2),
                    description=name,
                    expense_type='other',
                )
            else:
                Timesheet.objects.create(
                    purchase_order=po,
                    fitter=po.fitter,
                    timesheet_type='installation',
                    date=today,
                    hours=order_quantity or 1,
                    hourly_rate=order_price,
                    description=name,
                )
            added += 1

        if not added:
            return JsonResponse({'error': 'No valid lines to push'}, status=400)

        return JsonResponse({'success': True, 'added': added})

    max_sort = po.products.order_by('-sort_order').values_list('sort_order', flat=True).first() or 0

    added = 0
    for line in lines:
        name = (line.get('name') or '').strip()
        if not name:
            continue
        try:
            order_price = float(line.get('order_price', 0) or 0)
            order_quantity = float(line.get('order_quantity', 0) or 0)
        except (ValueError, TypeError):
            order_price = 0
            order_quantity = 0

        max_sort += 1
        PurchaseOrderProduct.objects.create(
            purchase_order=po,
            name=name,
            description=(line.get('description') or '').strip(),
            order_price=order_price,
            order_quantity=order_quantity,
            line_total=round(order_price * order_quantity, 2),
            sort_order=max_sort,
        )
        added += 1

    if not added:
        return JsonResponse({'error': 'No valid lines to push'}, status=400)

    new_total = po.products.aggregate(total=Sum('line_total'))['total'] or 0
    po.total = new_total
    po.save(update_fields=['total'])

    return JsonResponse({
        'success': True,
        'added': added,
        'po_total': str(new_total),
    })


@login_required
def purchase_order_delete_product(request, po_id, product_id):
    """Delete a product line item from a purchase order (AJAX)"""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    po = get_object_or_404(PurchaseOrder, workguru_id=po_id)
    product = get_object_or_404(PurchaseOrderProduct, id=product_id, purchase_order=po)
    product.delete()

    # Recalculate PO total
    new_total = po.products.aggregate(total=Sum('line_total'))['total'] or 0
    po.total = new_total
    po.save(update_fields=['total'])

    return JsonResponse({'success': True, 'po_total': str(new_total)})


@login_required
def po_add_timesheet(request, po_id):
    """Add a timesheet entry to a fitter purchase order (AJAX)"""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    po = get_object_or_404(PurchaseOrder, workguru_id=po_id)
    if po.po_type != 'fitter':
        return JsonResponse({'error': 'Not a fitter PO'}, status=400)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    order_id = data.get('order_id')
    date_str = data.get('date', '')
    description = data.get('description', '').strip()
    hours = data.get('hours', 0)
    hourly_rate = data.get('hourly_rate', 0)

    if not date_str:
        return JsonResponse({'error': 'Date is required'}, status=400)

    try:
        hours = float(hours or 0)
        hourly_rate = float(hourly_rate or 0)
    except (ValueError, TypeError):
        return JsonResponse({'error': 'Invalid hours or rate'}, status=400)

    order = None
    if order_id:
        try:
            order = Order.objects.get(id=int(order_id))
        except (Order.DoesNotExist, ValueError):
            pass

    ts = Timesheet.objects.create(
        order=order,
        timesheet_type='installation',
        fitter=po.fitter,
        date=date_str,
        purchase_order=po,
        hours=hours,
        hourly_rate=hourly_rate,
        description=description,
    )

    line_total = round(hours * hourly_rate, 2)

    return JsonResponse({
        'success': True,
        'timesheet': {
            'id': ts.id,
            'date': str(ts.date),
            'description': ts.description,
            'hours': str(ts.hours),
            'hourly_rate': str(ts.hourly_rate),
            'line_total': f'{line_total:.2f}',
            'order_id': order.id if order else None,
            'order_sale_number': order.sale_number if order else '',
        },
    })


@login_required
def po_delete_timesheet(request, po_id, timesheet_id):
    """Delete a timesheet entry from a fitter purchase order (AJAX)"""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    po = get_object_or_404(PurchaseOrder, workguru_id=po_id)

    # Allow deleting timesheets linked directly to this PO, or linked via a
    # purchase invoice line that belongs to one of this PO's linked invoices.
    linked_pi_ids = list(po.linked_purchase_invoices.values_list('id', flat=True))
    ts = Timesheet.objects.filter(
        Q(id=timesheet_id, purchase_order=po) |
        Q(id=timesheet_id, purchase_invoice_line__invoice_id__in=linked_pi_ids)
    ).first()
    if ts is None:
        return JsonResponse({'error': 'Timesheet not found'}, status=404)
    ts.delete()
    return JsonResponse({'success': True})


@login_required
def po_link_timesheet(request, po_id, timesheet_id):
    """Link an existing timesheet to this purchase order (AJAX)"""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    po = get_object_or_404(PurchaseOrder, workguru_id=po_id)
    if po.po_type != 'fitter':
        return JsonResponse({'error': 'Not a fitter PO'}, status=400)

    ts = get_object_or_404(Timesheet, id=timesheet_id)

    # Only link if not already linked to another PO
    if ts.purchase_order and ts.purchase_order != po:
        return JsonResponse({'error': 'Timesheet already linked to another PO'}, status=400)

    ts.purchase_order = po
    ts.save(update_fields=['purchase_order'])
    return JsonResponse({'success': True})


@login_required
def po_unlink_timesheet(request, po_id, timesheet_id):
    """Unlink a timesheet from this purchase order without deleting it (AJAX)"""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    po = get_object_or_404(PurchaseOrder, workguru_id=po_id)
    ts = get_object_or_404(Timesheet, id=timesheet_id, purchase_order=po)

    ts.purchase_order = None
    ts.save(update_fields=['purchase_order'])
    return JsonResponse({'success': True})


@login_required
def po_update_timesheet(request, po_id, timesheet_id):
    """Update hours/rate on a timesheet entry linked to a fitter PO (AJAX)"""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    po = get_object_or_404(PurchaseOrder, workguru_id=po_id)
    ts = get_object_or_404(Timesheet, id=timesheet_id, purchase_order=po)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    hours = data.get('hours')
    hourly_rate = data.get('hourly_rate')
    date_str = data.get('date', '').strip()

    update_fields = []
    try:
        if date_str:
            from datetime import date as date_type
            ts.date = date_type.fromisoformat(date_str)
            update_fields.append('date')
        if hours is not None:
            ts.hours = float(hours)
            update_fields.append('hours')
        if hourly_rate is not None:
            ts.hourly_rate = float(hourly_rate)
            update_fields.append('hourly_rate')
    except (ValueError, TypeError):
        return JsonResponse({'error': 'Invalid data'}, status=400)

    if update_fields:
        ts.save(update_fields=update_fields)

    line_total = round(float(ts.hours or 0) * float(ts.hourly_rate or 0), 2)

    return JsonResponse({
        'success': True,
        'date': ts.date.strftime('%Y-%m-%d') if ts.date else '',
        'date_display': ts.date.strftime('%d/%m/%Y') if ts.date else '',
        'hours': str(ts.hours),
        'hourly_rate': str(ts.hourly_rate),
        'line_total': f'{line_total:.2f}',
    })


@login_required
def po_add_expense(request, po_id):
    """Add an expense entry to a fitter purchase order (AJAX)"""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    po = get_object_or_404(PurchaseOrder, workguru_id=po_id)
    if po.po_type != 'fitter':
        return JsonResponse({'error': 'Not a fitter PO'}, status=400)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    expense_type = data.get('expense_type', 'petrol').strip()
    date_str = data.get('date', '')
    amount = data.get('amount', 0)
    description = data.get('description', '').strip()
    order_id = data.get('order_id')

    if not date_str:
        return JsonResponse({'error': 'Date is required'}, status=400)

    try:
        amount = float(amount or 0)
    except (ValueError, TypeError):
        return JsonResponse({'error': 'Invalid amount'}, status=400)

    order = None
    if order_id:
        try:
            order = Order.objects.get(id=int(order_id))
        except (Order.DoesNotExist, ValueError):
            pass

    exp = Expense.objects.create(
        order=order,
        fitter=po.fitter,
        purchase_order=po,
        expense_type=expense_type,
        date=date_str,
        amount=amount,
        description=description,
    )

    return JsonResponse({
        'success': True,
        'expense': {
            'id': exp.id,
            'expense_type': exp.expense_type,
            'date': str(exp.date),
            'amount': f'{amount:.2f}',
            'description': exp.description,
            'order_id': order.id if order else None,
            'order_sale_number': order.sale_number if order else '',
        },
    })


@login_required
def po_delete_expense(request, po_id, expense_id):
    """Delete an expense entry from a fitter purchase order (AJAX)"""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    po = get_object_or_404(PurchaseOrder, workguru_id=po_id)
    exp = get_object_or_404(Expense, id=expense_id, purchase_order=po)
    exp.delete()
    return JsonResponse({'success': True})


@login_required
def po_pull_from_invoice(request, po_id):
    """Pull lines from linked purchase invoices into this fitter PO's timesheets/expenses (AJAX POST).

    Lines whose description contains expense-related keywords are created as Expense records;
    all other lines are created as Timesheets linked to the PI line (idempotent via the
    purchase_invoice_line FK).
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    po = get_object_or_404(PurchaseOrder, workguru_id=po_id)
    if po.po_type != 'fitter':
        return JsonResponse({'error': 'Not a fitter PO'}, status=400)

    from django.utils import timezone as _tz

    EXPENSE_KEYWORDS = ('expense', 'expenses', 'petrol', 'materials', 'mileage', 'fuel', 'hotel', 'accommodation')

    ts_created = 0
    ts_linked = 0
    exp_created = 0

    for invoice in po.linked_purchase_invoices.prefetch_related('line_items__order').all():
        for line in invoice.line_items.all():
            effective_date = line.line_date or invoice.date or _tz.now().date()
            is_expense = any(kw in line.description.lower() for kw in EXPENSE_KEYWORDS)

            if is_expense:
                already = Expense.objects.filter(
                    purchase_order=po,
                    description=line.description,
                    date=effective_date,
                ).exists()
                if not already:
                    Expense.objects.create(
                        purchase_order=po,
                        fitter=po.fitter,
                        order=line.order,
                        date=effective_date,
                        amount=line.line_total,
                        description=line.description,
                        expense_type='other',
                    )
                    exp_created += 1
            else:
                ts, created = Timesheet.objects.get_or_create(
                    purchase_invoice_line=line,
                    defaults={
                        'purchase_order': po,
                        'order': line.order,
                        'timesheet_type': 'installation',
                        'fitter': po.fitter,
                        'date': effective_date,
                        'description': line.description,
                    },
                )
                if created:
                    ts_created += 1
                elif ts.purchase_order_id != po.id:
                    ts.purchase_order = po
                    ts.save(update_fields=['purchase_order'])
                    ts_linked += 1

    return JsonResponse({
        'success': True,
        'timesheets_created': ts_created,
        'timesheets_linked': ts_linked,
        'expenses_created': exp_created,
    })


@login_required
def purchase_order_delete_board_items(request, po_id):
    """Delete a group of board (PNX) items from a purchase order (AJAX)"""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    po = get_object_or_404(PurchaseOrder, workguru_id=po_id)
    boards_po = BoardsPO.objects.filter(po_number=po.display_number).first()
    if not boards_po:
        return JsonResponse({'error': 'No board PO linked'}, status=404)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    item_ids_str = data.get('item_ids', '')
    if not item_ids_str:
        return JsonResponse({'error': 'No item IDs provided'}, status=400)

    item_ids = [int(x) for x in item_ids_str.split(',') if x.strip().isdigit()]
    deleted_count = PNXItem.objects.filter(id__in=item_ids, boards_po=boards_po).delete()[0]

    # Recalculate board total from remaining PNX items
    remaining_items = boards_po.pnx_items.all()
    pnx_total = sum(item.get_cost() for item in remaining_items)

    # Update the PO total
    product_total = po.products.aggregate(total=Sum('line_total'))['total'] or 0
    new_total = product_total + pnx_total
    po.total = new_total
    po.save(update_fields=['total'])

    return JsonResponse({
        'success': True,
        'deleted_count': deleted_count,
        'po_total': str(new_total),
    })

    """Receive a purchase order - marks PO as received and updates linked items (AJAX)"""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    
    po = get_object_or_404(PurchaseOrder, workguru_id=po_id)
    
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)
    
    from django.utils import timezone
    today = timezone.now().strftime('%d/%m/%Y')
    received_items = 0
    
    # Update PO status
    po.status = 'Received'
    po.received_date = today
    po.save(update_fields=['status', 'received_date'])
    
    # Handle Carnehill (boards) POs — mark PNX items as received
    boards_po = BoardsPO.objects.filter(po_number=po.display_number).first()
    if boards_po:
        boards_po.boards_ordered = True
        boards_po.save(update_fields=['boards_ordered'])
        for pnx_item in boards_po.pnx_items.all():
            if not pnx_item.is_fully_received:
                pnx_item.received = True
                pnx_item.received_quantity = pnx_item.cnt
                pnx_item.save(update_fields=['received', 'received_quantity'])
                received_items += 1
    
    # Handle OS Doors POs — mark OSDoor items as received
    linked_orders = Order.objects.filter(os_doors_po=po.display_number)
    for order in linked_orders:
        for door in order.os_doors.all():
            if not door.is_fully_received:
                door.received = True
                door.received_quantity = door.quantity
                door.save(update_fields=['received', 'received_quantity'])
                received_items += 1
    
    # Mark regular product lines as received
    for product in po.products.all():
        if product.received_quantity < product.order_quantity:
            product.received_quantity = product.order_quantity
            product.save(update_fields=['received_quantity'])
            received_items += 1
    
    return JsonResponse({
        'success': True,
        'received_items': received_items,
        'status': po.status,
        'received_date': po.received_date,
    })


@login_required
def suppliers_list(request):
    """Display list of all suppliers"""
    suppliers = list(Supplier.objects.all().order_by('name'))
    
    # Calculate PO count and total spend per supplier
    for supplier in suppliers:
        pos = PurchaseOrder.objects.filter(supplier_id=supplier.workguru_id)
        supplier.total_spend = pos.aggregate(total=Sum('total'))['total'] or 0
        supplier.po_count = pos.count()
    
    context = {
        'suppliers': suppliers,
        'total_count': len(suppliers),
    }
    
    return render(request, 'stock_take/suppliers_list.html', context)


@login_required
def supplier_create(request):
    """Create a new supplier manually"""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    
    from django.contrib import messages
    from django.shortcuts import redirect
    
    name = request.POST.get('name', '').strip()
    if not name:
        messages.error(request, 'Supplier name is required.')
        return redirect('suppliers_list')
    
    # Check for duplicate name
    if Supplier.objects.filter(name__iexact=name).exists():
        messages.error(request, f'A supplier named "{name}" already exists.')
        return redirect('suppliers_list')
    
    # Generate a unique positive workguru_id for manually created suppliers
    # Use 900000+ range to avoid collisions with real WorkGuru IDs
    max_id = Supplier.objects.order_by('-workguru_id').values_list('workguru_id', flat=True).first() or 0
    manual_id = max(max_id + 1, 900000)
    
    supplier = Supplier.objects.create(
        workguru_id=manual_id,
        name=name,
        email=request.POST.get('email', '').strip() or None,
        phone=request.POST.get('phone', '').strip() or None,
        website=request.POST.get('website', '').strip() or None,
        address_1=request.POST.get('address_1', '').strip() or None,
        city=request.POST.get('city', '').strip() or None,
        country=request.POST.get('country', '').strip() or None,
        is_active=True,
    )
    
    messages.success(request, f'Supplier "{supplier.name}" created successfully.')
    return redirect('supplier_detail', supplier_id=supplier.workguru_id)


@login_required
def product_search(request):
    """Search stock items for autocomplete when adding products to POs (AJAX)"""
    q = request.GET.get('q', '').strip()
    if len(q) < 2:
        return JsonResponse({'results': []})

    items = StockItem.objects.filter(
        Q(sku__icontains=q) |
        Q(name__icontains=q) |
        Q(description__icontains=q)
    ).order_by('name')[:20]

    results = []
    for item in items:
        results.append({
            'id': item.id,
            'sku': item.sku,
            'name': item.name,
            'cost': str(item.cost),
            'description': item.description or '',
            'quantity': item.quantity,
            'pack_size': item.pack_size,
        })

    return JsonResponse({'results': results})


@login_required
def supplier_detail(request, supplier_id):
    """Display detailed view of a single supplier"""
    supplier = get_object_or_404(Supplier, workguru_id=supplier_id)
    
    purchase_orders = PurchaseOrder.objects.filter(supplier_id=supplier.workguru_id)
    total_spend = purchase_orders.aggregate(total=Sum('total'))['total'] or 0
    
    # Status breakdown
    status_counts = dict(
        purchase_orders.values_list('status')
        .annotate(c=Count('id'))
        .values_list('status', 'c')
    )
    
    context = {
        'supplier': supplier,
        'purchase_orders': purchase_orders,
        'total_spend': total_spend,
        'po_count': purchase_orders.count(),
        'draft_count': status_counts.get('Draft', 0),
        'approved_count': status_counts.get('Approved', 0),
        'received_count': status_counts.get('Received', 0),
        'contacts': supplier.contacts.all(),
    }
    
    return render(request, 'stock_take/supplier_detail.html', context)


@login_required
def supplier_save(request, supplier_id):
    """Save edited supplier details via AJAX POST"""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    supplier = get_object_or_404(Supplier, workguru_id=supplier_id)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    editable_fields = [
        'name', 'email', 'phone', 'fax', 'website', 'abn',
        'address_1', 'address_2', 'city', 'state', 'postcode', 'country',
        'currency', 'credit_limit', 'credit_days', 'credit_terms_type',
        'price_tier', 'supplier_tax_rate', 'estimate_lead_time', 'vat_rate',
        'xero_default_account_code',
    ]

    update_fields = []
    for field in editable_fields:
        if field in data:
            val = data[field]
            if field == 'credit_limit':
                try:
                    val = float(val) if val else 0
                except (ValueError, TypeError):
                    val = 0
            elif field == 'vat_rate':
                try:
                    val = float(val) if val not in (None, '') else None
                except (ValueError, TypeError):
                    val = None
            elif field == 'estimate_lead_time':
                try:
                    val = int(val) if val else None
                except (ValueError, TypeError):
                    val = None
            elif field == 'xero_default_account_code':
                # DB column is NOT NULL; clear to empty string rather than None.
                val = (str(val).strip() if val not in (None, '') else '')
            elif field == 'email':
                val = val if val and '@' in val else None
            elif field == 'website':
                if val and not val.startswith(('http://', 'https://')):
                    val = f'https://{val}' if val and '.' in val else None
                elif not val:
                    val = None
            else:
                val = val or None
            setattr(supplier, field, val)
            update_fields.append(field)

    if 'is_active' in data:
        supplier.is_active = data['is_active']
        update_fields.append('is_active')

    if update_fields:
        supplier.save(update_fields=update_fields)

    return JsonResponse({'success': True})


@login_required
def purchase_order_download_pdf(request, po_id):
    """Download a Purchase Order as a PDF"""
    po = get_object_or_404(PurchaseOrder, workguru_id=po_id)
    products = list(po.products.select_related('stock_item').all())

    # For Carnehill board POs: if no regular products, use grouped PNX board items
    if not products:
        products = _get_board_product_rows_for_pdf(po)

    supplier_obj = Supplier.objects.filter(workguru_id=po.supplier_id).first() if po.supplier_id else None
    supplier_vat_rate = supplier_obj.vat_rate if supplier_obj and supplier_obj.vat_rate is not None else None
    hide_descriptions = request.GET.get('hide_descriptions', '0') == '1'
    pdf_buffer = generate_purchase_order_pdf(po, products, supplier_vat_rate=supplier_vat_rate, hide_descriptions=hide_descriptions)

    response = HttpResponse(pdf_buffer.read(), content_type='application/pdf')
    filename = f'Purchase_Order_{po.display_number}.pdf'
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


@login_required
def supplier_contact_add(request, supplier_id):
    """Add a new contact to a supplier (AJAX POST)"""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    supplier = get_object_or_404(Supplier, workguru_id=supplier_id)
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    contact = SupplierContact.objects.create(
        supplier=supplier,
        first_name=data.get('first_name', '').strip(),
        last_name=data.get('last_name', '').strip(),
        email=data.get('email', '').strip(),
        phone=data.get('phone', '').strip(),
        position=data.get('position', '').strip(),
        is_default=bool(data.get('is_default', False)),
    )
    return JsonResponse({
        'success': True,
        'contact': {
            'id': contact.id,
            'first_name': contact.first_name,
            'last_name': contact.last_name,
            'email': contact.email,
            'phone': contact.phone,
            'position': contact.position,
            'is_default': contact.is_default,
        }
    })


@login_required
def supplier_contact_edit(request, supplier_id, contact_id):
    """Edit an existing supplier contact (AJAX POST)"""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    supplier = get_object_or_404(Supplier, workguru_id=supplier_id)
    contact = get_object_or_404(SupplierContact, id=contact_id, supplier=supplier)
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    contact.first_name = data.get('first_name', contact.first_name).strip()
    contact.last_name = data.get('last_name', contact.last_name).strip()
    contact.email = data.get('email', contact.email).strip()
    contact.phone = data.get('phone', contact.phone).strip()
    contact.position = data.get('position', contact.position).strip()
    contact.is_default = bool(data.get('is_default', contact.is_default))
    contact.save()
    return JsonResponse({'success': True})


@login_required
def supplier_contact_delete(request, supplier_id, contact_id):
    """Delete a supplier contact (AJAX POST)"""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    supplier = get_object_or_404(Supplier, workguru_id=supplier_id)
    contact = get_object_or_404(SupplierContact, id=contact_id, supplier=supplier)
    contact.delete()
    return JsonResponse({'success': True})


@login_required
def supplier_contact_set_default(request, supplier_id, contact_id):
    """Set one contact as the default for PO emails (AJAX POST)"""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    supplier = get_object_or_404(Supplier, workguru_id=supplier_id)
    contact = get_object_or_404(SupplierContact, id=contact_id, supplier=supplier)
    # Clear existing defaults then set this one
    SupplierContact.objects.filter(supplier=supplier, is_default=True).update(is_default=False)
    contact.is_default = True
    contact.save(update_fields=['is_default'])
    # Also sync supplier.email to the default contact's email
    if contact.email:
        supplier.email = contact.email
        supplier.save(update_fields=['email'])
    return JsonResponse({'success': True, 'email': contact.email})


@login_required
def purchase_order_send_email(request, po_id):
    """Send the Purchase Order PDF to the supplier via email (AJAX)"""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    po = get_object_or_404(PurchaseOrder, workguru_id=po_id)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    recipient = data.get('to', '').strip()
    cc_list = [e.strip() for e in data.get('cc', '').split(',') if e.strip()]
    subject = data.get('subject', '').strip()
    body = data.get('body', '').strip()
    attachment_ids = data.get('attachment_ids', [])

    if not recipient:
        # Fall back to supplier email
        supplier = Supplier.objects.filter(workguru_id=po.supplier_id).first()
        if supplier and supplier.email:
            recipient = supplier.email
        else:
            return JsonResponse({'error': 'No recipient email provided and supplier has no email on file.'}, status=400)

    if not subject:
        subject = f'Purchase Order {po.display_number} - Sliderobes'

    # ── Auto-calculate expected date if not already set ────────
    if not po.expected_date:
        supplier_obj = Supplier.objects.filter(workguru_id=po.supplier_id).first() if po.supplier_id else None
        lead_days = (supplier_obj.estimate_lead_time if supplier_obj and supplier_obj.estimate_lead_time else 10)
        expected = datetime.now() + timedelta(days=lead_days)
        # Push weekends to Monday
        if expected.weekday() == 5:  # Saturday
            expected += timedelta(days=2)
        elif expected.weekday() == 6:  # Sunday
            expected += timedelta(days=1)
        po.expected_date = expected.strftime('%Y-%m-%d')

    # Format the expected date for display in the email
    expected_display = ''
    if po.expected_date:
        try:
            for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y'):
                try:
                    exp_dt = datetime.strptime(po.expected_date, fmt)
                    expected_display = exp_dt.strftime('%d/%m/%Y')
                    break
                except ValueError:
                    continue
        except Exception:
            expected_display = po.expected_date

    if not body:
        body = (
            f'Dear {po.supplier_name or "Supplier"},\n\n'
            f'Please find attached Purchase Order {po.display_number}.\n\n'
        )
        if expected_display:
            body += f'Expected delivery date: {expected_display}\n\n'
        body += (
            f'If you have any questions, please do not hesitate to contact us.\n\n'
            f'Kind regards,\n'
            f'Sliderobes'
        )

    # Generate the PDF
    products = list(po.products.select_related('stock_item').all())

    # For Carnehill board POs: if no regular products, use grouped PNX board items
    if not products:
        products = _get_board_product_rows_for_pdf(po)

    # Resolve supplier for VAT rate
    _send_supplier = Supplier.objects.filter(workguru_id=po.supplier_id).first() if po.supplier_id else None
    supplier_vat_rate = _send_supplier.vat_rate if _send_supplier and _send_supplier.vat_rate is not None else None
    hide_descriptions = bool(data.get('hide_descriptions', False))
    pdf_buffer = generate_purchase_order_pdf(po, products, supplier_vat_rate=supplier_vat_rate, hide_descriptions=hide_descriptions)
    pdf_filename = f'Purchase_Order_{po.display_number}.pdf'

    try:
        email = EmailMessage(
            subject=subject,
            body=body,
            from_email=settings.PO_FROM_EMAIL,
            to=[recipient],
            cc=cc_list if cc_list else None,
        )
        email.attach(pdf_filename, pdf_buffer.read(), 'application/pdf')

        # Attach any selected PO attachments
        if attachment_ids:
            extra_attachments = PurchaseOrderAttachment.objects.filter(
                id__in=attachment_ids, purchase_order=po
            )
            for att in extra_attachments:
                try:
                    att.file.open('rb')
                    email.attach(att.filename, att.file.read())
                    att.file.close()
                except Exception as file_err:
                    logger.warning(f'Could not attach file {att.filename}: {file_err}')

        email.send(fail_silently=False)

        # Mark PO as email sent
        po.email_sent = True
        po.email_sent_at = datetime.now()
        po.email_sent_to = recipient
        # Lock the issue date to today if not already set
        if not po.issue_date:
            po.issue_date = datetime.now().strftime('%d/%m/%Y')
        update_fields = ['email_sent', 'email_sent_at', 'email_sent_to', 'issue_date', 'expected_date']

        # Auto-approve the PO when email is sent
        if po.status == 'Draft':
            po.status = 'Approved'
            po.approved_by_name = request.user.get_full_name() or request.user.username
            po.approved_date = datetime.now().strftime('%d-%m-%Y')
            update_fields += ['status', 'approved_by_name', 'approved_date']

            # Mark boards as ordered on the linked BoardsPO (if any)
            if po.display_number:
                from .models import BoardsPO
                BoardsPO.objects.filter(po_number=po.display_number).update(boards_ordered=True)

        po.save(update_fields=update_fields)
        
        logger.info(f'PO {po.display_number} emailed to {recipient} by {request.user}')
        return JsonResponse({
            'success': True,
            'message': f'Purchase order sent to {recipient}',
            'new_status': po.status,
        })
    except Exception as e:
        logger.error(f'Failed to send PO {po.display_number} email: {e}')
        return JsonResponse({'error': f'Failed to send email: {str(e)}'}, status=500)


@login_required
def purchase_order_update_status(request, po_id):
    """Update a PO's status (approve or revert to draft) via AJAX."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    po = get_object_or_404(PurchaseOrder, workguru_id=po_id)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    new_status = data.get('status', '').strip()

    if new_status == 'Approved':
        po.status = 'Approved'
        po.approved_by_name = request.user.get_full_name() or request.user.username
        po.approved_date = datetime.now().strftime('%d-%m-%Y')
        po.save(update_fields=['status', 'approved_by_name', 'approved_date'])
        # Mark boards as ordered on the linked BoardsPO (if any)
        if po.display_number:
            from .models import BoardsPO
            BoardsPO.objects.filter(po_number=po.display_number).update(boards_ordered=True)
    elif new_status == 'Draft':
        po.status = 'Draft'
        po.approved_by_name = None
        po.approved_date = None
        po.save(update_fields=['status', 'approved_by_name', 'approved_date'])
        # Revert boards_ordered when un-approving
        if po.display_number:
            from .models import BoardsPO
            BoardsPO.objects.filter(po_number=po.display_number).update(boards_ordered=False)
    elif new_status == 'Cancelled':
        po.status = 'Cancelled'
        po.save(update_fields=['status'])
        # Cancelled POs should not count as incoming stock
        if po.display_number:
            from .models import BoardsPO
            BoardsPO.objects.filter(po_number=po.display_number).update(boards_ordered=False)
        log_activity(
            user=request.user,
            event_type='po_cancelled',
            description=f'{request.user.get_full_name() or request.user.username} cancelled purchase order {po.display_number}.',
        )
    else:
        return JsonResponse({'error': f'Invalid status: {new_status}'}, status=400)

    return JsonResponse({'success': True, 'status': po.status})


@login_required
def purchase_order_delete(request, po_id):
    """Delete a purchase order and all its products/attachments"""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    from django.contrib import messages
    from django.shortcuts import redirect

    po = get_object_or_404(PurchaseOrder, workguru_id=po_id)
    display_number = po.display_number

    # Delete attachment files from storage
    for att in po.attachments.all():
        try:
            att.file.delete(save=False)
        except Exception:
            pass

    po.delete()

    log_activity(
        user=request.user,
        event_type='po_deleted',
        description=f'{request.user.get_full_name() or request.user.username} deleted purchase order {display_number}.',
    )

    messages.success(request, f'Purchase Order {display_number} deleted.')
    return redirect('purchase_orders_list')


@login_required
def purchase_order_split(request, po_id):
    """
    Split a purchase order: keep the original PO with updated quantities
    and create one new child PO with the split-off quantities.
    Expects JSON body:
    {
        "splits": {
            "<product_id>": { "po1_qty": <int>, "po2_qty": <int> }
        }
    }
    po1_qty = quantities staying on the original PO.
    po2_qty = quantities going to the new split PO.
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    import re
    from decimal import Decimal

    po = get_object_or_404(PurchaseOrder, workguru_id=po_id)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    splits = data.get('splits', {})
    if not splits:
        return JsonResponse({'error': 'No split data provided'}, status=400)

    products = list(po.products.all().order_by('sort_order', 'id'))
    if not products:
        return JsonResponse({'error': 'PO has no products to split'}, status=400)

    # Collect product lines for the new split PO
    new_po_lines = []
    # Track items for activity log
    moved_items = []
    remaining_items = []

    for product in products:
        pid = str(product.id)
        info = splits.get(pid, {})
        qty2 = Decimal(str(info.get('po2_qty', 0)))
        qty1 = Decimal(str(info.get('po1_qty', 0)))

        if qty2 > 0:
            new_po_lines.append((product, qty2))
            moved_items.append({
                'sku': product.sku or '',
                'name': product.name or '',
                'qty': str(qty2),
            })
        if qty1 > 0:
            remaining_items.append({
                'sku': product.sku or '',
                'name': product.name or '',
                'qty': str(qty1),
            })

    if not new_po_lines:
        return JsonResponse({'error': 'No quantities assigned to the new PO'}, status=400)

    # Determine the next split suffix
    base_display = po.display_number or ''
    root_match = re.match(r'^(PO\d+)(?:_\d+)?$', base_display)
    root_number = root_match.group(1) if root_match else base_display

    existing = PurchaseOrder.objects.filter(
        display_number__startswith=root_number + '_'
    ).values_list('display_number', flat=True)

    max_suffix = 0
    for dn in existing:
        suffix_match = re.match(r'^' + re.escape(root_number) + r'_(\d+)$', dn)
        if suffix_match:
            max_suffix = max(max_suffix, int(suffix_match.group(1)))

    new_suffix = max_suffix + 1
    new_display = f'{root_number}_{new_suffix}'

    max_wg_id = PurchaseOrder.objects.order_by('-workguru_id').values_list('workguru_id', flat=True).first() or 0
    new_wg_id = max(max_wg_id + 1, 800000)

    # Create the new split PO
    child = PurchaseOrder.objects.create(
        workguru_id=new_wg_id,
        number=new_display,
        display_number=new_display,
        description=f'Split from {po.display_number}',
        parent_po=po,
        po_type=po.po_type,
        supplier_id=po.supplier_id,
        supplier_name=po.supplier_name,
        fitter=po.fitter,
        project_id=po.project_id,
        project_number=po.project_number,
        project_name=po.project_name,
        status='Draft',
        currency=po.currency,
        exchange_rate=po.exchange_rate,
        warehouse_id=po.warehouse_id,
        warehouse_name=po.warehouse_name,
        delivery_address_1=po.delivery_address_1,
        delivery_address_2=po.delivery_address_2,
        delivery_instructions=po.delivery_instructions,
        suburb=po.suburb,
        state=po.state,
        postcode=po.postcode,
        client_id_wg=po.client_id_wg,
        client_name=po.client_name,
        contact_name=po.contact_name,
        creator_name=request.user.get_full_name() or request.user.username,
    )

    child_total = Decimal('0')
    for idx, (product, qty) in enumerate(new_po_lines):
        line_total = product.order_price * qty
        PurchaseOrderProduct.objects.create(
            purchase_order=child,
            product_id=product.product_id,
            sku=product.sku,
            supplier_code=product.supplier_code,
            name=product.name,
            description=product.description,
            notes=product.notes,
            order_price=product.order_price,
            order_quantity=qty,
            quantity=qty,
            received_quantity=0,
            invoice_price=product.invoice_price,
            line_total=line_total,
            unit_cost=product.unit_cost,
            tax_type=product.tax_type,
            tax_name=product.tax_name,
            tax_rate=product.tax_rate,
            account_code=product.account_code,
            expense_account_code=product.expense_account_code,
            sort_order=idx,
            stock_item=product.stock_item,
        )
        child_total += line_total

    child.total = child_total
    child.save(update_fields=['total'])

    # Copy project links to the new PO
    for proj in po.projects.all():
        PurchaseOrderProject.objects.create(
            purchase_order=child,
            project_type=proj.project_type,
            order=proj.order,
            label=proj.label,
            sort_order=proj.sort_order,
        )

    # Update the original PO: adjust quantities for remaining products
    original_total = Decimal('0')
    for product in products:
        pid = str(product.id)
        info = splits.get(pid, {})
        qty1 = Decimal(str(info.get('po1_qty', 0)))

        if qty1 > 0:
            line_total = product.order_price * qty1
            product.order_quantity = qty1
            product.quantity = qty1
            product.line_total = line_total
            product.save(update_fields=['order_quantity', 'quantity', 'line_total'])
            original_total += line_total
        else:
            # No quantity remaining on original — remove the line
            product.delete()

    po.total = original_total
    po.save(update_fields=['total'])

    user_display = request.user.get_full_name() or request.user.username

    # Build descriptive summary
    moved_summary = ', '.join(f'{m["sku"]} ×{m["qty"]}' for m in moved_items)
    remaining_summary = ', '.join(f'{r["sku"]} ×{r["qty"]}' for r in remaining_items)
    desc_parts = [f'{user_display} split {po.display_number} → {new_display}.']
    if moved_summary:
        desc_parts.append(f'Moved: {moved_summary}.')
    if remaining_summary:
        desc_parts.append(f'Remaining: {remaining_summary}.')

    log_activity(
        user=request.user,
        event_type='po_split',
        description=' '.join(desc_parts),
        purchase_order=po,
        extra_data={
            'original_po': po.display_number,
            'new_po': new_display,
            'new_po_wg_id': child.workguru_id,
            'moved_items': moved_items,
            'remaining_items': remaining_items,
        },
    )

    return JsonResponse({
        'success': True,
        'created': [{'display_number': new_display, 'workguru_id': child.workguru_id}],
        'message': f'Split off {new_display} from {po.display_number}',
    })


@login_required
def purchase_order_list_media_files(request, po_id):
    """Return a JSON list of media files available to attach from the app.
    Sources: linked BoardsPO (PNX, CSV) and linked Order (original_csv, processed_csv).
    """
    po = get_object_or_404(PurchaseOrder, workguru_id=po_id)

    files = []
    already_attached = set(po.attachments.values_list('filename', flat=True))

    # 1. BoardsPO files
    boards_po = BoardsPO.objects.filter(po_number=po.display_number).first()
    if boards_po:
        if boards_po.file:
            fname = boards_po.file.name.split('/')[-1]
            files.append({
                'source': 'boards_po',
                'field': 'file',
                'filename': fname,
                'description': 'PNX board order file',
                'already_attached': fname in already_attached,
            })
        if boards_po.csv_file:
            fname = boards_po.csv_file.name.split('/')[-1]
            files.append({
                'source': 'boards_po',
                'field': 'csv_file',
                'filename': fname,
                'description': 'CSV board order file',
                'already_attached': fname in already_attached,
            })
        if boards_po.dwg_file:
            fname = boards_po.dwg_file.name.split('/')[-1]
            files.append({
                'source': 'boards_po',
                'field': 'dwg_file',
                'filename': fname,
                'description': 'DWG drawing file',
                'already_attached': fname in already_attached,
            })

    # 2. Linked Order files
    linked_order = None
    if po.project_id:
        linked_order = Order.objects.filter(workguru_id=str(po.project_id)).first()

    if linked_order:
        if linked_order.original_csv:
            fname = linked_order.original_csv.name.split('/')[-1]
            files.append({
                'source': 'order',
                'field': 'original_csv',
                'order_id': linked_order.id,
                'filename': fname,
                'description': 'Original accessories CSV',
                'already_attached': fname in already_attached,
            })
        if linked_order.processed_csv:
            fname = linked_order.processed_csv.name.split('/')[-1]
            files.append({
                'source': 'order',
                'field': 'processed_csv',
                'order_id': linked_order.id,
                'filename': fname,
                'description': 'Processed accessories CSV',
                'already_attached': fname in already_attached,
            })

    # 3. Also check all orders linked to this boards_po for their CSVs
    if boards_po:
        for order in boards_po.orders.all():
            if linked_order and order.id == linked_order.id:
                continue  # Already handled above
            if order.original_csv:
                fname = order.original_csv.name.split('/')[-1]
                files.append({
                    'source': 'order',
                    'field': 'original_csv',
                    'order_id': order.id,
                    'filename': fname,
                    'description': f'Original CSV ({order.sale_number})',
                    'already_attached': fname in already_attached,
                })
            if order.processed_csv:
                fname = order.processed_csv.name.split('/')[-1]
                files.append({
                    'source': 'order',
                    'field': 'processed_csv',
                    'order_id': order.id,
                    'filename': fname,
                    'description': f'Processed CSV ({order.sale_number})',
                    'already_attached': fname in already_attached,
                })

    # Sort: files matching this PO number first, then alphabetically
    po_number = po.display_number or ''
    files.sort(key=lambda f: (0 if po_number and po_number in f['filename'] else 1, f['filename']))

    return JsonResponse({'files': files})


@login_required
def purchase_order_attach_media_file(request, po_id):
    """Attach a specific media file from the app to this purchase order."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    from django.core.files.base import ContentFile

    po = get_object_or_404(PurchaseOrder, workguru_id=po_id)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    source = data.get('source')
    field = data.get('field')
    order_id = data.get('order_id')

    file_obj = None
    description = ''

    if source == 'boards_po':
        boards_po = BoardsPO.objects.filter(po_number=po.display_number).first()
        if not boards_po:
            return JsonResponse({'error': 'BoardsPO not found'}, status=404)
        if field == 'file':
            file_obj = boards_po.file
            description = 'PNX board order file'
        elif field == 'csv_file':
            file_obj = boards_po.csv_file
            description = 'CSV board order file'
        elif field == 'dwg_file':
            file_obj = boards_po.dwg_file
            description = 'DWG drawing file'
    elif source == 'order' and order_id:
        order = get_object_or_404(Order, id=order_id)
        if field == 'original_csv':
            file_obj = order.original_csv
            description = f'Original accessories CSV ({order.sale_number})'
        elif field == 'processed_csv':
            file_obj = order.processed_csv
            description = f'Processed accessories CSV ({order.sale_number})'

    if not file_obj:
        return JsonResponse({'error': 'File not found'}, status=404)

    try:
        file_obj.open('rb')
        content = file_obj.read()
        file_obj.close()
        fname = file_obj.name.split('/')[-1]

        # Don't attach duplicates
        if po.attachments.filter(filename=fname).exists():
            return JsonResponse({'error': f'{fname} is already attached'}, status=400)

        att = PurchaseOrderAttachment(
            purchase_order=po,
            filename=fname,
            description=description,
            uploaded_by=request.user.get_full_name() or request.user.username,
        )
        att.file.save(fname, ContentFile(content), save=False)
        att.save()

        return JsonResponse({
            'success': True,
            'attachment': {
                'id': att.id,
                'filename': att.filename,
                'description': att.description,
                'uploaded_by': att.uploaded_by,
                'uploaded_at': att.uploaded_at.strftime('%d-%m-%Y %H:%M'),
                'url': att.file.url,
            }
        })
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@login_required
def purchase_order_upload_attachment(request, po_id):
    """Upload a file attachment to a purchase order"""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    po = get_object_or_404(PurchaseOrder, workguru_id=po_id)

    uploaded_file = request.FILES.get('file')
    if not uploaded_file:
        return JsonResponse({'error': 'No file provided'}, status=400)

    description = request.POST.get('description', '').strip()

    attachment = PurchaseOrderAttachment.objects.create(
        purchase_order=po,
        file=uploaded_file,
        filename=uploaded_file.name,
        description=description,
        uploaded_by=request.user.get_full_name() or request.user.username,
    )

    return JsonResponse({
        'success': True,
        'attachment': {
            'id': attachment.id,
            'filename': attachment.filename,
            'description': attachment.description,
            'uploaded_by': attachment.uploaded_by,
            'uploaded_at': attachment.uploaded_at.strftime('%d-%m-%Y %H:%M'),
            'url': attachment.file.url,
        }
    })


@login_required
def purchase_order_delete_attachment(request, po_id, attachment_id):
    """Delete a file attachment from a purchase order"""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    po = get_object_or_404(PurchaseOrder, workguru_id=po_id)
    attachment = get_object_or_404(PurchaseOrderAttachment, id=attachment_id, purchase_order=po)

    try:
        attachment.file.delete(save=False)
    except Exception:
        pass
    attachment.delete()

    return JsonResponse({'success': True})


# ── PO Invoices ────────────────────────────────────────────

@login_required
def po_upload_invoice(request, po_id):
    """Upload a supplier invoice to a purchase order."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    po = get_object_or_404(PurchaseOrder, workguru_id=po_id)

    invoice_number = request.POST.get('invoice_number', '').strip()
    date_str = request.POST.get('date', '').strip()
    due_date_str = request.POST.get('due_date', '').strip()
    amount = request.POST.get('amount', '0').strip()
    notes = request.POST.get('notes', '').strip()
    status = request.POST.get('status', 'pending').strip()
    uploaded_file = request.FILES.get('file')

    from decimal import Decimal, InvalidOperation
    try:
        amount_val = Decimal(amount) if amount else Decimal('0')
    except (InvalidOperation, ValueError):
        amount_val = Decimal('0')

    inv_date = None
    inv_due_date = None
    if date_str:
        for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y'):
            try:
                from datetime import datetime as _dt
                inv_date = _dt.strptime(date_str, fmt).date()
                break
            except ValueError:
                continue
    if due_date_str:
        for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y'):
            try:
                from datetime import datetime as _dt
                inv_due_date = _dt.strptime(due_date_str, fmt).date()
                break
            except ValueError:
                continue

    invoice = PurchaseOrderInvoice(
        purchase_order=po,
        invoice_number=invoice_number,
        date=inv_date,
        due_date=inv_due_date,
        amount=amount_val,
        status=status,
        notes=notes,
        uploaded_by=request.user.get_full_name() or request.user.username,
    )

    if uploaded_file:
        invoice.file = uploaded_file
        invoice.filename = uploaded_file.name

    invoice.save()

    return JsonResponse({
        'success': True,
        'invoice': {
            'id': invoice.id,
            'invoice_number': invoice.invoice_number,
            'filename': invoice.filename,
            'date': invoice.date.strftime('%d-%m-%Y') if invoice.date else '',
            'due_date': invoice.due_date.strftime('%d-%m-%Y') if invoice.due_date else '',
            'amount': str(invoice.amount),
            'currency': invoice.currency,
            'status': invoice.status,
            'notes': invoice.notes,
            'uploaded_by': invoice.uploaded_by,
            'uploaded_at': invoice.uploaded_at.strftime('%d-%m-%Y %H:%M'),
            'url': invoice.file.url if invoice.file else '',
        }
    })


@login_required
def po_update_invoice(request, po_id, invoice_id):
    """Update an existing PO invoice (status, amount, notes, etc.)."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    import json
    po = get_object_or_404(PurchaseOrder, workguru_id=po_id)
    invoice = get_object_or_404(PurchaseOrderInvoice, id=invoice_id, purchase_order=po)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    from decimal import Decimal, InvalidOperation

    if 'invoice_number' in data:
        invoice.invoice_number = data['invoice_number'].strip()
    if 'status' in data and data['status'] in dict(PurchaseOrderInvoice.STATUS_CHOICES):
        invoice.status = data['status']
    if 'amount' in data:
        try:
            invoice.amount = Decimal(str(data['amount']))
        except (InvalidOperation, ValueError):
            pass
    if 'notes' in data:
        invoice.notes = data['notes'].strip()
    if 'date' in data:
        date_str = data['date'].strip()
        if date_str:
            for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y'):
                try:
                    from datetime import datetime as _dt
                    invoice.date = _dt.strptime(date_str, fmt).date()
                    break
                except ValueError:
                    continue
        else:
            invoice.date = None
    if 'due_date' in data:
        dd_str = data['due_date'].strip()
        if dd_str:
            for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y'):
                try:
                    from datetime import datetime as _dt
                    invoice.due_date = _dt.strptime(dd_str, fmt).date()
                    break
                except ValueError:
                    continue
        else:
            invoice.due_date = None

    invoice.save()

    return JsonResponse({
        'success': True,
        'invoice': {
            'id': invoice.id,
            'invoice_number': invoice.invoice_number,
            'date': invoice.date.strftime('%d-%m-%Y') if invoice.date else '',
            'due_date': invoice.due_date.strftime('%d-%m-%Y') if invoice.due_date else '',
            'amount': str(invoice.amount),
            'status': invoice.status,
            'notes': invoice.notes,
        }
    })


@login_required
def po_delete_invoice(request, po_id, invoice_id):
    """Delete a supplier invoice from a purchase order."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    po = get_object_or_404(PurchaseOrder, workguru_id=po_id)
    invoice = get_object_or_404(PurchaseOrderInvoice, id=invoice_id, purchase_order=po)

    if invoice.file:
        try:
            invoice.file.delete(save=False)
        except Exception:
            pass
    invoice.delete()

    return JsonResponse({'success': True})


@login_required
def po_link_purchase_invoice(request, po_id):
    """Link an existing PurchaseInvoice to this PO (M2M)."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    po = get_object_or_404(PurchaseOrder, workguru_id=po_id)
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    invoice_id = data.get('invoice_id')
    if not invoice_id:
        return JsonResponse({'error': 'invoice_id is required'}, status=400)

    invoice = get_object_or_404(PurchaseInvoice, id=invoice_id)
    invoice.purchase_orders.add(po)

    return JsonResponse({
        'success': True,
        'invoice': {
            'id': invoice.id,
            'invoice_number': invoice.invoice_number,
            'supplier_name': invoice.supplier_name or '—',
            'date': invoice.date.strftime('%d/%m/%Y') if invoice.date else '—',
            'total': str(invoice.total),
            'amount_outstanding': str(invoice.amount_outstanding),
            'status': invoice.status,
            'payment_status': invoice.payment_status,
            'url': f'/purchase-invoices/{invoice.id}/',
        },
    })


@login_required
def po_unlink_purchase_invoice(request, po_id, invoice_id):
    """Remove the M2M link between a PurchaseInvoice and this PO."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    po = get_object_or_404(PurchaseOrder, workguru_id=po_id)
    invoice = get_object_or_404(PurchaseInvoice, id=invoice_id)
    invoice.purchase_orders.remove(po)
    return JsonResponse({'success': True})


@login_required
def purchase_order_attach_boards_files(request, po_id):
    """Attach PNX and CSV files from the linked BoardsPO to this purchase order"""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    from django.core.files.base import ContentFile

    po = get_object_or_404(PurchaseOrder, workguru_id=po_id)
    boards_po = BoardsPO.objects.filter(po_number=po.display_number).first()

    if not boards_po:
        return JsonResponse({'error': f'No BoardsPO found matching {po.display_number}'}, status=404)

    attached = []

    if boards_po.file:
        try:
            boards_po.file.open('rb')
            content = boards_po.file.read()
            boards_po.file.close()
            fname = boards_po.file.name.split('/')[-1]
            att = PurchaseOrderAttachment(
                purchase_order=po,
                filename=fname,
                description='PNX board order file',
                uploaded_by=request.user.get_full_name() or request.user.username,
            )
            att.file.save(fname, ContentFile(content), save=False)
            att.save()
            attached.append(fname)
        except Exception as e:
            logger.error(f'Error attaching PNX file: {e}')

    if boards_po.csv_file:
        try:
            boards_po.csv_file.open('rb')
            content = boards_po.csv_file.read()
            boards_po.csv_file.close()
            fname = boards_po.csv_file.name.split('/')[-1]
            att = PurchaseOrderAttachment(
                purchase_order=po,
                filename=fname,
                description='CSV board order file',
                uploaded_by=request.user.get_full_name() or request.user.username,
            )
            att.file.save(fname, ContentFile(content), save=False)
            att.save()
            attached.append(fname)
        except Exception as e:
            logger.error(f'Error attaching CSV file: {e}')

    if boards_po.dwg_file:
        try:
            boards_po.dwg_file.open('rb')
            content = boards_po.dwg_file.read()
            boards_po.dwg_file.close()
            fname = boards_po.dwg_file.name.split('/')[-1]
            att = PurchaseOrderAttachment(
                purchase_order=po,
                filename=fname,
                description='DWG drawing file',
                uploaded_by=request.user.get_full_name() or request.user.username,
            )
            att.file.save(fname, ContentFile(content), save=False)
            att.save()
            attached.append(fname)
        except Exception as e:
            logger.error(f'Error attaching DWG file: {e}')

    if not attached:
        return JsonResponse({'error': 'No files available to attach'}, status=400)

    return JsonResponse({
        'success': True,
        'message': f'Attached {len(attached)} file(s): {", ".join(attached)}',
    })


@login_required
def create_boards_purchase_order(request, order_id):
    """Create a local PurchaseOrder for boards and attach PNX/CSV files.
    This does NOT push to WorkGuru — it creates the local PO record only.
    """
    import re
    from django.contrib import messages
    from django.shortcuts import redirect
    from django.core.files.base import ContentFile

    order = get_object_or_404(Order, id=order_id)

    # Support specific boards_po_id via query param (for additional POs)
    boards_po_id = request.GET.get('boards_po_id') or request.POST.get('boards_po_id')
    if boards_po_id:
        boards_po = get_object_or_404(BoardsPO, id=int(boards_po_id))
    else:
        boards_po = order.boards_po

    if not boards_po:
        messages.error(request, 'This order does not have a BoardsPO assigned.')
        return redirect('order_details', order_id=order_id)

    # Check if a PurchaseOrder already exists for this BoardsPO number
    existing = PurchaseOrder.objects.filter(display_number=boards_po.po_number).first()
    if existing:
        # Attach files if not already attached
        _attach_boards_files_to_po(existing, boards_po, request.user)
        messages.info(request, f'Purchase Order {existing.display_number} already exists. Files attached.')
        return redirect('order_details', order_id=order_id)

    # Build customer name
    if order.customer:
        customer_name = f'{order.customer.first_name} {order.customer.last_name}'.strip()
    else:
        customer_name = f'{order.first_name} {order.last_name}'.strip()

    # Generate a unique workguru_id for manual POs (800000+ range)
    max_id = PurchaseOrder.objects.order_by('-workguru_id').values_list('workguru_id', flat=True).first() or 0
    manual_id = max(max_id + 1, 800000)

    # Use the boards_po po_number as the display number
    po_number = boards_po.po_number

    # Get Carnehill supplier if it exists
    supplier = Supplier.objects.filter(name__icontains='Carnehill').first()

    po = PurchaseOrder.objects.create(
        workguru_id=manual_id,
        number=po_number,
        display_number=po_number,
        description=f'Boards order for {customer_name} - Sale {order.sale_number}',
        supplier_id=supplier.workguru_id if supplier else None,
        supplier_name=supplier.name if supplier else 'Carnehill Joinery Ltd',
        project_id=int(order.workguru_id) if order.workguru_id else None,
        project_number=order.sale_number,
        project_name=customer_name,
        delivery_address_1='61 Boucher Crescent, BT126HU, Belfast',
        po_type='supplier',
        status='Draft',
        currency=supplier.currency.strip().upper() if supplier and supplier.currency else 'GBP',
        creator_name=request.user.get_full_name() or request.user.username,
    )

    # Attach PNX and CSV files
    _attach_boards_files_to_po(po, boards_po, request.user)

    messages.success(request, f'Purchase Order {po_number} created with board files attached.')
    return redirect('order_details', order_id=order_id)


def _attach_boards_files_to_po(po, boards_po, user):
    """Helper to attach PNX/CSV from a BoardsPO to a PurchaseOrder."""
    from django.core.files.base import ContentFile

    user_name = user.get_full_name() or user.username

    if boards_po.file:
        try:
            boards_po.file.open('rb')
            content = boards_po.file.read()
            boards_po.file.close()
            fname = boards_po.file.name.split('/')[-1]
            # Avoid duplicates
            if not po.attachments.filter(filename=fname).exists():
                att = PurchaseOrderAttachment(
                    purchase_order=po,
                    filename=fname,
                    description='PNX board order file',
                    uploaded_by=user_name,
                )
                att.file.save(fname, ContentFile(content), save=False)
                att.save()
        except Exception:
            pass

    if boards_po.csv_file:
        try:
            boards_po.csv_file.open('rb')
            content = boards_po.csv_file.read()
            boards_po.csv_file.close()
            fname = boards_po.csv_file.name.split('/')[-1]
            if not po.attachments.filter(filename=fname).exists():
                att = PurchaseOrderAttachment(
                    purchase_order=po,
                    filename=fname,
                    description='CSV board order file',
                    uploaded_by=user_name,
                )
                att.file.save(fname, ContentFile(content), save=False)
                att.save()
        except Exception:
            pass

    if boards_po.dwg_file:
        try:
            boards_po.dwg_file.open('rb')
            content = boards_po.dwg_file.read()
            boards_po.dwg_file.close()
            fname = boards_po.dwg_file.name.split('/')[-1]
            if not po.attachments.filter(filename=fname).exists():
                att = PurchaseOrderAttachment(
                    purchase_order=po,
                    filename=fname,
                    description='DWG drawing file',
                    uploaded_by=user_name,
                )
                att.file.save(fname, ContentFile(content), save=False)
                att.save()
        except Exception:
            pass

    # Generate and attach preview images for angled boards
    if boards_po.is_angled:
        from collections import defaultdict
        groups = defaultdict(list)
        for item in boards_po.pnx_items.all():
            if item.left_height is not None:
                key = (float(item.left_height or 0), float(item.right_height or 0),
                       float(item.cwidth), float(item.top_edge or 0))
                groups[key].append(item)

        for idx, ((lh, rh, w, te), items) in enumerate(sorted(groups.items()), 1):
            fname = f'angled_preview_{int(lh)}x{int(rh)}x{int(w)}.png'
            if po.attachments.filter(filename=fname).exists():
                continue
            try:
                png_data = _generate_angled_board_preview(lh, rh, w, te)
                if png_data:
                    desc = f'Angled board L:{int(lh)} R:{int(rh)} W:{int(w)}'
                    if te > 0:
                        desc += f' TE:{int(te)}'
                    desc += f'mm (x{sum(int(i.cnt) for i in items)})'
                    att = PurchaseOrderAttachment(
                        purchase_order=po,
                        filename=fname,
                        description=desc,
                        uploaded_by=user_name,
                    )
                    att.file.save(fname, ContentFile(png_data), save=False)
                    att.save()
            except Exception:
                pass


@login_required
def create_os_doors_purchase_order(request, order_id):
    """Create a local PurchaseOrder for OS Doors and add door items as products.
    Auto-generates the next PO number and links it to the order.
    Supports both AJAX (returns JSON) and regular requests (returns redirect).
    """
    import re
    from django.contrib import messages
    from django.shortcuts import redirect
    from decimal import Decimal

    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'

    order = get_object_or_404(Order, id=order_id)

    # If a PO already exists for this order's os_doors_po, just redirect
    if order.os_doors_po:
        existing = PurchaseOrder.objects.filter(display_number=order.os_doors_po).first()
        if existing:
            if is_ajax:
                return JsonResponse({'success': False, 'error': f'OS Doors Purchase Order {existing.display_number} already exists.'})
            messages.info(request, f'OS Doors Purchase Order {existing.display_number} already exists.')
            return redirect('order_details', order_id=order_id)

    # Auto-generate the next PO number (check both tables to avoid collisions)
    max_num = 0
    for pn in BoardsPO.objects.filter(po_number__regex=r'^PO\d+$').values_list('po_number', flat=True):
        m = re.search(r'(\d+)', pn)
        if m:
            max_num = max(max_num, int(m.group(1)))
    for field in ['display_number', 'number']:
        for pn in PurchaseOrder.objects.filter(**{f'{field}__regex': r'^PO\d+$'}).values_list(field, flat=True):
            m = re.search(r'(\d+)', pn)
            if m:
                max_num = max(max_num, int(m.group(1)))

    next_num = max(max_num + 1, 1000)
    po_number = f'PO{next_num}'
    while (BoardsPO.objects.filter(po_number=po_number).exists() or
           PurchaseOrder.objects.filter(display_number=po_number).exists() or
           PurchaseOrder.objects.filter(number=po_number).exists()):
        next_num += 1
        po_number = f'PO{next_num}'

    # Build customer name
    if order.customer:
        customer_name = f'{order.customer.first_name} {order.customer.last_name}'.strip() or getattr(order.customer, 'name', '')
    else:
        customer_name = f'{order.first_name} {order.last_name}'.strip()

    # Generate a unique workguru_id for manual POs (800000+ range)
    max_wg_id = PurchaseOrder.objects.order_by('-workguru_id').values_list('workguru_id', flat=True).first() or 0
    manual_wg_id = max(max_wg_id + 1, 800000)

    # Get O & S Doors supplier
    supplier = Supplier.objects.filter(name__icontains='O & S Doors').first()
    if not supplier:
        supplier = Supplier.objects.filter(name__icontains='O S Door').first()

    po = PurchaseOrder.objects.create(
        workguru_id=manual_wg_id,
        number=po_number,
        display_number=po_number,
        description=f'OS Doors for {customer_name} - Sale {order.sale_number}',
        supplier_id=supplier.workguru_id if supplier else None,
        supplier_name=supplier.name if supplier else 'O & S Doors Ltd',
        project_id=int(order.workguru_id) if order.workguru_id else None,
        project_number=order.sale_number,
        project_name=customer_name,
        delivery_address_1='61 Boucher Crescent, BT126HU, Belfast',
        po_type='supplier',
        status='Draft',
        currency='GBP',
        creator_name=request.user.get_full_name() or request.user.username,
    )

    # Add each OS door as a product line (if any exist)
    total = Decimal('0')
    door_count = 0
    for idx, door in enumerate(order.os_doors.all()):
        unit_price = door.cost_price or Decimal('0')
        qty = Decimal(str(door.quantity))
        line_total = unit_price * qty
        total += line_total
        door_count += 1

        PurchaseOrderProduct.objects.create(
            purchase_order=po,
            sku=door.door_style,
            name=f'{door.door_style} - {door.style_colour} ({door.height:.0f}x{door.width:.0f}mm) {door.colour}',
            description=door.item_description or '',
            order_price=unit_price,
            order_quantity=qty,
            quantity=qty,
            line_total=line_total,
            sort_order=idx,
        )

    # Update PO total
    po.total = total
    po.save(update_fields=['total'])

    # Link the PO number to the order and mark doors as ordered
    order.os_doors_po = po_number
    order.os_doors_required = True
    order.save(update_fields=['os_doors_po', 'os_doors_required'])
    if door_count:
        order.os_doors.update(ordered=True, po_number=po_number)

    messages.success(request, f'OS Doors Purchase Order {po_number} created with {door_count} door item(s).')
    if is_ajax:
        return JsonResponse({'success': True, 'po_number': po_number, 'workguru_id': po.workguru_id})
    return redirect('order_details', order_id=order_id)


@login_required
def add_additional_os_doors_po(request, order_id):
    """Create an additional OS Doors PurchaseOrder and add it to order.additional_os_doors_pos."""
    import re
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required'}, status=405)

    order = get_object_or_404(Order, id=order_id)

    # Auto-generate next PO number (avoid collisions with BoardsPO and PurchaseOrder)
    max_num = 0
    for pn in BoardsPO.objects.filter(po_number__regex=r'^PO\d+$').values_list('po_number', flat=True):
        m = re.search(r'(\d+)', pn)
        if m:
            max_num = max(max_num, int(m.group(1)))
    for field in ['display_number', 'number']:
        for pn in PurchaseOrder.objects.filter(**{f'{field}__regex': r'^PO\d+$'}).values_list(field, flat=True):
            m = re.search(r'(\d+)', pn)
            if m:
                max_num = max(max_num, int(m.group(1)))
    next_num = max(max_num + 1, 1000)
    po_number = f'PO{next_num}'
    while (BoardsPO.objects.filter(po_number=po_number).exists() or
           PurchaseOrder.objects.filter(display_number=po_number).exists() or
           PurchaseOrder.objects.filter(number=po_number).exists()):
        next_num += 1
        po_number = f'PO{next_num}'

    if order.customer:
        customer_name = f'{order.customer.first_name} {order.customer.last_name}'.strip() or getattr(order.customer, 'name', '')
    else:
        customer_name = f'{order.first_name} {order.last_name}'.strip()

    max_wg_id = PurchaseOrder.objects.order_by('-workguru_id').values_list('workguru_id', flat=True).first() or 0
    manual_wg_id = max(max_wg_id + 1, 800000)

    supplier = Supplier.objects.filter(name__icontains='O & S Doors').first()
    if not supplier:
        supplier = Supplier.objects.filter(name__icontains='O S Door').first()

    po = PurchaseOrder.objects.create(
        workguru_id=manual_wg_id,
        number=po_number,
        display_number=po_number,
        description=f'Additional OS Doors for {customer_name} - Sale {order.sale_number}',
        supplier_id=supplier.workguru_id if supplier else None,
        supplier_name=supplier.name if supplier else 'O & S Doors Ltd',
        project_id=int(order.workguru_id) if order.workguru_id else None,
        project_number=order.sale_number,
        project_name=customer_name,
        delivery_address_1='61 Boucher Crescent, BT126HU, Belfast',
        po_type='supplier',
        status='Draft',
        currency='GBP',
        creator_name=request.user.get_full_name() or request.user.username,
    )

    order.additional_os_doors_pos.add(po)

    return JsonResponse({
        'success': True,
        'po_number': po_number,
        'workguru_id': po.workguru_id,
        'url': f'/purchase-order/{po.workguru_id}/',
    })


@login_required
def change_additional_os_doors_po(request, order_id):
    """Replace an additional OS Doors PO with a different existing PO."""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Invalid request method'})

    order = get_object_or_404(Order, id=order_id)
    try:
        data = json.loads(request.body)
        old_workguru_id = data.get('old_workguru_id')
        new_display_number = data.get('new_display_number')
        if not old_workguru_id or not new_display_number:
            return JsonResponse({'success': False, 'error': 'old_workguru_id and new_display_number required'})

        old_po = PurchaseOrder.objects.get(workguru_id=old_workguru_id)
        new_po = PurchaseOrder.objects.filter(display_number=new_display_number).first() or \
                 PurchaseOrder.objects.get(number=new_display_number)
        order.additional_os_doors_pos.remove(old_po)
        order.additional_os_doors_pos.add(new_po)
        # Move OS Door rows from old PO to new PO so they remain visible
        OSDoor.objects.filter(customer=order, po_number=old_po.display_number).update(po_number=new_po.display_number)
        return JsonResponse({'success': True, 'po_number': new_po.display_number})
    except PurchaseOrder.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Purchase Order not found'})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@login_required
def remove_additional_os_doors_po(request, order_id):
    """Remove a PurchaseOrder from the order's additional_os_doors_pos M2M."""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Invalid request method'})

    order = get_object_or_404(Order, id=order_id)
    try:
        data = json.loads(request.body)
        workguru_id = data.get('workguru_id')
        if not workguru_id:
            return JsonResponse({'success': False, 'error': 'workguru_id required'})

        po = PurchaseOrder.objects.get(workguru_id=workguru_id)
        order.additional_os_doors_pos.remove(po)
        return JsonResponse({'success': True})
    except PurchaseOrder.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Purchase Order not found'})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@login_required
def create_raumplus_po(request):
    """Create a Purchase Order from the Raumplus shortage modal (JSON POST)."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    items = data.get('items', [])
    if not items:
        return JsonResponse({'error': 'No items provided'}, status=400)

    # Find the Raumplus supplier
    supplier = Supplier.objects.filter(name__icontains='Raumplus').first()

    # Generate a unique local workguru_id
    max_id = PurchaseOrder.objects.order_by('-workguru_id').values_list('workguru_id', flat=True).first() or 0
    manual_id = max(max_id + 1, 800000)

    # Generate sequential PO display number
    import re as _re
    last_num = 0
    for po_obj in PurchaseOrder.objects.filter(display_number__startswith='PO').order_by('-display_number'):
        m = _re.match(r'^PO(\d+)$', po_obj.display_number or '')
        if m:
            last_num = max(last_num, int(m.group(1)))
    display_number = f'PO{last_num + 1}'

    import datetime as _dt
    description = f'Raumplus stock order — generated {_dt.date.today().strftime("%d %B %Y")}'

    po = PurchaseOrder.objects.create(
        workguru_id=manual_id,
        number=display_number,
        display_number=display_number,
        description=description,
        supplier_id=supplier.workguru_id if supplier else None,
        supplier_name=supplier.name if supplier else 'Raumplus',
        delivery_address_1='61 Boucher Crescent, BT126HU, Belfast',
        po_type='supplier',
        status='Draft',
        currency=(supplier.currency.strip().upper() if supplier and supplier.currency else 'GBP'),
        creator_name=request.user.get_full_name() or request.user.username,
    )

    total = 0
    for sort_idx, item in enumerate(items, start=1):
        try:
            qty = float(item.get('order_qty', 0) or 0)
            unit_cost = float(item.get('cost', 0) or 0)
        except (ValueError, TypeError):
            qty, unit_cost = 0, 0

        line_total = round(qty * unit_cost, 2)
        total += line_total

        # Strip our internal prefix so the supplier code is just their number
        sku = item.get('sku', '')
        rau_idx = sku.upper().find('RAU_')
        supplier_code = sku[rau_idx + 4:] if rau_idx != -1 else sku

        # Link to the local StockItem if we can find it
        stock_item = StockItem.objects.filter(sku=sku).first()

        PurchaseOrderProduct.objects.create(
            purchase_order=po,
            sku=sku,
            supplier_code=supplier_code,
            name=item.get('name', ''),
            order_price=unit_cost,
            order_quantity=qty,
            line_total=line_total,
            minimum_order_quantity=item.get('min_order_qty', 1),
            sort_order=sort_idx,
            stock_item=stock_item,
        )

    po.total = round(total, 2)
    po.save(update_fields=['total'])

    log_activity(
        user=request.user,
        event_type='po_created',
        description=(
            f'{request.user.get_full_name() or request.user.username} created Raumplus purchase order '
            f'{display_number} with {len(items)} line item(s) totalling £{total:.2f}.'
        ),
    )

    return JsonResponse({
        'success': True,
        'display_number': display_number,
        'po_url': f'/purchase-order/{po.workguru_id}/',
    })


@login_required
def create_stock_shortage_po(request):
    """Create a draft Purchase Order from selected stock shortage items (JSON POST)."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    items = data.get('items', [])
    if not items:
        return JsonResponse({'error': 'No items provided'}, status=400)

    # Generate a unique local workguru_id
    max_id = PurchaseOrder.objects.order_by('-workguru_id').values_list('workguru_id', flat=True).first() or 0
    manual_id = max(max_id + 1, 800000)

    # Generate sequential PO display number
    import re as _re
    import datetime as _dt
    last_num = 0
    for po_obj in PurchaseOrder.objects.filter(display_number__startswith='PO').order_by('-display_number'):
        m = _re.match(r'^PO(\d+)$', po_obj.display_number or '')
        if m:
            last_num = max(last_num, int(m.group(1)))
    display_number = f'PO{last_num + 1}'

    description = f'Stock shortage order — generated {_dt.date.today().strftime("%d %B %Y")}'

    # Resolve supplier from the stock items (use the first one that has a supplier)
    supplier_id = None
    supplier_name = ''
    item_skus = [item.get('sku', '') for item in items if item.get('sku')]
    if item_skus:
        stock_item_with_supplier = StockItem.objects.filter(
            sku__in=item_skus, supplier__isnull=False
        ).select_related('supplier').first()
        if stock_item_with_supplier and stock_item_with_supplier.supplier:
            supplier_id = stock_item_with_supplier.supplier.workguru_id
            supplier_name = stock_item_with_supplier.supplier.name

    po = PurchaseOrder.objects.create(
        workguru_id=manual_id,
        number=display_number,
        display_number=display_number,
        description=description,
        supplier_id=supplier_id,
        supplier_name=supplier_name,
        delivery_address_1='61 Boucher Crescent, BT126HU, Belfast',
        po_type='supplier',
        status='Draft',
        currency=(
            stock_item_with_supplier.supplier.currency.strip().upper()
            if item_skus and stock_item_with_supplier and stock_item_with_supplier.supplier and stock_item_with_supplier.supplier.currency
            else 'GBP'
        ),
        creator_name=request.user.get_full_name() or request.user.username,
    )

    total = 0
    for sort_idx, item in enumerate(items, start=1):
        try:
            qty = float(item.get('order_qty', 0) or 0)
            unit_cost = float(item.get('cost', 0) or 0)
        except (ValueError, TypeError):
            qty, unit_cost = 0, 0

        line_total = round(qty * unit_cost, 2)
        total += line_total
        sku = item.get('sku', '')
        stock_item = StockItem.objects.filter(sku=sku).first()
        sup_code = (stock_item.supplier_code or sku) if stock_item else sku

        PurchaseOrderProduct.objects.create(
            purchase_order=po,
            sku=sku,
            supplier_code=sup_code,
            name=item.get('name', ''),
            order_price=unit_cost,
            order_quantity=qty,
            line_total=line_total,
            minimum_order_quantity=item.get('min_order_qty', 1),
            sort_order=sort_idx,
            stock_item=stock_item,
        )

    po.total = round(total, 2)
    po.save(update_fields=['total'])

    log_activity(
        user=request.user,
        event_type='po_created',
        description=(
            f'{request.user.get_full_name() or request.user.username} created stock shortage purchase order '
            f'{display_number} with {len(items)} line item(s).'
        ),
    )

    return JsonResponse({
        'success': True,
        'display_number': display_number,
        'po_url': f'/purchase-order/{po.workguru_id}/',
    })


@login_required
def raumplus_order_pdf(request):
    """Generate a Raumplus order PDF from the modal items (JSON POST)."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    items = data.get('items', [])
    if not items:
        return HttpResponse('No items provided', status=400)

    from .raumplus_pdf_generator import generate_raumplus_order_pdf
    import datetime as _dt
    buf = generate_raumplus_order_pdf(items, request.user)
    filename = f'Raumplus_Order_{_dt.date.today().strftime("%Y%m%d")}.pdf'
    response = HttpResponse(buf.read(), content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


@login_required
def save_raumplus_draft(request):
    """Create or update a Raumplus draft order (JSON POST)."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    items = data.get('items', [])
    name = (data.get('name') or 'Draft Order').strip()[:200]
    draft_id = data.get('draft_id')

    if draft_id:
        draft = get_object_or_404(RaumplusDraftOrder, id=draft_id, created_by=request.user)
        draft.name = name
        draft.items = items
        draft.save(update_fields=['name', 'items', 'updated_at'])
    else:
        draft = RaumplusDraftOrder.objects.create(
            name=name,
            items=items,
            created_by=request.user,
        )

    return JsonResponse({
        'success': True,
        'draft_id': draft.id,
        'name': draft.name,
        'updated_at': draft.updated_at.strftime('%d/%m/%Y %H:%M'),
    })


@login_required
def delete_raumplus_draft(request, draft_id):
    """Delete a saved Raumplus draft (DELETE or POST)."""
    if request.method not in ('DELETE', 'POST'):
        return JsonResponse({'error': 'DELETE or POST required'}, status=405)
    draft = get_object_or_404(RaumplusDraftOrder, id=draft_id, created_by=request.user)
    draft.delete()
    return JsonResponse({'success': True})


@login_required
def raumplus_copy_po_items(request, po_id):
    """Return line items from a Raumplus PO formatted for the order modal."""
    po = get_object_or_404(PurchaseOrder, workguru_id=po_id)
    products = po.products.all().order_by('sort_order', 'id')

    skus = [p.sku for p in products if p.sku]
    stock_map = {si.sku: si for si in StockItem.objects.filter(sku__in=skus)}

    items = []
    for p in products:
        if not p.sku:
            continue
        si = stock_map.get(p.sku)
        qty = int(float(p.order_quantity or p.quantity or 0))
        cost = float(si.cost) if si and si.cost else float(p.order_price or 0)
        min_oq = int(si.min_order_qty) if si and si.min_order_qty else 1
        items.append({
            'sku': p.sku,
            'name': p.name or (si.name if si else ''),
            'order_qty': qty,
            'suggested_qty': qty,
            'cost': cost,
            'min_order_qty': min_oq,
            'section': 'manual',
            'included': True,
        })

    return JsonResponse({
        'success': True,
        'items': items,
        'po_number': po.display_number or str(po.workguru_id),
    })


@login_required
def sync_os_doors_po(request, order_id):
    """Save current cost/qty from the frontend, then re-sync the linked PurchaseOrder's
    product lines.  Accepts optional ``door_values`` dict and optional
    ``po_display_number`` in the JSON body.  When ``po_display_number`` is supplied
    the named PO is synced and only doors whose ``po_number`` matches are included;
    otherwise the order's primary ``os_doors_po`` is synced with doors that belong
    to it (blank or matching po_number).
    Returns JSON.
    """
    import json
    from decimal import Decimal, InvalidOperation

    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required'}, status=405)

    order = get_object_or_404(Order, id=order_id)

    # Apply any unsaved cost/qty values sent from the frontend
    try:
        body = json.loads(request.body) if request.body else {}
    except (json.JSONDecodeError, ValueError):
        body = {}

    # Determine which PO we are syncing (primary or an additional one)
    po_workguru_id = body.get('po_workguru_id')
    if po_workguru_id:
        try:
            po_workguru_id = int(po_workguru_id)
        except (TypeError, ValueError):
            po_workguru_id = None

    if po_workguru_id:
        po = get_object_or_404(PurchaseOrder, workguru_id=po_workguru_id)
        if not po.display_number:
            return JsonResponse({'success': False, 'error': 'PO has no display number — cannot match door items.'})
        # Only doors explicitly assigned to this additional PO
        doors_qs = order.os_doors.filter(po_number=po.display_number)
    else:
        if not order.os_doors_po:
            return JsonResponse({'success': False, 'error': 'No PO linked to this order.'})
        po = PurchaseOrder.objects.filter(display_number=order.os_doors_po).first()
        if not po:
            return JsonResponse({'success': False, 'error': f'Purchase Order {order.os_doors_po} not found.'})
        # Doors belonging to the primary PO: blank po_number or explicitly set to the primary PO
        doors_qs = order.os_doors.filter(
            Q(po_number='') | Q(po_number__isnull=True) | Q(po_number=order.os_doors_po)
        )

    if not doors_qs.exists():
        return JsonResponse({'success': False, 'error': 'No OS door items for this PO.'})

    door_values = body.get('door_values', {})
    if door_values:
        for door_id_str, vals in door_values.items():
            try:
                door = order.os_doors.get(id=int(door_id_str))
                if 'cost_price' in vals:
                    try:
                        door.cost_price = Decimal(str(vals['cost_price']))
                    except (InvalidOperation, TypeError):
                        pass
                if 'quantity' in vals:
                    try:
                        door.quantity = int(vals['quantity'])
                    except (ValueError, TypeError):
                        pass
                door.save(update_fields=['cost_price', 'quantity'])
            except Exception:
                continue

    # Remove existing product lines and rebuild from the filtered door set
    po.products.all().delete()

    total = Decimal('0')
    for idx, door in enumerate(doors_qs.order_by('door_style', 'colour')):
        unit_price = door.cost_price if door.cost_price else Decimal('0')
        qty = Decimal(str(door.quantity))
        line_total = unit_price * qty
        total += line_total

        PurchaseOrderProduct.objects.create(
            purchase_order=po,
            sku=door.door_style,
            name=f'{door.door_style} - {door.style_colour} ({float(door.height):.0f}x{float(door.width):.0f}mm) {door.colour}',
            description=door.item_description or '',
            order_price=unit_price,
            order_quantity=qty,
            quantity=qty,
            line_total=line_total,
            sort_order=idx,
        )

    po.total = total
    po.save(update_fields=['total'])

    return JsonResponse({
        'success': True,
        'po_number': po.display_number,
        'line_count': doors_qs.count(),
        'total': str(total),
    })


@login_required
def product_add_allocation(request, po_id, product_id):
    """Add a customer/order allocation to a PO product line."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    po = get_object_or_404(PurchaseOrder, workguru_id=po_id)
    product = get_object_or_404(PurchaseOrderProduct, id=product_id, purchase_order=po)

    order_id = request.POST.get('order_id')
    quantity = request.POST.get('quantity', '1')
    notes = request.POST.get('notes', '').strip()

    if not order_id:
        return JsonResponse({'error': 'Order is required'}, status=400)

    order = get_object_or_404(Order, id=order_id)

    try:
        qty = float(quantity)
        if qty <= 0:
            return JsonResponse({'error': 'Quantity must be positive'}, status=400)
    except (ValueError, TypeError):
        return JsonResponse({'error': 'Invalid quantity'}, status=400)

    allocation = ProductCustomerAllocation.objects.create(
        product=product,
        order=order,
        quantity=qty,
        notes=notes,
    )

    customer_name = f'{order.first_name} {order.last_name}'.strip()
    if order.customer:
        customer_name = str(order.customer) if order.customer.name else f'{order.customer.first_name} {order.customer.last_name}'.strip()
    customer_name = customer_name or 'Unknown'

    return JsonResponse({
        'success': True,
        'allocation': {
            'id': allocation.id,
            'order_id': order.id,
            'sale_number': order.sale_number,
            'customer_name': customer_name,
            'quantity': float(allocation.quantity),
            'notes': allocation.notes,
        }
    })


@login_required
def product_delete_allocation(request, po_id, product_id, allocation_id):
    """Remove a customer allocation from a PO product line."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    try:
        po = get_object_or_404(PurchaseOrder, workguru_id=po_id)
        product = get_object_or_404(PurchaseOrderProduct, id=product_id, purchase_order=po)
        allocation = get_object_or_404(ProductCustomerAllocation, id=allocation_id, product=product)
        allocation.delete()
        return JsonResponse({'success': True})
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@login_required
def order_search(request):
    """Search orders by sale number or customer name. Returns JSON."""
    q = request.GET.get('q', '').strip()
    if len(q) < 2:
        return JsonResponse({'results': []})

    orders = Order.objects.filter(
        Q(sale_number__icontains=q) |
        Q(first_name__icontains=q) |
        Q(last_name__icontains=q) |
        Q(customer__name__icontains=q) |
        Q(customer__first_name__icontains=q) |
        Q(customer__last_name__icontains=q)
    ).select_related('customer').order_by('-order_date')[:20]

    results = []
    for o in orders:
        name = f'{o.first_name} {o.last_name}'.strip()
        if o.customer:
            name = str(o.customer) if o.customer.name else f'{o.customer.first_name} {o.customer.last_name}'.strip()
        results.append({
            'id': o.id,
            'sale_number': o.sale_number,
            'customer_name': name or 'Unknown',
        })

    return JsonResponse({'results': results})


@login_required
@login_required
def po_lines_api(request):
    """Return line items for a PO identified by workguru_id query param.

    For fitter POs, returns timesheet entries and expenses as line items.
    For supplier POs, returns product lines as before.
    """
    workguru_id = request.GET.get('workguru_id', '').strip()
    if not workguru_id:
        return JsonResponse({'error': 'workguru_id required'}, status=400)
    try:
        po = PurchaseOrder.objects.get(workguru_id=workguru_id)
    except (PurchaseOrder.DoesNotExist, ValueError, TypeError):
        return JsonResponse({'error': 'PO not found'}, status=404)

    if po.po_type == 'fitter':
        results = []

        # Timesheets — only direct timesheets (not PI-sourced, those belong to another invoice)
        timesheets = (
            po.timesheets
            .filter(purchase_invoice_line__isnull=True)
            .select_related('order', 'fitter')
            .order_by('order__sale_number', 'date')
        )
        for ts in timesheets:
            order_label = ''
            if ts.order:
                customer = ts.order.customer_name
                order_label = ts.order.sale_number
                if customer:
                    order_label += f' \u2014 {customer}'
            fitter_name = ts.fitter.name if ts.fitter else ''
            parts = []
            if fitter_name:
                parts.append(fitter_name)
            if order_label:
                parts.append(order_label)
            if ts.description:
                parts.append(ts.description)
            results.append({
                'id': None,
                'description': ' | '.join(parts) if parts else 'Timesheet',
                'quantity': float(ts.hours or 0),
                'order_price': float(ts.hourly_rate or 0),
            })

        # Expenses
        expenses = (
            po.expenses
            .select_related('order')
            .order_by('date')
        )
        for exp in expenses:
            order_label = ''
            if exp.order:
                customer = exp.order.customer_name
                order_label = exp.order.sale_number
                if customer:
                    order_label += f' \u2014 {customer}'
            exp_type = exp.get_expense_type_display()
            parts = [exp_type]
            if order_label:
                parts.append(order_label)
            if exp.description:
                parts.append(exp.description)
            results.append({
                'id': None,
                'description': ' | '.join(parts),
                'quantity': 1,
                'order_price': float(exp.amount or 0),
            })

        return JsonResponse({'results': results, 'freight_cost': 0.0})

    # Supplier PO: return product lines
    results = []
    for p in po.products.order_by('sort_order', 'id'):
        label = p.name or p.description or p.sku or ''
        if p.sku and p.name and p.sku != p.name:
            label = f'{p.sku} – {p.name}'
        invoiced_qty = p.invoice_lines.aggregate(t=Sum('quantity'))['t'] or 0
        results.append({
            'id': p.id,
            'sku': p.sku,
            'name': p.name,
            'description': label,
            'order_price': float(p.order_price),
            'quantity': float(p.order_quantity or p.quantity or 1),
            'invoiced_qty': float(invoiced_qty),
        })
    return JsonResponse({'results': results, 'freight_cost': float(po.freight_cost)})


def purchase_order_search(request):
    """Search purchase orders by display_number, supplier or project. Returns JSON.
    Used by the Boards PO combo selector in the order details page.
    """
    q = request.GET.get('q', '').strip()
    if len(q) < 2:
        return JsonResponse({'results': []})

    pos = PurchaseOrder.objects.filter(
        Q(display_number__icontains=q) |
        Q(supplier_name__icontains=q) |
        Q(project_number__icontains=q) |
        Q(project_name__icontains=q)
    )

    # Optionally restrict to a single supplier (e.g. when a purchase invoice
    # already has its supplier linked, only that supplier's POs are relevant).
    supplier = request.GET.get('supplier', '').strip()
    if supplier:
        pos = pos.filter(supplier_name__icontains=supplier)

    pos = pos.order_by('-display_number')[:20]
    results = []
    for po in pos:
        results.append({
            'id': po.workguru_id,
            'display_number': po.display_number or po.number or f'#{po.workguru_id}',
            'supplier_name': po.supplier_name or '',
            'project_number': po.project_number or '',
            'status': po.status or '',
            'po_type': po.po_type or 'supplier',
        })

    return JsonResponse({'results': results})


@login_required
def carnehill_summary(request):
    """Generate a PDF summary of all Carnehill POs that are Approved but not Received."""
    from decimal import Decimal
    from .po_pdf_generator import (
        _get_styles, _format_date, BRAND_DARK, BRAND_ACCENT, HEADER_BG,
        ROW_ALT, BORDER_COLOR, TEXT_PRIMARY, TEXT_SECONDARY
    )
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.units import mm
    from reportlab.lib import colors
    from reportlab.platypus import (
        SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image, KeepTogether
    )
    from reportlab.lib.styles import ParagraphStyle
    import io, os

    # Accept optional comma-separated PO IDs from query param
    selected_ids = request.GET.get('ids', '').strip()
    if selected_ids:
        id_list = [int(x) for x in selected_ids.split(',') if x.strip().isdigit()]
        pos = (
            PurchaseOrder.objects
            .filter(id__in=id_list, supplier_name__icontains='Carnehill', status='Approved')
            .order_by('-display_number')
        )
    else:
        pos = (
            PurchaseOrder.objects
            .filter(supplier_name__icontains='Carnehill', status='Approved')
            .order_by('-display_number')
        )

    buffer = io.BytesIO()
    styles = _get_styles()

    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        leftMargin=15 * mm,
        rightMargin=15 * mm,
        topMargin=15 * mm,
        bottomMargin=15 * mm,
        title='Carnehill Summary',
    )

    elements = []
    page_width = landscape(A4)[0] - 30 * mm

    # ─── LOGO ──────────────────────────────────────────────
    logo_path = os.path.join(settings.BASE_DIR, 'static', 'images', 'logo-full-light.png')
    if os.path.exists(logo_path):
        logo = Image(logo_path, width=50 * mm, height=14 * mm, kind='proportional')
        logo.hAlign = 'CENTER'
        elements.append(logo)
        elements.append(Spacer(1, 6 * mm))

    # ─── TITLE ─────────────────────────────────────────────
    from datetime import datetime as _dt
    title_style = ParagraphStyle('SummaryTitle', parent=styles['POTitle'], fontSize=14, leading=18)
    elements.append(Paragraph(
        '<b>Carnehill Summary — Approved POs</b>',
        title_style
    ))
    elements.append(Paragraph(
        f'Generated {_dt.now().strftime("%d/%m/%Y")}  ·  {pos.count()} purchase order{"s" if pos.count() != 1 else ""}',
        styles['AddressText']
    ))
    elements.append(Spacer(1, 8 * mm))

    grand_total = Decimal('0')

    for po in pos:
        po_elements = []

        # PO header row: number | description | issued | expected
        po_total = po.total or Decimal('0')
        grand_total += po_total

        issue = _format_date(po.issue_date) if po.issue_date else '—'
        expected = _format_date(po.expected_date) if po.expected_date else '—'

        header_data = [[
            Paragraph(f'<b>{po.display_number or po.number or "—"}</b>', styles['SupplierName']),
            Paragraph(po.description or '', styles['AddressText']),
            Paragraph(f'<font color="#6b7280">Issued:</font> {issue}', styles['CellTextRight']),
            Paragraph(f'<font color="#6b7280">Expected:</font> {expected}', styles['CellTextRight']),
        ]]
        header_tbl = Table(header_data, colWidths=[page_width * 0.15, page_width * 0.40, page_width * 0.22, page_width * 0.23])
        header_tbl.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('BACKGROUND', (0, 0), (-1, 0), HEADER_BG),
            ('LINEBELOW', (0, 0), (-1, 0), 0.6, BRAND_ACCENT),
            ('LEFTPADDING', (0, 0), (-1, -1), 4),
            ('RIGHTPADDING', (0, 0), (-1, -1), 4),
            ('TOPPADDING', (0, 0), (-1, -1), 3),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ]))
        po_elements.append(header_tbl)
        po_elements.append(Spacer(1, 1 * mm))

        # Products table
        products = list(po.products.all())
        is_board_po = False
        board_items = []
        if not products:
            # Fall back to PNX board items
            boards_po = BoardsPO.objects.filter(po_number=po.display_number).first()
            if boards_po:
                board_items = list(boards_po.pnx_items.all())
                is_board_po = bool(board_items)
            if not is_board_po:
                products = _get_board_product_rows_for_pdf(po)

        if is_board_po and board_items:
            # Board PO — show individual items with E1-E4 tick columns
            col_widths = [
                page_width * 0.30,   # Material
                page_width * 0.20,   # Dimensions
                page_width * 0.08,   # E1
                page_width * 0.08,   # E2
                page_width * 0.08,   # E3
                page_width * 0.08,   # E4
                page_width * 0.10,   # Qty
            ]
            table_data = [[
                Paragraph('<b>Material</b>', styles['CellText']),
                Paragraph('<b>Dimensions</b>', styles['CellText']),
                Paragraph('<b>E1</b>', styles['CellTextRight']),
                Paragraph('<b>E2</b>', styles['CellTextRight']),
                Paragraph('<b>E3</b>', styles['CellTextRight']),
                Paragraph('<b>E4</b>', styles['CellTextRight']),
                Paragraph('<b>Qty</b>', styles['CellTextRight']),
            ]]
            is_angled_po = boards_po.is_angled if boards_po else False
            if is_angled_po:
                sort_key = lambda x: (x.matname, float(x.left_height or 0), float(x.right_height or 0), float(x.cwidth))
            else:
                sort_key = lambda x: (x.matname, float(x.cleng), float(x.cwidth))
            for item in sorted(board_items, key=sort_key):
                tick = '\u2713'
                if is_angled_po and item.left_height is not None:
                    dims = f'L:{float(item.left_height):.0f} R:{float(item.right_height):.0f} W:{float(item.cwidth):.0f}'
                    if item.top_edge and float(item.top_edge) > 0:
                        dims += f' TE:{float(item.top_edge):.0f}'
                    dims += ' mm'
                else:
                    dims = f'{float(item.cleng):.0f} \u00d7 {float(item.cwidth):.0f} mm'
                table_data.append([
                    Paragraph(str(item.matname or ''), styles['CellText']),
                    Paragraph(dims, styles['CellText']),
                    Paragraph(tick if item.prfid1 else '', styles['CellTextRight']),
                    Paragraph(tick if item.prfid2 else '', styles['CellTextRight']),
                    Paragraph(tick if item.prfid3 else '', styles['CellTextRight']),
                    Paragraph(tick if item.prfid4 else '', styles['CellTextRight']),
                    Paragraph(f'{int(item.cnt)}', styles['CellTextRight']),
                ])
        elif products:
            col_widths = [
                page_width * 0.20,   # Code
                page_width * 0.65,   # Description
                page_width * 0.15,   # Qty
            ]
            table_data = [[
                Paragraph('<b>Code</b>', styles['CellText']),
                Paragraph('<b>Description</b>', styles['CellText']),
                Paragraph('<b>Qty</b>', styles['CellTextRight']),
            ]]
            for p in products:
                qty = p.order_quantity or getattr(p, 'quantity', 0) or Decimal('0')

                code = ''
                if hasattr(p, 'stock_item') and p.stock_item and getattr(p.stock_item, 'supplier_code', ''):
                    code = p.stock_item.supplier_code
                elif hasattr(p, 'supplier_code') and p.supplier_code:
                    code = p.supplier_code
                else:
                    code = p.sku or ''

                table_data.append([
                    Paragraph(str(code), styles['CellText']),
                    Paragraph(str(p.name or getattr(p, 'description', '') or ''), styles['CellText']),
                    Paragraph(f'{qty:,.0f}' if qty == int(qty) else f'{qty:,.2f}', styles['CellTextRight']),
                ])

        if (is_board_po and board_items) or products:
            last_col = len(col_widths) - 1
            prod_table = Table(table_data, colWidths=col_widths, repeatRows=1)
            prod_style = [
                ('LINEBELOW', (0, 0), (-1, 0), 0.6, BRAND_DARK),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, -1), 7.5),
                ('TOPPADDING', (0, 0), (-1, -1), 2),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
                ('LINEBELOW', (0, 1), (-1, -1), 0.2, BORDER_COLOR),
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                ('LEFTPADDING', (0, 0), (-1, -1), 3),
                ('RIGHTPADDING', (0, 0), (-1, -1), 3),
                ('ALIGN', (last_col, 0), (last_col, -1), 'RIGHT'),
            ]
            # Centre-align E1-E4 tick columns for board POs
            if is_board_po and board_items:
                for ec in range(2, 6):
                    prod_style.append(('ALIGN', (ec, 0), (ec, -1), 'CENTER'))
            # Alternate row shading
            for i in range(1, len(table_data)):
                if i % 2 == 0:
                    prod_style.append(('BACKGROUND', (0, i), (-1, i), ROW_ALT))

            prod_table.setStyle(TableStyle(prod_style))
            po_elements.append(prod_table)

        po_elements.append(Spacer(1, 4 * mm))
        elements.append(KeepTogether(po_elements))

    # ─── GRAND TOTAL ──────────────────────────────────────────
    elements.append(Spacer(1, 4 * mm))
    total_data = [[
        Paragraph('', styles['CellText']),
        Paragraph(f'<b>{pos.count()} approved PO{"s" if pos.count() != 1 else ""}</b>', styles['GrandTotalLabel']),
    ]]
    total_tbl = Table(total_data, colWidths=[page_width * 0.55, page_width * 0.45])
    total_tbl.setStyle(TableStyle([
        ('ALIGN', (1, 0), (-1, -1), 'RIGHT'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LINEABOVE', (1, 0), (-1, 0), 1, BRAND_DARK),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('RIGHTPADDING', (0, 0), (-1, -1), 6),
    ]))
    elements.append(total_tbl)

    # Build
    doc.build(elements)
    buffer.seek(0)

    response = HttpResponse(buffer, content_type='application/pdf')
    response['Content-Disposition'] = f'inline; filename="carnehill_summary_{_dt.now().strftime("%Y%m%d")}.pdf"'
    return response


@login_required
def approved_pos_report_pdf(request):
    """Generate a PDF report of all Approved POs with their value and expected date."""
    from decimal import Decimal
    from .po_pdf_generator import (
        _get_styles, _format_date, BRAND_DARK, BRAND_ACCENT, HEADER_BG,
        ROW_ALT, BORDER_COLOR
    )
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image, PageBreak
    )
    from reportlab.lib.styles import ParagraphStyle
    from datetime import datetime as _dt
    import io, os

    pos = (
        PurchaseOrder.objects
        .filter(status='Approved')
    )

    def _expected_sort_key(po):
        """POs with no parseable expected date sort first, then oldest first."""
        raw = (po.expected_date or '').strip()
        if not raw:
            return (0, datetime.min)
        for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%dT%H:%M:%S.%f'):
            try:
                return (1, datetime.strptime(raw[:19], fmt))
            except (ValueError, TypeError):
                continue
        return (0, datetime.min)

    pos = sorted(pos, key=_expected_sort_key)

    _CURRENCY_SYMBOLS = {'GBP': '£', 'EUR': '€', 'USD': '$'}

    def _value_cell(currency, amount, style):
        """Build a space-between value cell: symbol on the left, amount on the right."""
        symbol = _CURRENCY_SYMBOLS.get((currency or 'GBP').upper(), (currency or 'GBP'))
        inner = Table(
            [[Paragraph(symbol, style), Paragraph(f'{amount:,.2f}', styles['CellTextRight'])]],
            colWidths=[col_widths[3] * 0.30, col_widths[3] * 0.70],
        )
        inner.setStyle(TableStyle([
            ('LEFTPADDING', (0, 0), (-1, -1), 0),
            ('RIGHTPADDING', (0, 0), (-1, -1), 0),
            ('TOPPADDING', (0, 0), (-1, -1), 0),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('ALIGN', (0, 0), (0, 0), 'LEFT'),
            ('ALIGN', (1, 0), (1, 0), 'RIGHT'),
        ]))
        return inner

    buffer = io.BytesIO()
    styles = _get_styles()

    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=15 * mm,
        rightMargin=15 * mm,
        topMargin=15 * mm,
        bottomMargin=15 * mm,
        title='Approved Purchase Orders',
    )

    elements = []
    page_width = A4[0] - 30 * mm

    # ─── LOGO ──────────────────────────────────────────────
    logo_path = os.path.join(settings.BASE_DIR, 'static', 'images', 'logo-full-light.png')
    if os.path.exists(logo_path):
        logo = Image(logo_path, width=50 * mm, height=14 * mm, kind='proportional')
        logo.hAlign = 'CENTER'
        elements.append(logo)
        elements.append(Spacer(1, 6 * mm))

    # ─── TITLE ─────────────────────────────────────────────
    title_style = ParagraphStyle('ReportTitle', parent=styles['POTitle'], fontSize=14, leading=18)
    elements.append(Paragraph('<b>Approved Purchase Orders</b>', title_style))
    elements.append(Paragraph(
        f'Generated {_dt.now().strftime("%d/%m/%Y")}  ·  {len(pos)} purchase order{"s" if len(pos) != 1 else ""}',
        styles['AddressText']
    ))
    elements.append(Spacer(1, 8 * mm))

    # ─── TABLE ─────────────────────────────────────────────
    col_widths = [
        page_width * 0.16,   # PO
        page_width * 0.42,   # Supplier
        page_width * 0.22,   # Expected Date
        page_width * 0.20,   # Value
    ]
    table_data = [[
        Paragraph('<b>PO</b>', styles['CellText']),
        Paragraph('<b>Supplier</b>', styles['CellText']),
        Paragraph('<b>Expected Date</b>', styles['CellText']),
        Paragraph('<b>Value</b>', styles['CellTextRight']),
    ]]

    grand_total = Decimal('0')
    currency_totals = {}  # currency code -> Decimal
    for po in pos:
        po_total = po.total or Decimal('0')
        grand_total += po_total
        currency = po.currency or 'GBP'
        currency_totals[currency] = currency_totals.get(currency, Decimal('0')) + po_total
        expected = _format_date(po.expected_date) if po.expected_date else '—'
        table_data.append([
            Paragraph(str(po.display_number or po.number or '—'), styles['CellText']),
            Paragraph(str(po.supplier_name or '—'), styles['CellText']),
            Paragraph(expected, styles['CellText']),
            _value_cell(currency, po_total, styles['CellText']),
        ])

    last_col = len(col_widths) - 1
    table = Table(table_data, colWidths=col_widths, repeatRows=1)
    table_style = [
        ('LINEBELOW', (0, 0), (-1, 0), 0.6, BRAND_DARK),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 8),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ('LINEBELOW', (0, 1), (-1, -1), 0.2, BORDER_COLOR),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (-1, -1), 4),
        ('RIGHTPADDING', (0, 0), (-1, -1), 4),
        ('ALIGN', (last_col, 0), (last_col, -1), 'RIGHT'),
    ]
    for i in range(1, len(table_data)):
        if i % 2 == 0:
            table_style.append(('BACKGROUND', (0, i), (-1, i), ROW_ALT))
    table.setStyle(TableStyle(table_style))
    elements.append(table)

    # ─── GRAND TOTALS (per currency) ──────────────────────────
    elements.append(Spacer(1, 4 * mm))
    # Show one total row per currency (GBP first, then EUR, then any others)
    _currency_order = {'GBP': 0, 'EUR': 1, 'USD': 2}
    sorted_currencies = sorted(
        currency_totals.items(),
        key=lambda kv: (_currency_order.get(kv[0].upper(), 99), kv[0])
    )
    total_rows = []
    for code, amount in sorted_currencies:
        total_rows.append([
            Paragraph('<b>Total</b>', styles['GrandTotalLabel']),
            _value_cell(code, amount, styles['GrandTotalLabel']),
        ])
    total_tbl = Table(total_rows, colWidths=[page_width - col_widths[3], col_widths[3]])
    total_tbl.setStyle(TableStyle([
        ('ALIGN', (0, 0), (0, -1), 'RIGHT'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LINEABOVE', (0, 0), (-1, 0), 1, BRAND_DARK),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('LEFTPADDING', (1, 0), (1, -1), 4),
        ('RIGHTPADDING', (0, 0), (-1, -1), 4),
    ]))
    elements.append(total_tbl)

    # ─── PAGE 2: AGGREGATED ITEMS ─────────────────────────────
    # Sum the quantity of every line item across all approved POs.
    # Regular POs: lines sharing the same SKU are combined.
    # OS Doors POs: each unique (sku, name) combo is kept separate — doors
    #   differ by dimension/colour even when they share a style code.

    # Pre-compute which PO display_numbers belong to OS Doors orders.
    _os_doors_po_numbers = (
        set(Order.objects.exclude(os_doors_po='').values_list('os_doors_po', flat=True)) |
        set(OSDoor.objects.exclude(po_number='').exclude(po_number__isnull=True)
            .values_list('po_number', flat=True))
    )

    agg = {}  # key -> {'sku', 'name', 'qty'}
    for po in pos:
        is_osd_po = bool(po.display_number and po.display_number in _os_doors_po_numbers)
        if is_osd_po:
            continue  # OS Doors have their own dedicated section on page 3
        for line in po.products.all():
            sku = (line.sku or '').strip()
            name = (line.name or line.description or '').strip()
            key = sku.upper() if sku else f'NAME::{name.upper()}'
            qty = line.order_quantity or line.quantity or Decimal('0')
            if key not in agg:
                agg[key] = {'sku': sku, 'name': name, 'qty': Decimal('0')}
            agg[key]['qty'] += qty
            # Prefer a non-empty name if the first occurrence lacked one
            if not agg[key]['name'] and name:
                agg[key]['name'] = name

    agg_rows = sorted(agg.values(), key=lambda r: (r['sku'] or '', r['name'].upper()))

    if agg_rows:
        elements.append(PageBreak())

        if os.path.exists(logo_path):
            logo2 = Image(logo_path, width=50 * mm, height=14 * mm, kind='proportional')
            logo2.hAlign = 'CENTER'
            elements.append(logo2)
            elements.append(Spacer(1, 6 * mm))

        elements.append(Paragraph('<b>Combined Item Totals</b>', title_style))
        elements.append(Paragraph(
            f'Generated {_dt.now().strftime("%d/%m/%Y")}  ·  {len(agg_rows)} unique item{"s" if len(agg_rows) != 1 else ""}',
            styles['AddressText']
        ))
        elements.append(Spacer(1, 8 * mm))

        sku_style = ParagraphStyle(
            'ItemSku', parent=styles['CellText'], fontSize=7, leading=9, splitLongWords=False
        )
        item_col_widths = [
            page_width * 0.30,   # SKU
            page_width * 0.52,   # Description
            page_width * 0.18,   # Qty
        ]
        item_data = [[
            Paragraph('<b>SKU</b>', styles['CellText']),
            Paragraph('<b>Description</b>', styles['CellText']),
            Paragraph('<b>Total Qty</b>', styles['CellTextRight']),
        ]]
        for r in agg_rows:
            qty = r['qty']
            qty_str = f'{qty:,.0f}' if qty == int(qty) else f'{qty:,.2f}'
            item_data.append([
                Paragraph(str(r['sku'] or '—'), sku_style),
                Paragraph(str(r['name'] or '—'), styles['CellText']),
                Paragraph(qty_str, styles['CellTextRight']),
            ])

        item_table = Table(item_data, colWidths=item_col_widths, repeatRows=1)
        item_style = [
            ('LINEBELOW', (0, 0), (-1, 0), 0.6, BRAND_DARK),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 8),
            ('TOPPADDING', (0, 0), (-1, -1), 3),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
            ('LINEBELOW', (0, 1), (-1, -1), 0.2, BORDER_COLOR),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('LEFTPADDING', (0, 0), (-1, -1), 4),
            ('RIGHTPADDING', (0, 0), (-1, -1), 4),
            ('ALIGN', (2, 0), (2, -1), 'RIGHT'),
        ]
        for i in range(1, len(item_data)):
            if i % 2 == 0:
                item_style.append(('BACKGROUND', (0, i), (-1, i), ROW_ALT))
        item_table.setStyle(TableStyle(item_style))
        elements.append(item_table)

    # ─── PAGE 3: OS DOORS DUE IN ──────────────────────────────
    # A per-PO breakdown of every approved OS Doors purchase order,
    # with each door line (style, dimensions, colour, qty, price, total).
    os_doors_approved_pos = [
        po for po in pos
        if po.display_number and po.display_number in _os_doors_po_numbers
    ]

    # Shared styles for per-PO section headers (used in OS Doors + Carnehill pages)
    _sec_title_style = ParagraphStyle(
        'SecPoHeader', parent=styles['CellText'],
        fontSize=8.5, leading=11, fontName='Helvetica-Bold',
    )
    _meta_style = ParagraphStyle(
        'SecPoMeta', parent=styles['AddressText'], fontSize=7.5, leading=10,
    )

    if os_doors_approved_pos:
        elements.append(PageBreak())

        if os.path.exists(logo_path):
            logo3 = Image(logo_path, width=50 * mm, height=14 * mm, kind='proportional')
            logo3.hAlign = 'CENTER'
            elements.append(logo3)
            elements.append(Spacer(1, 6 * mm))

        total_door_lines = sum(po.products.count() for po in os_doors_approved_pos)
        elements.append(Paragraph('<b>OS Doors Due In</b>', title_style))
        elements.append(Paragraph(
            f'Generated {_dt.now().strftime("%d/%m/%Y")}  ·  '
            f'{len(os_doors_approved_pos)} purchase order{"s" if len(os_doors_approved_pos) != 1 else ""}  ·  '
            f'{total_door_lines} door line{"s" if total_door_lines != 1 else ""}',
            styles['AddressText']
        ))
        elements.append(Spacer(1, 8 * mm))

        osd_col_widths = [
            page_width * 0.54,  # Description
            page_width * 0.08,  # Qty
            page_width * 0.19,  # Unit Price
            page_width * 0.19,  # Total
        ]

        for po in os_doors_approved_pos:
            # Resolve the linked order
            linked_order = Order.objects.filter(os_doors_po=po.display_number).first()
            if not linked_order:
                first_door = OSDoor.objects.filter(
                    po_number=po.display_number
                ).select_related('customer').first()
                if first_door:
                    linked_order = first_door.customer

            sale_label = ''
            if linked_order:
                cname = linked_order.customer_name or ''
                sale_label = f'{linked_order.sale_number}  {cname}'.strip()

            expected = _format_date(po.expected_date) if po.expected_date else 'No date set'
            supplier = po.supplier_name or '—'

            header_text = (
                f'{po.display_number or po.number or "—"}  ·  '
                f'{supplier}'
                + (f'  ·  {sale_label}' if sale_label else '')
            )
            meta_text = f'Expected: {expected}'

            elements.append(Paragraph(header_text, _sec_title_style))
            elements.append(Paragraph(meta_text, _meta_style))
            elements.append(Spacer(1, 2 * mm))

            door_lines = list(po.products.all().order_by('sort_order', 'id'))
            if door_lines:
                currency = (po.currency or 'GBP').upper()
                symbol = _CURRENCY_SYMBOLS.get(currency, currency)

                osd_data = [[
                    Paragraph('<b>Description</b>', styles['CellText']),
                    Paragraph('<b>Qty</b>', styles['CellTextRight']),
                    Paragraph(f'<b>Unit ({symbol})</b>', styles['CellTextRight']),
                    Paragraph(f'<b>Total ({symbol})</b>', styles['CellTextRight']),
                ]]
                po_subtotal = Decimal('0')
                for line in door_lines:
                    qty = line.order_quantity or line.quantity or Decimal('0')
                    unit_price = line.order_price or Decimal('0')
                    line_total = line.line_total if line.line_total else (qty * unit_price)
                    po_subtotal += line_total
                    qty_str = f'{qty:,.0f}' if qty == int(qty) else f'{qty:,.2f}'
                    desc = (line.name or line.description or '—').strip()
                    osd_data.append([
                        Paragraph(desc, styles['CellText']),
                        Paragraph(qty_str, styles['CellTextRight']),
                        Paragraph(f'{unit_price:,.2f}', styles['CellTextRight']),
                        Paragraph(f'{line_total:,.2f}', styles['CellTextRight']),
                    ])
                # Subtotal row (spans first three cols)
                osd_data.append([
                    Paragraph('<b>Subtotal</b>', styles['CellTextRight']),
                    Paragraph('', styles['CellTextRight']),
                    Paragraph('', styles['CellTextRight']),
                    Paragraph(f'<b>{po_subtotal:,.2f}</b>', styles['CellTextRight']),
                ])

                osd_table = Table(osd_data, colWidths=osd_col_widths, repeatRows=1)
                osd_style_cmds = [
                    ('LINEBELOW', (0, 0), (-1, 0), 0.6, BRAND_DARK),
                    ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                    ('FONTSIZE', (0, 0), (-1, -1), 8),
                    ('TOPPADDING', (0, 0), (-1, -1), 3),
                    ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
                    ('LINEBELOW', (0, 1), (-1, -2), 0.2, BORDER_COLOR),
                    ('LINEABOVE', (0, -1), (-1, -1), 0.6, BRAND_DARK),
                    ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                    ('LEFTPADDING', (0, 0), (-1, -1), 4),
                    ('RIGHTPADDING', (0, 0), (-1, -1), 4),
                    ('ALIGN', (1, 0), (-1, -1), 'RIGHT'),
                    ('SPAN', (0, -1), (2, -1)),
                ]
                for i in range(1, len(osd_data) - 1):
                    if i % 2 == 0:
                        osd_style_cmds.append(('BACKGROUND', (0, i), (-1, i), ROW_ALT))
                osd_table.setStyle(TableStyle(osd_style_cmds))
                elements.append(osd_table)
            else:
                elements.append(Paragraph('No line items found for this PO.', styles['AddressText']))

            elements.append(Spacer(1, 6 * mm))

    # ─── PAGE 4: CARNEHILL DUE IN ─────────────────────────────
    carnehill_approved_pos = [
        po for po in pos
        if 'carnehill' in (po.supplier_name or '').lower()
        and po.display_number not in _os_doors_po_numbers
    ]

    if carnehill_approved_pos:
        elements.append(PageBreak())

        if os.path.exists(logo_path):
            logo4 = Image(logo_path, width=50 * mm, height=14 * mm, kind='proportional')
            logo4.hAlign = 'CENTER'
            elements.append(logo4)
            elements.append(Spacer(1, 6 * mm))

        def _ch_line_count(po):
            bpo = BoardsPO.objects.filter(po_number=po.display_number).first()
            return bpo.pnx_items.count() if bpo else po.products.count()

        total_carnehill_lines = sum(_ch_line_count(po) for po in carnehill_approved_pos)
        elements.append(Paragraph('<b>Carnehill Due In</b>', title_style))
        elements.append(Paragraph(
            f'Generated {_dt.now().strftime("%d/%m/%Y")}  ·  '
            f'{len(carnehill_approved_pos)} purchase order{"s" if len(carnehill_approved_pos) != 1 else ""}  ·  '
            f'{total_carnehill_lines} line{"s" if total_carnehill_lines != 1 else ""}',
            styles['AddressText']
        ))
        elements.append(Spacer(1, 8 * mm))

        ch_col_widths = [
            page_width * 0.54,  # Description
            page_width * 0.08,  # Qty
            page_width * 0.19,  # Unit Price
            page_width * 0.19,  # Total
        ]

        for po in carnehill_approved_pos:
            expected = _format_date(po.expected_date) if po.expected_date else 'No date set'

            # Resolve sale reference: try project fields on the PO first,
            # then fall back to the linked BoardsPO orders.
            sale_label = ''
            if po.project_number:
                sale_label = f'{po.project_number}  {po.project_name or ""}'.strip()
            if not sale_label:
                boards_po_obj = BoardsPO.objects.filter(po_number=po.display_number).first()
                if boards_po_obj:
                    linked_orders = list(boards_po_obj.orders.all()[:3])
                    if linked_orders:
                        sale_label = ', '.join(
                            f'{o.sale_number} {o.customer_name}'.strip()
                            for o in linked_orders
                        )

            header_text = (
                f'{po.display_number or po.number or "—"}  ·  '
                f'{po.supplier_name or "—"}'
                + (f'  ·  {sale_label}' if sale_label else '')
            )
            meta_text = f'Expected: {expected}'

            elements.append(Paragraph(header_text, _sec_title_style))
            elements.append(Paragraph(meta_text, _meta_style))
            elements.append(Spacer(1, 2 * mm))

            currency = (po.currency or 'GBP').upper()
            symbol = _CURRENCY_SYMBOLS.get(currency, currency)

            ch_data = [[
                Paragraph('<b>Description</b>', styles['CellText']),
                Paragraph('<b>Qty</b>', styles['CellTextRight']),
                Paragraph(f'<b>Unit ({symbol})</b>', styles['CellTextRight']),
                Paragraph(f'<b>Total ({symbol})</b>', styles['CellTextRight']),
            ]]
            po_subtotal = Decimal('0')

            # ── Boards PO: group PNX items by material + dimensions ──────────
            boards_po_obj = BoardsPO.objects.filter(po_number=po.display_number).first()
            if boards_po_obj:
                from collections import defaultdict
                pnx_items = list(boards_po_obj.pnx_items.all())
                for item in pnx_items:
                    item.calculated_cost = item.get_cost()

                is_angled = boards_po_obj.is_angled
                groups = defaultdict(lambda: {
                    'matname': '', 'cleng': 0, 'cwidth': 0,
                    'left_height': None, 'right_height': None, 'top_edge': 0,
                    'total_qty': Decimal('0'), 'total_cost': Decimal('0'),
                })
                for item in pnx_items:
                    if is_angled and item.left_height is not None:
                        key = (item.matname, float(item.left_height or 0),
                               float(item.right_height or 0), float(item.cwidth),
                               float(item.top_edge or 0))
                    else:
                        key = (item.matname, float(item.cleng), float(item.cwidth))
                    grp = groups[key]
                    grp['matname'] = item.matname
                    grp['cleng'] = float(item.cleng)
                    grp['cwidth'] = float(item.cwidth)
                    grp['left_height'] = float(item.left_height) if item.left_height is not None else None
                    grp['right_height'] = float(item.right_height) if item.right_height is not None else None
                    grp['top_edge'] = float(item.top_edge or 0)
                    grp['total_qty'] += item.cnt
                    grp['total_cost'] += item.calculated_cost

                for key in sorted(groups):
                    grp = groups[key]
                    qty = grp['total_qty']
                    cost = grp['total_cost']
                    unit_price = (cost / qty) if qty else Decimal('0')
                    if grp['left_height'] is not None:
                        dims = f"L:{grp['left_height']:.0f} R:{grp['right_height']:.0f} W:{grp['cwidth']:.0f}"
                        if grp['top_edge'] > 0:
                            dims += f" TE:{grp['top_edge']:.0f}"
                        desc = f"{grp['matname']} — {dims}mm"
                    else:
                        desc = f"{grp['matname']} — {grp['cleng']:.0f}×{grp['cwidth']:.0f}mm"
                    qty_str = f'{qty:,.0f}' if qty == int(qty) else f'{qty:,.2f}'
                    po_subtotal += cost
                    ch_data.append([
                        Paragraph(desc, styles['CellText']),
                        Paragraph(qty_str, styles['CellTextRight']),
                        Paragraph(f'{unit_price:,.2f}', styles['CellTextRight']),
                        Paragraph(f'{cost:,.2f}', styles['CellTextRight']),
                    ])
            else:
                # ── Non-boards Carnehill PO: use standard product lines ───────
                for line in po.products.all().order_by('sort_order', 'id'):
                    qty = line.order_quantity or line.quantity or Decimal('0')
                    unit_price = line.order_price or Decimal('0')
                    line_total = line.line_total if line.line_total else (qty * unit_price)
                    po_subtotal += line_total
                    qty_str = f'{qty:,.0f}' if qty == int(qty) else f'{qty:,.2f}'
                    desc = (line.name or line.description or '—').strip()
                    ch_data.append([
                        Paragraph(desc, styles['CellText']),
                        Paragraph(qty_str, styles['CellTextRight']),
                        Paragraph(f'{unit_price:,.2f}', styles['CellTextRight']),
                        Paragraph(f'{line_total:,.2f}', styles['CellTextRight']),
                    ])

            if len(ch_data) > 1:
                ch_data.append([
                    Paragraph('<b>Subtotal</b>', styles['CellTextRight']),
                    Paragraph('', styles['CellTextRight']),
                    Paragraph('', styles['CellTextRight']),
                    Paragraph(f'<b>{po_subtotal:,.2f}</b>', styles['CellTextRight']),
                ])
                ch_table = Table(ch_data, colWidths=ch_col_widths, repeatRows=1)
                ch_style_cmds = [
                    ('LINEBELOW', (0, 0), (-1, 0), 0.6, BRAND_DARK),
                    ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                    ('FONTSIZE', (0, 0), (-1, -1), 8),
                    ('TOPPADDING', (0, 0), (-1, -1), 3),
                    ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
                    ('LINEBELOW', (0, 1), (-1, -2), 0.2, BORDER_COLOR),
                    ('LINEABOVE', (0, -1), (-1, -1), 0.6, BRAND_DARK),
                    ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                    ('LEFTPADDING', (0, 0), (-1, -1), 4),
                    ('RIGHTPADDING', (0, 0), (-1, -1), 4),
                    ('ALIGN', (1, 0), (-1, -1), 'RIGHT'),
                    ('SPAN', (0, -1), (2, -1)),
                ]
                for i in range(1, len(ch_data) - 1):
                    if i % 2 == 0:
                        ch_style_cmds.append(('BACKGROUND', (0, i), (-1, i), ROW_ALT))
                ch_table.setStyle(TableStyle(ch_style_cmds))
                elements.append(ch_table)
            else:
                elements.append(Paragraph('No line items found for this PO.', styles['AddressText']))

            elements.append(Spacer(1, 6 * mm))

    # Build
    doc.build(elements)
    buffer.seek(0)

    response = HttpResponse(buffer, content_type='application/pdf')
    response['Content-Disposition'] = f'inline; filename="approved_pos_report_{_dt.now().strftime("%Y%m%d")}.pdf"'
    return response


@login_required
def purchase_order_push_to_xero(request, po_id):
    """
    Push a purchase order to Xero as an AUTHORISED purchase order.
    Manual action triggered from the PO detail page.
    """
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required'}, status=405)

    from stock_take.services import xero_api
    from django.utils import timezone
    import traceback

    try:
        po = get_object_or_404(PurchaseOrder, workguru_id=po_id)

        # Prevent duplicate pushes
        if po.xero_purchase_order_id:
            return JsonResponse({
                'success': False,
                'error': f'Already pushed to Xero (ID: {po.xero_purchase_order_id})'
            }, status=400)

        if not po.supplier_name:
            return JsonResponse({'success': False, 'error': 'Purchase order has no supplier name'}, status=400)

        # Look up the supplier to get their tax rate for Xero
        supplier_obj = Supplier.objects.filter(workguru_id=po.supplier_id).first() if po.supplier_id else None
        supplier_tax_type = supplier_obj.supplier_tax_rate if supplier_obj and supplier_obj.supplier_tax_rate else None

        # Build line items from PO products
        products = po.products.all().order_by('sort_order', 'id')
        if not products.exists():
            return JsonResponse({'success': False, 'error': 'Purchase order has no line items'}, status=400)

        line_items = []
        for product in products:
            line = {
                'description': product.name or product.description or product.sku or 'Item',
                'quantity': float(product.order_quantity or product.quantity or 1),
                'unit_amount': float(product.order_price or 0),
            }
            if product.account_code:
                line['account_code'] = product.account_code
            if supplier_tax_type:
                line['tax_type'] = supplier_tax_type
            if product.supplier_code:
                line['item_code'] = product.supplier_code
            line_items.append(line)

        # Format dates for Xero (YYYY-MM-DD)
        issue_date = None
        if po.issue_date:
            try:
                parsed = datetime.strptime(po.issue_date[:10], '%Y-%m-%d')
                issue_date = parsed.strftime('%Y-%m-%d')
            except (ValueError, TypeError):
                pass

        delivery_date = None
        if po.expected_date:
            try:
                parsed = datetime.strptime(po.expected_date[:10], '%Y-%m-%d')
                delivery_date = parsed.strftime('%Y-%m-%d')
            except (ValueError, TypeError):
                pass

        # Build delivery address as a plain string (Xero expects a string, not an object)
        delivery_address = None
        if any([po.delivery_address_1, po.delivery_address_2, po.suburb, po.state, po.postcode]):
            parts = [
                p for p in [
                    po.delivery_address_1,
                    po.delivery_address_2,
                    po.suburb,
                    po.state,
                    po.postcode,
                ] if p
            ]
            delivery_address = '\n'.join(parts)

        # Build reference from project info
        reference = po.description or ''

        result = xero_api.create_purchase_order(
            contact_name=po.supplier_name,
            po_number=po.display_number or f'PO-{po.workguru_id}',
            line_items=line_items,
            date=issue_date,
            delivery_date=delivery_date,
            reference=reference,
            currency=po.currency or 'GBP',
            status='SUBMITTED',
            delivery_address=delivery_address,
        )

        if result and 'PurchaseOrders' in result:
            xero_po = result['PurchaseOrders'][0]
            xero_po_id = xero_po.get('PurchaseOrderID', '')

            if xero_po_id:
                po.xero_purchase_order_id = xero_po_id
                po.xero_pushed_at = timezone.now()
                po.save(update_fields=['xero_purchase_order_id', 'xero_pushed_at'])

                log_activity(
                    user=request.user,
                    event_type='xero_push',
                    description=f'Pushed {po.display_number} to Xero (ID: {xero_po_id})',
                )

            return JsonResponse({
                'success': True,
                'xero_id': xero_po_id,
                'xero_po_number': xero_po.get('PurchaseOrderNumber', ''),
                'xero_status': xero_po.get('Status', ''),
            })
        else:
            error_detail = xero_api.get_last_api_error() or 'Unknown error'
            return JsonResponse({
                'success': False,
                'error': f'Failed to create purchase order in Xero: {error_detail}'
            }, status=500)

    except Exception as e:
        logger.error(f"Push to Xero failed: {traceback.format_exc()}")
        return JsonResponse({
            'success': False,
            'error': f'Server error: {str(e)}'
        }, status=500)


@login_required
def purchase_order_remove_xero_sync(request, po_id):
    """Remove the Xero sync flag from a purchase order (does not delete from Xero)."""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required'}, status=405)

    po = get_object_or_404(PurchaseOrder, workguru_id=po_id)

    if not po.xero_purchase_order_id:
        return JsonResponse({'success': False, 'error': 'Not synced to Xero'}, status=400)

    po.xero_purchase_order_id = None
    po.xero_pushed_at = None
    po.save(update_fields=['xero_purchase_order_id', 'xero_pushed_at'])

    log_activity(
        user=request.user,
        event_type='xero_unsync',
        description=f'Removed Xero sync from {po.display_number}',
    )

    return JsonResponse({'success': True})