from django.contrib.auth.decorators import login_required
from django.shortcuts import render, get_object_or_404, redirect
from django.http import JsonResponse
from .models import StockItem, Category, StockTakeGroup, StockHistory, Accessory, PurchaseOrderProduct, Supplier, Substitution, PriceHistory, ProductLink, log_activity
import json
from decimal import Decimal
from datetime import datetime, timedelta
from django.utils import timezone
from django.db import models
from django.db.models import Sum


from django.views.decorators.http import require_POST


@login_required
def product_detail(request, item_id):
    """Display detailed product information"""
    product = StockItem.objects.filter(id=item_id).first()
    if not product:
        from django.contrib import messages
        messages.warning(request, 'That product no longer exists.')
        return redirect('stock_list')
    
    # Get categories and groups for the edit dropdowns
    categories = list(Category.objects.values('id', 'name').order_by('name'))
    stock_take_groups = list(StockTakeGroup.objects.values('id', 'name').order_by('name'))
    suppliers = list(Supplier.objects.values('id', 'name').order_by('name'))
    
    # Calculate stock trajectory based on order fit dates
    today = timezone.now().date()
    current_stock = product.quantity
    
    # Get all accessories linked to this stock item with fit dates
    # Exclude already-allocated items (stock already deducted for those)
    accessories = Accessory.objects.filter(
        stock_item=product,
        order__job_finished=False,
        is_allocated=False
    ).select_related('order').order_by('order__fit_date')
    
    # Build stock trajectory data
    stock_points = {}
    stock_points[today.isoformat()] = current_stock
    
    # ── Past adjustments from StockHistory (stock_take + adjustment types) ──
    # These are authoritative snapshots — include them as past data points
    past_adjustments = StockHistory.objects.filter(
        stock_item=product,
        change_type__in=['adjustment', 'stock_take'],
        created_at__date__lt=today,
    ).order_by('created_at')
    
    adjustment_dates = set()
    for adj in past_adjustments:
        adj_date = adj.created_at.date()
        date_key = adj_date.isoformat()
        
        # Add a point for the day BEFORE the adjustment showing pre-adjustment stock
        pre_adj_date = (adj_date - timedelta(days=1)).isoformat()
        pre_adj_qty = adj.quantity - adj.change_amount
        if pre_adj_date not in adjustment_dates:
            stock_points[pre_adj_date] = pre_adj_qty
        
        # The adjustment itself — authoritative value at that date
        stock_points[date_key] = adj.quantity
        adjustment_dates.add(date_key)
    
    # Calculate past stock (add back what was used) — only for dates without adjustment overrides
    past_accessories = accessories.filter(order__fit_date__lt=today)
    past_stock = current_stock
    for acc in past_accessories.order_by('-order__fit_date'):
        past_stock += int(acc.quantity)
        date_key = acc.order.fit_date.isoformat()
        if date_key not in adjustment_dates:
            stock_points[date_key] = past_stock
    
    # Collect all future events (outflows from accessories, inflows from POs)
    future_events = {}  # date_iso -> net change
    
    # Outflows from future accessories
    future_accessories = accessories.filter(order__fit_date__gt=today)
    for acc in future_accessories.order_by('order__fit_date'):
        date_key = acc.order.fit_date.isoformat()
        future_events[date_key] = future_events.get(date_key, 0) - int(acc.quantity)
    
    # Inflows from pending POs with expected dates in the future
    incoming_po_dates = set()  # Track which dates have PO arrivals for chart markers
    incoming_po_lines = PurchaseOrderProduct.objects.filter(
        stock_item=product,
        purchase_order__status__in=['Approved', 'Ordered', 'Sent', 'Draft'],
    ).exclude(
        purchase_order__expected_date__isnull=True
    ).exclude(
        purchase_order__expected_date=''
    ).select_related('purchase_order', 'stock_item')
    
    for po_line in incoming_po_lines:
        try:
            exp_str = po_line.purchase_order.expected_date.strip()
            # Parse various date formats (ISO with/without time, etc.)
            if 'T' in exp_str:
                expected = datetime.fromisoformat(exp_str.replace('Z', '+00:00')).date()
            else:
                expected = datetime.strptime(exp_str[:10], '%Y-%m-%d').date()
            
            if expected >= today:
                pack_size = int(getattr(po_line.stock_item, 'pack_size', 1) or 1)
                incoming_qty = int((po_line.order_quantity - po_line.received_quantity) * pack_size)
                if incoming_qty > 0:
                    date_key = expected.isoformat()
                    future_events[date_key] = future_events.get(date_key, 0) + incoming_qty
                    incoming_po_dates.add(date_key)
        except (ValueError, TypeError, AttributeError):
            pass
    
    # Build future trajectory from sorted events
    running_stock = current_stock
    for date_key in sorted(future_events.keys()):
        running_stock += future_events[date_key]
        stock_points[date_key] = running_stock
    
    # Sort by date and prepare for chart
    sorted_dates = sorted(stock_points.keys())
    
    # Build per-point metadata for the chart (is this a PO arrival date?)
    po_arrival_indices = [i for i, d in enumerate(sorted_dates) if d in incoming_po_dates]
    adjustment_indices = [i for i, d in enumerate(sorted_dates) if d in adjustment_dates]
    
    history_data = {
        'labels': [datetime.fromisoformat(d).strftime('%b %d') for d in sorted_dates],
        'quantities': [stock_points[d] for d in sorted_dates],
        'today_index': sorted_dates.index(today.isoformat()) if today.isoformat() in sorted_dates else 0,
        'po_arrival_indices': po_arrival_indices,
        'adjustment_indices': adjustment_indices,
    }
    
    # Calculate metrics
    # Count ALL unallocated accessories (both past-dated and future-dated orders).
    # Using only future_accessories here caused a bug: when overdue-but-unallocated
    # materials were finally allocated the stock dropped but allocated count didn't
    # (those items were never in future_accessories), making remaining go more negative.
    allocated = int(accessories.aggregate(total=Sum('quantity'))['total'] or 0)

    # Sum pending incoming PO quantities (positive entries in future_events are PO arrivals)
    total_incoming = sum(qty for qty in future_events.values() if qty > 0)

    # remaining = what we have + what is arriving - what is still needed
    remaining = current_stock + total_incoming - allocated
    
    # Purchase orders containing this product
    po_lines = PurchaseOrderProduct.objects.filter(
        stock_item=product
    ).select_related('purchase_order').order_by('-purchase_order__workguru_id')
    
    # Orders/projects using this product (via accessories)
    order_accessories = Accessory.objects.filter(
        stock_item=product
    ).select_related('order', 'order__customer').order_by('-order__fit_date', '-order__order_date')
    
    # Stock change history for the Last Modified tab
    stock_changes = StockHistory.objects.filter(
        stock_item=product
    ).select_related('created_by').order_by('-created_at')[:50]
    
    # Substitutions where this product's SKU is either the missing or replacement
    substitutions = Substitution.objects.filter(
        models.Q(missing_sku=product.sku) | models.Q(replacement_sku=product.sku)
    )
    
    # Price history for this product
    price_history = PriceHistory.objects.filter(
        stock_item=product
    ).select_related('created_by').order_by('-created_at')[:50]
    
    # Linked products
    linked_products = ProductLink.objects.filter(
        product=product
    ).select_related('linked_product', 'linked_product__supplier')
    
    return render(request, 'stock_take/product_detail.html', {
        'product': product,
        'categories': json.dumps(categories),
        'stock_take_groups': json.dumps(stock_take_groups),
        'suppliers': json.dumps(suppliers),
        'tracking_choices': json.dumps(list(StockItem.TRACKING_CHOICES)),
        'order_source_choices': json.dumps(list(StockItem.ORDER_SOURCE_CHOICES)),
        'stock_history': json.dumps(history_data),
        'allocated': allocated,
        'incoming': total_incoming,
        'remaining': remaining,
        'po_lines': po_lines,
        'order_accessories': order_accessories,
        'stock_changes': stock_changes,
        'substitutions': substitutions,
        'price_history': price_history,
        'linked_products': linked_products,
    })


@login_required
@require_POST
def product_add_substitution(request, item_id):
    """Add a substitution from the product detail page via AJAX."""
    product = get_object_or_404(StockItem, id=item_id)
    missing_sku = request.POST.get('missing_sku', '').strip()
    missing_name = request.POST.get('missing_name', '').strip()
    replacement_sku = request.POST.get('replacement_sku', '').strip()
    replacement_name = request.POST.get('replacement_name', '').strip()

    if not missing_sku or not replacement_sku:
        return JsonResponse({'success': False, 'error': 'Both missing and replacement SKU are required.'}, status=400)

    sub = Substitution.objects.create(
        missing_sku=missing_sku,
        missing_name=missing_name,
        replacement_sku=replacement_sku,
        replacement_name=replacement_name,
    )
    return JsonResponse({
        'success': True,
        'substitution': {
            'id': sub.id,
            'missing_sku': sub.missing_sku,
            'missing_name': sub.missing_name,
            'replacement_sku': sub.replacement_sku,
            'replacement_name': sub.replacement_name,
            'created_at': sub.created_at.strftime('%d/%m/%Y'),
            'direction': 'replaces' if sub.replacement_sku == product.sku else 'replaced_by',
        }
    })


@login_required
def add_product(request):
    """Create a new product - renders an editable form, saves on POST"""
    if request.method == 'POST':
        try:
            # Support both FormData (multipart) and JSON
            if request.content_type and 'multipart' in request.content_type:
                data = request.POST
                image_file = request.FILES.get('image')
            else:
                data = json.loads(request.body)
                image_file = None
            
            # Required fields
            sku = data.get('sku', '').strip()
            name = data.get('name', '').strip()
            
            if not sku:
                return JsonResponse({'success': False, 'error': 'SKU is required'})
            if not name:
                return JsonResponse({'success': False, 'error': 'Name is required'})
            
            # Check for duplicate SKU
            if StockItem.objects.filter(sku=sku).exists():
                return JsonResponse({'success': False, 'error': f'A product with SKU "{sku}" already exists'})
            
            # Build the product
            product = StockItem(
                sku=sku,
                name=name,
                description=data.get('description', ''),
                cost=Decimal(str(data.get('cost', '0') or '0')),
                pack_cost_price=Decimal(str(data.get('pack_cost_price'))) if data.get('pack_cost_price') else None,
                location=data.get('location', ''),
                quantity=int(data.get('quantity', 0) or 0),
                tracking_type=data.get('tracking_type', 'not-classified'),
                order_source=data.get('order_source', 'item'),
                min_order_qty=int(data['min_order_qty']) if data.get('min_order_qty') else None,
                par_level=int(data.get('par_level', 0) or 0),
                serial_or_batch=data.get('serial_or_batch', ''),
            )
            
            # Supplier fields
            supplier_sku = data.get('supplier_sku', '')
            product.supplier_sku = supplier_sku
            product.supplier_code = supplier_sku

            # Optional FK fields
            category_id = data.get('category_id')
            if category_id:
                product.category_id = int(category_id)
            
            group_id = data.get('stock_take_group_id')
            if group_id:
                product.stock_take_group_id = int(group_id)
            
            supplier_id = data.get('supplier_id')
            if supplier_id:
                product.supplier_id = int(supplier_id)
            
            # Product dimensions
            for field in ('length', 'width', 'height', 'weight'):
                val = data.get(field)
                if val:
                    setattr(product, field, Decimal(str(val)))
            
            # Box dimensions
            for field in ('box_length', 'box_width', 'box_height'):
                val = data.get(field)
                if val:
                    setattr(product, field, Decimal(str(val)))
            
            box_qty = data.get('box_quantity')
            if box_qty:
                product.box_quantity = int(box_qty)

            pack_sz = data.get('pack_size')
            if pack_sz:
                product.pack_size = int(pack_sz)
            
            # Image
            if image_file:
                product.image = image_file
            
            product.save()
            
            # Invalidate stock list cache so the new product appears immediately
            from django.core.cache import cache
            cache.clear()
            
            log_activity(
                request.user,
                'product_created',
                f'Product created: {product.sku} — {product.name} (qty: {product.quantity})',
            )

            # Create initial stock history entry
            if product.quantity != 0:
                StockHistory.objects.create(
                    stock_item=product,
                    quantity=product.quantity,
                    change_amount=product.quantity,
                    change_type='initial',
                    reference='Product created',
                    notes=f'Initial stock of {product.quantity} units',
                    created_by=request.user,
                )
            
            return JsonResponse({
                'success': True,
                'product_id': product.id,
            })
            
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)}, status=400)
    
    # GET - render the add product page
    categories = list(Category.objects.values('id', 'name').order_by('name'))
    stock_take_groups = list(StockTakeGroup.objects.values('id', 'name').order_by('name'))
    suppliers = list(Supplier.objects.values('id', 'name').order_by('name'))

    # Support copying from an existing product
    prefill = {}
    copy_from_id = request.GET.get('copy_from')
    if copy_from_id:
        try:
            source = StockItem.objects.get(id=copy_from_id)
            prefill = {
                'sku': source.sku or '',
                'name': f'Copy of {source.name}',
                'description': source.description or '',
                'cost': str(source.cost or '0'),
                'pack_cost_price': str(source.pack_cost_price) if source.pack_cost_price is not None else '',
                'pack_size': str(source.pack_size or 1),
                'order_source': source.order_source or 'item',
                'location': source.location or '',
                'tracking_type': source.tracking_type or '',
                'par_level': str(source.par_level or '0'),
                'min_order_qty': str(source.min_order_qty) if source.min_order_qty is not None else '',
                'serial_or_batch': source.serial_or_batch or '',
                'category_id': str(source.category_id) if source.category_id else '',
                'stock_take_group_id': str(source.stock_take_group_id) if source.stock_take_group_id else '',
                'supplier_id': str(source.supplier_id) if source.supplier_id else '',
                'length': str(source.length) if source.length else '',
                'width': str(source.width) if source.width else '',
                'height': str(source.height) if source.height else '',
                'weight': str(source.weight) if source.weight else '',
                'box_length': str(source.box_length) if source.box_length else '',
                'box_width': str(source.box_width) if source.box_width else '',
                'box_height': str(source.box_height) if source.box_height else '',
                'box_quantity': str(source.box_quantity) if source.box_quantity else '',
                'supplier_code': source.supplier_code or '',
                'supplier_sku': source.supplier_sku or '',
            }
        except StockItem.DoesNotExist:
            pass

    return render(request, 'stock_take/add_product.html', {
        'categories': json.dumps(categories),
        'stock_take_groups': json.dumps(stock_take_groups),
        'suppliers': json.dumps(suppliers),
        'tracking_choices': json.dumps(list(StockItem.TRACKING_CHOICES)),
        'order_source_choices': json.dumps(list(StockItem.ORDER_SOURCE_CHOICES)),
        'prefill': json.dumps(prefill),
    })


@login_required
def upload_product_image(request, item_id):
    """Upload or remove a product image"""
    from django.core.cache import cache

    product = get_object_or_404(StockItem, id=item_id)
    
    if request.method == 'POST':
        image_file = request.FILES.get('image')
        if not image_file:
            return JsonResponse({'success': False, 'error': 'No image file provided'})
        
        # Delete old image if exists
        if product.image:
            product.image.delete(save=False)
        
        product.image = image_file
        product.save()
        cache.clear()
        return JsonResponse({'success': True})
    
    elif request.method == 'DELETE':
        if product.image:
            product.image.delete(save=False)
            product.image = None
            product.save()
        cache.clear()
        return JsonResponse({'success': True})
    
    return JsonResponse({'success': False, 'error': 'Invalid method'}, status=405)


@login_required
def delete_product(request, item_id):
    """Delete a stock item / product"""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    product = get_object_or_404(StockItem, id=item_id)
    sku = product.sku
    name = product.name

    # Delete the image file if it exists
    if product.image:
        product.image.delete(save=False)

    product.delete()

    # Invalidate stock list cache
    from django.core.cache import cache
    cache.clear()

    log_activity(
        user=request.user,
        event_type='delete',
        description=f'{request.user.get_full_name() or request.user.username} deleted product {sku} ({name}).',
    )

    from django.contrib import messages
    messages.success(request, f'Product {sku} deleted.')
    return redirect('stock_list')


@login_required
@require_POST
def product_add_link(request, item_id):
    """Add a linked product via AJAX."""
    product = get_object_or_404(StockItem, id=item_id)
    linked_id = request.POST.get('linked_product_id')
    quantity_ratio = request.POST.get('quantity_ratio', '1')
    notes = request.POST.get('notes', '').strip()

    if not linked_id:
        return JsonResponse({'success': False, 'error': 'Linked product is required.'}, status=400)

    try:
        linked_id = int(linked_id)
    except (ValueError, TypeError):
        return JsonResponse({'success': False, 'error': 'Invalid product ID.'}, status=400)

    if linked_id == item_id:
        return JsonResponse({'success': False, 'error': 'Cannot link a product to itself.'}, status=400)

    linked_product = get_object_or_404(StockItem, id=linked_id)

    try:
        quantity_ratio = Decimal(quantity_ratio)
        if quantity_ratio <= 0:
            raise ValueError
    except (Exception,):
        return JsonResponse({'success': False, 'error': 'Quantity ratio must be a positive number.'}, status=400)

    link, created = ProductLink.objects.get_or_create(
        product=product,
        linked_product=linked_product,
        defaults={'quantity_ratio': quantity_ratio, 'notes': notes},
    )
    if not created:
        return JsonResponse({'success': False, 'error': 'This link already exists.'}, status=400)

    log_activity(
        user=request.user,
        event_type='update',
        description=f'{request.user.get_full_name() or request.user.username} linked {product.sku} → {linked_product.sku} (×{quantity_ratio}).',
    )

    return JsonResponse({
        'success': True,
        'link': {
            'id': link.id,
            'linked_sku': linked_product.sku,
            'linked_name': linked_product.name,
            'linked_id': linked_product.id,
            'quantity_ratio': str(link.quantity_ratio),
            'notes': link.notes,
        },
    })


@login_required
@require_POST
def product_delete_link(request, item_id, link_id):
    """Delete a product link via AJAX."""
    product = get_object_or_404(StockItem, id=item_id)
    link = get_object_or_404(ProductLink, id=link_id, product=product)
    linked_sku = link.linked_product.sku
    link.delete()

    log_activity(
        user=request.user,
        event_type='update',
        description=f'{request.user.get_full_name() or request.user.username} removed link {product.sku} → {linked_sku}.',
    )

    return JsonResponse({'success': True})
