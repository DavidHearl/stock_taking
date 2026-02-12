from django.contrib.auth.decorators import login_required
from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse, StreamingHttpResponse, HttpResponse
from django.db.models import Count, Sum, Q
from django.core.mail import EmailMessage
from django.conf import settings
from .services.workguru_api import WorkGuruAPI, WorkGuruAPIError
from .models import BoardsPO, Order, OSDoor, PurchaseOrder, PurchaseOrderProduct, StockItem, Supplier
from .po_pdf_generator import generate_purchase_order_pdf
import logging
import requests
import json
import time

logger = logging.getLogger(__name__)


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
    
    # Dates from detail (may be ISO format)
    po.approved_date = detail_data.get('approvedDate')
    po.invoice_due_date = detail_data.get('invoiceDueDate')
    
    # Detail gives richer date info (ISO format) - update if present
    if detail_data.get('issueDate'):
        po.issue_date = detail_data.get('issueDate')
    if detail_data.get('expectedDate'):
        po.expected_date = detail_data.get('expectedDate')
    if detail_data.get('receivedDate'):
        po.received_date = detail_data.get('receivedDate')
    if detail_data.get('invoiceDate'):
        po.invoice_date = detail_data.get('invoiceDate')
    
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
        
        for po_data in po_list:
            wg_id = po_data.get('id')
            
            # Create/update PO from list data
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
            
            synced = 0
            products_total = 0
            suppliers_total = 0
            
            for i, po_data in enumerate(po_list):
                wg_id = po_data.get('id')
                
                # Save PO from list data using shared helper
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
        'related_pos': related_pos,
        'os_door_items': os_door_items,
        'supplier_email': supplier_email,
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
        delivery_address_1=delivery_address or None,
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
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[recipient],
            cc=cc_list if cc_list else None,
        )
        email.attach(pdf_filename, pdf_buffer.read(), 'application/pdf')
        email.send(fail_silently=False)

        logger.info(f'PO {po.display_number} emailed to {recipient} by {request.user}')
        return JsonResponse({
            'success': True,
            'message': f'Purchase order sent to {recipient}',
        })
    except Exception as e:
        logger.error(f'Failed to send PO {po.display_number} email: {e}')
        return JsonResponse({'error': f'Failed to send email: {str(e)}'}, status=500)
