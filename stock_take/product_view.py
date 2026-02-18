from django.contrib.auth.decorators import login_required
from django.shortcuts import render, get_object_or_404, redirect
from django.http import JsonResponse
from .models import StockItem, Category, StockTakeGroup, StockHistory, Accessory, PurchaseOrderProduct, Supplier
import json
from decimal import Decimal
from datetime import datetime, timedelta
from django.utils import timezone
from django.db.models import Sum


@login_required
def product_detail(request, item_id):
    """Display detailed product information"""
    product = get_object_or_404(StockItem, id=item_id)
    
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
    
    # Calculate past stock (add back what was used)
    past_accessories = accessories.filter(order__fit_date__lt=today)
    past_stock = current_stock
    for acc in past_accessories.order_by('-order__fit_date'):
        past_stock += int(acc.quantity)
        stock_points[acc.order.fit_date.isoformat()] = past_stock
    
    # Calculate future stock (subtract what will be used)
    future_accessories = accessories.filter(order__fit_date__gt=today)
    future_stock = current_stock
    for acc in future_accessories.order_by('order__fit_date'):
        future_stock -= int(acc.quantity)
        stock_points[acc.order.fit_date.isoformat()] = future_stock
    
    # Sort by date and prepare for chart
    sorted_dates = sorted(stock_points.keys())
    history_data = {
        'labels': [datetime.fromisoformat(d).strftime('%b %d') for d in sorted_dates],
        'quantities': [stock_points[d] for d in sorted_dates],
        'today_index': sorted_dates.index(today.isoformat()) if today.isoformat() in sorted_dates else 0
    }
    
    # Calculate metrics
    allocated = int(future_accessories.aggregate(total=Sum('quantity'))['total'] or 0)
    remaining = current_stock - allocated
    
    # Purchase orders containing this product
    po_lines = PurchaseOrderProduct.objects.filter(
        stock_item=product
    ).select_related('purchase_order').order_by('-purchase_order__workguru_id')
    
    # Orders/projects using this product (via accessories)
    order_accessories = Accessory.objects.filter(
        stock_item=product
    ).select_related('order', 'order__customer').order_by('-order__order_date')
    
    # Stock change history for the Last Modified tab
    stock_changes = StockHistory.objects.filter(
        stock_item=product
    ).select_related('created_by').order_by('-created_at')[:50]
    
    return render(request, 'stock_take/product_detail.html', {
        'product': product,
        'categories': json.dumps(categories),
        'stock_take_groups': json.dumps(stock_take_groups),
        'suppliers': json.dumps(suppliers),
        'tracking_choices': json.dumps(list(StockItem.TRACKING_CHOICES)),
        'stock_history': json.dumps(history_data),
        'allocated': allocated,
        'remaining': remaining,
        'po_lines': po_lines,
        'order_accessories': order_accessories,
        'stock_changes': stock_changes,
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
                location=data.get('location', ''),
                quantity=int(data.get('quantity', 0) or 0),
                tracking_type=data.get('tracking_type', 'not-classified'),
                min_order_qty=int(data['min_order_qty']) if data.get('min_order_qty') else None,
                par_level=int(data.get('par_level', 0) or 0),
                serial_or_batch=data.get('serial_or_batch', ''),
            )
            
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
            
            # Image
            if image_file:
                product.image = image_file
            
            product.save()
            
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
                'name': f'Copy of {source.name}',
                'description': source.description or '',
                'cost': str(source.cost or '0'),
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
            }
        except StockItem.DoesNotExist:
            pass

    return render(request, 'stock_take/add_product.html', {
        'categories': json.dumps(categories),
        'stock_take_groups': json.dumps(stock_take_groups),
        'suppliers': json.dumps(suppliers),
        'tracking_choices': json.dumps(list(StockItem.TRACKING_CHOICES)),
        'prefill': json.dumps(prefill),
    })


@login_required
def upload_product_image(request, item_id):
    """Upload or remove a product image"""
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
        return JsonResponse({'success': True})
    
    elif request.method == 'DELETE':
        if product.image:
            product.image.delete(save=False)
            product.image = None
            product.save()
        return JsonResponse({'success': True})
    
    return JsonResponse({'success': False, 'error': 'Invalid method'}, status=405)
