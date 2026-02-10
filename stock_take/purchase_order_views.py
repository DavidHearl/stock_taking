from django.contrib.auth.decorators import login_required
from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse, StreamingHttpResponse
from django.db.models import Count, Sum, Q
from .services.workguru_api import WorkGuruAPI, WorkGuruAPIError
from .models import BoardsPO, Order, OSDoor, PurchaseOrder, PurchaseOrderProduct, StockItem, Supplier
import logging
import requests
import json
import time

logger = logging.getLogger(__name__)


def sync_purchase_orders_from_workguru():
    """Sync purchase orders from WorkGuru API to local database, including products and suppliers"""
    try:
        api = WorkGuruAPI.authenticate()
        
        # Step 1: Fetch all POs from list endpoint
        url = f"{api.base_url}/api/services/app/PurchaseOrder/GetPurchaseOrdersForMob"
        params = {
            'MaxResultCount': 1000,
            'SkipCount': 0,
            'IsActive': True
        }
        
        api.log_section("SYNCING PURCHASE ORDERS")
        
        response = requests.get(url, headers=api.headers, params=params, timeout=30)
        
        if response.status_code != 200:
            return False, f"Error fetching POs: {response.status_code}"
        
        data = response.json()
        po_list = data.get('result', {}).get('items', [])
        api.log(f"Fetched {len(po_list)} purchase orders from list\n")
        
        synced_count = 0
        products_synced = 0
        suppliers_synced = 0
        
        for po_data in po_list:
            wg_id = po_data.get('id')
            
            # Create/update PO from list data first
            po, created = PurchaseOrder.objects.update_or_create(
                workguru_id=wg_id,
                defaults={
                    'number': po_data.get('number'),
                    'display_number': po_data.get('displayNumber'),
                    'revision': po_data.get('revision', 0),
                    'description': po_data.get('description'),
                    'project_id': po_data.get('projectId'),
                    'project_number': po_data.get('projectNumber'),
                    'project_name': po_data.get('projectName'),
                    'supplier_id': po_data.get('supplierId'),
                    'supplier_name': po_data.get('supplierName'),
                    'supplier_invoice_number': po_data.get('supplierInvoiceNumber'),
                    'issue_date': po_data.get('issueDate'),
                    'expected_date': po_data.get('expectedDate'),
                    'received_date': po_data.get('receivedDate'),
                    'invoice_date': po_data.get('invoiceDate'),
                    'status': po_data.get('status', 'Draft'),
                    'total': po_data.get('total') or 0,
                    'forecast_total': po_data.get('forecastTotal') or 0,
                    'base_currency_total': po_data.get('baseCurrencyTotal') or 0,
                    'currency': po_data.get('currency', 'GBP'),
                    'exchange_rate': po_data.get('exchangeRate') or 1.0,
                    'warehouse_id': po_data.get('warehouseId'),
                    'delivery_address_1': po_data.get('deliveryAddress1'),
                    'delivery_address_2': po_data.get('deliveryAddress2'),
                    'delivery_instructions': po_data.get('deliveryInstructions'),
                    'sent_to_supplier': po_data.get('sentToSupplier'),
                    'sent_to_accounting': po_data.get('sentToAccounting'),
                    'billable': po_data.get('billable', False),
                    'is_advanced': po_data.get('isAdvancedPurchaseOrder', False),
                    'is_rfq': po_data.get('isRFQ', False),
                    'creator_name': po_data.get('creatorUserFullName'),
                    'received_by_name': po_data.get('receivedByUserFullName'),
                    'raw_data': po_data,
                }
            )
            synced_count += 1
            
            # Step 2: Fetch detail for products and supplier info
            try:
                detail_url = f"{api.base_url}/api/services/app/PurchaseOrder/GetPurchaseOrderByIdForMob"
                detail_resp = requests.get(detail_url, headers=api.headers, params={'id': wg_id}, timeout=15)
                
                if detail_resp.status_code == 200:
                    detail_data = detail_resp.json().get('result', {})
                    
                    # Update PO with richer detail data (totals etc)
                    detail_total = detail_data.get('total')
                    if detail_total is not None:
                        po.total = detail_total
                        po.forecast_total = detail_data.get('forecastTotal') or po.forecast_total
                        po.base_currency_total = detail_data.get('baseCurrencyTotal') or po.base_currency_total
                        po.raw_data = detail_data  # Store full detail as raw
                        po.save(update_fields=['total', 'forecast_total', 'base_currency_total', 'raw_data'])
                    
                    # Extract supplier data
                    supplier_data = detail_data.get('supplier')
                    if supplier_data and supplier_data.get('id'):
                        sup, sup_created = Supplier.objects.update_or_create(
                            workguru_id=supplier_data['id'],
                            defaults={
                                'name': supplier_data.get('name', ''),
                                'email': supplier_data.get('email'),
                                'phone': supplier_data.get('phone'),
                                'website': supplier_data.get('website'),
                                'address_1': supplier_data.get('address1'),
                                'address_2': supplier_data.get('address2'),
                                'city': supplier_data.get('city'),
                                'state': supplier_data.get('state'),
                                'postcode': supplier_data.get('postcode'),
                                'country': supplier_data.get('country'),
                                'currency': supplier_data.get('currency'),
                                'credit_limit': supplier_data.get('creditLimit') or 0,
                                'credit_days': supplier_data.get('creditDays'),
                                'is_active': supplier_data.get('isActive', True),
                                'raw_data': supplier_data,
                            }
                        )
                        if sup_created:
                            suppliers_synced += 1
                    
                    # Extract products (key is 'products', NOT 'purchaseOrderProducts')
                    po_products = detail_data.get('products', [])
                    if po_products:
                        po.products.all().delete()
                        
                        for prod in po_products:
                            sku = prod.get('sku', '')
                            stock_item = None
                            if sku:
                                stock_item = StockItem.objects.filter(sku=sku).first()
                            
                            PurchaseOrderProduct.objects.create(
                                purchase_order=po,
                                sku=sku,
                                supplier_code=prod.get('supplierCode', ''),
                                name=prod.get('name', ''),
                                description=prod.get('description', ''),
                                order_price=prod.get('buyPrice') or prod.get('orderPrice') or 0,
                                order_quantity=prod.get('orderQuantity') or 0,
                                received_quantity=prod.get('receivedQuantity') or 0,
                                invoice_price=prod.get('invoicePrice') or 0,
                                line_total=prod.get('lineTotal') or 0,
                                stock_item=stock_item,
                            )
                            products_synced += 1
                            
            except Exception as e:
                api.log(f"  Warning: Could not fetch detail for PO {wg_id}: {e}\n")
        
        msg = f"Synced {synced_count} POs, {products_synced} product lines, {suppliers_synced} new suppliers"
        api.log(f"{msg}\n\n")
        return True, msg
            
    except Exception as e:
        logger.error(f"Error syncing purchase orders: {e}")
        return False, str(e)


@login_required
def sync_purchase_orders_stream(request):
    """Stream sync progress to avoid broken pipe timeouts"""
    def event_stream():
        try:
            api = WorkGuruAPI.authenticate()
            
            url = f"{api.base_url}/api/services/app/PurchaseOrder/GetPurchaseOrdersForMob"
            params = {'MaxResultCount': 1000, 'SkipCount': 0, 'IsActive': True}
            
            response = requests.get(url, headers=api.headers, params=params, timeout=30)
            
            if response.status_code != 200:
                yield f"data: {json.dumps({'error': f'API error: {response.status_code}'})}\n\n"
                return
            
            data = response.json()
            po_list = data.get('result', {}).get('items', [])
            total = len(po_list)
            
            yield f"data: {json.dumps({'status': 'started', 'total': total})}\n\n"
            
            synced = 0
            products_total = 0
            suppliers_total = 0
            
            for i, po_data in enumerate(po_list):
                wg_id = po_data.get('id')
                
                # Save PO from list data
                po, _ = PurchaseOrder.objects.update_or_create(
                    workguru_id=wg_id,
                    defaults={
                        'number': po_data.get('number'),
                        'display_number': po_data.get('displayNumber'),
                        'revision': po_data.get('revision', 0),
                        'description': po_data.get('description'),
                        'project_id': po_data.get('projectId'),
                        'project_number': po_data.get('projectNumber'),
                        'project_name': po_data.get('projectName'),
                        'supplier_id': po_data.get('supplierId'),
                        'supplier_name': po_data.get('supplierName'),
                        'supplier_invoice_number': po_data.get('supplierInvoiceNumber'),
                        'issue_date': po_data.get('issueDate'),
                        'expected_date': po_data.get('expectedDate'),
                        'received_date': po_data.get('receivedDate'),
                        'invoice_date': po_data.get('invoiceDate'),
                        'status': po_data.get('status', 'Draft'),
                        'total': po_data.get('total') or 0,
                        'forecast_total': po_data.get('forecastTotal') or 0,
                        'base_currency_total': po_data.get('baseCurrencyTotal') or 0,
                        'currency': po_data.get('currency', 'GBP'),
                        'exchange_rate': po_data.get('exchangeRate') or 1.0,
                        'warehouse_id': po_data.get('warehouseId'),
                        'delivery_address_1': po_data.get('deliveryAddress1'),
                        'delivery_address_2': po_data.get('deliveryAddress2'),
                        'delivery_instructions': po_data.get('deliveryInstructions'),
                        'sent_to_supplier': po_data.get('sentToSupplier'),
                        'sent_to_accounting': po_data.get('sentToAccounting'),
                        'billable': po_data.get('billable', False),
                        'is_advanced': po_data.get('isAdvancedPurchaseOrder', False),
                        'is_rfq': po_data.get('isRFQ', False),
                        'creator_name': po_data.get('creatorUserFullName'),
                        'received_by_name': po_data.get('receivedByUserFullName'),
                        'raw_data': po_data,
                    }
                )
                
                # Fetch detail for products + supplier
                try:
                    detail_url = f"{api.base_url}/api/services/app/PurchaseOrder/GetPurchaseOrderByIdForMob"
                    detail_resp = requests.get(detail_url, headers=api.headers, params={'id': wg_id}, timeout=15)
                    
                    if detail_resp.status_code == 200:
                        detail_data = detail_resp.json().get('result', {})
                        
                        # Update totals
                        detail_total = detail_data.get('total')
                        if detail_total is not None:
                            po.total = detail_total
                            po.forecast_total = detail_data.get('forecastTotal') or po.forecast_total
                            po.base_currency_total = detail_data.get('baseCurrencyTotal') or po.base_currency_total
                            po.raw_data = detail_data
                            po.save(update_fields=['total', 'forecast_total', 'base_currency_total', 'raw_data'])
                        
                        # Supplier
                        supplier_data = detail_data.get('supplier')
                        if supplier_data and supplier_data.get('id'):
                            _, sup_created = Supplier.objects.update_or_create(
                                workguru_id=supplier_data['id'],
                                defaults={
                                    'name': supplier_data.get('name', ''),
                                    'email': supplier_data.get('email'),
                                    'phone': supplier_data.get('phone'),
                                    'website': supplier_data.get('website'),
                                    'address_1': supplier_data.get('address1'),
                                    'address_2': supplier_data.get('address2'),
                                    'city': supplier_data.get('city'),
                                    'state': supplier_data.get('state'),
                                    'postcode': supplier_data.get('postcode'),
                                    'country': supplier_data.get('country'),
                                    'currency': supplier_data.get('currency'),
                                    'credit_limit': supplier_data.get('creditLimit') or 0,
                                    'credit_days': supplier_data.get('creditDays'),
                                    'is_active': supplier_data.get('isActive', True),
                                    'raw_data': supplier_data,
                                }
                            )
                            if sup_created:
                                suppliers_total += 1
                        
                        # Products
                        po_products = detail_data.get('products', [])
                        if po_products:
                            po.products.all().delete()
                            for prod in po_products:
                                sku = prod.get('sku', '')
                                stock_item = StockItem.objects.filter(sku=sku).first() if sku else None
                                PurchaseOrderProduct.objects.create(
                                    purchase_order=po,
                                    sku=sku,
                                    supplier_code=prod.get('supplierCode', ''),
                                    name=prod.get('name', ''),
                                    description=prod.get('description', ''),
                                    order_price=prod.get('buyPrice') or prod.get('orderPrice') or 0,
                                    order_quantity=prod.get('orderQuantity') or 0,
                                    received_quantity=prod.get('receivedQuantity') or 0,
                                    invoice_price=prod.get('invoicePrice') or 0,
                                    line_total=prod.get('lineTotal') or 0,
                                    stock_item=stock_item,
                                )
                                products_total += 1
                except Exception:
                    pass
                
                synced += 1
                
                # Send progress every 10 POs
                if synced % 10 == 0 or synced == total:
                    yield f"data: {json.dumps({'status': 'progress', 'synced': synced, 'total': total, 'products': products_total, 'suppliers': suppliers_total})}\n\n"
            
            yield f"data: {json.dumps({'status': 'complete', 'synced': synced, 'total': total, 'products': products_total, 'suppliers': suppliers_total})}\n\n"
            
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
    
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
    
    context = {
        'purchase_orders': queryset,
        'total_count': total_count,
        'total_filtered': total_filtered,
        'filtered_count': queryset.count(),
        'status_filter': status_filter,
        'search_query': search_query,
        'excluded_suppliers': excluded_suppliers,
        'all_suppliers': all_suppliers,
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
        Supplier.objects.values_list('name', flat=True).order_by('name')
    )
    
    # Try to find a linked local Order via project_id -> Order.workguru_id
    linked_order = None
    if purchase_order.project_id:
        linked_order = Order.objects.filter(workguru_id=str(purchase_order.project_id)).first()
    
    # Find the BoardsPO that matches this PO's display_number (Carnehill board POs)
    boards_po = BoardsPO.objects.filter(po_number=purchase_order.display_number).first()
    pnx_items = []
    pnx_total_cost = 0
    if boards_po:
        pnx_items = list(boards_po.pnx_items.all())
        for item in pnx_items:
            item.calculated_cost = item.get_cost()
        pnx_total_cost = sum(item.calculated_cost for item in pnx_items)
        # Update the PO total to reflect the real board cost
        if pnx_total_cost and purchase_order.total != pnx_total_cost:
            purchase_order.total = pnx_total_cost
            purchase_order.save(update_fields=['total'])
    
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
    
    context = {
        'purchase_order': purchase_order,
        'products': products,
        'all_suppliers': all_suppliers,
        'linked_order': linked_order,
        'boards_po': boards_po,
        'pnx_items': pnx_items,
        'pnx_total_cost': pnx_total_cost,
        'related_pos': related_pos,
        'os_door_items': os_door_items,
    }
    
    return render(request, 'stock_take/purchase_order_detail.html', context)


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
    }
    
    for json_key, model_field in field_map.items():
        if json_key in data:
            val = data[json_key]
            if json_key == 'total':
                try:
                    val = float(val) if val else 0
                except (ValueError, TypeError):
                    val = 0
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
    }
    
    return render(request, 'stock_take/supplier_detail.html', context)
