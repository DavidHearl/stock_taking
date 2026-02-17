from django.contrib.auth.decorators import login_required
from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse, StreamingHttpResponse, HttpResponse
from django.db.models import Count, Sum, Q
from django.core.mail import EmailMessage
from django.conf import settings
from .services.workguru_api import WorkGuruAPI, WorkGuruAPIError
from .models import BoardsPO, Order, OSDoor, PNXItem, PurchaseOrder, PurchaseOrderAttachment, PurchaseOrderProduct, ProductCustomerAllocation, StockItem, Supplier
from .po_pdf_generator import generate_purchase_order_pdf
import logging
import requests
import json
import time
from datetime import datetime

logger = logging.getLogger(__name__)


def _format_date(date_str):
    """Convert ISO date string (e.g. 2026-02-11T00:00:00+00:00) to DD-MM-YYYY."""
    if not date_str:
        return date_str
    try:
        dt = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
        return dt.strftime('%d-%m-%Y')
    except (ValueError, AttributeError):
        return date_str


def _build_po_defaults_from_list(po_data):
    """Build the defaults dict for PurchaseOrder from list endpoint data.
    Captures ALL available fields from the GetPurchaseOrdersForMob response."""
    return {
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
        'issue_date': _format_date(po_data.get('issueDate')),
        'expected_date': _format_date(po_data.get('expectedDate')),
        'received_date': _format_date(po_data.get('receivedDate')),
        'invoice_date': _format_date(po_data.get('invoiceDate')),
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
        # Fields previously missing from list endpoint
        'accounting_system_number': po_data.get('accountingSystemNumber'),
        'client_id_wg': po_data.get('clientId'),
        'client_name': po_data.get('client'),
        'suburb': po_data.get('suburb'),
        'state': po_data.get('state'),
        'cis_deduction': po_data.get('cisDeduction') or 0,
        'raw_data': po_data,
    }


def _update_po_from_detail(po, detail_data):
    """Update a PurchaseOrder with the richer detail endpoint data.
    Captures ALL available fields from GetPurchaseOrderByIdForMob."""
    po.total = detail_data.get('total') if detail_data.get('total') is not None else po.total
    po.forecast_total = detail_data.get('forecastTotal') or po.forecast_total
    po.base_currency_total = detail_data.get('baseCurrencyTotal') or po.base_currency_total
    po.tax_total = detail_data.get('taxTotal') or 0
    po.base_currency_tax_total = detail_data.get('baseCurrencyTaxTotal') or 0
    po.invoiced_amount = detail_data.get('invoicedAmount') or 0
    po.amount_outstanding = detail_data.get('amountOutstanding') or 0
    po.volume = detail_data.get('volume') or 0
    po.weight = detail_data.get('weight') or 0
    po.cis_deduction = detail_data.get('cisDeduction') or 0
    po.is_landed_costs_po = detail_data.get('isLandedCostsPo', False)
    po.stock_used_on_projects = detail_data.get('stockUsedOnProjects', False)
    
    # Dates from detail (may be ISO format) - format to DD-MM-YYYY
    po.approved_date = _format_date(detail_data.get('approvedDate'))
    po.invoice_due_date = _format_date(detail_data.get('invoiceDueDate'))
    
    # Detail gives richer date info (ISO format) - update if present
    if detail_data.get('issueDate'):
        po.issue_date = _format_date(detail_data.get('issueDate'))
    if detail_data.get('expectedDate'):
        po.expected_date = _format_date(detail_data.get('expectedDate'))
    if detail_data.get('receivedDate'):
        po.received_date = _format_date(detail_data.get('receivedDate'))
    if detail_data.get('invoiceDate'):
        po.invoice_date = _format_date(detail_data.get('invoiceDate'))
    
    # Delivery detail
    po.suburb = detail_data.get('suburb') or po.suburb
    po.state = detail_data.get('state') or po.state
    po.postcode = detail_data.get('postcode') or po.postcode
    po.delivery_address_1 = detail_data.get('deliveryAddress1') or po.delivery_address_1
    po.delivery_address_2 = detail_data.get('deliveryAddress2') or po.delivery_address_2
    po.delivery_instructions = detail_data.get('deliveryInstructions') or po.delivery_instructions
    
    # Warehouse
    warehouse = detail_data.get('warehouse')
    if warehouse and isinstance(warehouse, dict):
        po.warehouse_name = warehouse.get('name')
    
    # Contact
    contact = detail_data.get('contact')
    if contact and isinstance(contact, dict):
        full_name = contact.get('fullName', '').strip()
        if full_name:
            po.contact_name = full_name
    
    # Client
    po.client_id_wg = detail_data.get('clientId') or po.client_id_wg
    client = detail_data.get('client')
    if client and isinstance(client, dict):
        po.client_name = client.get('name')
    
    # Approved/received by
    approved_by = detail_data.get('approvedByUser')
    if approved_by and isinstance(approved_by, dict):
        po.approved_by_name = approved_by.get('fullName') or approved_by.get('name')
    received_by = detail_data.get('receivedByUser')
    if received_by and isinstance(received_by, dict):
        name = received_by.get('fullName') or received_by.get('name')
        if name:
            po.received_by_name = name
    creator = detail_data.get('creatorUser')
    if creator and isinstance(creator, dict):
        name = creator.get('fullName') or creator.get('name')
        if name:
            po.creator_name = name
    
    # Accounting
    po.accounting_system_number = detail_data.get('accountingSystemNumber') or po.accounting_system_number
    po.supplier_invoice_number = detail_data.get('supplierInvoiceNumber') or po.supplier_invoice_number
    
    # WorkGuru timestamps
    po.creation_time_wg = detail_data.get('creationTime')
    po.last_modification_time_wg = detail_data.get('lastModificationTime')
    
    # Store full detail as raw
    po.raw_data = detail_data
    po.save()


def _sync_supplier_from_detail(supplier_data):
    """Sync supplier data from PO detail response. Returns (supplier, created)."""
    if not supplier_data or not supplier_data.get('id'):
        return None, False
    
    sup, sup_created = Supplier.objects.update_or_create(
        workguru_id=supplier_data['id'],
        defaults={
            'name': supplier_data.get('name', ''),
            'email': supplier_data.get('email'),
            'phone': supplier_data.get('phone'),
            'fax': supplier_data.get('fax'),
            'website': supplier_data.get('website'),
            'address_1': supplier_data.get('address1'),
            'address_2': supplier_data.get('address2'),
            'city': supplier_data.get('city'),
            'state': supplier_data.get('state'),
            'postcode': supplier_data.get('postcode'),
            'country': supplier_data.get('country'),
            'currency': supplier_data.get('currency'),
            'abn': supplier_data.get('abn'),
            'credit_limit': supplier_data.get('creditLimit') or 0,
            'credit_days': supplier_data.get('creditDays'),
            'number_of_credit_days': supplier_data.get('numberOfCreditDays'),
            'credit_terms_type': supplier_data.get('creditTermsType'),
            'price_tier': supplier_data.get('priceTier'),
            'supplier_tax_rate': supplier_data.get('supplierTaxRate'),
            'estimate_lead_time': supplier_data.get('estimateLeadTime'),
            'is_active': supplier_data.get('isActive', True),
            'raw_data': supplier_data,
        }
    )
    return sup, sup_created


def _sync_products_for_po(po, po_products):
    """Sync product line items for a purchase order. Returns count of products synced."""
    if not po_products:
        return 0
    
    # Build new products first, only delete old ones if build succeeds
    new_products = []
    for prod in po_products:
        if prod.get('isDeleted'):
            continue
        
        sku = prod.get('sku', '')
        stock_item = StockItem.objects.filter(sku=sku).first() if sku else None
        
        new_products.append(PurchaseOrderProduct(
            purchase_order=po,
            workguru_id=prod.get('id'),
            product_id=prod.get('productId'),
            sku=prod.get('sku') or '',
            supplier_code=prod.get('supplierCode') or '',
            name=prod.get('name') or '',
            description=prod.get('description') or '',
            notes=prod.get('notes') or '',
            order_price=prod.get('buyPrice') or prod.get('orderPrice') or 0,
            order_quantity=prod.get('orderQuantity') or 0,
            quantity=prod.get('quantity') or 0,
            received_quantity=prod.get('receivedQuantity') or 0,
            invoice_price=prod.get('invoicePrice') or 0,
            line_total=prod.get('lineTotal') or 0,
            unit_cost=prod.get('unitCost') or 0,
            minimum_order_quantity=prod.get('minimumOrderQuantity') or 0,
            tax_type=prod.get('taxType'),
            tax_name=prod.get('taxName'),
            tax_rate=prod.get('taxRate') or 0,
            tax_amount=prod.get('taxAmount') or 0,
            account_code=prod.get('accountCode'),
            expense_account_code=prod.get('expenseAccountCode'),
            sort_order=prod.get('sortOrder') or 0,
            weight=prod.get('weight') or 0,
            received_date=prod.get('receivedDate'),
            stock_item=stock_item,
        ))
    
    # Only delete and recreate if we successfully built products
    if new_products:
        po.products.all().delete()
        PurchaseOrderProduct.objects.bulk_create(new_products)
    
    return len(new_products)


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
        
        # Get all existing PO workguru_ids to skip them
        existing_ids = set(PurchaseOrder.objects.values_list('workguru_id', flat=True))
        new_pos = [po_data for po_data in po_list if po_data.get('id') not in existing_ids]
        api.log(f"Skipping {len(po_list) - len(new_pos)} existing POs, syncing {len(new_pos)} new POs\n")
        
        for po_data in new_pos:
            wg_id = po_data.get('id')
            
            # Create PO from list data (only new ones)
            po, created = PurchaseOrder.objects.update_or_create(
                workguru_id=wg_id,
                defaults=_build_po_defaults_from_list(po_data),
            )
            synced_count += 1
            
            # Step 2: Fetch detail for products and supplier info
            try:
                detail_url = f"{api.base_url}/api/services/app/PurchaseOrder/GetPurchaseOrderByIdForMob"
                detail_resp = requests.get(detail_url, headers=api.headers, params={'id': wg_id}, timeout=15)
                
                if detail_resp.status_code == 200:
                    detail_data = detail_resp.json().get('result', {})
                    
                    # Update PO with richer detail data
                    _update_po_from_detail(po, detail_data)
                    
                    # Sync supplier
                    supplier_data = detail_data.get('supplier')
                    _, sup_created = _sync_supplier_from_detail(supplier_data)
                    if sup_created:
                        suppliers_synced += 1
                    
                    # Sync products
                    po_products = detail_data.get('products', [])
                    products_synced += _sync_products_for_po(po, po_products)
                else:
                    api.log(f"  Warning: Detail fetch for PO {wg_id} returned {detail_resp.status_code}\n")
                            
            except Exception as e:
                api.log(f"  Warning: Could not fetch detail for PO {wg_id}: {e}\n")
                logger.warning(f"Could not fetch detail for PO {wg_id}: {e}")
        
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
            
            # Skip POs already in the system
            existing_ids = set(PurchaseOrder.objects.values_list('workguru_id', flat=True))
            new_pos = [po_data for po_data in po_list if po_data.get('id') not in existing_ids]
            skipped = total - len(new_pos)
            total = len(new_pos)
            
            yield f"data: {json.dumps({'status': 'progress', 'synced': 0, 'total': total, 'skipped': skipped, 'products': 0, 'suppliers': 0})}\n\n"
            
            synced = 0
            products_total = 0
            suppliers_total = 0
            
            for i, po_data in enumerate(new_pos):
                wg_id = po_data.get('id')
                
                # Create PO from list data (only new ones)
                po, _ = PurchaseOrder.objects.update_or_create(
                    workguru_id=wg_id,
                    defaults=_build_po_defaults_from_list(po_data),
                )
                
                # Fetch detail for products + supplier
                try:
                    detail_url = f"{api.base_url}/api/services/app/PurchaseOrder/GetPurchaseOrderByIdForMob"
                    detail_resp = requests.get(detail_url, headers=api.headers, params={'id': wg_id}, timeout=15)
                    
                    if detail_resp.status_code == 200:
                        detail_data = detail_resp.json().get('result', {})
                        
                        # Update PO with detail data
                        _update_po_from_detail(po, detail_data)
                        
                        # Supplier
                        supplier_data = detail_data.get('supplier')
                        _, sup_created = _sync_supplier_from_detail(supplier_data)
                        if sup_created:
                            suppliers_total += 1
                        
                        # Products
                        po_products = detail_data.get('products', [])
                        products_total += _sync_products_for_po(po, po_products)
                except Exception as e:
                    logger.warning(f"Could not fetch detail for PO {wg_id}: {e}")
                
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
    
    # Supplier objects for the create PO modal
    supplier_objects = Supplier.objects.filter(is_active=True).order_by('name')
    
    context = {
        'purchase_orders': queryset,
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
    if purchase_order.supplier_id:
        supplier_obj = Supplier.objects.filter(workguru_id=purchase_order.supplier_id).first()
        if supplier_obj and supplier_obj.email:
            supplier_email = supplier_obj.email

    context = {
        'purchase_order': purchase_order,
        'products': products,
        'all_suppliers': all_suppliers,
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
        'attachments': purchase_order.attachments.all(),
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
    products = po.products.all()

    pdf_buffer = generate_purchase_order_pdf(po, products)

    response = HttpResponse(pdf_buffer.read(), content_type='application/pdf')
    filename = f'Purchase_Order_{po.display_number}.pdf'
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


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

    if not body:
        body = (
            f'Dear {po.supplier_name or "Supplier"},\n\n'
            f'Please find attached Purchase Order {po.display_number}.\n\n'
            f'If you have any questions, please do not hesitate to contact us.\n\n'
            f'Kind regards,\n'
            f'Sliderobes'
        )

    # Generate the PDF
    products = po.products.all()
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

        # Auto-approve the PO when email is sent
        if po.status == 'Draft':
            po.status = 'Approved'
            po.approved_by_name = request.user.get_full_name() or request.user.username
            po.approved_date = datetime.now().strftime('%d-%m-%Y')
            po.save(update_fields=['status', 'approved_by_name', 'approved_date'])
        
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

    if not order.boards_po:
        messages.error(request, 'This order does not have a BoardsPO assigned.')
        return redirect('order_details', order_id=order_id)

    # Check if a PurchaseOrder already exists for this BoardsPO number
    existing = PurchaseOrder.objects.filter(display_number=order.boards_po.po_number).first()
    if existing:
        # Attach files if not already attached
        _attach_boards_files_to_po(existing, order.boards_po, request.user)
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
    po_number = order.boards_po.po_number

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
    _attach_boards_files_to_po(po, order.boards_po, request.user)

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