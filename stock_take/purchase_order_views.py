from django.contrib.auth.decorators import login_required
from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse, StreamingHttpResponse, HttpResponse
from django.db.models import Count, Sum, Q
from django.core.mail import EmailMessage
from django.conf import settings
from .models import BoardsPO, Order, OSDoor, PNXItem, PurchaseOrder, PurchaseOrderAttachment, PurchaseOrderProduct, PurchaseOrderProject, ProductCustomerAllocation, StockItem, Supplier, SupplierContact
from .po_pdf_generator import generate_purchase_order_pdf
import logging
import requests
import json
import time
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


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

    groups = defaultdict(lambda: {
        'matname': '', 'cleng': 0, 'cwidth': 0,
        'total_qty': Decimal('0'), 'total_cost': Decimal('0'),
    })
    for item in pnx_items:
        key = (item.matname, float(item.cleng), float(item.cwidth))
        grp = groups[key]
        grp['matname'] = item.matname
        grp['cleng'] = float(item.cleng)
        grp['cwidth'] = float(item.cwidth)
        grp['total_qty'] += item.cnt
        grp['total_cost'] += item.get_cost()

    class _BoardRow:
        """Lightweight duck-type wrapper matching PurchaseOrderProduct interface."""
        def __init__(self, sku, name, qty, unit_price, line_total):
            self.sku = sku
            self.supplier_code = ''
            self.stock_item = None
            self.name = name
            self.description = name
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
        rows.append(_BoardRow(
            sku=sku,
            name=f"{grp['matname']} — {grp['cleng']:.0f}×{grp['cwidth']:.0f}mm",
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
    if status_filter != 'all':
        queryset = queryset.filter(status=status_filter.capitalize())
    
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

    # Strategy 3: product allocations
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

    # Annotate POs with linked order info (try each strategy in order)
    po_list = list(queryset)
    for po in po_list:
        info = wg_id_to_order.get(po.project_id)
        if not info and po.project_name:
            info = name_to_order.get(po.project_name)
        if not info:
            info = alloc_map.get(po.pk)
        po.linked_order_info = info

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
        'show_zero': show_zero,
        'zero_count': zero_count,
        'draft_count': status_counts.get('Draft', 0),
        'approved_count': status_counts.get('Approved', 0),
        'received_count': status_counts.get('Received', 0),
        'cancelled_count': status_counts.get('Cancelled', 0),
    }
    
    return render(request, 'stock_take/purchase_orders_list.html', context)


@login_required
def purchase_order_detail(request, po_id):
    """Display detailed view of a single purchase order from local database"""
    purchase_order = get_object_or_404(PurchaseOrder, workguru_id=po_id)
    products = purchase_order.products.all()
    
    # All suppliers for the dropdown
    all_suppliers = list(
        Supplier.objects.order_by('name')
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
        groups = defaultdict(lambda: {
            'matname': '', 'cleng': 0, 'cwidth': 0,
            'total_qty': Decimal('0'), 'received_qty': Decimal('0'),
            'total_cost': Decimal('0'), 'items': [],
        })
        for item in pnx_items:
            key = (item.matname, float(item.cleng), float(item.cwidth))
            grp = groups[key]
            grp['matname'] = item.matname
            grp['cleng'] = float(item.cleng)
            grp['cwidth'] = float(item.cwidth)
            grp['total_qty'] += item.cnt
            grp['received_qty'] += item.received_quantity
            grp['total_cost'] += item.calculated_cost
            grp['items'].append(item)

        for key, grp in sorted(groups.items(), key=lambda x: x[0]):
            sku = f"{grp['matname'].rstrip('_')}_{int(grp['cwidth'])}"
            qty = grp['total_qty']
            cost = grp['total_cost']
            unit_price = (cost / qty) if qty else Decimal('0')
            board_product_rows.append({
                'sku': sku,
                'name': f"{grp['matname']} — {grp['cleng']:.0f}×{grp['cwidth']:.0f}mm",
                'order_price': unit_price,
                'order_quantity': qty,
                'received_quantity': grp['received_qty'],
                'invoice_price': Decimal('0'),
                'line_total': cost,
                'is_board': True,
                'items': grp['items'],
                'item_ids': ','.join(str(i.id) for i in grp['items']),
            })
    
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
        'original_customer_name': original_customer_name,
        'original_customer_number': original_customer_number,
        'expected_delivery_date': expected_delivery_date,
        'po_projects': po_projects,
        'has_stock_project': has_stock_project,
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
                        product.received_quantity = float(prod_data['received_quantity']) if prod_data['received_quantity'] else 0
                    except (ValueError, TypeError):
                        pass
                if 'invoice_price' in prod_data:
                    try:
                        product.invoice_price = float(prod_data['invoice_price']) if prod_data['invoice_price'] else 0
                    except (ValueError, TypeError):
                        pass
                if 'line_total' in prod_data:
                    try:
                        product.line_total = float(prod_data['line_total']) if prod_data['line_total'] else 0
                    except (ValueError, TypeError):
                        pass
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
def purchase_order_receive(request, po_id):
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
def purchase_order_create(request):
    """Create a new purchase order manually"""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    from django.contrib import messages
    from django.shortcuts import redirect

    supplier_id = request.POST.get('supplier_id', '').strip()
    description = request.POST.get('description', '').strip()
    expected_date = request.POST.get('expected_date', '').strip()
    delivery_address = request.POST.get('delivery_address', '').strip()
    delivery_instructions = request.POST.get('delivery_instructions', '').strip()

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
        supplier_id=supplier.workguru_id,
        supplier_name=supplier.name,
        expected_date=expected_date or None,
        delivery_address_1=delivery_address or '61 Boucher Crescent, BT126HU, Belfast',
        delivery_instructions=delivery_instructions or None,
        status='Draft',
        currency='GBP',
        creator_name=request.user.get_full_name() or request.user.username,
    )

    messages.success(request, f'Purchase order {display_number} created.')
    return redirect('purchase_order_detail', po_id=po.workguru_id)


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
        'price_tier', 'supplier_tax_rate', 'estimate_lead_time',
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
            elif field == 'estimate_lead_time':
                try:
                    val = int(val) if val else None
                except (ValueError, TypeError):
                    val = None
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

    pdf_buffer = generate_purchase_order_pdf(po, products)

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

    pdf_buffer = generate_purchase_order_pdf(po, products)
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
    elif new_status == 'Draft':
        po.status = 'Draft'
        po.approved_by_name = None
        po.approved_date = None
        po.save(update_fields=['status', 'approved_by_name', 'approved_date'])
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

    messages.success(request, f'Purchase Order {display_number} deleted.')
    return redirect('purchase_orders_list')


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
        status='Draft',
        currency='GBP',
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


@login_required
def create_os_doors_purchase_order(request, order_id):
    """Create a local PurchaseOrder for OS Doors and add door items as products.
    Auto-generates the next PO number and links it to the order.
    """
    import re
    from django.contrib import messages
    from django.shortcuts import redirect
    from decimal import Decimal

    order = get_object_or_404(Order, id=order_id)

    if not order.os_doors.exists():
        messages.error(request, 'This order has no OS door items. Add doors first.')
        return redirect('order_details', order_id=order_id)

    # If a PO already exists for this order's os_doors_po, just redirect
    if order.os_doors_po:
        existing = PurchaseOrder.objects.filter(display_number=order.os_doors_po).first()
        if existing:
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
        status='Draft',
        currency='GBP',
        creator_name=request.user.get_full_name() or request.user.username,
    )

    # Add each OS door as a product line
    total = Decimal('0')
    for idx, door in enumerate(order.os_doors.all()):
        unit_price = door.cost_price or Decimal('0')
        qty = Decimal(str(door.quantity))
        line_total = unit_price * qty
        total += line_total

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
    order.save(update_fields=['os_doors_po'])
    order.os_doors.update(ordered=True, po_number=po_number)

    messages.success(request, f'OS Doors Purchase Order {po_number} created with {order.os_doors.count()} door item(s).')
    return redirect('order_details', order_id=order_id)


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

    po = get_object_or_404(PurchaseOrder, workguru_id=po_id)
    product = get_object_or_404(PurchaseOrderProduct, id=product_id, purchase_order=po)
    allocation = get_object_or_404(ProductCustomerAllocation, id=allocation_id, product=product)
    allocation.delete()

    return JsonResponse({'success': True})


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
    ).order_by('-display_number')[:20]

    results = []
    for po in pos:
        results.append({
            'id': po.workguru_id,
            'display_number': po.display_number or po.number or f'#{po.workguru_id}',
            'supplier_name': po.supplier_name or '',
            'project_number': po.project_number or '',
            'status': po.status or '',
        })

    return JsonResponse({'results': results})