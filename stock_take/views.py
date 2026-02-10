from .forms import OrderForm, BoardsPOForm, OSDoorForm, AccessoryCSVForm, Accessory, SubstitutionForm, CSVSkipItemForm
from .models import Order, BoardsPO, PNXItem, OSDoor, StockItem, Accessory, Remedial, RemedialAccessory, FitAppointment, Customer, Designer

import csv
import io
import os
import logging
from decimal import Decimal, InvalidOperation
from django.conf import settings
from django.shortcuts import render, redirect, get_object_or_404
from django.http import HttpResponse, JsonResponse
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.utils import timezone
from django.db.models import Sum, F, Count, Q, Prefetch
from django.db import models
from django.views.decorators.http import require_http_methods
from .models import StockItem, ImportHistory, Category, Schedule, StockTakeGroup, Substitution, CSVSkipItem
from django.template.loader import render_to_string
import datetime
from django.utils import timezone

# Initialize logger
logger = logging.getLogger(__name__)

@login_required
def ordering(request):
    # Handle price per square meter update
    if request.method == 'POST' and 'price_per_sqm' in request.POST:
        try:
            price_per_sqm = float(request.POST.get('price_per_sqm', 12))
            request.session['price_per_sqm'] = str(price_per_sqm)  # Store as string for Decimal conversion
            messages.success(request, f'Price per square meter updated to £{price_per_sqm:.2f}')
        except (ValueError, TypeError):
            messages.error(request, 'Invalid price per square meter value')
        return redirect('ordering')
    
    # Get price per square meter from session, default to 12
    price_per_sqm_str = request.session.get('price_per_sqm', '12')
    price_per_sqm = float(price_per_sqm_str)
    
    # Get tab filter - default to WIP
    tab = request.GET.get('tab', 'wip')  # 'all', 'wip', 'completed'
    
    # Handle sorting
    sort_by = request.GET.get('sort', 'order_date')
    sort_order = request.GET.get('order', 'desc')
    
    # Define valid sort fields
    valid_sort_fields = {
        'first_name': 'first_name',
        'last_name': 'last_name', 
        'sale_number': 'sale_number',
        'customer_number': 'customer_number',
        'order_date': 'order_date',
        'fit_date': 'fit_date',
        'boards_po': 'boards_po__po_number',
        'job_finished': 'job_finished'
    }
    
    if sort_by not in valid_sort_fields:
        sort_by = 'order_date'
    
    # Build ordering string
    order_field = valid_sort_fields[sort_by]
    
    # Default sorting: Always group by name (last_name, first_name) but use fit_date as primary
    # Furthest fit_date should be at top (descending by default)
    # Names stay together, ordered by their latest/furthest fit_date
    from django.db.models import Max
    
    if sort_by == 'fit_date':
        # Primary sort by fit_date, but keep same names together
        if sort_order == 'asc':
            ordering = ['job_finished', 'last_name', 'first_name', 'fit_date', models.F('boards_po__po_number').asc(nulls_last=True)]
        else:
            ordering = ['job_finished', 'last_name', 'first_name', '-fit_date', models.F('boards_po__po_number').asc(nulls_last=True)]
    elif sort_by == 'last_name':
        if sort_order == 'asc':
            ordering = ['job_finished', 'last_name', 'first_name', '-fit_date', models.F('boards_po__po_number').asc(nulls_last=True)]
        else:
            ordering = ['job_finished', '-last_name', '-first_name', '-fit_date', models.F('boards_po__po_number').asc(nulls_last=True)]
    elif sort_by == 'first_name':
        if sort_order == 'asc':
            ordering = ['job_finished', 'first_name', 'last_name', '-fit_date', models.F('boards_po__po_number').asc(nulls_last=True)]
        else:
            ordering = ['job_finished', '-first_name', '-last_name', '-fit_date', models.F('boards_po__po_number').asc(nulls_last=True)]
    else:
        # For other fields, still group by name then sort by the field
        if sort_order == 'asc':
            ordering = ['job_finished', order_field, 'last_name', 'first_name', models.F('boards_po__po_number').asc(nulls_last=True)]
        else:
            ordering = ['job_finished', f'-{order_field}', 'last_name', 'first_name', models.F('boards_po__po_number').asc(nulls_last=True)]
    
    # Sort by job_finished first (incomplete first), then by boards_po.po_number (nulls last), then by selected field
    # OPTIMIZATION: Don't load PNX items upfront - they'll be loaded via AJAX when row is clicked
    # OPTIMIZATION: Removed prefetch and annotations - will lazy load indicators via AJAX
    from django.db.models import Exists, OuterRef, Q
    
    # Get all orders for statistics
    all_orders = Order.objects.select_related('boards_po').all()
    total_orders = all_orders.count()
    completed_orders = all_orders.filter(job_finished=True).count()
    wip_orders = all_orders.filter(job_finished=False).count()
    
    # Filter orders based on tab
    if tab == 'completed':
        orders = all_orders.filter(job_finished=True).order_by(*ordering)
    elif tab == 'wip':
        orders = all_orders.filter(job_finished=False).order_by(*ordering)
    else:  # 'all'
        orders = all_orders.order_by(*ordering)
    
    # OPTIMIZATION: Don't load BoardsPO details upfront - only load basic list for the edit section
    # Get boards count for each PO using prefetch
    from django.db.models import Count, Sum
    boards_pos = BoardsPO.objects.prefetch_related('pnx_items').annotate(
        boards_count=Count('pnx_items')
    ).order_by('-po_number')
    
    # Get accessories CSVs (orders with original_csv uploaded)
    accessories_csvs = Order.objects.filter(
        original_csv__isnull=False
    ).exclude(
        original_csv=''
    ).order_by('-original_csv_uploaded_at')
    
    form = OrderForm(request.POST or None, initial={'order_type': 'sale'})
    po_form = BoardsPOForm()
    accessories_csv_form = AccessoryCSVForm()
    
    if request.method == 'POST' and form.is_valid():
        form.save()
        return redirect('ordering')
    
    return render(request, 'stock_take/ordering.html', {
        'orders': orders,
        'boards_pos': boards_pos,
        'accessories_csvs': accessories_csvs,
        'form': form,
        'po_form': po_form,
        'accessories_csv_form': accessories_csv_form,
        'price_per_sqm': price_per_sqm,
        'current_sort': sort_by,
        'current_order': sort_order,
        'current_tab': tab,
        'total_orders': total_orders,
        'completed_orders': completed_orders,
        'wip_orders': wip_orders,
    })

@login_required
def search_customers(request):
    """AJAX endpoint to search for existing customers"""
    query = request.GET.get('q', '').strip()
    
    if len(query) < 2:
        return JsonResponse({'customers': []})
    
    # Search in Customer table — include the name field from WorkGuru
    customers_from_table = Customer.objects.filter(
        Q(name__icontains=query) |
        Q(first_name__icontains=query) |
        Q(last_name__icontains=query) |
        Q(address__icontains=query) |
        Q(postcode__icontains=query) |
        Q(code__icontains=query) |
        Q(email__icontains=query) |
        Q(phone__icontains=query) |
        Q(city__icontains=query)
    )
    
    # Build results dictionary to deduplicate
    customer_dict = {}
    
    # Add customers from Customer table
    for c in customers_from_table:
        key = c.pk
        if key not in customer_dict:
            # Use name field if available, fall back to first/last
            display_name = c.name or f"{c.first_name} {c.last_name}".strip()
            customer_dict[key] = {
                'id': c.id,
                'customer_id': c.id,
                'first_name': c.first_name,
                'last_name': c.last_name,
                'name': display_name,
                'anthill_customer_id': c.anthill_customer_id,
                'address': c.address or c.address_1 or '',
                'postcode': c.postcode,
                'city': c.city or '',
                'phone': c.phone or '',
                'email': c.email or '',
            }
    
    # Convert to list and sort by name
    customer_list = sorted(customer_dict.values(), key=lambda x: x.get('name', ''))[:50]
    
    return JsonResponse({'customers': customer_list})

@login_required
def add_designer(request):
    """AJAX endpoint to add a new designer"""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Invalid request method'})
    
    try:
        import json
        data = json.loads(request.body)
        name = data.get('name', '').strip()
        
        if not name:
            return JsonResponse({'success': False, 'error': 'Designer name is required'})
        
        # Check if designer already exists
        existing_designer = Designer.objects.filter(name__iexact=name).first()
        if existing_designer:
            return JsonResponse({
                'success': True,
                'designer': {
                    'id': existing_designer.id,
                    'name': existing_designer.name
                },
                'message': 'Designer already exists'
            })
        
        # Create new designer
        designer = Designer.objects.create(name=name)
        
        return JsonResponse({
            'success': True,
            'designer': {
                'id': designer.id,
                'name': designer.name
            }
        })
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})

@login_required
def load_order_details_ajax(request, sale_number):
    """AJAX endpoint to load order details on demand"""
    from django.http import JsonResponse
    from django.template.loader import render_to_string
    
    # Get price per square meter from session
    price_per_sqm_str = request.session.get('price_per_sqm', '12')
    price_per_sqm = float(price_per_sqm_str)
    
    try:
        order = Order.objects.select_related('boards_po').prefetch_related(
            'accessories__stock_item',
            'boards_po__pnx_items',
            'os_doors'
        ).get(sale_number=sale_number)
        
        # Load PNX items if boards_po exists
        if order.boards_po:
            order.order_pnx_items = list(order.boards_po.pnx_items.filter(customer__icontains=order.sale_number))
            # Calculate cost for each item and total
            for item in order.order_pnx_items:
                item.calculated_cost = item.get_cost(price_per_sqm)
            order.pnx_total_cost = sum(item.calculated_cost for item in order.order_pnx_items)
        else:
            order.order_pnx_items = []
            order.pnx_total_cost = 0
        
        # Separate glass items from accessories
        all_accessories = order.accessories.all()
        glass_items = [acc for acc in all_accessories if acc.sku.upper().startswith('GLS')]
        non_glass_accessories = [acc for acc in all_accessories if not acc.sku.upper().startswith('GLS')]
        
        # Render the detail row HTML
        html = render_to_string('stock_take/partials/order_detail_row.html', {
            'order': order,
            'price_per_sqm': price_per_sqm,
            'glass_items': glass_items,
            'non_glass_accessories': non_glass_accessories,
        })
        
        return JsonResponse({'success': True, 'html': html})
    except Order.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Order not found'})

@login_required
def load_order_indicators_ajax(request):
    """AJAX endpoint to load order indicators (has_missing, has_accessories, has_remedials) in background"""
    from django.http import JsonResponse
    from django.db.models import Exists, OuterRef
    
    sale_numbers = request.GET.get('sale_numbers', '').split(',')
    if not sale_numbers or sale_numbers == ['']:
        return JsonResponse({'success': False, 'error': 'No sale numbers provided'})
    
    # Get indicators for all requested orders in one query
    orders = Order.objects.filter(sale_number__in=sale_numbers).annotate(
        has_missing=Exists(
            Accessory.objects.filter(order_id=OuterRef('pk'), missing=True)
        ),
        has_accessories=Exists(
            Accessory.objects.filter(order_id=OuterRef('pk'))
        ),
        has_remedials=Exists(
            Remedial.objects.filter(original_order_id=OuterRef('pk'))
        )
    ).values('sale_number', 'has_missing', 'has_accessories', 'has_remedials')
    
    indicators = {order['sale_number']: order for order in orders}
    
    return JsonResponse({'success': True, 'indicators': indicators})

@login_required
def search_orders(request):
    """Search for orders by name, sale number, or customer number"""
    query = request.GET.get('q', '').strip()
    orders = []
    
    if query:
        # Search by first name, last name, sale number, or customer number
        orders = Order.objects.filter(
            Q(first_name__icontains=query) |
            Q(last_name__icontains=query) |
            Q(sale_number__icontains=query) |
            Q(customer_number__icontains=query)
        ).order_by('-order_date')[:10]  # Limit to 10 results
    
    return render(request, 'stock_take/search_results.html', {
        'query': query,
        'orders': orders,
    })

@login_required
def material_report(request):
    """Display aggregated material usage report with filtering"""
    from datetime import datetime, timedelta
    from django.utils import timezone
    from collections import defaultdict
    
    # Get filter parameters
    filter_type = request.GET.get('filter', 'all')  # 'all', 'week', 'month'
    date_param = request.GET.get('date', '')
    include_completed = request.GET.get('include_completed', 'true') == 'true'
    
    # Set default date to today if not provided
    if not date_param:
        current_date = timezone.now().date()
    else:
        try:
            current_date = datetime.strptime(date_param, '%Y-%m-%d').date()
        except ValueError:
            current_date = timezone.now().date()
    
    # Calculate date range based on filter
    if filter_type == 'week':
        # Get the start of the week (Monday)
        start_of_week = current_date - timedelta(days=current_date.weekday())
        end_of_week = start_of_week + timedelta(days=6)
        date_range = (start_of_week, end_of_week)
        period_label = f"Week of {start_of_week.strftime('%B %d, %Y')} - {end_of_week.strftime('%B %d, %Y')}"
    elif filter_type == 'month':
        # Get the first and last day of the month
        start_of_month = current_date.replace(day=1)
        if current_date.month == 12:
            end_of_month = current_date.replace(year=current_date.year + 1, month=1, day=1) - timedelta(days=1)
        else:
            end_of_month = current_date.replace(month=current_date.month + 1, day=1) - timedelta(days=1)
        date_range = (start_of_month, end_of_month)
        period_label = f"{start_of_month.strftime('%B %Y')}"
    else:
        # All time - no date filter
        date_range = None
        period_label = "All Time"
    
    # Aggregate materials from different sources
    materials = defaultdict(lambda: {'quantity': 0, 'orders': set()})
    
    # 1. Aggregate from PNX Items (boards)
    # Get all orders within the date range, then get their PNX items
    if date_range:
        orders_in_range = Order.objects.filter(order_date__range=date_range)
        if not include_completed:
            orders_in_range = orders_in_range.filter(order_pnx_items__is_fully_received=False)
        boards_pos_in_range = BoardsPO.objects.filter(orders__in=orders_in_range).distinct()
        pnx_query = PNXItem.objects.filter(boards_po__in=boards_pos_in_range)
    else:
        orders_in_range = Order.objects.all()
        if not include_completed:
            orders_in_range = orders_in_range.filter(order_pnx_items__is_fully_received=False)
        pnx_query = PNXItem.objects.all()
    
    for item in pnx_query:
        key = f"PNX-{item.matname}"
        materials[key]['quantity'] += int(float(item.cnt))
        materials[key]['name'] = item.matname
        # Store all unique SKUs for this material
        if 'skus' not in materials[key]:
            materials[key]['skus'] = set()
        materials[key]['skus'].add(item.barcode)
        materials[key]['sku'] = item.barcode  # Keep for compatibility
        materials[key]['type'] = 'Board'
        materials[key]['source'] = 'PNX'
        # Add all orders that use this BoardsPO
        for order in item.boards_po.orders.all():
            if not date_range or (date_range[0] <= order.order_date <= date_range[1]):
                materials[key]['orders'].add((order.id, order.sale_number))
    
    # 2. Aggregate from Accessories
    accessory_query = Accessory.objects.select_related('order', 'stock_item')
    if date_range:
        accessory_query = accessory_query.filter(
            order__order_date__range=date_range
        )
    if not include_completed:
        accessory_query = accessory_query.filter(order__in=orders_in_range)
    
    for accessory in accessory_query:
        key = f"ACC-{accessory.sku}-{accessory.name}"
        materials[key]['quantity'] += int(float(accessory.quantity))
        materials[key]['name'] = accessory.name
        materials[key]['sku'] = accessory.sku
        materials[key]['type'] = 'Accessory'
        materials[key]['source'] = 'CSV'
        materials[key]['stock'] = accessory.available_quantity if accessory.stock_item else 0
        materials[key]['orders'].add((accessory.order.id, accessory.order.sale_number))
    
    # 3. Aggregate from OS Doors (if they have materials)
    # OS Doors don't seem to have specific materials, so we'll skip this for now
    
    # Convert to list and sort by quantity descending
    material_list = []
    boards_list = []
    rau_list = []
    accessories_list = []
    
    # Get all stock items for RAU lookup
    rau_stock_items = {item.sku: item.quantity for item in StockItem.objects.filter(sku__icontains='RAU')}
    
    for key, data in materials.items():
        # Sort orders by sale number
        sorted_orders = sorted(list(data['orders']), key=lambda x: x[1])
        
        # Handle SKU display differently for boards (grouped by material name)
        if data['type'] == 'Board':
            sku_display = ', '.join(sorted(data['skus']))
        else:
            sku_display = data.get('sku', '')
            
        material_data = {
            'sku': sku_display,
            'name': data['name'],
            'quantity': data['quantity'],
            'type': data['type'],
            'source': data['source'],
            'order_count': len(data['orders']),
            'orders': sorted_orders
        }
        
        # Add stock information
        if data['type'] == 'Accessory':
            material_data['stock'] = data.get('stock', 0)
        elif data.get('sku') and 'RAU' in data['sku'].upper():
            # For RAU materials, we need to check stock for each SKU in the group
            # For now, we'll use the first SKU found or 0 if none match
            rau_stock = 0
            for sku in data.get('skus', [data['sku']]):
                if sku in rau_stock_items:
                    rau_stock += rau_stock_items[sku]
            material_data['stock'] = rau_stock
        else:
            material_data['stock'] = None  # Not applicable for boards
        
        # Categorize materials
        if data.get('sku') and 'RAU' in data['sku'].upper():
            rau_list.append(material_data)
        elif data['type'] == 'Board':
            boards_list.append(material_data)
        else:
            accessories_list.append(material_data)
        
        material_list.append(material_data)
    
    # Sort each category by quantity descending
    boards_list.sort(key=lambda x: x['quantity'], reverse=True)
    rau_list.sort(key=lambda x: x['quantity'], reverse=True)
    accessories_list.sort(key=lambda x: x['quantity'], reverse=True)
    
    # Calculate materials with insufficient stock (RAU and Accessories only)
    materials_missing_stock = 0
    for material in rau_list + accessories_list:
        if material['stock'] is not None and material['stock'] < material['quantity']:
            materials_missing_stock += 1
    
    # Calculate board popularity for chart (top 10 by order count)
    board_popularity = sorted(boards_list, key=lambda x: x['order_count'], reverse=True)[:10]
    max_orders = max([b['order_count'] for b in board_popularity]) if board_popularity else 1
    
    # Add percentage for chart display
    for board in board_popularity:
        board['percentage'] = (board['order_count'] / max_orders) * 100 if max_orders > 0 else 0
    
    # Calculate top 10 styles by usage (extract from Material Name)
    import re
    styles_usage = {}
    for material in material_list:
        # Look for pattern like "S750 - Black Mat" in material name
        match = re.search(r'S\d{3} - .+', material['name'])
        if match:
            style_code = match.group(0).strip()
            if style_code not in styles_usage:
                styles_usage[style_code] = {
                    'style': style_code,
                    'quantity': 0,
                    'orders': set(),
                    'order_count': 0
                }
            styles_usage[style_code]['quantity'] += material['quantity']
            styles_usage[style_code]['orders'].update(material['orders'])
            styles_usage[style_code]['order_count'] = len(styles_usage[style_code]['orders'])
    
    # Convert to list and sort by quantity descending, take top 10
    top_styles = sorted(styles_usage.values(), key=lambda x: x['quantity'], reverse=True)[:10]
    max_style_quantity = max([s['quantity'] for s in top_styles]) if top_styles else 1
    
    # Add percentage for chart display
    for style in top_styles:
        style['percentage'] = (style['quantity'] / max_style_quantity) * 100 if max_style_quantity > 0 else 0
    
    # Calculate navigation dates
    if filter_type == 'week':
        prev_date = (current_date - timedelta(days=7)).strftime('%Y-%m-%d')
        next_date = (current_date + timedelta(days=7)).strftime('%Y-%m-%d')
    elif filter_type == 'month':
        if current_date.month == 1:
            prev_date = current_date.replace(year=current_date.year - 1, month=12).strftime('%Y-%m-%d')
        else:
            prev_date = current_date.replace(month=current_date.month - 1).strftime('%Y-%m-%d')
        
        if current_date.month == 12:
            next_date = current_date.replace(year=current_date.year + 1, month=1).strftime('%Y-%m-%d')
        else:
            next_date = current_date.replace(month=current_date.month + 1).strftime('%Y-%m-%d')
    else:
        prev_date = next_date = None
    
    return render(request, 'stock_take/material_report.html', {
        'boards': boards_list,
        'rau_materials': rau_list,
        'accessories': accessories_list,
        'filter_type': filter_type,
        'current_date': current_date.strftime('%Y-%m-%d') if filter_type != 'all' else None,
        'period_label': period_label,
        'prev_date': prev_date,
        'next_date': next_date,
        'total_materials': len(material_list),
        'total_quantity': sum(m['quantity'] for m in material_list),
        'board_popularity': board_popularity,
        'max_orders': max_orders,
        'materials_missing_stock': materials_missing_stock,
        'top_styles': top_styles,
        'max_style_quantity': max_style_quantity,
        'include_completed': include_completed,
    })

@login_required
def material_shortage(request):
    """Analyze material shortages based on upcoming orders and historical usage"""
    from datetime import timedelta
    from django.utils import timezone
    from collections import defaultdict
    
    today = timezone.now().date()
    
    # Handle search query
    search_query = request.GET.get('search', '').strip()
    search_result = None
    
    if search_query:
        # Get all active orders (not finished)
        active_orders = Order.objects.filter(job_finished=False).prefetch_related('accessories__stock_item')
        
        # Find accessories matching the search query (by SKU or name)
        matching_accessories = Accessory.objects.filter(
            order__job_finished=False,
            sku__icontains=search_query
        ).select_related('stock_item', 'order') | Accessory.objects.filter(
            order__job_finished=False,
            name__icontains=search_query
        ).select_related('stock_item', 'order')
        
        if matching_accessories.exists():
            # Group by SKU to aggregate
            search_data = defaultdict(lambda: {
                'sku': '',
                'name': '',
                'current_stock': 0,
                'required_qty': 0,
                'shortage': 0,
                'orders': []
            })
            
            for accessory in matching_accessories:
                key = accessory.sku
                if not search_data[key]['sku']:
                    search_data[key]['sku'] = accessory.sku
                    search_data[key]['name'] = accessory.name
                    if accessory.stock_item:
                        search_data[key]['current_stock'] = accessory.stock_item.quantity
                
                search_data[key]['required_qty'] += float(accessory.quantity)
                search_data[key]['orders'].append({
                    'sale_number': accessory.order.sale_number,
                    'first_name': accessory.order.first_name,
                    'last_name': accessory.order.last_name,
                    'fit_date': accessory.order.fit_date,
                    'quantity': float(accessory.quantity),
                    'order_id': accessory.order.id
                })
            
            # Calculate shortage for each item
            for key, data in search_data.items():
                data['shortage'] = max(0, data['required_qty'] - data['current_stock'])
            
            # Convert to list
            search_result = list(search_data.values())
    
    # Get upcoming orders with prefetched accessories and stock items (OPTIMIZATION)
    upcoming_orders = Order.objects.filter(
        job_finished=False,
        fit_date__gte=today
    ).prefetch_related(
        'accessories__stock_item'
    ).order_by('fit_date')
    
    # Calculate material requirements for upcoming orders
    upcoming_requirements = defaultdict(lambda: {
        'name': '',
        'sku': '',
        'required_qty': 0,
        'current_stock': 0,
        'shortage': 0,
        'orders': [],
        'is_stock': True  # Default to stock item
    })
    
    # Process accessories from upcoming orders (exclude RAU items)
    for order in upcoming_orders:
        for accessory in order.accessories.all():
            # Skip RAU items
            if 'RAU' in accessory.sku.upper():
                continue
                
            key = accessory.sku
            if not upcoming_requirements[key]['name']:
                upcoming_requirements[key]['name'] = accessory.name
                upcoming_requirements[key]['sku'] = accessory.sku
                if accessory.stock_item:
                    upcoming_requirements[key]['current_stock'] = accessory.stock_item.quantity
                    # Check if this is a non-stock item
                    upcoming_requirements[key]['is_stock'] = accessory.stock_item.tracking_type == 'stock'
            
            upcoming_requirements[key]['required_qty'] += float(accessory.quantity)
            upcoming_requirements[key]['orders'].append({
                'sale_number': order.sale_number,
                'fit_date': order.fit_date,
                'quantity': float(accessory.quantity)
            })
    
    # Calculate shortage for each material
    for key, data in upcoming_requirements.items():
        data['shortage'] = max(0, data['required_qty'] - data['current_stock'])
    
    # Historical usage analysis - Recent (last 3 months) - OPTIMIZED
    three_months_ago = today - timedelta(days=90)
    recent_orders = Order.objects.filter(
        job_finished=False,
        order_date__gte=three_months_ago,
        order_date__lte=today
    ).prefetch_related(
        'accessories__stock_item'
    )
    
    # Historical usage analysis - All time - OPTIMIZED
    all_time_orders = Order.objects.filter(
        job_finished=False
    ).prefetch_related(
        'accessories__stock_item'
    ).all()
    
    # Calculate all-time usage for baseline
    all_time_usage = defaultdict(lambda: {'total_used': 0, 'order_count': 0, 'first_order': None, 'last_order': None})
    
    for order in all_time_orders:
        for accessory in order.accessories.all():
            # Skip RAU items
            if 'RAU' in accessory.sku.upper():
                continue
                
            key = accessory.sku
            all_time_usage[key]['total_used'] += float(accessory.quantity)
            all_time_usage[key]['order_count'] += 1
            
            # Track date range
            if all_time_usage[key]['first_order'] is None or order.order_date < all_time_usage[key]['first_order']:
                all_time_usage[key]['first_order'] = order.order_date
            if all_time_usage[key]['last_order'] is None or order.order_date > all_time_usage[key]['last_order']:
                all_time_usage[key]['last_order'] = order.order_date
    
    # Calculate historical usage combining recent and all-time data
    historical_usage = defaultdict(lambda: {
        'name': '',
        'sku': '',
        'total_used': 0,
        'monthly_average': 0,
        'all_time_monthly_avg': 0,
        'weighted_monthly_avg': 0,
        'predicted_2month': 0,
        'current_stock': 0,
        'predicted_shortage': 0,
        'order_count': 0,
        'is_stock': True  # Default to stock item
    })
    
    for order in recent_orders:
        for accessory in order.accessories.all():
            # Skip RAU items
            if 'RAU' in accessory.sku.upper():
                continue
                
            key = accessory.sku
            if not historical_usage[key]['name']:
                historical_usage[key]['name'] = accessory.name
                historical_usage[key]['sku'] = accessory.sku
                if accessory.stock_item:
                    historical_usage[key]['current_stock'] = accessory.stock_item.quantity
                    # Check if this is a non-stock item
                    historical_usage[key]['is_stock'] = accessory.stock_item.tracking_type == 'stock'
            
            historical_usage[key]['total_used'] += float(accessory.quantity)
            historical_usage[key]['order_count'] += 1
    
    # Calculate predictions combining recent and all-time averages
    for key, data in historical_usage.items():
        # Recent average monthly usage (last 3 months)
        recent_monthly_avg = data['total_used'] / 3
        data['monthly_average'] = recent_monthly_avg
        
        # All-time average monthly usage
        if key in all_time_usage and all_time_usage[key]['first_order'] and all_time_usage[key]['last_order']:
            months_span = max(1, ((all_time_usage[key]['last_order'] - all_time_usage[key]['first_order']).days / 30.44))
            all_time_monthly_avg = all_time_usage[key]['total_used'] / months_span
            data['all_time_monthly_avg'] = all_time_monthly_avg
            
            # Weighted average: 70% recent trend, 30% historical baseline
            data['weighted_monthly_avg'] = (recent_monthly_avg * 0.7) + (all_time_monthly_avg * 0.3)
        else:
            data['all_time_monthly_avg'] = recent_monthly_avg
            data['weighted_monthly_avg'] = recent_monthly_avg
        
        # Predicted usage for next 2 months using weighted average
        data['predicted_2month'] = data['weighted_monthly_avg'] * 2
        # Predicted shortage
        data['predicted_shortage'] = max(0, data['predicted_2month'] - data['current_stock'])
    
    # Convert to lists and sort by shortage
    upcoming_list = [data for data in upcoming_requirements.values() if data['shortage'] > 0]
    upcoming_list.sort(key=lambda x: x['shortage'], reverse=True)
    
    # Filter predicted list to only include stock items with meaningful shortage (at least 1 unit)
    predicted_list = [data for data in historical_usage.values() 
                     if data['predicted_shortage'] >= 1 and data['is_stock']]
    predicted_list.sort(key=lambda x: x['predicted_shortage'], reverse=True)
    
    # Calculate totals
    total_upcoming_items = len(upcoming_list)
    total_predicted_items = len(predicted_list)
    total_upcoming_shortage = sum(item['shortage'] for item in upcoming_list)
    total_predicted_shortage = sum(item['predicted_shortage'] for item in predicted_list)
    
    # Get materials that appear in both lists (critical items)
    critical_items = []
    upcoming_skus = {item['sku'] for item in upcoming_list}
    for item in predicted_list:
        if item['sku'] in upcoming_skus:
            # Find the corresponding upcoming item
            upcoming_item = next((u for u in upcoming_list if u['sku'] == item['sku']), None)
            if upcoming_item:
                critical_items.append({
                    'sku': item['sku'],
                    'name': item['name'],
                    'current_stock': item['current_stock'],
                    'upcoming_shortage': upcoming_item['shortage'],
                    'predicted_shortage': item['predicted_shortage'],
                    'total_shortage': upcoming_item['shortage'] + item['predicted_shortage']
                })
    
    # Filter out any critical items where total_shortage is 0 or negative
    critical_items = [item for item in critical_items if item['total_shortage'] > 0]
    critical_items.sort(key=lambda x: x['total_shortage'], reverse=True)
    
    # Get set of critical SKUs for template filtering
    critical_skus = {item['sku'] for item in critical_items}
    
    return render(request, 'stock_take/material_shortage.html', {
        'upcoming_shortages': upcoming_list,
        'predicted_shortages': predicted_list,
        'critical_items': critical_items,
        'critical_skus': critical_skus,
        'total_upcoming_items': total_upcoming_items,
        'total_predicted_items': total_predicted_items,
        'total_upcoming_shortage': total_upcoming_shortage,
        'total_predicted_shortage': total_predicted_shortage,
        'upcoming_orders_count': upcoming_orders.count(),
        'historical_period_days': 90,
        'search_query': search_query,
        'search_result': search_result,
    })

def round_to_min_order_qty(quantity, min_order_qty):
    """Round up quantity to nearest multiple of minimum order quantity
    If min_order_qty is 1, round up to nearest 10 for cleaner ordering
    """
    import math
    if not min_order_qty or min_order_qty <= 0:
        return quantity
    
    # For items with min_order_qty of 1, round to nearest 10
    if min_order_qty == 1:
        return math.ceil(quantity / 10) * 10
    
    return math.ceil(quantity / min_order_qty) * min_order_qty

@login_required
def raumplus_storage(request):
    """Analyze RAU (Raumplus) material shortages based on upcoming orders and historical usage"""
    from datetime import timedelta
    from django.utils import timezone
    from collections import defaultdict
    from decimal import Decimal
    
    today = timezone.now().date()
    MIN_ORDER_VALUE = Decimal('5000.00')  # £5000 minimum order for free shipping (inc VAT)
    MIN_LINE_COST = Decimal('100.00')  # £100 minimum per line item
    VAT_RATE = Decimal('1.20')  # 20% VAT
    
    # Get upcoming orders with prefetched accessories and stock items (OPTIMIZATION)
    upcoming_orders = Order.objects.filter(
        job_finished=False,
        fit_date__gte=today
    ).prefetch_related(
        'accessories__stock_item'
    ).order_by('fit_date')
    
    # Calculate material requirements for upcoming orders (RAU items only)
    upcoming_requirements = defaultdict(lambda: {
        'name': '',
        'sku': '',
        'required_qty': 0,
        'current_stock': 0,
        'shortage': 0,
        'cost': Decimal('0.00'),
        'orders': []
    })
    
    # Process accessories from upcoming orders (RAU items only)
    for order in upcoming_orders:
        for accessory in order.accessories.all():
            # Only process RAU items
            if 'RAU' not in accessory.sku.upper():
                continue
                
            key = accessory.sku
            if not upcoming_requirements[key]['name']:
                upcoming_requirements[key]['name'] = accessory.name
                upcoming_requirements[key]['sku'] = accessory.sku
                if accessory.stock_item:
                    upcoming_requirements[key]['current_stock'] = accessory.stock_item.quantity
                    upcoming_requirements[key]['cost'] = accessory.stock_item.cost
            
            upcoming_requirements[key]['required_qty'] += float(accessory.quantity)
            upcoming_requirements[key]['orders'].append({
                'sale_number': order.sale_number,
                'fit_date': order.fit_date,
                'quantity': float(accessory.quantity)
            })
    
    # Calculate shortage for each material
    for key, data in upcoming_requirements.items():
        data['shortage'] = max(0, data['required_qty'] - data['current_stock'])
        data['line_cost'] = Decimal(str(data['shortage'])) * data['cost']
    
    # Historical usage analysis - Recent (last 3 months, RAU items only) - OPTIMIZED
    three_months_ago = today - timedelta(days=90)
    recent_orders = Order.objects.filter(
        order_date__gte=three_months_ago,
        order_date__lte=today
    ).prefetch_related(
        'accessories__stock_item'
    )
    
    # Historical usage analysis - All time (RAU items only) - OPTIMIZED
    all_time_orders = Order.objects.prefetch_related(
        'accessories__stock_item'
    ).all()
    
    # Calculate all-time usage for baseline
    all_time_usage = defaultdict(lambda: {'total_used': 0, 'order_count': 0, 'first_order': None, 'last_order': None})
    
    for order in all_time_orders:
        for accessory in order.accessories.all():
            # Only process RAU items
            if 'RAU' not in accessory.sku.upper():
                continue
                
            key = accessory.sku
            all_time_usage[key]['total_used'] += float(accessory.quantity)
            all_time_usage[key]['order_count'] += 1
            
            # Track date range
            if all_time_usage[key]['first_order'] is None or order.order_date < all_time_usage[key]['first_order']:
                all_time_usage[key]['first_order'] = order.order_date
            if all_time_usage[key]['last_order'] is None or order.order_date > all_time_usage[key]['last_order']:
                all_time_usage[key]['last_order'] = order.order_date
    
    # Calculate historical usage combining recent and all-time data
    historical_usage = defaultdict(lambda: {
        'name': '',
        'sku': '',
        'total_used': 0,
        'monthly_average': 0,
        'all_time_monthly_avg': 0,
        'weighted_monthly_avg': 0,
        'predicted_4month': 0,
        'current_stock': 0,
        'predicted_shortage': 0,
        'order_count': 0,
        'cost': Decimal('0.00')
    })
    
    for order in recent_orders:
        for accessory in order.accessories.all():
            # Only process RAU items
            if 'RAU' not in accessory.sku.upper():
                continue
                
            key = accessory.sku
            if not historical_usage[key]['name']:
                historical_usage[key]['name'] = accessory.name
                historical_usage[key]['sku'] = accessory.sku
                if accessory.stock_item:
                    historical_usage[key]['current_stock'] = accessory.stock_item.quantity
                    historical_usage[key]['cost'] = accessory.stock_item.cost
            
            historical_usage[key]['total_used'] += float(accessory.quantity)
            historical_usage[key]['order_count'] += 1
    
    # Calculate predictions combining recent and all-time averages
    for key, data in historical_usage.items():
        # Recent average monthly usage (last 3 months)
        recent_monthly_avg = data['total_used'] / 3
        data['monthly_average'] = recent_monthly_avg
        
        # All-time average monthly usage
        if key in all_time_usage and all_time_usage[key]['first_order'] and all_time_usage[key]['last_order']:
            months_span = max(1, ((all_time_usage[key]['last_order'] - all_time_usage[key]['first_order']).days / 30.44))
            all_time_monthly_avg = all_time_usage[key]['total_used'] / months_span
            data['all_time_monthly_avg'] = all_time_monthly_avg
            
            # Weighted average: 70% recent trend, 30% historical baseline
            data['weighted_monthly_avg'] = (recent_monthly_avg * 0.7) + (all_time_monthly_avg * 0.3)
        else:
            data['all_time_monthly_avg'] = recent_monthly_avg
            data['weighted_monthly_avg'] = recent_monthly_avg
        
        # Predicted usage for next 4 months using weighted average with 20% buffer
        data['predicted_4month'] = data['weighted_monthly_avg'] * 4 * 1.2  # Add 20% buffer
        
        # Special handling for 4mm gasket - maintain target stock of 800
        if 'GASKET - 4MM' in data['name'].upper() or 'GASKET-4MM' in data['name'].upper():
            target_stock = 800
            data['predicted_shortage'] = max(0, max(data['predicted_4month'], target_stock) - data['current_stock'])
        else:
            # Predicted shortage with buffer
            data['predicted_shortage'] = max(0, data['predicted_4month'] - data['current_stock'])
        
        data['line_cost'] = Decimal(str(data['predicted_shortage'])) * data['cost']
    
    # Convert to lists and sort by shortage
    upcoming_list = [data for data in upcoming_requirements.values() if data['shortage'] > 0]
    upcoming_list.sort(key=lambda x: x['shortage'], reverse=True)
    
    predicted_list = [data for data in historical_usage.values() if data['predicted_shortage'] > 0]
    predicted_list.sort(key=lambda x: x['predicted_shortage'], reverse=True)
    
    # Calculate totals
    total_upcoming_items = len(upcoming_list)
    total_predicted_items = len(predicted_list)
    total_upcoming_shortage = sum(item['shortage'] for item in upcoming_list)
    total_predicted_shortage = sum(item['predicted_shortage'] for item in predicted_list)
    
    # Helper function to get style prefix
    def get_style_prefix(sku, name=''):
        # Check both SKU and name for style codes
        text_to_check = (sku + ' ' + name).upper()
        if 'S150' in text_to_check:
            return 'S150'
        elif 'S750' in text_to_check:
            return 'S750'
        elif 'S751' in text_to_check:
            return 'S751'
        elif 'S753' in text_to_check:
            return 'S753'
        return None
    
    MIN_STYLE_BUFFER = 8  # Always maintain at least 8 styles in stock after orders
    
    # Get materials that appear in both lists (critical items)
    critical_items = []
    upcoming_skus = {item['sku'] for item in upcoming_list}
    from stock_take.models import StockItem
    
    # OPTIMIZATION: Fetch all needed stock items in one query
    critical_skus_list = [item['sku'] for item in predicted_list if item['sku'] in upcoming_skus]
    stock_items_dict = {si.sku: si for si in StockItem.objects.filter(sku__in=critical_skus_list)}
    
    for item in predicted_list:
        if item['sku'] in upcoming_skus:
            # Find the corresponding upcoming item
            upcoming_item = next((u for u in upcoming_list if u['sku'] == item['sku']), None)
            if upcoming_item:
                # Get stock item for min_order_qty from pre-fetched dict
                stock_item = stock_items_dict.get(item['sku'])
                min_order_qty = stock_item.min_order_qty if stock_item and stock_item.min_order_qty else 1
                
                total_shortage = upcoming_item['shortage'] + item['predicted_shortage']
                
                # For style items, add buffer to ensure 8 remain in stock
                is_style = get_style_prefix(item['sku'], item['name']) is not None
                if is_style:
                    order_qty = round_to_min_order_qty(total_shortage + MIN_STYLE_BUFFER, min_order_qty)
                else:
                    order_qty = round_to_min_order_qty(total_shortage, min_order_qty)
                
                # Ensure minimum line cost of 100 pounds
                line_cost = Decimal(str(order_qty)) * item['cost']
                if line_cost < MIN_LINE_COST and item['cost'] > 0:
                    min_qty_for_cost = int((MIN_LINE_COST / item['cost']).to_integral_value() + 1)
                    order_qty = max(order_qty, min_qty_for_cost)
                    order_qty = round_to_min_order_qty(order_qty, min_order_qty)
                    line_cost = Decimal(str(order_qty)) * item['cost']
                
                critical_items.append({
                    'sku': item['sku'],
                    'name': item['name'],
                    'current_stock': item['current_stock'],
                    'upcoming_shortage': upcoming_item['shortage'],
                    'predicted_shortage': item['predicted_shortage'],
                    'total_shortage': total_shortage,
                    'min_order_qty': min_order_qty,
                    'order_qty': order_qty,
                    'cost': item['cost'],
                    'line_cost': line_cost,
                    'is_style': is_style
                })
    
    # Filter out any critical items where total_shortage is 0 or negative
    critical_items = [item for item in critical_items if item['total_shortage'] > 0]
    critical_items.sort(key=lambda x: x['total_shortage'], reverse=True)
    
    # Calculate total costs
    critical_total_cost = sum(item['line_cost'] for item in critical_items)
    predicted_total_cost = sum(item['line_cost'] for item in predicted_list)
    
    # OPTIMIZATION: Fetch all predicted stock items in one query
    predicted_skus_list = [item['sku'] for item in predicted_list if item['sku'] not in upcoming_skus]
    predicted_stock_items_dict = {si.sku: si for si in StockItem.objects.filter(sku__in=predicted_skus_list)}
    
    # Calculate items only in predicted (not in critical) with min order qty
    predicted_only_list = []
    for item in predicted_list:
        if item['sku'] not in upcoming_skus:
            stock_item = predicted_stock_items_dict.get(item['sku'])
            min_order_qty = stock_item.min_order_qty if stock_item and stock_item.min_order_qty else 1
            
            # For style items, add buffer to ensure 8 remain in stock
            is_style = get_style_prefix(item['sku'], item['name']) is not None
            if is_style:
                order_qty = round_to_min_order_qty(item['predicted_shortage'] + MIN_STYLE_BUFFER, min_order_qty)
            else:
                order_qty = round_to_min_order_qty(item['predicted_shortage'], min_order_qty)
            
            # Ensure minimum line cost of 100 pounds
            line_cost = Decimal(str(order_qty)) * item['cost']
            if line_cost < MIN_LINE_COST and item['cost'] > 0:
                min_qty_for_cost = int((MIN_LINE_COST / item['cost']).to_integral_value() + 1)
                order_qty = max(order_qty, min_qty_for_cost)
                order_qty = round_to_min_order_qty(order_qty, min_order_qty)
                line_cost = Decimal(str(order_qty)) * item['cost']
            
            predicted_only_list.append({
                **item,
                'min_order_qty': min_order_qty,
                'order_qty': order_qty,
                'line_cost': line_cost,
                'is_style': is_style
            })
    
    predicted_only_cost = sum(item['line_cost'] for item in predicted_only_list)
    
    # Get set of critical SKUs for template filtering
    critical_skus = {item['sku'] for item in critical_items}
    
    # Suggest additional items to reach £5000 minimum order
    suggested_items = []
    current_order_value = critical_total_cost + predicted_only_cost
    
    # Check stock levels for each style variant
    from django.db.models import Q
    
    MIN_STYLES_PER_VARIANT = 8
    style_prefixes = ['S150', 'S750', 'S751', 'S753']
    styles_to_order = []
    style_stock_status = {}
    
    for prefix in style_prefixes:
        # Get all unique items (colors) with this prefix that are RAU items
        all_styles = StockItem.objects.filter(
            Q(sku__istartswith=prefix) & Q(sku__icontains='RAU')
        )
        
        # Get all unique color variants (each unique SKU is a color)
        total_variants = all_styles.count()
        in_stock_count = all_styles.filter(quantity__gt=0).count()
        out_of_stock_count = total_variants - in_stock_count
        
        style_stock_status[prefix] = {
            'in_stock': in_stock_count,
            'out_of_stock': out_of_stock_count,
            'total_variants': total_variants
        }
        
        # Order any out-of-stock color variants to maintain all colors
        if out_of_stock_count > 0:
            out_of_stock = all_styles.filter(quantity=0).exclude(
                sku__in=[item['sku'] for item in critical_items + predicted_only_list]
            )
            
            for stock_item in out_of_stock:
                min_order_qty = stock_item.min_order_qty if stock_item.min_order_qty else 1
                
                # Calculate quantity to meet minimum line cost
                if stock_item.cost > 0:
                    min_qty_for_cost = int((MIN_LINE_COST / stock_item.cost).to_integral_value() + 1)
                    suggested_qty = max(min_order_qty, min_qty_for_cost)
                    suggested_qty = round_to_min_order_qty(suggested_qty, min_order_qty)
                else:
                    suggested_qty = max(min_order_qty, 1)
                
                item_cost = Decimal(str(suggested_qty)) * stock_item.cost
                
                styles_to_order.append({
                    'sku': stock_item.sku,
                    'name': stock_item.name,
                    'current_stock': stock_item.quantity,
                    'min_order_qty': min_order_qty,
                    'suggested_qty': suggested_qty,
                    'cost': stock_item.cost,
                    'line_cost': item_cost,
                    'is_style': True,
                    'variant': prefix,
                    'reason': f'Stock variant {prefix} below minimum'
                })
    
    # Add style items first
    suggested_items.extend(styles_to_order)
    accumulated_value = sum(item['line_cost'] * VAT_RATE for item in styles_to_order)
    
    # Now check if we need more items to reach minimum order value (inc VAT)
    current_order_value_inc_vat = (critical_total_cost + predicted_only_cost) * VAT_RATE
    remaining_value = MIN_ORDER_VALUE - (current_order_value_inc_vat + accumulated_value)
    
    if remaining_value > 0:
        # Priority items that are always needed (higher weighting)
        PRIORITY_KEYWORDS = {
            'TOP ROLLER': 3.0,          # Triple priority
            'BOTTOM ROLLER': 2.5,       # 2.5x priority
            'FRAME SCREW': 2.5,         # 2.5x priority
            'GASKET - 4MM': 2.0,        # Double priority - frequently used
            'GASKET - 6MM': 0.5,        # Low priority - hardly used
            'GASKET - 8MM': 0.5,        # Low priority - very rarely used
        }
        
        # Items to exclude from suggestions (we have plenty)
        EXCLUDE_KEYWORDS = [
            'DUST BRUSH CLIP',
            'DUST EXCLUDING BRUSH',
        ]
        
        # Get all RAU stock items that are not already in shortages or suggested styles
        all_rau_items = StockItem.objects.filter(
            sku__icontains='RAU'
        ).exclude(
            sku__in=[item['sku'] for item in critical_items + predicted_only_list + suggested_items]
        )
        
        # Filter out excluded items
        filtered_items = []
        for stock_item in all_rau_items:
            item_name_upper = stock_item.name.upper()
            # Skip if matches any exclude keyword
            if any(keyword in item_name_upper for keyword in EXCLUDE_KEYWORDS):
                continue
            
            # Calculate priority score
            priority_multiplier = 1.0
            for keyword, multiplier in PRIORITY_KEYWORDS.items():
                if keyword in item_name_upper:
                    priority_multiplier = multiplier
                    break
            
            # Calculate score: priority * quantity (items we use frequently get higher score)
            score = stock_item.quantity * priority_multiplier
            filtered_items.append((stock_item, score, priority_multiplier))
        
        # Sort by score (descending) - prioritizes frequently used items and priority items
        filtered_items.sort(key=lambda x: x[1], reverse=True)
        
        for stock_item, score, priority_multiplier in filtered_items:
            if accumulated_value >= remaining_value:
                break
                
            # Suggest ordering based on min order quantity or reasonable default
            min_order_qty = stock_item.min_order_qty if stock_item.min_order_qty else 1
            
            # For priority items, suggest more quantity
            if priority_multiplier > 1.0:
                base_qty = max(min_order_qty, min(10, max(5, int(stock_item.quantity * 0.2))))
            else:
                base_qty = max(min_order_qty, min(5, max(2, int(stock_item.quantity * 0.1))))
            
            suggested_qty = round_to_min_order_qty(base_qty, min_order_qty)
            item_cost = Decimal(str(suggested_qty)) * stock_item.cost
            
            # Ensure minimum line cost of 100 pounds
            if item_cost < MIN_LINE_COST and stock_item.cost > 0:
                min_qty_for_cost = int((MIN_LINE_COST / stock_item.cost).to_integral_value() + 1)
                suggested_qty = max(suggested_qty, min_qty_for_cost)
                suggested_qty = round_to_min_order_qty(suggested_qty, min_order_qty)
                item_cost = Decimal(str(suggested_qty)) * stock_item.cost
            
            # Determine reason
            if priority_multiplier > 1.0:
                reason = 'High priority item - frequently needed'
            else:
                reason = 'To reach minimum order value'
            
            suggested_items.append({
                'sku': stock_item.sku,
                'name': stock_item.name,
                'current_stock': stock_item.quantity,
                'min_order_qty': min_order_qty,
                'suggested_qty': suggested_qty,
                'cost': stock_item.cost,
                'line_cost': item_cost,
                'is_style': get_style_prefix(stock_item.sku, stock_item.name) is not None,
                'reason': reason
            })
            
            accumulated_value += item_cost * VAT_RATE
    
    suggested_total_cost = sum(item['line_cost'] for item in suggested_items)
    
    # Calculate totals (pre-VAT)
    total_order_value = critical_total_cost + predicted_only_cost + suggested_total_cost
    current_order_value = critical_total_cost + predicted_only_cost
    
    # Calculate totals (inc VAT)
    total_order_value_inc_vat = total_order_value * VAT_RATE
    current_order_value_inc_vat = current_order_value * VAT_RATE
    suggested_total_cost_inc_vat = suggested_total_cost * VAT_RATE
    
    remaining_to_min = max(Decimal('0.00'), MIN_ORDER_VALUE - current_order_value_inc_vat)
    
    # Define ordering rules for display
    ordering_rules = [
        {
            'category': 'Minimum Order Values',
            'rules': [
                f'Minimum total order value: £{MIN_ORDER_VALUE:,.2f} (inc VAT for free shipping)',
                f'Minimum line item cost: £{MIN_LINE_COST:,.2f} per SKU (pre-VAT)',
                'All prices shown are pre-VAT; 20% VAT applied when calculating order totals',
            ]
        },
        {
            'category': 'Style Variants',
            'rules': [
                'Maintain all style color variants in stock for each type (S150, S750, S751, S753)',
                f'Add buffer of {MIN_STYLE_BUFFER} units when ordering styles to ensure stock after orders',
                'Any out-of-stock color variant will be suggested for reordering',
            ]
        },
        {
            'category': 'Priority Items (Higher Weighting)',
            'rules': [
                'Top Rollers: 3.0x priority (frequently needed)',
                'Bottom Rollers: 2.5x priority (frequently needed)',
                'Frame Screws: 2.5x priority (frequently needed)',
                'Gasket - 4mm: 2.0x priority (high usage)',
            ]
        },
        {
            'category': 'Low Priority Items (Lower Weighting)',
            'rules': [
                'Gasket - 6mm: 0.5x priority (hardly used)',
                'Gasket - 8mm: 0.5x priority (very rarely used)',
            ]
        },
        {
            'category': 'Excluded Items',
            'rules': [
                'Dust Brush Clip: Excluded from suggestions (not used)',
                'Dust Excluding Brush: Excluded from suggestions (sufficient stock)',
            ]
        },
        {
            'category': 'Rounding Rules',
            'rules': [
                'Items with minimum order quantity of 1: Rounded to nearest 10 (e.g., 257 → 260)',
                'Items with other minimum order quantities: Rounded to nearest multiple of min qty',
            ]
        },
        {
            'category': 'Prediction Model',
            'rules': [
                'Historical period: Last 90 days of orders',
                'Prediction window: Next 4 months + 20% buffer',
                'Weighted average: 70% recent trend + 30% historical baseline',
            ]
        },
        {
            'category': 'Target Stock Levels',
            'rules': [
                'Gasket - 4mm: Minimum target stock of 800 units (high usage item)',
                'All other items: Predicted 4-month usage with 20% safety buffer',
            ]
        },
    ]
    
    return render(request, 'stock_take/raumplus_storage.html', {
        'upcoming_shortages': upcoming_list,
        'predicted_shortages': predicted_only_list,
        'critical_items': critical_items,
        'suggested_items': suggested_items,
        'critical_skus': critical_skus,
        'total_upcoming_items': total_upcoming_items,
        'total_predicted_items': len(predicted_only_list),
        'total_upcoming_shortage': total_upcoming_shortage,
        'total_predicted_shortage': total_predicted_shortage,
        'upcoming_orders_count': upcoming_orders.count(),
        'historical_period_days': 90,
        'critical_total_cost': critical_total_cost,
        'predicted_total_cost': predicted_only_cost,
        'suggested_total_cost': suggested_total_cost,
        'total_order_value': total_order_value,
        'total_order_value_inc_vat': total_order_value_inc_vat,
        'current_order_value': current_order_value,
        'current_order_value_inc_vat': current_order_value_inc_vat,
        'suggested_total_cost_inc_vat': suggested_total_cost_inc_vat,
        'remaining_to_min': remaining_to_min,
        'min_order_value': MIN_ORDER_VALUE,
        'min_line_cost': MIN_LINE_COST,
        'vat_rate': VAT_RATE,
        'style_stock_status': style_stock_status,
        'ordering_rules': ordering_rules,
    })

@login_required
def substitutions(request):
    """Display and manage substitutions and skip items"""
    substitutions = Substitution.objects.all()
    skip_items = CSVSkipItem.objects.all()
    form = SubstitutionForm(request.POST or None)
    skip_form = CSVSkipItemForm(request.POST or None)
    
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Substitution added successfully.')
        return redirect('substitutions')
    
    if request.method == 'POST' and skip_form.is_valid():
        skip_form.save()
        messages.success(request, 'Skip item added successfully.')
        return redirect('substitutions')
    
    return render(request, 'stock_take/substitutions.html', {
        'substitutions': substitutions,
        'skip_items': skip_items,
        'form': form,
        'skip_form': skip_form,
    })

@login_required
@login_required
def create_boards_po(request):
    """Create a new BoardsPO entry and parse PNX file for items"""
    if request.method == 'POST':
        form = BoardsPOForm(request.POST, request.FILES)
        if form.is_valid():
            boards_po = form.save(commit=False)
            # Always set boards_ordered to False on creation - user will tick when ordered
            boards_po.boards_ordered = False
            boards_po.save()
            
            # If order_id is provided, associate the new BoardsPO with the order
            order_id = request.POST.get('order_id')
            if order_id:
                try:
                    order = Order.objects.get(id=order_id)
                    order.boards_po = boards_po
                    order.save()
                except Order.DoesNotExist:
                    pass
            
            # Parse PNX file if uploaded
            if boards_po.file:
                try:
                    # Read the PNX file
                    file_content = boards_po.file.read().decode('utf-8')
                    io_string = io.StringIO(file_content)
                    reader = csv.DictReader(io_string, delimiter=';')
                    
                    items_created = 0
                    for row in reader:
                        # Skip empty rows
                        if not row.get('BARCODE', '').strip():
                            continue
                            
                        try:
                            # Handle both old and new PNX formats:
                            # Old format: CUSTOMER="BFS-RW-403054 Customer Name", ORDERNAME="20393"
                            # New format: CUSTOMER="", ORDERNAME="BFS-NR-410415 Customer Name"
                            customer_field = row.get('CUSTOMER', '').strip()
                            ordername_field = row.get('ORDERNAME', '').strip()
                            
                            # If CUSTOMER is empty, use ORDERNAME instead
                            if not customer_field and ordername_field:
                                customer_value = ordername_field
                            # If CUSTOMER has value, combine it with ORDERNAME for compatibility
                            elif customer_field and ordername_field:
                                customer_value = f"{customer_field};{ordername_field}"
                            else:
                                customer_value = customer_field
                            
                            PNXItem.objects.create(
                                boards_po=boards_po,
                                barcode=row.get('BARCODE', '').strip(),
                                matname=row.get('MATNAME', '').strip(),
                                cleng=Decimal(row.get('CLENG', '0').strip() or '0'),
                                cwidth=Decimal(row.get('CWIDTH', '0').strip() or '0'),
                                cnt=Decimal(row.get('CNT', '0').strip() or '0'),
                                customer=customer_value,
                                # Additional PNX fields
                                grain=row.get('GRAIN', '').strip(),
                                articlename=row.get('ARTICLENAME', '').strip(),
                                partdesc=row.get('PARTDESC', '').strip(),
                                prfid1=row.get('PRFID1', '').strip(),
                                prfid2=row.get('PRFID2', '').strip(),
                                prfid3=row.get('PRFID3', '').strip(),
                                prfid4=row.get('PRFID4', '').strip(),
                                ordername=ordername_field
                            )
                            items_created += 1
                        except (ValueError, KeyError) as e:
                            # Skip rows with invalid data
                            continue
                    
                    messages.success(request, f'Boards PO {boards_po.po_number} created successfully with {items_created} items.')
                except Exception as e:
                    messages.warning(request, f'Boards PO {boards_po.po_number} created but there was an error parsing the PNX file: {str(e)}')
            else:
                messages.success(request, f'Boards PO {boards_po.po_number} created successfully.')
            
            return redirect('ordering')
        else:
            messages.error(request, 'Error creating Boards PO. Please check the form.')
    return redirect('ordering')


@login_required
def reimport_pnx(request, boards_po_id):
    """Re-import PNX file to refresh all item data"""
    boards_po = get_object_or_404(BoardsPO, id=boards_po_id)
    
    if not boards_po.file:
        messages.error(request, 'No PNX file available to re-import.')
        return redirect(request.META.get('HTTP_REFERER', 'ordering'))
    
    try:
        # Delete existing PNX items
        deleted_count = boards_po.pnx_items.count()
        boards_po.pnx_items.all().delete()
        
        # Re-read and parse the PNX file
        boards_po.file.seek(0)
        file_content = boards_po.file.read().decode('utf-8')
        io_string = io.StringIO(file_content)
        reader = csv.DictReader(io_string, delimiter=';')
        
        items_created = 0
        for row in reader:
            # Skip empty rows
            if not row.get('BARCODE', '').strip():
                continue
                
            try:
                # Handle both old and new PNX formats
                customer_field = row.get('CUSTOMER', '').strip()
                ordername_field = row.get('ORDERNAME', '').strip()
                
                if not customer_field and ordername_field:
                    customer_value = ordername_field
                elif customer_field and ordername_field:
                    customer_value = f"{customer_field};{ordername_field}"
                else:
                    customer_value = customer_field
                
                PNXItem.objects.create(
                    boards_po=boards_po,
                    barcode=row.get('BARCODE', '').strip(),
                    matname=row.get('MATNAME', '').strip(),
                    cleng=Decimal(row.get('CLENG', '0').strip() or '0'),
                    cwidth=Decimal(row.get('CWIDTH', '0').strip() or '0'),
                    cnt=Decimal(row.get('CNT', '0').strip() or '0'),
                    customer=customer_value,
                    grain=row.get('GRAIN', '').strip(),
                    articlename=row.get('ARTICLENAME', '').strip(),
                    partdesc=row.get('PARTDESC', '').strip(),
                    prfid1=row.get('PRFID1', '').strip(),
                    prfid2=row.get('PRFID2', '').strip(),
                    prfid3=row.get('PRFID3', '').strip(),
                    prfid4=row.get('PRFID4', '').strip(),
                    ordername=ordername_field
                )
                items_created += 1
            except (ValueError, KeyError) as e:
                continue
        
        messages.success(request, f'Re-imported {items_created} items (deleted {deleted_count} old items).')
    except Exception as e:
        messages.error(request, f'Error re-importing PNX file: {str(e)}')
    
    return redirect(request.META.get('HTTP_REFERER', 'ordering'))


@login_required
def update_boards_ordered(request, boards_po_id):
    """Update the boards_ordered status for a Boards PO"""
    if request.method == 'POST':
        import json
        try:
            boards_po = get_object_or_404(BoardsPO, id=boards_po_id)
            data = json.loads(request.body)
            boards_po.boards_ordered = data.get('boards_ordered', False)
            boards_po.save()
            return JsonResponse({'success': True})
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})
    return JsonResponse({'success': False, 'error': 'Invalid request method'})

@login_required
def update_po_number(request, boards_po_id):
    """Update the PO number for a Boards PO"""
    if request.method == 'POST':
        import json
        try:
            boards_po = get_object_or_404(BoardsPO, id=boards_po_id)
            data = json.loads(request.body)
            new_po_number = data.get('po_number', '').strip()
            
            if not new_po_number:
                return JsonResponse({'success': False, 'error': 'PO number cannot be empty'})
            
            # Check if PO number already exists (excluding current PO)
            if BoardsPO.objects.filter(po_number=new_po_number).exclude(id=boards_po_id).exists():
                return JsonResponse({'success': False, 'error': 'PO number already exists'})
            
            boards_po.po_number = new_po_number
            boards_po.save()
            return JsonResponse({'success': True, 'po_number': new_po_number})
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})
    return JsonResponse({'success': False, 'error': 'Invalid request method'})


@login_required
def update_pnx_item(request, pnx_item_id):
    """Update PNX item dimensions/count and regenerate PNX/CSV files"""
    if request.method == 'POST':
        import json
        try:
            pnx_item = get_object_or_404(PNXItem, id=pnx_item_id)
            data = json.loads(request.body)
            
            # Update the specified field
            field = data.get('field')
            value = data.get('value')
            
            if field not in ['cleng', 'cwidth', 'cnt']:
                return JsonResponse({'success': False, 'error': 'Invalid field'})
            
            # Update the field
            setattr(pnx_item, field, Decimal(str(value)))
            pnx_item.save()
            
            # Regenerate PNX and CSV files for the boards PO
            boards_po = pnx_item.boards_po
            if boards_po:
                regenerate_pnx_csv_files(boards_po)
            
            return JsonResponse({'success': True})
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})
    return JsonResponse({'success': False, 'error': 'Invalid request method'})


def update_pnx_dimensions(request):
    """Batch update PNX item dimensions/count/edging and regenerate PNX/CSV files"""
    if request.method == 'POST':
        import json
        try:
            data = json.loads(request.body)
            changes = data.get('changes', [])
            boards_po_id = data.get('boards_po_id')
            
            if not changes:
                return JsonResponse({'success': False, 'error': 'No changes provided'})
            
            # Update all changed items
            for change in changes:
                pnx_id = change.get('pnxId')
                field = change.get('field')
                value = change.get('value')
                
                # Handle dimension fields (numeric)
                if field in ['cleng', 'cwidth', 'cnt']:
                    pnx_item = PNXItem.objects.filter(id=pnx_id).first()
                    if pnx_item:
                        setattr(pnx_item, field, Decimal(str(value)))
                        pnx_item.save()
                # Handle edging fields (string)
                elif field in ['prfid1', 'prfid2', 'prfid3', 'prfid4']:
                    pnx_item = PNXItem.objects.filter(id=pnx_id).first()
                    if pnx_item:
                        setattr(pnx_item, field, value)
                        pnx_item.save()
            
            # Regenerate PNX and CSV files
            if boards_po_id:
                boards_po = BoardsPO.objects.filter(id=boards_po_id).first()
                if boards_po:
                    regenerate_pnx_csv_files(boards_po)
            
            return JsonResponse({'success': True})
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})
    return JsonResponse({'success': False, 'error': 'Invalid request method'})


def add_board_item(request):
    """Add a new board item to a Boards PO and regenerate files"""
    if request.method == 'POST':
        import json
        try:
            data = json.loads(request.body)
            boards_po_id = data.get('boards_po_id')
            
            if not boards_po_id:
                return JsonResponse({'success': False, 'error': 'No Boards PO specified'})
            
            boards_po = BoardsPO.objects.filter(id=boards_po_id).first()
            if not boards_po:
                return JsonResponse({'success': False, 'error': 'Boards PO not found'})
            
            # Get the linked order (reverse relation - boards_po has many orders)
            linked_order = boards_po.orders.first()
            
            # Get designer initials from the linked order's designer
            designer_initials = ''
            if linked_order and linked_order.designer:
                # Get initials from designer name (e.g., "John Smith" -> "JS")
                name_parts = linked_order.designer.name.split()
                designer_initials = ''.join([part[0].upper() for part in name_parts if part])
            
            # Create the new PNX item
            pnx_item = PNXItem.objects.create(
                boards_po=boards_po,
                barcode=data.get('barcode', ''),
                matname=data.get('matname', ''),
                cleng=Decimal(str(data.get('cleng', 0))),
                cwidth=Decimal(str(data.get('cwidth', 0))),
                cnt=Decimal(str(data.get('cnt', 1))),
                grain=data.get('grain', ''),
                partdesc=data.get('partdesc', ''),
                prfid1=data.get('prfid1', ''),
                prfid2=data.get('prfid2', ''),
                prfid3=data.get('prfid3', ''),
                prfid4=data.get('prfid4', ''),
                # Set customer based on linked order
                customer=f"BFS-{designer_initials}-{linked_order.sale_number}" if linked_order else '',
                ordername=linked_order.sale_number if linked_order else '',
            )
            
            # Regenerate PNX and CSV files
            regenerate_pnx_csv_files(boards_po)
            
            return JsonResponse({'success': True, 'item_id': pnx_item.id})
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})
    return JsonResponse({'success': False, 'error': 'Invalid request method'})


def reimport_pnx(request, boards_po_id):
    """Delete all PNX items and re-import from the PNX file"""
    if request.method == 'POST':
        try:
            boards_po = get_object_or_404(BoardsPO, id=boards_po_id)
            
            if not boards_po.file:
                return JsonResponse({'success': False, 'error': 'No PNX file found'})
            
            # Delete all existing items
            boards_po.pnx_items.all().delete()
            
            # Re-import from file
            boards_po.file.seek(0)
            content = boards_po.file.read().decode('utf-8')
            reader = csv.DictReader(io.StringIO(content), delimiter=';')
            
            count = 0
            for row in reader:
                barcode = row.get('BARCODE', '').strip()
                if not barcode:
                    continue
                
                customer_field = row.get('CUSTOMER', '').strip()
                ordername_field = row.get('ORDERNAME', '').strip()
                customer_value = ordername_field if not customer_field and ordername_field else customer_field
                
                PNXItem.objects.create(
                    boards_po=boards_po,
                    barcode=barcode,
                    matname=row.get('MATNAME', '').strip(),
                    cleng=Decimal(str(row.get('CLENG', '0').strip() or '0')),
                    cwidth=Decimal(str(row.get('CWIDTH', '0').strip() or '0')),
                    cnt=Decimal(str(row.get('CNT', '0').strip() or '0')),
                    customer=customer_value,
                    grain=row.get('GRAIN', '').strip(),
                    articlename=row.get('ARTICLENAME', '').strip(),
                    partdesc=row.get('PARTDESC', '').strip(),
                    prfid1=row.get('PRFID1', '').strip(),
                    prfid2=row.get('PRFID2', '').strip(),
                    prfid3=row.get('PRFID3', '').strip(),
                    prfid4=row.get('PRFID4', '').strip(),
                    ordername=ordername_field
                )
                count += 1
            
            return JsonResponse({'success': True, 'count': count})
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})
    return JsonResponse({'success': False, 'error': 'Invalid request method'})


def regenerate_pnx_csv_files(boards_po):
    """Regenerate both PNX and CSV files from current PNX items"""
    import os
    from django.conf import settings
    
    # Get all PNX items for this boards PO
    pnx_items = boards_po.pnx_items.all().order_by('barcode')
    
    if not pnx_items.exists():
        return
    
    # Generate PNX content (semicolon-delimited)
    pnx_output = io.StringIO()
    pnx_header = ['SPARE', 'BARCODE', 'MATNAME', 'CLENG', 'CWIDTH', 'CNT', 'OVERS', 'UNDERS', 'GRAIN', 
                  'QUICKEDGE0', 'CUSTOMER', 'ORDERNAME', 'ARTICLENAME', 'PARTDESC', 'PRFID1', 'PRFID3', 
                  'PRFID4', 'PRFID2']
    pnx_output.write(';'.join(pnx_header) + '\n')
    
    for item in pnx_items:
        row = [
            '',  # SPARE
            item.barcode,
            item.matname,
            str(item.cleng),
            str(item.cwidth),
            str(item.cnt),
            '',  # OVERS
            '',  # UNDERS
            item.grain,
            '',  # QUICKEDGE0
            item.customer,
            item.ordername,
            item.articlename,
            item.partdesc,
            item.prfid1,
            item.prfid3,
            item.prfid4,
            item.prfid2,
        ]
        pnx_output.write(';'.join(row) + '\n')
    
    # Write directly to existing PNX file path (overwrite, don't create new)
    if boards_po.file:
        pnx_path = boards_po.file.path
        with open(pnx_path, 'w', encoding='utf-8') as f:
            f.write(pnx_output.getvalue())
    
    # Generate CSV content (comma-delimited) - exact header order as specified
    csv_output = io.StringIO()
    csv_writer = csv.writer(csv_output)
    csv_writer.writerow(['BARCODE', 'MATNAME', 'CLENG', 'CWIDTH', 'CNT', 'GRAIN', 'CUSTOMER', 
                         'ORDERNAME', 'ARTICLENAME', 'PARTDESC', 'PRFID1', 'PRFID3', 'PRFID4', 'PRFID2'])
    
    for item in pnx_items:
        csv_writer.writerow([
            item.barcode,
            item.matname,
            str(item.cleng),
            str(item.cwidth),
            str(item.cnt),
            item.grain,
            item.customer,
            item.ordername,
            item.articlename,
            item.partdesc,
            item.prfid1,
            item.prfid3,
            item.prfid4,
            item.prfid2,
        ])
    
    # Write directly to existing CSV file path (overwrite, don't create new)
    if boards_po.csv_file:
        csv_path = boards_po.csv_file.path
        with open(csv_path, 'w', encoding='utf-8', newline='') as f:
            f.write(csv_output.getvalue())


@login_required
def update_pnx_received(request, pnx_item_id):
    """Update the received quantity for a PNX item"""
    if request.method == 'POST':
        import json
        try:
            pnx_item = get_object_or_404(PNXItem, id=pnx_item_id)
            data = json.loads(request.body)
            
            # Handle both boolean (for backward compatibility) and quantity updates
            if 'received' in data:
                # Boolean update - set to full quantity if received, 0 if not
                received = data.get('received', False)
                pnx_item.received_quantity = pnx_item.cnt if received else 0
            elif 'received_quantity' in data:
                # Quantity update
                received_quantity = Decimal(str(data.get('received_quantity', 0)))
                pnx_item.received_quantity = received_quantity
            
            pnx_item.save()
            return JsonResponse({'success': True})
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})
    return JsonResponse({'success': False, 'error': 'Invalid request method'})

@login_required
def update_pnx_batch(request):
    """Update the received status for multiple PNX items"""
    if request.method == 'POST':
        import json
        try:
            data = json.loads(request.body)
            changes = data.get('changes', {})
            
            # Update all PNX items in batch
            for pnx_id, value in changes.items():
                try:
                    pnx_item = PNXItem.objects.get(id=int(pnx_id))
                    
                    # Handle both boolean and quantity values
                    if isinstance(value, bool):
                        # Boolean update - set to full quantity if received, 0 if not
                        pnx_item.received_quantity = pnx_item.cnt if value else 0
                    else:
                        # Quantity update
                        received_quantity = Decimal(str(value))
                        pnx_item.received_quantity = received_quantity
                    
                    pnx_item.save()
                except PNXItem.DoesNotExist:
                    continue
            
            return JsonResponse({'success': True})
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})
    return JsonResponse({'success': False, 'error': 'Invalid request method'})

@login_required
def update_os_doors_batch(request):
    """Update the received status and cost price for multiple OS Doors"""
    if request.method == 'POST':
        import json
        try:
            data = json.loads(request.body)
            changes = data.get('changes', {})
            
            # Update all OS Doors in batch
            for os_door_id, updates in changes.items():
                try:
                    os_door = OSDoor.objects.get(id=int(os_door_id))
                    
                    # Handle dictionary of updates (new format)
                    if isinstance(updates, dict):
                        # Update cost price if provided
                        if 'cost_price' in updates:
                            os_door.cost_price = Decimal(str(updates['cost_price']))
                        
                        # Update quantity if provided
                        if 'quantity' in updates:
                            os_door.quantity = int(updates['quantity'])
                        
                        # Update received quantity if provided
                        if 'received_quantity' in updates:
                            received_quantity = Decimal(str(updates['received_quantity']))
                            os_door.received_quantity = received_quantity
                    # Handle legacy boolean/quantity values
                    elif isinstance(updates, bool):
                        # Boolean update - set to full quantity if received, 0 if not
                        os_door.received_quantity = os_door.quantity if updates else 0
                    else:
                        # Quantity update
                        received_quantity = Decimal(str(updates))
                        os_door.received_quantity = received_quantity
                    
                    os_door.save()
                except OSDoor.DoesNotExist:
                    continue
            
            return JsonResponse({'success': True})
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})
    return JsonResponse({'success': False, 'error': 'Invalid request method'})


@login_required
def delete_os_doors_batch(request):
    """Delete multiple OS Door items by ID"""
    if request.method == 'POST':
        import json
        try:
            data = json.loads(request.body)
            ids = data.get('ids', [])
            if not ids:
                return JsonResponse({'success': False, 'error': 'No items selected'})
            deleted_count, _ = OSDoor.objects.filter(id__in=[int(i) for i in ids]).delete()
            return JsonResponse({'success': True, 'deleted': deleted_count})
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})
    return JsonResponse({'success': False, 'error': 'Invalid request method'})


@login_required
def delete_accessories_batch(request):
    """Delete multiple Accessory items by ID"""
    if request.method == 'POST':
        import json
        try:
            data = json.loads(request.body)
            ids = data.get('ids', [])
            if not ids:
                return JsonResponse({'success': False, 'error': 'No items selected'})
            deleted_count, _ = Accessory.objects.filter(id__in=[int(i) for i in ids]).delete()
            return JsonResponse({'success': True, 'deleted': deleted_count})
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})
    return JsonResponse({'success': False, 'error': 'Invalid request method'})


@login_required
def replace_pnx_file(request, boards_po_id):
    """Replace the PNX file for a Boards PO and re-parse items"""
    if request.method == 'POST':
        boards_po = get_object_or_404(BoardsPO, id=boards_po_id)
        
        if 'pnx_file' not in request.FILES:
            messages.error(request, 'No file uploaded.')
            return redirect('ordering')
        
        pnx_file = request.FILES['pnx_file']
        
        # Validate file extension
        if not pnx_file.name.lower().endswith('.pnx'):
            messages.error(request, 'Only .pnx files are allowed.')
            return redirect('ordering')
        
        try:
            # Delete existing PNX items for this PO
            boards_po.pnx_items.all().delete()
            
            # Update the file
            boards_po.file = pnx_file
            boards_po.save()
            
            # Re-parse the new PNX file
            file_content = boards_po.file.read().decode('utf-8')
            io_string = io.StringIO(file_content)
            reader = csv.DictReader(io_string, delimiter=';')
            
            items_created = 0
            for row in reader:
                # Skip empty rows
                if not row.get('BARCODE', '').strip():
                    continue
                    
                try:
                    # Handle both old and new PNX formats:
                    # Old format: CUSTOMER="BFS-RW-403054 Customer Name", ORDERNAME="20393"
                    # New format: CUSTOMER="", ORDERNAME="BFS-NR-410415 Customer Name"
                    customer_field = row.get('CUSTOMER', '').strip()
                    ordername_field = row.get('ORDERNAME', '').strip()
                    
                    # If CUSTOMER is empty, use ORDERNAME instead
                    if not customer_field and ordername_field:
                        customer_value = ordername_field
                    # If CUSTOMER has value, combine it with ORDERNAME for compatibility
                    elif customer_field and ordername_field:
                        customer_value = f"{customer_field};{ordername_field}"
                    else:
                        customer_value = customer_field
                    
                    PNXItem.objects.create(
                        boards_po=boards_po,
                        barcode=row.get('BARCODE', '').strip(),
                        matname=row.get('MATNAME', '').strip(),
                        cleng=Decimal(row.get('CLENG', '0').strip() or '0'),
                        cwidth=Decimal(row.get('CWIDTH', '0').strip() or '0'),
                        cnt=Decimal(row.get('CNT', '0').strip() or '0'),
                        customer=customer_value
                    )
                    items_created += 1
                except (ValueError, KeyError) as e:
                    # Skip rows with invalid data
                    continue
            
            messages.success(request, f'Successfully replaced PNX file for {boards_po.po_number}. Created {items_created} items.')
            
        except Exception as e:
            messages.error(request, f'Error processing PNX file: {str(e)}')
    
    return redirect('ordering')

@login_required
def delete_boards_po(request, boards_po_id):
    """Delete a Boards PO"""
    if request.method == 'POST':
        boards_po = get_object_or_404(BoardsPO, id=boards_po_id)
        po_number = boards_po.po_number
        
        # Check if any orders are linked to this PO
        if boards_po.orders.exists():
            messages.error(request, f'Cannot delete PO {po_number}. There are {boards_po.orders.count()} orders linked to it.')
            return redirect('ordering')
        
        # Delete the PO
        boards_po.delete()
        messages.success(request, f'Successfully deleted PO {po_number}.')
    
    return redirect('ordering')

@login_required
def preview_pnx_file(request, boards_po_id):
    """Preview PNX file content"""
    boards_po = get_object_or_404(BoardsPO, id=boards_po_id)
    
    if not boards_po.file:
        return JsonResponse({'success': False, 'error': 'No file attached to this PO'})
    
    try:
        # Read the file
        file_content = boards_po.file.read().decode('utf-8')
        lines = file_content.split('\n')
        
        # Parse into table format
        rows = []
        header = None
        for line in lines:
            if line.strip():
                fields = line.split(';')
                if header is None:
                    header = fields
                else:
                    rows.append(fields)
        
        # Count items (excluding header)
        item_count = len(rows)
        
        return JsonResponse({
            'success': True,
            'header': header,
            'rows': rows,
            'po_number': boards_po.po_number,
            'line_count': len(lines),
            'item_count': item_count
        })
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})

@login_required
def preview_csv_file(request, boards_po_id):
    """Preview CSV file content"""
    boards_po = get_object_or_404(BoardsPO, id=boards_po_id)
    
    if not boards_po.csv_file:
        return JsonResponse({'success': False, 'error': 'No CSV file attached to this PO'})
    
    try:
        # Read the file
        file_content = boards_po.csv_file.read().decode('utf-8')
        lines = file_content.split('\n')
        
        # Parse into table format
        rows = []
        header = None
        for line in lines:
            if line.strip():
                fields = line.split(',')
                if header is None:
                    header = fields
                else:
                    rows.append(fields)
        
        # Count items (excluding header)
        item_count = len(rows)
        
        return JsonResponse({
            'success': True,
            'header': header,
            'rows': rows,
            'po_number': boards_po.po_number,
            'line_count': len(lines),
            'item_count': item_count
        })
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})

@login_required
def update_both_files(request, boards_po_id):
    """Update both PNX and CSV files with the same data"""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Invalid request method'})
    
    boards_po = get_object_or_404(BoardsPO, id=boards_po_id)
    
    try:
        import json
        from django.core.files.base import ContentFile
        
        data = json.loads(request.body)
        header = data.get('header', [])
        rows = data.get('rows', [])
        
        # Build PNX content (semicolon-delimited)
        pnx_lines = []
        pnx_lines.append(';'.join(header))
        for row in rows:
            pnx_lines.append(';'.join(row))
        pnx_content = '\n'.join(pnx_lines)
        
        # Build CSV content (comma-delimited)
        csv_lines = []
        csv_lines.append(','.join(header))
        for row in rows:
            csv_lines.append(','.join(row))
        csv_content = '\n'.join(csv_lines)
        
        # Update PNX file
        if boards_po.file:
            pnx_path = boards_po.file.name
            boards_po.file.delete(save=False)
            boards_po.file.save(pnx_path.split('/')[-1], ContentFile(pnx_content.encode('utf-8')), save=False)
        
        # Update CSV file
        if boards_po.csv_file:
            csv_path = boards_po.csv_file.name
            boards_po.csv_file.delete(save=False)
            boards_po.csv_file.save(csv_path.split('/')[-1], ContentFile(csv_content.encode('utf-8')), save=False)
        else:
            # Create CSV file if it doesn't exist
            csv_filename = f'PO_{boards_po.po_number}.csv'
            boards_po.csv_file.save(csv_filename, ContentFile(csv_content.encode('utf-8')), save=False)
        
        boards_po.save()
        
        return JsonResponse({'success': True, 'message': 'Both PNX and CSV files updated successfully'})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})

@login_required
def update_pnx_file(request, boards_po_id):
    """Update PNX file content from edited table data"""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Invalid request method'})
    
    boards_po = get_object_or_404(BoardsPO, id=boards_po_id)
    
    if not boards_po.file:
        return JsonResponse({'success': False, 'error': 'No PNX file attached to this PO'})
    
    try:
        import json
        from django.core.files.base import ContentFile
        
        data = json.loads(request.body)
        header = data.get('header', [])
        rows = data.get('rows', [])
        
        # Build PNX content (semicolon-delimited)
        lines = []
        lines.append(';'.join(header))
        for row in rows:
            lines.append(';'.join(row))
        
        pnx_content = '\n'.join(lines)
        
        # Save updated PNX file
        file_path = boards_po.file.name
        boards_po.file.delete(save=False)
        boards_po.file.save(file_path.split('/')[-1], ContentFile(pnx_content.encode('utf-8')), save=True)
        
        return JsonResponse({'success': True, 'message': 'PNX file updated successfully'})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})

@login_required
def update_csv_file(request, boards_po_id):
    """Update CSV file content from edited table data"""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Invalid request method'})
    
    boards_po = get_object_or_404(BoardsPO, id=boards_po_id)
    
    if not boards_po.csv_file:
        return JsonResponse({'success': False, 'error': 'No CSV file attached to this PO'})
    
    try:
        import json
        from django.core.files.base import ContentFile
        
        data = json.loads(request.body)
        header = data.get('header', [])
        rows = data.get('rows', [])
        
        # Build CSV content (comma-delimited)
        lines = []
        lines.append(','.join(header))
        for row in rows:
            lines.append(','.join(row))
        
        csv_content = '\n'.join(lines)
        
        # Save updated CSV file
        file_path = boards_po.csv_file.name
        boards_po.csv_file.delete(save=False)
        boards_po.csv_file.save(file_path.split('/')[-1], ContentFile(csv_content.encode('utf-8')), save=True)
        
        return JsonResponse({'success': True, 'message': 'CSV file updated successfully'})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})

@login_required
def generate_csv_file(request, boards_po_id):
    """Generate and save CSV file from PNX file"""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Invalid request method'})
    
    boards_po = get_object_or_404(BoardsPO, id=boards_po_id)
    
    if not boards_po.file:
        return JsonResponse({'success': False, 'error': 'No PNX file attached to this PO'})
    
    try:
        from django.core.files.base import ContentFile
        
        # Read the PNX file
        file_content = boards_po.file.read().decode('utf-8')
        lines = file_content.split('\n')
        
        # Create CSV content
        csv_lines = []
        for line in lines:
            if line.strip():
                # Split by semicolon and join with comma
                fields = line.split(';')
                csv_lines.append(','.join(fields))
        
        csv_content = '\n'.join(csv_lines)
        
        # Save CSV file
        csv_filename = f'PO_{boards_po.po_number}.csv'
        boards_po.csv_file.save(csv_filename, ContentFile(csv_content.encode('utf-8')), save=True)
        
        return JsonResponse({'success': True, 'message': 'CSV file generated successfully'})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})

@login_required
def download_pnx_as_csv_boardspo(request, boards_po_id):
    """Convert PNX file (semicolon-delimited) to CSV (comma-delimited) and download"""
    boards_po = get_object_or_404(BoardsPO, id=boards_po_id)
    
    if not boards_po.file:
        messages.error(request, 'No file attached to this PO')
        return redirect('ordering')
    
    try:
        # Read the PNX file
        file_content = boards_po.file.read().decode('utf-8')
        lines = file_content.split('\n')
        
        # Create CSV response
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = f'attachment; filename="PO_{boards_po.po_number}.csv"'
        
        writer = csv.writer(response)
        
        # Convert each line from semicolon to comma
        for line in lines:
            if line.strip():
                # Split by semicolon and write as comma-separated
                fields = line.split(';')
                writer.writerow(fields)
        
        return response
    except Exception as e:
        messages.error(request, f'Error converting PNX to CSV: {str(e)}')
        return redirect('ordering')

@login_required
def delete_accessory_csv(request, csv_id):
    """Delete an accessory CSV file"""
    if request.method == 'POST':
        order = get_object_or_404(Order, id=csv_id)
        sale_number = order.sale_number
        
        # Clear the CSV file fields
        if order.original_csv:
            order.original_csv.delete(save=False)
        if order.processed_csv:
            order.processed_csv.delete(save=False)
        
        order.original_csv = None
        order.processed_csv = None
        order.original_csv_uploaded_at = None
        order.processed_csv_created_at = None
        order.save()
        
        messages.success(request, f'Successfully deleted CSV for sale {sale_number}.')
    
    return redirect('ordering')

@login_required
def preview_accessory_csv(request, csv_id):
    """Preview accessories CSV file content"""
    order = get_object_or_404(Order, id=csv_id)
    
    if not order.original_csv:
        return JsonResponse({'success': False, 'error': 'No CSV file attached to this order'})
    
    try:
        # Read the file
        file_content = order.original_csv.read().decode('utf-8')
        lines = file_content.split('\n')
        
        # Parse into table format
        rows = []
        header = None
        for line in lines:
            if line.strip():
                fields = line.split(',')
                if header is None:
                    header = fields
                else:
                    rows.append(fields)
        
        # Count items (excluding header)
        item_count = len(rows)
        
        return JsonResponse({
            'success': True,
            'header': header,
            'rows': rows,
            'sale_number': order.sale_number,
            'line_count': len(lines),
            'item_count': item_count
        })
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})

@login_required
def update_order_financial(request, order_id):
    """Update financial fields for an order"""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Invalid request method'})
    
    order = get_object_or_404(Order, id=order_id)
    
    try:
        import json
        data = json.loads(request.body)
        field = data.get('field')
        value = data.get('value', 0)
        
        # Validate field name
        valid_fields = ['materials_cost', 'installation_cost', 'manufacturing_cost', 
                       'total_value_inc_vat', 'total_value_exc_vat', 'profit']
        if field not in valid_fields:
            return JsonResponse({'success': False, 'error': 'Invalid field'})
        
        # Update the field
        from decimal import Decimal
        setattr(order, field, Decimal(str(value)))
        order.save()
        
        return JsonResponse({'success': True})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})

@login_required
def recalculate_order_financials(request, order_id):
    """Recalculate all financial fields from source data"""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Invalid request method'})
    
    order = get_object_or_404(Order, id=order_id)
    
    try:
        from decimal import Decimal
        
        # Get price per square meter from session
        price_per_sqm_str = request.session.get('price_per_sqm', '12')
        price_per_sqm = float(price_per_sqm_str)
        
        # Recalculate materials cost from boards, accessories, and OS doors
        materials_cost = order.calculate_materials_cost(price_per_sqm)
        
        # Recalculate installation cost from timesheets and expenses
        installation_cost = order.calculate_installation_cost()
        
        # Recalculate manufacturing cost from timesheets
        manufacturing_cost = order.calculate_manufacturing_cost()
        
        # Calculate total value exc VAT from inc VAT
        if order.total_value_inc_vat > 0:
            total_value_exc_vat = order.total_value_inc_vat / Decimal('1.2')
        else:
            total_value_exc_vat = Decimal('0.00')
        
        # Calculate profit
        profit = total_value_exc_vat - materials_cost - installation_cost - manufacturing_cost
        
        # Update and save the order
        order.materials_cost = materials_cost
        order.installation_cost = installation_cost
        order.manufacturing_cost = manufacturing_cost
        order.total_value_exc_vat = total_value_exc_vat
        order.profit = profit
        order.save()
        
        return JsonResponse({
            'success': True,
            'materials_cost': str(materials_cost),
            'installation_cost': str(installation_cost),
            'manufacturing_cost': str(manufacturing_cost),
            'total_value_exc_vat': str(total_value_exc_vat),
            'profit': str(profit)
        })
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})

@login_required
def save_all_order_financials(request, order_id):
    """Save all financial fields for an order at once"""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Invalid request method'})
    
    order = get_object_or_404(Order, id=order_id)
    
    try:
        import json
        from decimal import Decimal
        data = json.loads(request.body)
        
        # Update all financial fields
        order.total_value_inc_vat = Decimal(str(data.get('total_value_inc_vat', 0)))
        order.total_value_exc_vat = Decimal(str(data.get('total_value_exc_vat', 0)))
        order.materials_cost = Decimal(str(data.get('materials_cost', 0)))
        order.installation_cost = Decimal(str(data.get('installation_cost', 0)))
        order.manufacturing_cost = Decimal(str(data.get('manufacturing_cost', 0)))
        order.profit = Decimal(str(data.get('profit', 0)))
        order.fully_costed = data.get('fully_costed', False)
        order.save()
        
        return JsonResponse({'success': True})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})

@login_required
def update_customer_info(request, order_id):
    """Update customer information for an order"""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Invalid request method'})
    
    order = get_object_or_404(Order, id=order_id)
    
    try:
        import json
        data = json.loads(request.body)
        
        order.first_name = data.get('first_name', '')
        order.last_name = data.get('last_name', '')
        order.anthill_id = data.get('anthill_id', '')
        order.address = data.get('address', '')
        order.postcode = data.get('postcode', '')
        order.save()
        
        return JsonResponse({'success': True})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})

@login_required
def update_sale_info(request, order_id):
    """Update sale information for an order"""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Invalid request method'})
    
    order = get_object_or_404(Order, id=order_id)
    
    try:
        import json
        from datetime import datetime
        data = json.loads(request.body)
        
        order.sale_number = data.get('sale_number', '')
        order.customer_number = data.get('customer_number', '')
        order.workguru_id = data.get('workguru_id', '')
        
        # Handle designer field
        designer_id = data.get('designer_id')
        if designer_id:
            order.designer = Designer.objects.get(id=designer_id)
        else:
            order.designer = None
        
        # Parse dates
        if data.get('order_date'):
            order.order_date = datetime.strptime(data['order_date'], '%Y-%m-%d').date()
        if data.get('fit_date'):
            order.fit_date = datetime.strptime(data['fit_date'], '%Y-%m-%d').date()
        
        # Update total value inc VAT
        if data.get('total_value_inc_vat'):
            order.total_value_inc_vat = Decimal(str(data['total_value_inc_vat']))
            # Auto-calculate exc VAT
            order.total_value_exc_vat = order.total_value_inc_vat / Decimal('1.2')
        
        order.save()
        
        return JsonResponse({'success': True})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})

@login_required
def update_order_type(request, order_id):
    """Update order type"""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Invalid request method'})
    
    order = get_object_or_404(Order, id=order_id)
    
    try:
        import json
        data = json.loads(request.body)
        
        order_type = data.get('order_type', '')
        if order_type in ['sale', 'remedial', 'warranty']:
            order.order_type = order_type
            order.save()
            return JsonResponse({'success': True})
        else:
            return JsonResponse({'success': False, 'error': 'Invalid order type'})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})

@login_required
def update_boards_po(request, order_id):
    """Update boards PO for an order"""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Invalid request method'})
    
    order = get_object_or_404(Order, id=order_id)
    
    try:
        import json
        data = json.loads(request.body)
        
        po_id = data.get('boards_po_id', '')
        if po_id:
            from .models import BoardsPO
            boards_po = BoardsPO.objects.get(id=po_id)
            order.boards_po = boards_po
        else:
            order.boards_po = None
        
        order.save()
        return JsonResponse({'success': True})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})

@login_required
def update_job_checkbox(request, order_id):
    """Update job checkbox fields (all_items_ordered, job_finished)"""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Invalid request method'})
    
    order = get_object_or_404(Order, id=order_id)
    
    try:
        import json
        data = json.loads(request.body)
        
        if 'all_items_ordered' in data:
            order.all_items_ordered = bool(data['all_items_ordered'])
        if 'job_finished' in data:
            order.job_finished = bool(data['job_finished'])
        
        order.save()
        return JsonResponse({'success': True})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})

@login_required
def order_details(request, order_id):
    """Display and edit order details, including boards PO assignment"""
    order = get_object_or_404(Order, id=order_id)
    
    # Get or create workflow progress for this order
    from .models import OrderWorkflowProgress, WorkflowStage, TaskCompletion
    workflow_progress, created = OrderWorkflowProgress.objects.get_or_create(
        order=order,
        defaults={'current_stage': WorkflowStage.objects.first()}
    )
    
    # Get all available workflow stages
    workflow_stages = WorkflowStage.objects.all().prefetch_related('tasks')
    
    # Get task completions for current stage
    task_completions = {}
    if workflow_progress.current_stage:
        for task in workflow_progress.current_stage.tasks.all():
            completion, _ = TaskCompletion.objects.get_or_create(
                order_progress=workflow_progress,
                task=task
            )
            task_completions[task.id] = completion
    
    # Group stages by phase for workflow modal
    phases = [
        ('enquiry', 'Enquiry'),
        ('lead', 'Lead'),
        ('sale', 'Sale'),
    ]
    stages_by_phase = {}
    for phase_code, phase_display in phases:
        stages_by_phase[phase_code] = workflow_stages.filter(phase=phase_code)
    
    # Get price per square meter from session, default to 12
    price_per_sqm_str = request.session.get('price_per_sqm', '12')
    price_per_sqm = float(price_per_sqm_str)
    
    if request.method == 'POST':
        # Check if this is an OS door form submission
        if 'door_style' in request.POST:
            os_door_form = OSDoorForm(request.POST)
            if os_door_form.is_valid():
                os_door = os_door_form.save(commit=False)
                os_door.customer = order
                os_door.save()
                messages.success(request, 'OS Door added successfully.')
                return redirect('order_details', order_id=order_id)
        else:
            form = OrderForm(request.POST, instance=order)
            if form.is_valid():
                form.save()
                messages.success(request, f'Order {order.sale_number} updated successfully.')
                return redirect('order_details', order_id=order_id)
    else:
        form = OrderForm(instance=order)
    
    # Initialize OS door form with pre-populated data if existing OS doors exist
    if order.os_doors.exists():
        # Get the most recent OS door item to pre-populate form
        latest_os_door = order.os_doors.order_by('-id').first()
        os_door_form = OSDoorForm(initial={
            'door_style': latest_os_door.door_style,
            'style_colour': latest_os_door.style_colour,
            'colour': latest_os_door.colour,
            'height': latest_os_door.height,
            'width': latest_os_door.width,
            'item_description': latest_os_door.item_description,
        })
    else:
        os_door_form = OSDoorForm()
    
    # Get other orders with the same boards PO (excluding current order)
    other_orders = []
    if order.boards_po:
        other_orders = Order.objects.filter(boards_po=order.boards_po).exclude(id=order.id)
    
    # Get PNX items associated with this order (based on sale_number in customer field)
    order_pnx_items = []
    pnx_total_cost = 0
    if order.boards_po:
        order_pnx_items = order.boards_po.pnx_items.filter(customer__icontains=order.sale_number)
        # Calculate cost for each item and total using dynamic price
        for item in order_pnx_items:
            item.calculated_cost = item.get_cost(price_per_sqm)
        pnx_total_cost = sum(item.calculated_cost for item in order_pnx_items)
    
    # Check if order has OS door accessories
    has_os_door_accessories = order.accessories.filter(is_os_door=True).exists()
    
    # Separate glass items (SKU starts with GLS) from other accessories
    glass_items_qs = order.accessories.filter(sku__istartswith='GLS')
    non_glass_accessories_qs = order.accessories.exclude(sku__istartswith='GLS')
    
    # Convert to lists and sort by out-of-stock status
    # Out of stock = remaining (available - allocated) < 0
    glass_items = sorted(
        list(glass_items_qs),
        key=lambda x: ((x.available_quantity - x.allocated_quantity) >= 0, x.id)
    )
    non_glass_accessories = sorted(
        list(non_glass_accessories_qs),
        key=lambda x: ((x.available_quantity - x.allocated_quantity) >= 0, x.id)
    )
    
    # Automatically remove items that are in skip lists
    skip_items_removed = 0
    
    # Remove order-specific skip items
    for skip_item in order.csv_skip_items.all():
        accessories_to_remove = order.accessories.filter(sku=skip_item.sku)
        for accessory in accessories_to_remove:
            accessory.delete()
            skip_items_removed += 1
    
    # Remove global skip items
    global_skip_items = CSVSkipItem.objects.filter(order__isnull=True)
    for skip_item in global_skip_items:
        accessories_to_remove = order.accessories.filter(sku=skip_item.sku)
        for accessory in accessories_to_remove:
            accessory.delete()
            skip_items_removed += 1
    
    # Regenerate processed CSV if items were removed
    if skip_items_removed > 0:
        if order.accessories.exists():
            processed_rows = []
            for accessory in order.accessories.all():
                row = {
                    'Sku': accessory.sku,
                    'Name': accessory.name,
                    'Description': accessory.description,
                    'CostPrice': str(accessory.cost_price),
                    'SellPrice': str(accessory.sell_price),
                    'Quantity': str(accessory.quantity),
                    'Billable': 'TRUE' if accessory.billable else 'FALSE'
                }
                processed_rows.append(row)
            
            if processed_rows:
                processed_csv_content = io.StringIO()
                fieldnames = ['Sku', 'Name', 'Description', 'CostPrice', 'SellPrice', 'Quantity', 'Billable']
                writer = csv.DictWriter(processed_csv_content, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(processed_rows)
                
                # Save updated processed CSV
                filename = order.original_csv.name.split('_')[-1] if order.original_csv else 'accessories.csv'
                order.processed_csv.save(f"{order.customer_number}_processed_{filename}", 
                                       io.BytesIO(processed_csv_content.getvalue().encode('utf-8')))
                order.processed_csv_created_at = timezone.now()
                order.save()
        
        messages.info(request, f'Automatically removed {skip_items_removed} items from skip lists.')
    
    # Calculate total materials cost and save it to the order
    materials_cost = order.calculate_materials_cost(price_per_sqm)
    if order.materials_cost != materials_cost:
        order.materials_cost = materials_cost
        order.save(update_fields=['materials_cost'])
    
    # Calculate materials cost breakdown
    boards_cost = Decimal('0.00')
    if order.boards_po:
        # Only include PNX items for this specific order (filtered by sale_number)
        order_pnx_items_for_cost = order.boards_po.pnx_items.filter(customer__icontains=order.sale_number)
        for pnx_item in order_pnx_items_for_cost:
            boards_cost += pnx_item.get_cost(price_per_sqm)
    
    accessories_cost = Decimal('0.00')
    for accessory in order.accessories.all():
        accessories_cost += accessory.cost_price * accessory.quantity
    
    os_doors_cost = Decimal('0.00')
    for os_door in order.os_doors.all():
        os_doors_cost += os_door.cost_price * os_door.quantity
    
    # Get timesheets and expenses for this order
    installation_timesheets = order.timesheets.filter(timesheet_type='installation').select_related('fitter', 'helper')
    manufacturing_timesheets = order.timesheets.filter(timesheet_type='manufacturing').select_related('factory_worker')
    expenses = order.expenses.all().select_related('fitter')
    
    # Calculate costs from timesheets with breakdown
    calculated_installation_cost = order.calculate_installation_cost()
    calculated_manufacturing_cost = order.calculate_manufacturing_cost()
    
    # Calculate installation cost breakdown (fitters vs helpers vs expenses)
    installation_fitter_cost = sum(
        ts.total_cost for ts in installation_timesheets if ts.fitter
    )
    installation_helper_cost = sum(
        ts.total_cost for ts in installation_timesheets if ts.helper
    )
    
    # Calculate expenses breakdown by type
    petrol_cost = sum(exp.amount for exp in expenses.filter(expense_type='petrol'))
    materials_expense_cost = sum(exp.amount for exp in expenses.filter(expense_type='materials'))
    other_expense_cost = sum(exp.amount for exp in expenses.filter(expense_type='other'))
    
    # Calculate manufacturing cost breakdown by worker
    from collections import defaultdict
    manufacturing_by_worker = defaultdict(Decimal)
    for ts in manufacturing_timesheets:
        worker_name = ts.worker_name
        manufacturing_by_worker[worker_name] += ts.total_cost
    
    # Get all available boards POs for the dropdown
    from .models import BoardsPO
    from django.db.models import Q, Count
    
    # If the current order is finished, show all POs
    # If not finished, only show POs that have at least one unfinished order or no orders
    if order.job_finished:
        available_pos = BoardsPO.objects.all().order_by('-po_number')
    else:
        # Get POs that either have no orders or have at least one order that isn't finished
        available_pos = BoardsPO.objects.annotate(
            total_orders=Count('orders'),
            finished_orders=Count('orders', filter=Q(orders__job_finished=True))
        ).filter(
            Q(total_orders=0) | Q(total_orders__gt=models.F('finished_orders'))
        ).order_by('-po_number')
    
    return render(request, 'stock_take/order_details.html', {
        'order': order,
        'form': form,
        'os_door_form': os_door_form,
        'other_orders': other_orders,
        'available_pos': available_pos,
        'order_pnx_items': order_pnx_items,
        'pnx_total_cost': pnx_total_cost,
        'has_os_door_accessories': has_os_door_accessories,
        'glass_items': glass_items,
        'non_glass_accessories': non_glass_accessories,
        'price_per_sqm': price_per_sqm,
        'materials_cost': materials_cost,
        'boards_cost': boards_cost,
        'accessories_cost': accessories_cost,
        'os_doors_cost': os_doors_cost,
        'workflow_progress': workflow_progress,
        'workflow_stages': workflow_stages,
        'task_completions': task_completions,
        'phases': phases,
        'stages_by_phase': stages_by_phase,
        'installation_timesheets': installation_timesheets,
        'manufacturing_timesheets': manufacturing_timesheets,
        'expenses': expenses,
        'calculated_installation_cost': calculated_installation_cost,
        'calculated_manufacturing_cost': calculated_manufacturing_cost,
        'installation_fitter_cost': installation_fitter_cost,
        'installation_helper_cost': installation_helper_cost,
        'petrol_cost': petrol_cost,
        'materials_expense_cost': materials_expense_cost,
        'other_expense_cost': other_expense_cost,
        'manufacturing_by_worker': dict(manufacturing_by_worker),
        'designers': Designer.objects.all().order_by('name'),
    })

def completed_stock_takes(request):
    """Display completed stock takes with analytics and filtering"""
    from datetime import timedelta
    from django.db.models import Count, Q
    from django.db.models.functions import TruncMonth
    
    # Base queryset
    completed_schedules = Schedule.objects.filter(
        status='completed',
        completed_date__isnull=False
    ).prefetch_related('stock_take_groups__category')
    
    # Get filter parameters
    search_query = request.GET.get('search', '').strip()
    date_from = request.GET.get('date_from', '')
    date_to = request.GET.get('date_to', '')
    assigned_to = request.GET.get('assigned_to', '')
    auto_generated = request.GET.get('auto_generated', '')
    sort_by = request.GET.get('sort', '-completed_date')
    
    # Apply search filter
    if search_query:
        completed_schedules = completed_schedules.filter(
            Q(name__icontains=search_query) |
            Q(description__icontains=search_query) |
            Q(locations__icontains=search_query) |
            Q(assigned_to__icontains=search_query) |
            Q(notes__icontains=search_query)
        )
    
    # Apply date filters
    if date_from:
        try:
            from_date = timezone.datetime.strptime(date_from, '%Y-%m-%d').replace(tzinfo=timezone.get_current_timezone())
            completed_schedules = completed_schedules.filter(completed_date__gte=from_date)
        except ValueError:
            pass
    
    if date_to:
        try:
            to_date = timezone.datetime.strptime(date_to, '%Y-%m-%d').replace(tzinfo=timezone.get_current_timezone())
            to_date = to_date.replace(hour=23, minute=59, second=59)
            completed_schedules = completed_schedules.filter(completed_date__lte=to_date)
        except ValueError:
            pass
    
    # Apply assigned_to filter
    if assigned_to:
        completed_schedules = completed_schedules.filter(assigned_to__icontains=assigned_to)
    
    # Apply auto_generated filter
    if auto_generated == 'true':
        completed_schedules = completed_schedules.filter(auto_generated=True)
    elif auto_generated == 'false':
        completed_schedules = completed_schedules.filter(auto_generated=False)
    
    # Apply sorting
    valid_sorts = {
        'name': 'name',
        '-name': '-name',
        'completed_date': 'completed_date',
        '-completed_date': '-completed_date',
        'scheduled_date': 'scheduled_date',
        '-scheduled_date': '-scheduled_date',
        'assigned_to': 'assigned_to',
        '-assigned_to': '-assigned_to',
    }
    if sort_by in valid_sorts:
        completed_schedules = completed_schedules.order_by(valid_sorts[sort_by])
    else:
        completed_schedules = completed_schedules.order_by('-completed_date')
    
    # Calculate statistics
    total_completed = completed_schedules.count()
    
    # Get all completed schedules for overall stats (before filtering)
    all_completed = Schedule.objects.filter(status='completed', completed_date__isnull=False)
    
    # Time-based stats
    now = timezone.now()
    last_7_days = all_completed.filter(completed_date__gte=now - timedelta(days=7)).count()
    last_30_days = all_completed.filter(completed_date__gte=now - timedelta(days=30)).count()
    this_year = all_completed.filter(completed_date__year=now.year).count()
    
    # Auto vs Manual
    auto_generated_count = all_completed.filter(auto_generated=True).count()
    manual_count = all_completed.filter(auto_generated=False).count()
    
    # Get unique assigned users
    assigned_users = all_completed.exclude(assigned_to='').values_list('assigned_to', flat=True).distinct().order_by('assigned_to')
    
    # Monthly breakdown for the last 6 months
    six_months_ago = now - timedelta(days=180)
    monthly_stats = all_completed.filter(
        completed_date__gte=six_months_ago
    ).annotate(
        month=TruncMonth('completed_date')
    ).values('month').annotate(
        count=Count('id')
    ).order_by('month')
    
    # Get most active groups
    from django.db.models import Count
    top_groups = StockTakeGroup.objects.filter(
        schedule__status='completed',
        schedule__completed_date__isnull=False
    ).annotate(
        completion_count=Count('schedule')
    ).order_by('-completion_count')[:5]
    
    context = {
        'completed_schedules': completed_schedules,
        'total_completed': total_completed,
        'last_7_days': last_7_days,
        'last_30_days': last_30_days,
        'this_year': this_year,
        'auto_generated_count': auto_generated_count,
        'manual_count': manual_count,
        'assigned_users': assigned_users,
        'monthly_stats': list(monthly_stats),
        'top_groups': top_groups,
        # Filter values for form persistence
        'search_query': search_query,
        'date_from': date_from,
        'date_to': date_to,
        'assigned_to_filter': assigned_to,
        'auto_generated_filter': auto_generated,
        'current_sort': sort_by,
    }
    
    return render(request, 'stock_take/completed_stock_takes.html', context)

@login_required
def map_view(request):
    """Display a map with order locations using Leaflet and OpenStreetMap"""
    # Get all orders with addresses for mapping
    orders_with_addresses = Order.objects.exclude(address__isnull=True).exclude(address='').order_by('-order_date')
    all_orders = Order.objects.all().order_by('-order_date')

    # Calculate statistics
    completed_count = all_orders.filter(job_finished=True).count()
    in_progress_count = all_orders.filter(job_finished=False).count()

    return render(request, 'stock_take/map.html', {
        'orders': orders_with_addresses,
        'orders_with_addresses': orders_with_addresses,
        'completed_count': completed_count,
        'in_progress_count': in_progress_count,
    })

def import_csv(request):
    """Import CSV file and update/add items, keep categories/groups"""
    if request.method == 'POST':
        csv_file = request.FILES.get('csv_file')
        if not csv_file or not csv_file.name.endswith('.csv'):
            messages.error(request, 'Please upload a CSV file.')
            return redirect('stock_list')

        try:
            # Read CSV file
            data_set = csv_file.read().decode('UTF-8')
            io_string = io.StringIO(data_set)
            reader = csv.DictReader(io_string)

            items_created = 0
            items_updated = 0
            csv_skus = set()  # Track SKUs from CSV for orphan detection
            
            for row in reader:
                sku = row.get('Sku', '').strip()
                name = row.get('Name', '').strip()
                cost_str = row.get('Cost', '0')
                location = row.get('Location', '').strip()
                quantity_str = row.get('Quantity', '0')
                serial_or_batch = row.get('SerialOrBatch', '').strip()
                category_name = row.get('Category', '').strip()

                # Add SKU to tracking set
                if sku:
                    csv_skus.add(sku)

                try:
                    quantity = int(float(quantity_str)) if quantity_str else 0
                except (ValueError, TypeError):
                    quantity = 0

                try:
                    cost = float(cost_str) if cost_str else 0
                except (ValueError, TypeError):
                    cost = 0

                # Find or create category
                category = None
                if category_name:
                    category, _ = Category.objects.get_or_create(
                        name=category_name,
                        defaults={'description': f'Auto-created from CSV import'}
                    )

                # Update existing item or create new
                item, created = StockItem.objects.get_or_create(
                    sku=sku,
                    defaults={
                        'name': name,
                        'cost': cost,
                        'category': category,
                        'category_name': category_name,
                        'location': location,
                        'quantity': quantity,
                        'serial_or_batch': serial_or_batch,
                        'tracking_type': 'not-classified'  # Default for new items
                    }
                )
                if not created:
                    # Update fields but preserve tracking_type
                    item.name = name
                    item.cost = cost
                    item.category = category
                    item.category_name = category_name
                    item.location = location
                    item.quantity = quantity  # Always update quantity on re-import
                    item.serial_or_batch = serial_or_batch
                    # Note: tracking_type is NOT updated on re-import to preserve user settings
                    item.save()
                    items_updated += 1
                else:
                    items_created += 1

            # Find items in DB that are NOT in the CSV (orphaned items)
            orphaned_items = StockItem.objects.exclude(sku__in=csv_skus)
            orphaned_count = orphaned_items.count()
            
            # Record import history
            ImportHistory.objects.create(
                filename=csv_file.name,
                record_count=items_created + items_updated
            )
            
            # Clear cache after import
            from django.core.cache import cache
            cache.clear()

            # Reset pending schedules and re-populate
            reset_and_populate_schedules()

            if orphaned_count > 0:
                # Store orphaned items in session for review
                request.session['orphaned_item_ids'] = list(orphaned_items.values_list('id', flat=True))
                request.session['last_import_file'] = csv_file.name
                messages.warning(request, f'Imported {items_created} new items, updated {items_updated} items. {orphaned_count} items in database not found in CSV.')
                return redirect('review_orphaned_items')
            else:
                messages.success(request, f'Imported {items_created} new items, updated {items_updated} items from {csv_file.name}')
        except Exception as e:
            messages.error(request, f'Error importing CSV: {str(e)}')

    return redirect('stock_list')

def review_orphaned_items(request):
    """Review items in DB that were not in the last CSV import"""
    orphaned_item_ids = request.session.get('orphaned_item_ids', [])
    last_import_file = request.session.get('last_import_file', 'Unknown')
    
    if not orphaned_item_ids:
        messages.info(request, 'No orphaned items to review.')
        return redirect('stock_list')
    
    if request.method == 'POST':
        # Handle deletion of selected items
        items_to_delete = request.POST.getlist('delete_items')
        if items_to_delete:
            deleted_count = StockItem.objects.filter(id__in=items_to_delete).delete()[0]
            messages.success(request, f'Deleted {deleted_count} item(s)')
            
            # Clear cache after deletion
            from django.core.cache import cache
            cache.clear()
        
        # Clear session
        request.session.pop('orphaned_item_ids', None)
        request.session.pop('last_import_file', None)
        
        return redirect('stock_list')
    
    orphaned_items = StockItem.objects.filter(id__in=orphaned_item_ids).select_related('category')
    
    return render(request, 'stock_take/review_orphaned_items.html', {
        'orphaned_items': orphaned_items,
        'last_import_file': last_import_file,
        'orphaned_count': len(orphaned_item_ids),
    })

def category_edit(request, category_id):
    category = get_object_or_404(Category, id=category_id)
    if request.method == 'POST':
        category.name = request.POST.get('name', category.name)
        category.description = request.POST.get('description', category.description)
        category.color = request.POST.get('color', category.color)
        category.save()
        messages.success(request, 'Category updated successfully.')
        return redirect('category_list')
    # For GET, this view is only used by the modal form, so redirect
    return redirect('category_list')

def import_history(request):
    """Display import history with analytics and filtering"""
    from datetime import timedelta
    from django.db.models import Sum, Count, Q
    from django.db.models.functions import TruncMonth
    
    # Base queryset
    imports = ImportHistory.objects.all()
    
    # Get filter parameters
    search_query = request.GET.get('search', '').strip()
    date_from = request.GET.get('date_from', '')
    date_to = request.GET.get('date_to', '')
    sort_by = request.GET.get('sort', '-imported_at')
    
    # Apply search filter
    if search_query:
        imports = imports.filter(filename__icontains=search_query)
    
    # Apply date filters
    if date_from:
        try:
            from_date = timezone.datetime.strptime(date_from, '%Y-%m-%d').replace(tzinfo=timezone.get_current_timezone())
            imports = imports.filter(imported_at__gte=from_date)
        except ValueError:
            pass
    
    if date_to:
        try:
            to_date = timezone.datetime.strptime(date_to, '%Y-%m-%d').replace(tzinfo=timezone.get_current_timezone())
            to_date = to_date.replace(hour=23, minute=59, second=59)
            imports = imports.filter(imported_at__lte=to_date)
        except ValueError:
            pass
    
    # Apply sorting
    valid_sorts = {
        'imported_at': 'imported_at',
        '-imported_at': '-imported_at',
        'filename': 'filename',
        '-filename': '-filename',
        'record_count': 'record_count',
        '-record_count': '-record_count',
    }
    if sort_by in valid_sorts:
        imports = imports.order_by(valid_sorts[sort_by])
    else:
        imports = imports.order_by('-imported_at')
    
    # Calculate statistics
    total_imports = imports.count()
    total_records = imports.aggregate(Sum('record_count'))['record_count__sum'] or 0
    
    # Get all imports for overall stats (before filtering)
    all_imports = ImportHistory.objects.all()
    
    # Time-based stats
    now = timezone.now()
    last_7_days = all_imports.filter(imported_at__gte=now - timedelta(days=7)).count()
    last_30_days = all_imports.filter(imported_at__gte=now - timedelta(days=30)).count()
    
    # Average records per import
    avg_records = all_imports.aggregate(models.Avg('record_count'))['record_count__avg'] or 0
    
    context = {
        'imports': imports,
        'total_imports': total_imports,
        'total_records': total_records,
        'last_7_days': last_7_days,
        'last_30_days': last_30_days,
        'avg_records': round(avg_records, 0),
        # Filter values for form persistence
        'search_query': search_query,
        'date_from': date_from,
        'date_to': date_to,
        'current_sort': sort_by,
    }
    
    return render(request, 'stock_take/import_history.html', context)

def reset_and_populate_schedules():
    """Reset pending schedules, schedule high priority first, and remove completed jobs from pending if completed in last month."""
    # Remove all pending auto-generated schedules
    Schedule.objects.filter(status='pending', auto_generated=True).delete()

    # Remove completed jobs from pending if completed in last month
    one_month_ago = timezone.now() - datetime.timedelta(days=30)
    recent_completed = Schedule.objects.filter(
        status='completed', 
        completed_date__isnull=False,
        completed_date__gte=one_month_ago
    )
    # Remove any pending schedule for the same group as a recently completed one
    for completed in recent_completed:
        for group in completed.stock_take_groups.all():
            Schedule.objects.filter(status='pending', stock_take_groups=group, auto_generated=True).delete()

    # Schedule new auto jobs, prioritizing groups that haven't been completed recently
    # Groups are ordered by: 1) Never completed or completed >1 month ago, 2) High weighting first
    all_groups = StockTakeGroup.objects.all()
    
    # Separate groups into those recently completed vs not recently completed
    never_or_old_completed = []
    recently_completed_expired = []
    
    for group in all_groups:
        # Check if completed in last 60 days (wider window to determine "recently completed")
        sixty_days_ago = timezone.now() - datetime.timedelta(days=60)
        has_recent_completion = Schedule.objects.filter(
            stock_take_groups=group,
            status='completed',
            completed_date__isnull=False,
            completed_date__gte=sixty_days_ago
        ).exists()
        
        if has_recent_completion:
            recently_completed_expired.append(group)
        else:
            never_or_old_completed.append(group)
    
    # Sort each group by priority (weighting)
    never_or_old_completed.sort(key=lambda g: (-g.weighting, g.name))
    recently_completed_expired.sort(key=lambda g: (-g.weighting, g.name))
    
    # Combine: never/old completed first, then recently completed (back of queue)
    groups = never_or_old_completed + recently_completed_expired
    scheduled_date = timezone.now() + datetime.timedelta(days=2)
    def next_weekday(dt):
        while dt.weekday() >= 5:
            dt += datetime.timedelta(days=1)
        return dt
    for group in groups:
        items_needing_check = group.items_needing_check
        
        # Check if this group has been completed within the last month
        # Include completed schedules without completion dates (legacy data) to be safe
        recently_completed = Schedule.objects.filter(
            stock_take_groups=group,
            status='completed',
            completed_date__isnull=False,
            completed_date__gte=one_month_ago
        ).exists()
        
        # Only create schedule if items need checking AND group hasn't been completed recently
        if items_needing_check.exists() and not recently_completed:
            items_list = list(items_needing_check.values_list('sku', 'name', 'quantity'))
            items_summary = ', '.join([f"{item[0]} ({item[2]} left)" for item in items_list[:5]])
            if len(items_list) > 5:
                items_summary += f" and {len(items_list) - 5} more items"
            scheduled_date = next_weekday(scheduled_date)
            schedule = Schedule.objects.create(
                name=f"Auto: {group.name} Stock Take",
                description=f"Auto-generated stock take for {group.name} - {items_needing_check.count()} items need checking",
                scheduled_date=scheduled_date,
                auto_generated=True,
                notes=f"Priority: {group.priority_label} | Threshold: {group.auto_schedule_threshold}\nItems needing check: {items_summary}",
                locations=', '.join(items_needing_check.values_list('location', flat=True).distinct())
            )
            schedule.stock_take_groups.add(group)
            scheduled_date += datetime.timedelta(days=1)

def auto_create_stock_take_schedules():
    """Auto-create schedules for stock take groups with items needing checking, remove obsolete ones"""

    def next_weekday(dt):
        # If dt is Saturday (5) or Sunday (6), move to Monday
        while dt.weekday() >= 5:
            dt += datetime.timedelta(days=1)
        return dt

    for group in StockTakeGroup.objects.all():
        items_needing_check = group.items_needing_check

        # Check if this group has been completed within the last month
        one_month_ago = timezone.now() - datetime.timedelta(days=30)
        recently_completed = Schedule.objects.filter(
            stock_take_groups=group,
            status='completed',
            completed_date__isnull=False,
            completed_date__gte=one_month_ago
        ).exists()

        # Remove auto-generated pending schedules if recently completed or no items need checking
        if recently_completed or not items_needing_check.exists():
            Schedule.objects.filter(
                stock_take_groups=group,
                status='pending',
                auto_generated=True
            ).delete()

        # Create new auto-generated schedule if needed and not already present and not recently completed
        if items_needing_check.exists() and not recently_completed:
            existing_schedule = Schedule.objects.filter(
                stock_take_groups=group,
                status='pending',
                auto_generated=True
            ).exists()

            if not existing_schedule:
                items_list = list(items_needing_check.values_list('sku', 'name', 'quantity'))
                items_summary = ', '.join([f"{item[0]} ({item[2]} left)" for item in items_list[:5]])
                if len(items_list) > 5:
                    items_summary += f" and {len(items_list) - 5} more items"

                # Schedule first stock take 2 days after import, skip weekends
                scheduled_date = timezone.now() + datetime.timedelta(days=2)
                scheduled_date = next_weekday(scheduled_date)

                schedule = Schedule.objects.create(
                    name=f"Auto: {group.name} Stock Take",
                    description=f"Auto-generated stock take for {group.name} - {items_needing_check.count()} items need checking",
                    scheduled_date=scheduled_date,
                    auto_generated=True,
                    notes=f"Priority: {group.priority_label} | Threshold: {group.auto_schedule_threshold}\nItems needing check: {items_summary}",
                    locations=', '.join(items_needing_check.values_list('location', flat=True).distinct())
                )
                schedule.stock_take_groups.add(group)

def stock_list(request):
    """Display all stock items in a table"""
    import time
    from django.core.cache import cache
    from django.db.models import Max
    
    start_time = time.time()
    
    # Create cache key based on filters and last modification time
    search = request.GET.get('search', '')
    category_filter = request.GET.get('category', '')
    stock_take_group_filter = request.GET.get('stock_take_group', '')
    tracking_type_filter = request.GET.get('tracking_type', '')
    
    # Get last modification time for cache invalidation
    last_import = ImportHistory.objects.aggregate(Max('imported_at'))['imported_at__max']
    last_update = StockItem.objects.aggregate(Max('id'))['id__max'] or 0
    cache_version = f"{last_import}_{last_update}"
    
    cache_key = f"stock_list_{search}_{category_filter}_{stock_take_group_filter}_{tracking_type_filter}_{cache_version}"
    
    # Try to get cached data
    cached_data = cache.get(cache_key)
    if cached_data and not request.GET.get('nocache'):
        cached_data['query_time'] = f"{time.time() - start_time:.3f} (cached)"
        return render(request, 'stock_take/stock_list.html', cached_data)
    
    # Don't auto-create schedules on every page load - too slow
    # auto_create_stock_take_schedules()
    
    # Use only() to limit fields loaded from database
    items = StockItem.objects.select_related('category', 'stock_take_group').only(
        'id', 'sku', 'name', 'cost', 'quantity', 'tracking_type', 
        'location', 'serial_or_batch', 'category__name', 'category__color',
        'stock_take_group__name'
    ).all()
    
    # Apply filters (already defined above for cache key)
    if search:
        items = items.filter(
            Q(sku__icontains=search) | 
            Q(name__icontains=search) |
            Q(serial_or_batch__icontains=search)
        )
    
    if category_filter:
        items = items.filter(category_id=category_filter)
    
    if stock_take_group_filter:
        items = items.filter(stock_take_group_id=stock_take_group_filter)
    
    if tracking_type_filter:
        items = items.filter(tracking_type=tracking_type_filter)
    
    latest_import = ImportHistory.objects.first()
    import_history = ImportHistory.objects.all()[:10]  # Last 10 imports
    
    # Calculate statistics - use count() to avoid loading all items
    all_items_query = StockItem.objects.all()
    
    # Separate items by stock status (exclude non-stock items from low/zero)
    in_stock_items = list(items.filter(
        quantity__gte=10
    ).exclude(tracking_type='non-stock').select_related('category', 'stock_take_group'))
    
    low_stock_items = list(items.filter(
        quantity__gte=1, 
        quantity__lt=10
    ).exclude(tracking_type='non-stock').select_related('category', 'stock_take_group'))
    
    zero_quantity_items = list(items.filter(
        quantity=0
    ).exclude(tracking_type='non-stock').select_related('category', 'stock_take_group'))
    
    # Non-stock items (separate tab)
    non_stock_items = list(items.filter(
        tracking_type='non-stock'
    ).select_related('category', 'stock_take_group'))
    
    # Calculate total value from the items we already have
    total_value = sum(item.cost * item.quantity for item in in_stock_items + low_stock_items + zero_quantity_items + non_stock_items)
    
    # Use counts from the lists we already have
    total_items_count = len(in_stock_items) + len(low_stock_items) + len(zero_quantity_items) + len(non_stock_items)
    zero_quantity_count = len(zero_quantity_items)
    low_stock_count = len(low_stock_items)
    in_stock_count = len(in_stock_items)
    non_stock_count = len(non_stock_items)
    
    # Items needing stock takes (convert to list for caching)
    items_needing_stock_take = list(items.filter(
        stock_take_group__isnull=False
    ).filter(
        Q(quantity__lte=F('stock_take_group__auto_schedule_threshold')) |
        Q(last_checked__lt=timezone.now() - timezone.timedelta(days=30))
    ).select_related('category', 'stock_take_group')[:100])  # Limit to 100
    
    # Get filter options and convert to list for caching
    categories = list(Category.objects.all())
    stock_take_groups = list(StockTakeGroup.objects.select_related('category').all())
    
    # Add performance timing
    end_time = time.time()
    query_time = end_time - start_time
    
    context = {
        'in_stock_items': in_stock_items,
        'low_stock_items': low_stock_items,
        'zero_quantity_items': zero_quantity_items,
        'non_stock_items': non_stock_items,
        'items_needing_stock_take': items_needing_stock_take,
        'latest_import': latest_import,
        'import_history': import_history,
        'total_value': total_value,
        'total_items_count': total_items_count,
        'zero_quantity_count': zero_quantity_count,
        'low_stock_count': low_stock_count,
        'in_stock_count': in_stock_count,
        'non_stock_count': non_stock_count,
        'categories': categories,
        'stock_take_groups': stock_take_groups,
        'query_time': f'{query_time:.3f}',
        'current_filters': {
            'search': search,
            'category': category_filter,
            'stock_take_group': stock_take_group_filter,
            'tracking_type': tracking_type_filter,
        }
    }
    
    # Cache the context for next time
    cache.set(cache_key, context, timeout=60*60*24)  # Cache for 24 hours
    
    return render(request, 'stock_take/stock_list.html', context)

def category_list(request):
    """Display all categories with stock take groups"""
    categories = Category.objects.filter(parent=None).prefetch_related(
        'stock_take_groups__stock_items',
        'stockitem_set'
    ).annotate(
        item_count=models.Count('stockitem')
    ).order_by('name')

    # Add unassigned items for each category
    for category in categories:
        category.unassigned_items = category.stockitem_set.filter(stock_take_group__isnull=True)

    return render(request, 'stock_take/categories.html', {
        'categories': categories,
    })

def delete_category(request, category_id):
    """Delete a category and all related items/groups"""
    if request.method == 'POST':
        category = get_object_or_404(Category, id=category_id)
        # Delete all stock take groups and items in this category
        StockTakeGroup.objects.filter(category=category).delete()
        StockItem.objects.filter(category=category).delete()
        category.delete()
        return JsonResponse({'success': True})
    return JsonResponse({'success': False, 'error': 'Method not allowed'})

def delete_import(request, import_id):
    """Delete an import history record"""
    if request.method == 'POST':
        try:
            import_record = get_object_or_404(ImportHistory, id=import_id)
            filename = import_record.filename
            import_record.delete()
            messages.success(request, f'Import record for "{filename}" deleted successfully.')
            return JsonResponse({'success': True})
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})
    
    return JsonResponse({'success': False, 'error': 'Method not allowed'})

def delete_schedule(request, schedule_id):
    """Delete a stock take schedule"""
    if request.method == 'POST':
        try:
            schedule = get_object_or_404(Schedule, id=schedule_id)
            schedule.delete()
            return JsonResponse({'success': True})
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})
    return JsonResponse({'success': False, 'error': 'Method not allowed'})

def delete_substitution(request, substitution_id):
    """Delete a substitution"""
    if request.method == 'POST':
        try:
            substitution = get_object_or_404(Substitution, id=substitution_id)
            substitution.delete()
            return JsonResponse({'success': True})
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})
    return JsonResponse({'success': False, 'error': 'Method not allowed'})

def edit_substitution(request, substitution_id):
    """Edit a substitution"""
    substitution = get_object_or_404(Substitution, id=substitution_id)
    
    if request.method == 'POST':
        form = SubstitutionForm(request.POST, instance=substitution)
        if form.is_valid():
            form.save()
            messages.success(request, 'Substitution updated successfully.')
            return redirect('substitutions')
    else:
        form = SubstitutionForm(instance=substitution)
    
    return render(request, 'stock_take/edit_substitution.html', {
        'form': form,
        'substitution': substitution,
    })

def stock_take_group_create(request):
    """Create or update a stock take group"""
    if request.method == 'POST':
        try:
            group_id = request.POST.get('group_id')
            category_id = request.POST.get('category')
            
            if group_id:
                # Update existing group
                group = get_object_or_404(StockTakeGroup, id=group_id)
                group.name = request.POST.get('name')
                group.description = request.POST.get('description', '')
                group.weighting = int(request.POST.get('weighting', 2))
                group.color = request.POST.get('color', '#6c757d')
                group.auto_schedule_threshold = int(request.POST.get('threshold', 5))
                group.save()
                messages.success(request, 'Stock take group updated successfully!')
            else:
                # Create new group
                StockTakeGroup.objects.create(
                    name=request.POST.get('name'),
                    description=request.POST.get('description', ''),
                    category_id=category_id,
                    weighting=int(request.POST.get('weighting', 2)),
                    color=request.POST.get('color', '#6c757d'),
                    auto_schedule_threshold=int(request.POST.get('threshold', 5))
                )
                messages.success(request, 'Stock take group created successfully!')
        except Exception as e:
            messages.error(request, f'Error saving stock take group: {str(e)}')
    
    return redirect('category_list')

def schedule_update_status(request, schedule_id):
    """Update schedule status"""
    if request.method == 'POST':
        try:
            schedule = get_object_or_404(Schedule, id=schedule_id)
            new_status = request.POST.get('status')
            
            # If marking as completed, set the completed_date
            if new_status == 'completed' and schedule.status != 'completed':
                schedule.completed_date = timezone.now()
                schedule.status = new_status
                schedule.save()
                
                # Add a success message to verify it's working
                messages.success(request, f'Stock take "{schedule.name}" has been completed successfully!')
                
            # If changing from completed to another status, clear completed_date
            elif new_status != 'completed' and schedule.status == 'completed':
                schedule.completed_date = None
                schedule.status = new_status
                schedule.save()
            else:
                schedule.status = new_status
                schedule.save()
            
            return JsonResponse({
                'success': True, 
                'message': f'Schedule status updated to {new_status}',
                'completed_date': schedule.completed_date.isoformat() if schedule.completed_date else None
            })
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})
    
    return JsonResponse({'success': False, 'error': 'Method not allowed'})

def update_item(request, item_id):
    """Update a single stock item via AJAX"""
    if request.method == 'POST':
        try:
            item = StockItem.objects.get(id=item_id)
            
            item.sku = request.POST.get('sku', item.sku)
            item.name = request.POST.get('name', item.name)
            
            # Handle cost conversion
            cost_str = request.POST.get('cost', str(item.cost))
            try:
                cost_str = cost_str.replace('£', '').strip()
                item.cost = float(cost_str)
            except (ValueError, TypeError):
                item.cost = 0
            
            item.location = request.POST.get('location', item.location)
            
            # Handle quantity conversion
            quantity_str = request.POST.get('quantity', str(item.quantity))
            try:
                old_quantity = item.quantity
                item.quantity = int(float(quantity_str))
                
                # Update last_checked if quantity changed
                if old_quantity != item.quantity:
                    item.last_checked = timezone.now()
            except (ValueError, TypeError):
                item.quantity = 0
            
            item.serial_or_batch = request.POST.get('serial_or_batch', item.serial_or_batch)
            
            # Handle tracking_type if provided
            tracking_type = request.POST.get('tracking_type')
            if tracking_type and tracking_type in ['stock', 'non-stock', 'not-classified']:
                item.tracking_type = tracking_type
            
            item.save()
            
            # Clear cache after updating item
            from django.core.cache import cache
            cache.clear()
            
            # Auto-create schedules if needed
            auto_create_stock_take_schedules()
            
            return HttpResponse('Success')
        except Exception as e:
            return HttpResponse(f'Error: {str(e)}', status=400)
    
    return HttpResponse('Method not allowed', status=405)

def schedule_list(request):
    """Display all schedules grouped by status with priority sorting"""
    all_schedules = Schedule.objects.prefetch_related('stock_take_groups').all()
    
    # Sort by priority (weighting) and date
    pending_schedules = all_schedules.filter(status='pending').annotate(
        priority=models.Sum('stock_take_groups__weighting')
    ).order_by('-priority', 'scheduled_date')
    
    in_progress_schedules = all_schedules.filter(status='in_progress').order_by('scheduled_date')
    completed_schedules = all_schedules.filter(status='completed').order_by('-scheduled_date')
    
    # Get auto-generated schedules count
    auto_schedules_count = pending_schedules.filter(auto_generated=True).count()
    
    categories = Category.objects.filter(parent=None)
    stock_take_groups = StockTakeGroup.objects.select_related('category').all()
    
    context = {
        'schedules': all_schedules,
        'pending_schedules': pending_schedules,
        'in_progress_schedules': in_progress_schedules,
        'completed_schedules': completed_schedules,
        'pending_count': pending_schedules.count(),
        'in_progress_count': in_progress_schedules.count(),
        'completed_count': completed_schedules.count(),
        'auto_schedules_count': auto_schedules_count,
        'categories': categories,
        'stock_take_groups': stock_take_groups,
    }
    
    return render(request, 'stock_take/schedules.html', context)

def schedule_create(request):
    """Create a new schedule"""
    if request.method == 'POST':
        try:
            schedule = Schedule.objects.create(
                name=request.POST.get('name'),
                description=request.POST.get('description', ''),
                locations=request.POST.get('locations', ''),
                scheduled_date=request.POST.get('scheduled_date'),
                assigned_to=request.POST.get('assigned_to', ''),
                notes=request.POST.get('notes', '')
            )
            
            # Add stock take groups instead of categories
            group_ids = request.POST.getlist('stock_take_groups')
            if group_ids:
                schedule.stock_take_groups.set(group_ids)
            
            messages.success(request, 'Schedule created successfully!')
        except Exception as e:
            messages.error(request, f'Error creating schedule: {str(e)}')
    
    return redirect('schedule_list')

def schedule_edit(request, schedule_id):
    """Edit an existing schedule"""
    if request.method == 'POST':
        try:
            schedule = get_object_or_404(Schedule, id=schedule_id)
            
            # Only allow editing of certain fields
            if 'scheduled_date' in request.POST:
                schedule.scheduled_date = request.POST.get('scheduled_date')
            if 'name' in request.POST:
                schedule.name = request.POST.get('name')
            if 'description' in request.POST:
                schedule.description = request.POST.get('description', '')
            if 'locations' in request.POST:
                schedule.locations = request.POST.get('locations', '')
            if 'assigned_to' in request.POST:
                schedule.assigned_to = request.POST.get('assigned_to', '')
            if 'notes' in request.POST:
                schedule.notes = request.POST.get('notes', '')
            
            schedule.save()
            
            # Update stock take groups if provided
            if 'stock_take_groups' in request.POST:
                group_ids = request.POST.getlist('stock_take_groups')
                schedule.stock_take_groups.set(group_ids)
            
            return JsonResponse({'success': True, 'message': 'Schedule updated successfully!'})
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})
    
    return JsonResponse({'success': False, 'error': 'Method not allowed'})

def get_unassigned_items(request):
    """Get items available for assignment to stock take groups"""
    if request.method == 'GET':
        category_id = request.GET.get('category_id')
        group_id = request.GET.get('group_id')

        category = None
        if category_id:
            category = get_object_or_404(Category, id=category_id)
        elif group_id:
            group = get_object_or_404(StockTakeGroup, id=group_id)
            category = group.category

        if group_id:
            # Getting items to assign TO a specific group
            items = StockItem.objects.filter(
                category=category,
                stock_take_group__isnull=True
            ).select_related('category')
        elif category_id:
            # Getting items from category to assign to any group
            items = StockItem.objects.filter(
                category_id=category_id,
                stock_take_group__isnull=True
            ).select_related('category')
        else:
            items = StockItem.objects.none()

        html = render_to_string('stock_take/assignment_content.html', {
            'items': items,
            'category': category,
            'group_id': group_id,
            'category_id': category_id,
        })

        return JsonResponse({'html': html})
    return JsonResponse({'error': 'Method not allowed'})

def assign_item_to_group(request):
    """Assign item to stock take group via AJAX"""
    if request.method == 'POST':
        try:
            item_id = request.POST.get('item_id')
            group_id = request.POST.get('group_id')
            
            item = get_object_or_404(StockItem, id=item_id)
            
            if group_id:
                group = get_object_or_404(StockTakeGroup, id=group_id)
                item.stock_take_group = group
            else:
                item.stock_take_group = None
            
            item.save()
            return JsonResponse({'success': True})
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})
    
    return JsonResponse({'success': False, 'error': 'Method not allowed'})

    """Create or update a stock take group"""
    if request.method == 'POST':
        try:
            group_id = request.POST.get('group_id')
            category_id = request.POST.get('category')
            
            category = get_object_or_404(Category, id=category_id)
            
            if group_id:
                # Update existing group
                group = get_object_or_404(StockTakeGroup, id=group_id)
                group.name = request.POST.get('name')
                group.description = request.POST.get('description', '')
                group.color = request.POST.get('color', '#6c757d')
                group.weighting = int(request.POST.get('weighting', 2))
                group.auto_schedule_threshold = int(request.POST.get('threshold', 5))
                group.save()
                messages.success(request, 'Stock take group updated successfully!')
            else:
                # Create new group
                StockTakeGroup.objects.create(
                    name=request.POST.get('name'),
                    description=request.POST.get('description', ''),
                    category=category,
                    color=request.POST.get('color', '#6c757d'),
                    weighting=int(request.POST.get('weighting', 2)),
                    auto_schedule_threshold=int(request.POST.get('threshold', 5))
                )
                messages.success(request, 'Stock take group created successfully!')
        except Exception as e:
            messages.error(request, f'Error saving stock take group: {str(e)}')
    
    return redirect('category_list')

def export_csv(request):
    """Export current database as CSV with required columns only"""
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="stock_export_{timezone.now().strftime("%Y%m%d_%H%M%S")}.csv"'

    writer = csv.writer(response)
    writer.writerow(['Sku', 'Name', 'Cost', 'Category', 'Location', 'Quantity', 'SerialOrBatch'])

    for item in StockItem.objects.all():
        writer.writerow([
            item.sku,
            item.name,
            item.cost,
            item.category.name if item.category else item.category_name,
            item.location,
            item.quantity,
            item.serial_or_batch or ''
        ])

    return response

def category_create(request):
    """Create or update a category"""
    if request.method == 'POST':
        try:
            category_id = request.POST.get('category_id')
            parent_id = request.POST.get('parent')
            
            if category_id:
                # Update existing category
                category = get_object_or_404(Category, id=category_id)
                category.name = request.POST.get('name')
                category.description = request.POST.get('description', '')
                category.color = request.POST.get('color', '#6c757d')
                if parent_id:
                    category.parent_id = parent_id
                category.save()
                messages.success(request, 'Category updated successfully!')
            else:
                # Create new category
                category_data = {
                    'name': request.POST.get('name'),
                    'description': request.POST.get('description', ''),
                    'color': request.POST.get('color', '#6c757d')
                }
                if parent_id:
                    category_data['parent_id'] = parent_id
                
                Category.objects.create(**category_data)
                messages.success(request, 'Category created successfully!')
        except Exception as e:
            messages.error(request, f'Error saving category: {str(e)}')
    
    return redirect('category_list')

def category_delete(request, category_id):
    """Delete a category"""
    if request.method == 'POST':
        try:
            category = get_object_or_404(Category, id=category_id)
            category.delete()
            messages.success(request, 'Category deleted successfully!')
        except Exception as e:
            messages.error(request, f'Error deleting category: {str(e)}')
    
    return redirect('category_list')


    """Update schedule status"""
    if request.method == 'POST':
        try:
            schedule = get_object_or_404(Schedule, id=schedule_id)
            schedule.status = request.POST.get('status')
            schedule.save()
            
            return JsonResponse({'success': True})
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})
    
    return JsonResponse({'success': False, 'error': 'Method not allowed'})

def stock_take_detail(request, schedule_id):
    """Detailed view for conducting a stock take"""
    schedule = get_object_or_404(Schedule, id=schedule_id)
    
    # Get all items in the stock take groups for this schedule
    items = StockItem.objects.filter(
        stock_take_group__in=schedule.stock_take_groups.all()
    ).select_related('category', 'stock_take_group').order_by('stock_take_group__name', 'sku')
    
    # Group items by stock take group
    items_by_group = {}
    for item in items:
        group = item.stock_take_group
        if group not in items_by_group:
            items_by_group[group] = []
        items_by_group[group].append(item)
    
    return render(request, 'stock_take/stock_take_detail.html', {
        'schedule': schedule,
        'items_by_group': items_by_group,
        'total_items': items.count(),
    })

def update_stock_count(request):
    """Update stock count during stock take via AJAX"""
    if request.method == 'POST':
        try:
            item_id = request.POST.get('item_id')
            counted_quantity = int(request.POST.get('counted_quantity', 0))
            notes = request.POST.get('notes', '')
            
            item = get_object_or_404(StockItem, id=item_id)
            old_quantity = item.quantity
            
            # Update the item
            item.quantity = counted_quantity
            item.last_checked = timezone.now()
            item.save()
            
            # Create a stock adjustment record (we'll add this model later)
            # For now, just return success
            
            variance = counted_quantity - old_quantity
            
            return JsonResponse({
                'success': True, 
                'old_quantity': old_quantity,
                'new_quantity': counted_quantity,
                'variance': variance
            })
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})
    
    return JsonResponse({'success': False, 'error': 'Method not allowed'})

def export_stock_take_csv(request, schedule_id):
    """Export CSV for specific stock take schedule with required columns only"""
    schedule = get_object_or_404(Schedule, id=schedule_id)
    
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="stock_take_{schedule.name}_{timezone.now().strftime("%Y%m%d_%H%M%S")}.csv"'
    
    writer = csv.writer(response)
    writer.writerow(['Sku', 'Name', 'Cost', 'Category', 'Location', 'Quantity', 'SerialOrBatch'])

    items = StockItem.objects.filter(
        stock_take_group__in=schedule.stock_take_groups.all()
    ).select_related('category', 'stock_take_group')

    for item in items:
        writer.writerow([
            item.sku,
            item.name,
            item.cost,
            item.category.name if item.category else item.category_name,
            item.location,
            item.quantity,
            item.serial_or_batch or ''
        ])
    
    return response

def delete_stock_take_group(request, group_id):
    """Delete a stock take group"""
    if request.method == 'POST':
        try:
            group = get_object_or_404(StockTakeGroup, id=group_id)
            group.delete()
            return JsonResponse({'success': True})
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})
    return JsonResponse({'success': False, 'error': 'Method not allowed'})


@login_required
def upload_accessories_csv(request):
    """Upload and process accessories CSV for an order - auto-detects order from filename"""
    import logging
    logging.basicConfig(filename='csv_upload.log', level=logging.DEBUG)
    logging.info(f"upload_accessories_csv called with method: {request.method}")
    
    if request.method == 'POST':
        csv_file = request.FILES.get('csv_file')
        logging.info(f"csv_file received: {csv_file}")
        
        if not csv_file:
            messages.error(request, 'No file uploaded.')
            return redirect('ordering')
        
        # Validate file extension
        if not csv_file.name.lower().endswith('.csv'):
            messages.error(request, 'Only .csv files are allowed.')
            return redirect('ordering')
        
        # Extract customer numbers from filename
        filename = csv_file.name
        import re
        # Find all sequences of digits in the filename
        number_matches = re.findall(r'\d+', filename)
        logging.info(f"filename: {filename}, number_matches: {number_matches}")
        
        potential_customer_numbers = []
        for match in number_matches:
            # Try different formats: 020483, 20483, etc.
            potential_customer_numbers.extend([
                match,  # 20483
                match.zfill(6),  # 020483 (pad to 6 digits)
                f"0{match}",  # 020483 (add leading zero)
            ])
        
        # Remove duplicates
        potential_customer_numbers = list(set(potential_customer_numbers))
        logging.info(f"potential_customer_numbers: {potential_customer_numbers}")
        
        # Find matching orders
        matching_orders = []
        for customer_num in potential_customer_numbers:
            orders = Order.objects.filter(customer_number=customer_num)
            matching_orders.extend(list(orders))
        
        # Remove duplicates
        matching_orders = list(set(matching_orders))
        logging.info(f"matching_orders count: {len(matching_orders)}")
        
        if not matching_orders:
            messages.error(request, f'No orders found matching customer numbers extracted from filename "{filename}". Extracted numbers: {", ".join(potential_customer_numbers)}')
            return redirect('ordering')
        elif len(matching_orders) > 1:
            # Multiple matches - show error with options
            order_list = [f"{order.customer_number} ({order.first_name} {order.last_name})" for order in matching_orders]
            messages.error(request, f'Multiple orders match the filename "{filename}". Please use a more specific filename. Matching orders: {", ".join(order_list)}')
            return redirect('ordering')
        
        # Single match - use this order
        order = matching_orders[0]
        logging.info(f"Using order: {order.customer_number} - {order.first_name} {order.last_name}")
        
        # Save the original CSV file to the order
        order.original_csv.save(f"{order.customer_number}_original_{filename}", csv_file)
        order.original_csv_uploaded_at = timezone.now()
        order.save()
        
        try:
            # Read CSV file content
            csv_file.seek(0)  # Reset file pointer
            file_content = csv_file.read().decode('utf-8')
            io_string = io.StringIO(file_content)
            
            # Detect delimiter
            sample = file_content[:1024]  # First 1KB should be enough to detect delimiter
            sniffer = csv.Sniffer()
            try:
                delimiter = sniffer.sniff(sample, delimiters=',\t;').delimiter
                logging.info(f"Detected CSV delimiter: '{delimiter}'")
            except csv.Error:
                # Try to detect manually by counting delimiters
                comma_count = sample.count(',')
                tab_count = sample.count('\t')
                semicolon_count = sample.count(';')
                
                if tab_count > comma_count and tab_count > semicolon_count:
                    delimiter = '\t'
                    logging.info("Manual detection: using tab delimiter")
                elif semicolon_count > comma_count:
                    delimiter = ';'
                    logging.info("Manual detection: using semicolon delimiter")
                else:
                    delimiter = ','
                    logging.info("Manual detection: using comma delimiter")
            
            reader = csv.DictReader(io_string, delimiter=delimiter)
            
            # Log detected fieldnames
            logging.info(f"CSV fieldnames: {reader.fieldnames}")
            
            accessories_created = 0
            accessories_updated = 0
            missing_items = 0
            substitutions_used = 0
            substitutions_created = 0
            os_doors_required = False
            rows_processed = 0
            processed_rows = []  # Store processed rows for the new CSV
            
            # Safe decimal conversion functions
            def safe_decimal(value, default='0'):
                try:
                    cleaned = str(value).strip() or default
                    return Decimal(cleaned)
                except (ValueError, TypeError, InvalidOperation):
                    return Decimal(default)
            
            for row in reader:
                rows_processed += 1
                logging.info(f"Processing row {rows_processed}: {row}")
                sku = row.get('Sku', '').strip()
                if not sku:
                    logging.info(f"Skipping row {rows_processed}: empty SKU")
                    continue
                
                # Check if this is an OS Door requirement indicator
                if 'DOR_VNL_OSD_MTM' in sku:
                    os_doors_required = True
                    continue  # Skip creating an accessory for this row
                
                # Store original row for processed CSV
                processed_row = row.copy()
                
                # Always set SellPrice to 0 in processed CSV
                processed_row['SellPrice'] = '0'
                
                # Parse billable field early (needed for substitution creation)
                billable_str = row.get('Billable', 'TRUE') or 'TRUE'
                billable = billable_str.strip().upper() == 'TRUE'
                
                # Check if accessory already exists for this order and SKU
                existing_accessory = Accessory.objects.filter(order=order, sku=sku).first()
                
                # Try to find matching stock item
                try:
                    stock_item = StockItem.objects.get(sku=sku)
                    missing = False
                    substituted = False
                except StockItem.DoesNotExist:
                    # Check for substitution
                    try:
                        substitution = Substitution.objects.get(missing_sku=sku)
                        replacement_sku = substitution.replacement_sku
                        if replacement_sku and replacement_sku.strip():
                            # Apply substitution immediately
                            processed_row['Sku'] = replacement_sku
                            processed_row['Name'] = substitution.replacement_name or substitution.missing_name
                            processed_row['Description'] = substitution.description or substitution.replacement_name or substitution.missing_name
                            if substitution.cost_price is not None:
                                processed_row['CostPrice'] = str(substitution.cost_price)
                            # Always set SellPrice to 0 for substituted items
                            processed_row['SellPrice'] = '0'
                            # Set quantity from substitution if available, otherwise check description or set default
                            if substitution.quantity is not None and substitution.quantity > 0:
                                processed_row['Quantity'] = str(substitution.quantity)
                            else:
                                # Check if original description is a number (like "8")
                                original_desc = row.get('Description', '').strip()
                                if original_desc and original_desc.isdigit():
                                    processed_row['Quantity'] = original_desc
                                elif not processed_row.get('Quantity') or processed_row.get('Quantity').strip() in ('', '0'):
                                    # If no valid quantity found, set to 1
                                    processed_row['Quantity'] = '1'
                            processed_row['Billable'] = 'FALSE'  # Always set to FALSE for substituted items
                            
                            # Try to find the replacement stock item
                            try:
                                stock_item = StockItem.objects.get(sku=replacement_sku)
                                missing = False
                                substituted = True
                                substitutions_used += 1
                            except StockItem.DoesNotExist:
                                # Substitution applied but replacement SKU not in stock
                                stock_item = None
                                missing = False  # Don't mark as missing since substitution was applied
                                substituted = True
                                substitutions_used += 1
                        else:
                            # Substitution exists but no replacement SKU
                            stock_item = None
                            missing = True
                            missing_items += 1
                    except Substitution.DoesNotExist:
                        # No substitution found, create one for manual handling
                        logging.info(f"Creating substitution for missing SKU: {sku}")
                        Substitution.objects.create(
                            missing_sku=sku,
                            missing_name=row.get('Name', '').strip() if row.get('Name') else '',
                            replacement_sku='',  # Empty, to be filled manually
                            replacement_name='',
                            description='Auto-created from CSV upload',
                            cost_price=safe_decimal(row.get('CostPrice', '0')),
                            sell_price=safe_decimal(row.get('SellPrice', '0')),
                            quantity=int(safe_decimal(row.get('Quantity', '0'))),
                            billable=billable
                        )
                        stock_item = None
                        missing = True
                        missing_items += 1
                        substitutions_created += 1
                        logging.info(f"Substitution created for {sku}, total created: {substitutions_created}")
                
                # Use processed_row data if substitution was applied
                final_sku = processed_row.get('Sku', sku)
                final_name = processed_row.get('Name', (row.get('Name') or '').strip())
                final_description = processed_row.get('Description', (row.get('Description') or '').strip())
                final_cost_price = safe_decimal(processed_row.get('CostPrice', row.get('CostPrice', '0')))
                final_sell_price = safe_decimal(processed_row.get('SellPrice', row.get('SellPrice', '0')))
                final_quantity = safe_decimal(processed_row.get('Quantity', row.get('Quantity', '0')))
                final_billable = processed_row.get('Billable', billable) == 'TRUE'

                if existing_accessory:
                    # Update existing accessory
                    existing_accessory.sku = final_sku
                    existing_accessory.name = final_name
                    existing_accessory.description = final_description
                    existing_accessory.cost_price = final_cost_price
                    existing_accessory.sell_price = final_sell_price
                    existing_accessory.quantity = final_quantity
                    existing_accessory.billable = final_billable
                    existing_accessory.stock_item = stock_item
                    existing_accessory.missing = missing
                    existing_accessory.save()
                    accessories_updated += 1
                else:
                    # Create new accessory
                    Accessory.objects.create(
                        order=order,
                        sku=final_sku,
                        name=final_name,
                        description=final_description,
                        cost_price=final_cost_price,
                        sell_price=final_sell_price,
                        quantity=final_quantity,
                        billable=final_billable,
                        stock_item=stock_item,
                        missing=missing
                    )
                    accessories_created += 1
                
                # Add processed row to the list (only for rows that weren't skipped)
                processed_rows.append(processed_row)
            
            # Update order with OS doors requirement
            if os_doors_required:
                order.os_doors_required = True
                order.save()
            
            # Set flag if there are missing items that need resolution
            if substitutions_created > 0:
                order.csv_has_missing_items = True
                order.save()
            
            # Create processed CSV with substitutions applied
            if processed_rows:
                processed_csv_content = io.StringIO()
                fieldnames = processed_rows[0].keys()
                writer = csv.DictWriter(processed_csv_content, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(processed_rows)
                
                # Save processed CSV to order
                processed_csv_content.seek(0)
                order.processed_csv.save(f"{order.customer_number}_processed_{filename}", 
                                       io.BytesIO(processed_csv_content.getvalue().encode('utf-8')))
            
            success_msg = f'Successfully processed accessories for order {order.sale_number} (auto-matched from filename "{filename}").'
            if accessories_created > 0:
                success_msg += f' {accessories_created} new accessories created.'
            if accessories_updated > 0:
                success_msg += f' {accessories_updated} existing accessories updated.'
            if substitutions_used > 0:
                success_msg += f' {substitutions_used} items substituted with replacements.'
            if substitutions_created > 0:
                success_msg += f' {substitutions_created} new substitutions created for manual review.'
            if missing_items > 0:
                success_msg += f' {missing_items} items still marked as missing.'
            if os_doors_required:
                success_msg += f' OS Doors marked as required.'
            
            messages.success(request, success_msg)
            
        except UnicodeDecodeError:
            logging.error(f"UnicodeDecodeError processing CSV file: {filename}")
            messages.error(request, 'Error reading CSV file. Please ensure it is saved as UTF-8 encoding.')
        except Exception as e:
            logging.error(f"Exception processing CSV file: {filename}, error: {str(e)}")
            import traceback
            logging.error(traceback.format_exc())
            messages.error(request, f'Error processing CSV file: {str(e)}')
    
    return redirect('order_details', order_id=order.id)


@login_required
def delete_accessory(request, accessory_id):
    """Delete a specific accessory"""
    if request.method == 'POST':
        accessory = get_object_or_404(Accessory, id=accessory_id)
        order = accessory.order
        accessory_name = accessory.name
        accessory.delete()
        
        # Regenerate processed CSV after deletion
        if order.accessories.exists():
            processed_rows = []
            for acc in order.accessories.all():
                row = {
                    'Sku': acc.sku,
                    'Name': acc.name,
                    'Description': acc.description,
                    'CostPrice': str(acc.cost_price),
                    'SellPrice': str(acc.sell_price),
                    'Quantity': str(acc.quantity),
                    'Billable': 'TRUE' if acc.billable else 'FALSE'
                }
                processed_rows.append(row)
            
            if processed_rows:
                processed_csv_content = io.StringIO()
                fieldnames = ['Sku', 'Name', 'Description', 'CostPrice', 'SellPrice', 'Quantity', 'Billable']
                writer = csv.DictWriter(processed_csv_content, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(processed_rows)
                
                # Save updated processed CSV
                filename = order.original_csv.name.split('_')[-1] if order.original_csv else 'accessories.csv'
                order.processed_csv.save(f"{order.customer_number}_processed_{filename}", 
                                       io.BytesIO(processed_csv_content.getvalue().encode('utf-8')))
        
        messages.success(request, f'Accessory "{accessory_name}" has been removed from order {order.sale_number} and CSV regenerated.')
        return redirect('order_details', order_id=order.id)
    return JsonResponse({'success': False, 'error': 'Method not allowed'})


@login_required
def update_os_doors_po(request, order_id):
    """Update OS Doors PO for an order"""
    if request.method == 'POST':
        order = get_object_or_404(Order, id=order_id)
        po_number = request.POST.get('os_doors_po', '').strip()
        order.os_doors_po = po_number
        order.save()
        messages.success(request, f'OS Doors PO updated for order {order.sale_number}.')
        return redirect('order_details', order_id=order_id)
    return JsonResponse({'success': False, 'error': 'Method not allowed'})


@login_required
def delete_all_accessories(request, order_id):
    """Delete all accessories for an order"""
    if request.method == 'POST':
        order = get_object_or_404(Order, id=order_id)
        deleted_count = order.accessories.count()
        order.accessories.all().delete()
        messages.success(request, f'All {deleted_count} accessories have been removed from order {order.sale_number}.')
        return redirect('order_details', order_id=order_id)
    return JsonResponse({'success': False, 'error': 'Method not allowed'})


@login_required
def search_stock_items(request):
    """Search stock items by SKU or name (AJAX endpoint)"""
    from django.db.models import Q
    
    query = request.GET.get('q', '').strip()
    
    if len(query) < 2:
        return JsonResponse({'results': []})
    
    # Search in both SKU and name fields
    stock_items = StockItem.objects.filter(
        Q(sku__icontains=query) | Q(name__icontains=query)
    ).order_by('sku')[:20]  # Limit to 20 results
    
    results = [
        {
            'id': item.id,
            'sku': item.sku,
            'name': item.name,
            'quantity': item.quantity,
        }
        for item in stock_items
    ]
    
    return JsonResponse({'results': results})


@login_required
def swap_accessory(request, accessory_id):
    """Swap an accessory item with a different stock item and regenerate CSV"""
    import json
    import csv
    import io
    from django.core.files.base import ContentFile
    
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            new_stock_item_id = data.get('new_stock_item_id')
            
            if not new_stock_item_id:
                return JsonResponse({'success': False, 'error': 'No stock item selected'})
            
            # Get the accessory and new stock item
            accessory = get_object_or_404(Accessory, id=accessory_id)
            new_stock_item = get_object_or_404(StockItem, id=new_stock_item_id)
            order = accessory.order
            
            # Update accessory with new stock item details
            old_sku = accessory.sku
            old_name = accessory.name
            
            accessory.sku = new_stock_item.sku
            accessory.name = new_stock_item.name
            accessory.cost_price = new_stock_item.cost
            accessory.stock_item = new_stock_item
            accessory.missing = False  # Item is no longer missing since we found a replacement
            accessory.save()
            
            # Regenerate processed CSV
            if order.original_csv:
                # Read original CSV
                order.original_csv.seek(0)
                file_content = order.original_csv.read().decode('utf-8')
                io_string = io.StringIO(file_content)
                
                # Detect delimiter
                sample = file_content[:1024]
                sniffer = csv.Sniffer()
                try:
                    delimiter = sniffer.sniff(sample, delimiters=',\t;').delimiter
                except csv.Error:
                    delimiter = ','
                
                io_string.seek(0)
                reader = csv.DictReader(io_string, delimiter=delimiter)
                
                # Create processed CSV with updated item
                output = io.StringIO()
                fieldnames = reader.fieldnames
                writer = csv.DictWriter(output, fieldnames=fieldnames, delimiter=delimiter)
                writer.writeheader()
                
                for row in reader:
                    # If this row matches the old SKU, replace with new SKU
                    if row.get('Sku', '').strip() == old_sku:
                        row['Sku'] = new_stock_item.sku
                        row['Name'] = new_stock_item.name
                        row['CostPrice'] = str(new_stock_item.cost)
                    
                    writer.writerow(row)
                
                # Save processed CSV
                csv_content = output.getvalue()
                filename = f"{order.customer_number}_processed_{order.original_csv.name.split('/')[-1]}"
                order.processed_csv.save(filename, ContentFile(csv_content.encode('utf-8')))
                order.processed_csv_created_at = timezone.now()
                order.save()
            
            return JsonResponse({
                'success': True,
                'message': f'Swapped {old_sku} to {new_stock_item.sku}. Processed CSV updated.'
            })
            
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})
    
    return JsonResponse({'success': False, 'error': 'Method not allowed'})


@login_required
def update_accessory_quantities(request):
    """Update quantities for multiple accessories and regenerate CSV"""
    import json
    import csv
    import io
    from django.core.files.base import ContentFile
    
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            changes = data.get('changes', {})
            
            if not changes:
                return JsonResponse({'success': False, 'error': 'No changes provided'})
            
            order = None
            # Update each accessory quantity
            for accessory_id, new_quantity in changes.items():
                accessory = get_object_or_404(Accessory, id=accessory_id)
                if order is None:
                    order = accessory.order
                accessory.quantity = new_quantity
                accessory.save()
            
            # Regenerate CSV if original exists
            csv_regenerated = False
            if order and order.original_csv:
                # Read original CSV
                order.original_csv.seek(0)
                file_content = order.original_csv.read().decode('utf-8')
                io_string = io.StringIO(file_content)
                
                # Detect delimiter
                sample = file_content[:1024]
                sniffer = csv.Sniffer()
                try:
                    delimiter = sniffer.sniff(sample, delimiters=',\t;').delimiter
                except csv.Error:
                    delimiter = ','
                
                io_string.seek(0)
                reader = csv.DictReader(io_string, delimiter=delimiter)
                
                # Create processed CSV with updated quantities
                output = io.StringIO()
                fieldnames = reader.fieldnames
                writer = csv.DictWriter(output, fieldnames=fieldnames, delimiter=delimiter)
                writer.writeheader()
                
                # Build a map of SKU to current quantity from accessories
                sku_quantities = {}
                for acc in order.accessories.all():
                    sku_quantities[acc.sku] = str(int(acc.quantity))
                
                for row in reader:
                    sku = row.get('Sku', '').strip()
                    if sku in sku_quantities:
                        row['Quantity'] = sku_quantities[sku]
                    writer.writerow(row)
                
                # Save processed CSV
                csv_content = output.getvalue()
                filename = f"{order.customer_number}_processed_{order.original_csv.name.split('/')[-1]}"
                order.processed_csv.save(filename, ContentFile(csv_content.encode('utf-8')))
                order.processed_csv_created_at = timezone.now()
                order.save()
                csv_regenerated = True
            
            return JsonResponse({
                'success': True,
                'message': f'Updated {len(changes)} accessory quantities',
                'csv_regenerated': csv_regenerated
            })
            
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})
    
    return JsonResponse({'success': False, 'error': 'Method not allowed'})


@login_required
def add_accessory_item(request, order_id):
    """Add a new accessory item to an order"""
    import json
    
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            stock_item_id = data.get('stock_item_id')
            quantity = data.get('quantity', 1)
            
            if not stock_item_id:
                return JsonResponse({'success': False, 'error': 'No stock item selected'})
            
            order = get_object_or_404(Order, id=order_id)
            stock_item = get_object_or_404(StockItem, id=stock_item_id)
            
            # Create new accessory
            accessory = Accessory.objects.create(
                order=order,
                sku=stock_item.sku,
                name=stock_item.name,
                description='',
                cost_price=stock_item.cost,
                sell_price=stock_item.cost,  # Default to cost price
                quantity=quantity,
                billable=True,
                stock_item=stock_item,
                missing=False
            )
            
            return JsonResponse({
                'success': True,
                'message': f'Added {quantity}x {stock_item.sku} to order'
            })
            
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})
    
    return JsonResponse({'success': False, 'error': 'Method not allowed'})


@login_required
def regenerate_csv(request, order_id):
    """Regenerate the processed CSV from current accessory data"""
    import csv
    import io
    from django.core.files.base import ContentFile
    
    if request.method == 'POST':
        try:
            order = get_object_or_404(Order, id=order_id)
            
            if not order.original_csv:
                return JsonResponse({'success': False, 'error': 'No original CSV found'})
            
            # Read original CSV
            order.original_csv.seek(0)
            file_content = order.original_csv.read().decode('utf-8')
            io_string = io.StringIO(file_content)
            
            # Detect delimiter
            sample = file_content[:1024]
            sniffer = csv.Sniffer()
            try:
                delimiter = sniffer.sniff(sample, delimiters=',\t;').delimiter
            except csv.Error:
                delimiter = ','
            
            io_string.seek(0)
            reader = csv.DictReader(io_string, delimiter=delimiter)
            
            # Create processed CSV with current accessory data
            output = io.StringIO()
            fieldnames = reader.fieldnames
            writer = csv.DictWriter(output, fieldnames=fieldnames, delimiter=delimiter)
            writer.writeheader()
            
            # Build a map of SKU to current accessory data
            sku_data = {}
            processed_skus = set()  # Track which SKUs we've written
            
            for acc in order.accessories.all():
                sku_data[acc.sku] = {
                    'sku': acc.sku,
                    'name': acc.name,
                    'description': acc.description or '',
                    'quantity': str(int(acc.quantity)),
                    'cost': str(acc.cost_price),
                    'sell': str(acc.sell_price),
                    'billable': 'True' if acc.billable else 'False'
                }
            
            # Update existing rows
            for row in reader:
                sku = row.get('Sku', '').strip()
                if sku in sku_data:
                    # Update with current data
                    row['Name'] = sku_data[sku]['name']
                    row['Description'] = sku_data[sku]['description']
                    row['Quantity'] = sku_data[sku]['quantity']
                    row['CostPrice'] = sku_data[sku]['cost']
                    row['SellPrice'] = sku_data[sku]['sell']
                    row['Billable'] = sku_data[sku]['billable']
                    processed_skus.add(sku)
                writer.writerow(row)
            
            # Add new items that weren't in the original CSV
            for sku, data in sku_data.items():
                if sku not in processed_skus:
                    new_row = {
                        'Sku': data['sku'],
                        'Name': data['name'],
                        'Description': data['description'],
                        'CostPrice': data['cost'],
                        'SellPrice': data['sell'],
                        'Quantity': data['quantity'],
                        'Billable': data['billable']
                    }
                    writer.writerow(new_row)
            
            # Save processed CSV
            csv_content = output.getvalue()
            filename = f"{order.customer_number}_processed_{order.original_csv.name.split('/')[-1]}"
            order.processed_csv.save(filename, ContentFile(csv_content.encode('utf-8')))
            order.processed_csv_created_at = timezone.now()
            order.save()
            
            return JsonResponse({
                'success': True,
                'message': 'Processed CSV regenerated successfully'
            })
            
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})
    
    return JsonResponse({'success': False, 'error': 'Method not allowed'})


@login_required
def remove_order_csv(request, order_id, csv_type):
    """Remove original or processed CSV file from an order"""
    if request.method == 'POST':
        order = get_object_or_404(Order, id=order_id)
        
        if csv_type == 'original' and order.original_csv:
            order.original_csv.delete()
            messages.success(request, f'Original CSV file has been removed from order {order.sale_number}.')
        elif csv_type == 'processed' and order.processed_csv:
            order.processed_csv.delete()
            messages.success(request, f'Processed CSV file has been removed from order {order.sale_number}.')
        else:
            messages.error(request, f'No {csv_type} CSV file found for this order.')
        
        return redirect('order_details', order_id=order_id)
    return JsonResponse({'success': False, 'error': 'Method not allowed'})


@login_required
def resolve_missing_items(request, order_id):
    """Resolve missing items by applying substitutions from the substitution table"""
    if request.method == 'POST':
        order = get_object_or_404(Order, id=order_id)
        
        # Get all missing accessories
        missing_accessories = order.accessories.filter(missing=True)
        logging.info(f"Found {missing_accessories.count()} missing accessories")
        for acc in missing_accessories:
            logging.info(f"Missing accessory: {acc.sku} - {acc.name}")
        
        # Get all substitutions that could apply to missing items
        substitutions = Substitution.objects.filter(
            missing_sku__in=[acc.sku for acc in missing_accessories],
            replacement_sku__isnull=False
        ).exclude(replacement_sku='')
        
        logging.info(f"Found {len(substitutions)} applicable substitutions")
        for sub in substitutions:
            logging.info(f"Substitution: {sub.missing_sku} -> {sub.replacement_sku}")
        
        resolved_count = 0
        
        for substitution in substitutions:
            # Find accessories with this missing SKU
            accessories = order.accessories.filter(sku=substitution.missing_sku, missing=True)
            logging.info(f"Found {accessories.count()} accessories to update for {substitution.missing_sku}")
            
            for accessory in accessories:
                replacement_stock_item = None  # Initialize to None
                
                # Try to find the replacement stock item
                try:
                    replacement_stock_item = StockItem.objects.get(sku=substitution.replacement_sku)
                    logging.info(f"Found replacement stock item: {replacement_stock_item.name} (cost: {replacement_stock_item.cost})")
                    
                    # Use data from stock item
                    replacement_name = replacement_stock_item.name
                    replacement_cost = replacement_stock_item.cost
                    replacement_description = replacement_stock_item.name
                    
                except StockItem.DoesNotExist:
                    logging.warning(f"Replacement stock item not found: {substitution.replacement_sku}, using substitution data only")
                    # Use data from substitution object
                    replacement_name = substitution.replacement_name or substitution.missing_name
                    replacement_cost = substitution.cost_price or 0
                    replacement_description = substitution.description or substitution.replacement_name or substitution.missing_name
                
                logging.info(f"Resolving accessory {accessory.sku} -> {substitution.replacement_sku}")
                logging.info(f"Before update: name={accessory.name}, desc={accessory.description}, cost={accessory.cost_price}, sell={accessory.sell_price}, qty={accessory.quantity}, billable={accessory.billable}")
                
                # Handle quantity first - check original description before overwriting
                original_description = accessory.description
                if original_description and original_description.strip().isdigit():
                    # If original description is just a number, use it as quantity
                    accessory.quantity = Decimal(int(original_description.strip()))
                    logging.info(f"Setting quantity to {accessory.quantity} from original description")
                elif substitution.quantity is not None and substitution.quantity > 0:
                    # Use quantity from substitution if available
                    accessory.quantity = Decimal(substitution.quantity)
                    logging.info(f"Setting quantity to {substitution.quantity} from substitution")
                else:
                    # Default to 1 if no quantity found
                    accessory.quantity = Decimal(1)
                    logging.info("Setting quantity to default 1")
                
                # Update the accessory with substitution data
                accessory.sku = substitution.replacement_sku
                accessory.name = replacement_name
                accessory.description = replacement_description
                accessory.stock_item = replacement_stock_item
                accessory.missing = False
                
                # Use cost price from replacement data
                accessory.cost_price = replacement_cost
                logging.info(f"Setting cost_price to {replacement_cost}")
                
                # Set sell price to 0 as requested
                accessory.sell_price = 0
                
                # Set billable to false as requested
                accessory.billable = False
                
                logging.info(f"After update: name={accessory.name}, desc={accessory.description}, cost={accessory.cost_price}, sell={accessory.sell_price}, qty={accessory.quantity}, billable={accessory.billable}")
                
                accessory.save()
                resolved_count += 1
        
        # Also remove items marked for skipping (both order-specific and global)
        skip_items_removed = 0
        
        # Remove order-specific skip items
        for skip_item in order.csv_skip_items.all():
            accessories_to_remove = order.accessories.filter(sku=skip_item.sku)
            logging.info(f"Removing {accessories_to_remove.count()} accessories with SKU {skip_item.sku} (order-specific skip)")
            for accessory in accessories_to_remove:
                accessory.delete()
                skip_items_removed += 1
        
        # Remove global skip items
        global_skip_items = CSVSkipItem.objects.filter(order__isnull=True)
        for skip_item in global_skip_items:
            accessories_to_remove = order.accessories.filter(sku=skip_item.sku)
            if accessories_to_remove.exists():
                logging.info(f"Removing {accessories_to_remove.count()} accessories with SKU {skip_item.sku} (global skip)")
                for accessory in accessories_to_remove:
                    accessory.delete()
                    skip_items_removed += 1
        
        # Clear the order-specific skip items after processing
        order.csv_skip_items.all().delete()
        
        # Regenerate processed CSV with resolved data
        if order.accessories.exists():
            processed_rows = []
            for accessory in order.accessories.all():
                row = {
                    'Sku': accessory.sku,
                    'Name': accessory.name,
                    'Description': accessory.description,
                    'CostPrice': str(accessory.cost_price),
                    'SellPrice': str(accessory.sell_price),
                    'Quantity': str(accessory.quantity),
                    'Billable': 'TRUE' if accessory.billable else 'FALSE'
                }
                processed_rows.append(row)
            
            if processed_rows:
                processed_csv_content = io.StringIO()
                fieldnames = ['Sku', 'Name', 'Description', 'CostPrice', 'SellPrice', 'Quantity', 'Billable']
                writer = csv.DictWriter(processed_csv_content, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(processed_rows)
                
                # Save updated processed CSV to order
                processed_csv_content.seek(0)
                filename = order.original_csv.name.split('_')[-1] if order.original_csv else 'accessories.csv'
                order.processed_csv.save(f"{order.customer_number}_processed_{filename}", 
                                       io.BytesIO(processed_csv_content.getvalue().encode('utf-8')))
                order.processed_csv_created_at = timezone.now()
                order.save()
                logging.info(f"Regenerated processed CSV with {len(processed_rows)} items")
        
        # Check if all missing items have been resolved
        remaining_missing = order.accessories.filter(missing=True).count()
        if remaining_missing == 0:
            order.csv_has_missing_items = False
            order.save()
        
        success_msg = f'Successfully resolved {resolved_count} missing items using substitutions.'
        if skip_items_removed > 0:
            success_msg += f' Removed {skip_items_removed} items from skip list.'
        messages.success(request, success_msg)
        
        return redirect('order_details', order_id=order_id)
    
    return JsonResponse({'success': False, 'error': 'Method not allowed'})


@login_required
def add_substitution(request, order_id):
    """Add a substitution for a specific order"""
    order = get_object_or_404(Order, id=order_id)
    
    if request.method == 'POST':
        form = SubstitutionForm(request.POST)
        if form.is_valid():
            substitution = form.save(commit=False)
            # Find the missing accessory to get the name
            missing_accessory = order.accessories.filter(sku=substitution.missing_sku, missing=True).first()
            if missing_accessory:
                substitution.missing_name = missing_accessory.name
            substitution.save()
            messages.success(request, 'Substitution added successfully.')
            return redirect('order_details', order_id=order_id)
    else:
        form = SubstitutionForm()
    
    return redirect('order_details', order_id=order_id)


@login_required
def add_skip_item(request, order_id):
    """Add an item to skip/remove for a specific order"""
    order = get_object_or_404(Order, id=order_id)
    
    if request.method == 'POST':
        form = CSVSkipItemForm(request.POST)
        if form.is_valid():
            skip_item = form.save(commit=False)
            skip_item.order = order
            skip_item.save()
            messages.success(request, 'Item added to skip list successfully.')
            return redirect('order_details', order_id=order_id)
    else:
        form = CSVSkipItemForm()
    
    return redirect('order_details', order_id=order_id)


@login_required
def delete_skip_item(request, skip_item_id):
    """Delete a skip item"""
    if request.method == 'POST':
        skip_item = get_object_or_404(CSVSkipItem, id=skip_item_id)
        skip_item.delete()
        messages.success(request, f'Item "{skip_item.name}" removed from skip list.')
        
        # Redirect based on whether it was an order-specific or global skip item
        if skip_item.order:
            return redirect('order_details', order_id=skip_item.order.id)
        else:
            return redirect('substitutions')
    return JsonResponse({'success': False, 'error': 'Method not allowed'})

@login_required
def download_processed_csv(request, order_id):
    """Download processed CSV with proper filename and force download dialog"""
    order = get_object_or_404(Order, id=order_id)
    
    if not order.processed_csv:
        messages.error(request, 'No processed CSV available for this order.')
        return redirect('order_details', order_id=order_id)
    
    # Read the file content
    try:
        with order.processed_csv.open('rb') as f:
            file_content = f.read()
    except Exception as e:
        messages.error(request, f'Error reading processed CSV: {str(e)}')
        return redirect('order_details', order_id=order_id)
    
    # Create response with proper headers for download
    response = HttpResponse(file_content, content_type='text/csv')
    filename = f"{order.customer_number}_WG_Accessories.csv"
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    response['Content-Length'] = len(file_content)
    
    return response


@login_required
def download_current_accessories_csv(request, order_id):
    """Generate and download CSV from current accessories in database"""
    order = get_object_or_404(Order, id=order_id)
    
    if not order.accessories.exists():
        messages.error(request, 'No accessories available for this order.')
        return redirect('order_details', order_id=order_id)
    
    # Create CSV in memory
    output = io.StringIO()
    writer = csv.writer(output)
    
    # Write header with all columns
    writer.writerow(['Sku', 'Name', 'Description', 'CostPrice', 'SellPrice', 'Quantity', 'Billable'])
    
    # Write accessory data
    for accessory in order.accessories.all().order_by('sku'):
        writer.writerow([
            accessory.sku,
            accessory.name,
            accessory.description,
            accessory.cost_price,
            accessory.sell_price,
            accessory.quantity,
            'TRUE' if accessory.billable else 'FALSE'
        ])
    
    # Get CSV content
    csv_content = output.getvalue()
    output.close()
    
    # Create response
    response = HttpResponse(csv_content, content_type='text/csv')
    filename = f"{order.customer_number or order.sale_number}_Accessories.csv"
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    
    return response


@login_required
def download_pnx_as_csv(request, order_id):
    """Generate and download CSV from PNX items for this order"""
    order = get_object_or_404(Order, id=order_id)
    
    if not order.boards_po:
        messages.error(request, 'No PNX file available for this order.')
        return redirect('order_details', order_id=order_id)
    
    # Get PNX items for this order - use sale_number with icontains to match how order_details does it
    pnx_items = order.boards_po.pnx_items.filter(customer__icontains=order.sale_number).order_by('barcode')
    
    if not pnx_items.exists():
        messages.error(request, 'No PNX items found for this order.')
        return redirect('order_details', order_id=order_id)
    
    # Build CUSTOMER field: BFS-<designer_initials>-<sale_number>
    designer_initials = ''
    if order.designer and order.designer.name:
        # Get initials from designer name (e.g., "John Smith" -> "JS")
        designer_initials = ''.join(word[0].upper() for word in order.designer.name.split() if word)
    customer_value = f"BFS-{designer_initials}-{order.sale_number}"
    
    # Create CSV in memory
    output = io.StringIO()
    writer = csv.writer(output)
    
    # Write header - exact order as specified
    writer.writerow(['BARCODE', 'MATNAME', 'CLENG', 'CWIDTH', 'CNT', 'GRAIN', 'CUSTOMER', 'ORDERNAME', 'ARTICLENAME', 'PARTDESC', 'PRFID1', 'PRFID3', 'PRFID4', 'PRFID2'])
    
    # Write PNX item data in matching order
    for item in pnx_items:
        writer.writerow([
            item.barcode,
            item.matname,
            item.cleng,
            item.cwidth,
            item.cnt,
            item.grain,
            customer_value,
            item.ordername,
            item.articlename,
            item.partdesc,
            item.prfid1,
            item.prfid3,
            item.prfid4,
            item.prfid2,
        ])
    
    # Get CSV content
    csv_content = output.getvalue()
    output.close()
    
    # Create response
    response = HttpResponse(csv_content, content_type='text/csv')
    filename = f"{order.boards_po.po_number}_{order.sale_number}_PNX_Items.csv"
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    
    return response


@login_required
def push_accessories_to_workguru(request, order_id):
    """Push accessories to WorkGuru project via API."""
    from .services.workguru_api import WorkGuruAPI, WorkGuruAPIError
    from .services.workguru_accessories import push_accessories_to_project

    order = get_object_or_404(Order, id=order_id)

    if not order.workguru_id:
        messages.error(request, 'This order does not have a WorkGuru Project ID.')
        return redirect('order_details', order_id=order_id)

    if not order.accessories.exists():
        messages.error(request, 'No accessories to push to WorkGuru.')
        return redirect('order_details', order_id=order_id)

    try:
        api = WorkGuruAPI.authenticate()
        result = push_accessories_to_project(api, order)

        if result['success_count'] > 0:
            messages.success(
                request,
                f"Successfully pushed {result['success_count']} item(s) to WorkGuru "
                f"project {order.workguru_id} ({result['method']} mode)."
            )
        if result['error_count'] > 0:
            error_summary = f"Failed to push {result['error_count']} item(s). "
            error_summary += '; '.join(result['errors'][:3])
            if len(result['errors']) > 3:
                error_summary += f'... and {len(result["errors"]) - 3} more'
            messages.error(request, error_summary)

    except WorkGuruAPIError as e:
        messages.error(request, str(e))

    return redirect('order_details', order_id=order_id)


@login_required
def create_workguru_po(request, order_id):
    """Create a Purchase Order in WorkGuru and link it as the Boards PO for this order."""
    from .services.workguru_api import WorkGuruAPI, WorkGuruAPIError
    from .services.workguru_boards import create_boards_po_in_workguru

    order = get_object_or_404(Order, id=order_id)

    if not order.workguru_id:
        messages.error(request, 'This order does not have a WorkGuru Project ID. Set it first.')
        return redirect('order_details', order_id=order_id)

    if order.boards_po:
        messages.warning(request, f'This order already has a Boards PO ({order.boards_po.po_number}).')
        return redirect('order_details', order_id=order_id)

    try:
        api = WorkGuruAPI.authenticate()
        po_number = create_boards_po_in_workguru(api, order)
        messages.success(request, f'WorkGuru PO {po_number} created and linked to this order.')
    except WorkGuruAPIError as e:
        messages.error(request, str(e))

    return redirect('order_details', order_id=order_id)


@login_required
def push_boards_to_workguru_po(request, order_id):
    """Push board line items and PNX/CSV files to the WorkGuru Purchase Order."""
    from .services.workguru_api import WorkGuruAPI, WorkGuruAPIError
    from .services.workguru_boards import push_boards_to_po

    order = get_object_or_404(Order, id=order_id)

    if not order.workguru_id:
        messages.error(request, 'This order does not have a WorkGuru Project ID.')
        return redirect('order_details', order_id=order_id)

    if not order.boards_po:
        messages.error(request, 'This order does not have a Boards PO. Create one first.')
        return redirect('order_details', order_id=order_id)

    try:
        api = WorkGuruAPI.authenticate()
        summary = push_boards_to_po(api, order)

        msg_parts = [f"{summary['line_count']} board line item(s) added to PO {order.boards_po.po_number}"]
        if summary['stock_count']:
            msg_parts.append(f"{summary['stock_count']} existing stock")
        if summary['adhoc_count']:
            msg_parts.append(f"{summary['adhoc_count']} using generic boards product")
        if summary['files_uploaded']:
            msg_parts.append(f"{summary['files_uploaded']} file(s) uploaded")
        messages.success(request, ' | '.join(msg_parts))

    except WorkGuruAPIError as e:
        messages.error(request, str(e))

    return redirect('order_details', order_id=order_id)


@login_required
def create_workguru_os_doors_po(request, order_id):
    """Create a Purchase Order in WorkGuru for OS Doors."""
    from .services.workguru_api import WorkGuruAPI, WorkGuruAPIError
    from .services.workguru_os_doors import create_os_doors_po_in_workguru

    order = get_object_or_404(Order, id=order_id)

    if not order.workguru_id:
        messages.error(request, 'This order does not have a WorkGuru Project ID. Set it first.')
        return redirect('order_details', order_id=order_id)

    if order.os_doors_po:
        messages.warning(request, f'This order already has an OS Doors PO ({order.os_doors_po}).')
        return redirect('order_details', order_id=order_id)

    try:
        api = WorkGuruAPI.authenticate()
        po_number = create_os_doors_po_in_workguru(api, order)
        messages.success(request, f'WorkGuru OS Doors PO {po_number} created and linked to this order.')
    except WorkGuruAPIError as e:
        messages.error(request, str(e))

    return redirect('order_details', order_id=order_id)


@login_required
def push_os_doors_to_workguru_po(request, order_id):
    """Push OS Door line items to the WorkGuru Purchase Order."""
    from .services.workguru_api import WorkGuruAPI, WorkGuruAPIError
    from .services.workguru_os_doors import push_os_doors_to_po

    order = get_object_or_404(Order, id=order_id)

    if not order.workguru_id:
        messages.error(request, 'This order does not have a WorkGuru Project ID.')
        return redirect('order_details', order_id=order_id)

    if not order.os_doors_po:
        messages.error(request, 'This order does not have an OS Doors PO. Create one first.')
        return redirect('order_details', order_id=order_id)

    if not order.os_doors.exists():
        messages.error(request, 'No OS door items found for this order.')
        return redirect('order_details', order_id=order_id)

    try:
        api = WorkGuruAPI.authenticate()
        summary = push_os_doors_to_po(api, order)

        msg_parts = [f"{summary['line_count']} OS door line item(s) added to PO {order.os_doors_po}"]
        if summary['stock_count']:
            msg_parts.append(f"{summary['stock_count']} stock")
        if summary['adhoc_count']:
            msg_parts.append(f"{summary['adhoc_count']} adhoc")
        messages.success(request, ' | '.join(msg_parts))

    except WorkGuruAPIError as e:
        messages.error(request, str(e))

    return redirect('order_details', order_id=order_id)


@login_required
def boards_summary(request):
    """Display summary of all boards POs with their received status and filtering"""
    from django.db.models import Count, Q
    
    # Get filter parameters
    search_query = request.GET.get('search', '').strip()
    status_filter = request.GET.get('status', '')
    order_type_filter = request.GET.get('order_type', '')
    sort_by = request.GET.get('sort', 'po_number')
    
    # Base queryset with prefetch
    boards_pos = BoardsPO.objects.all().prefetch_related(
        Prefetch('pnx_items', queryset=PNXItem.objects.order_by('barcode', 'matname', 'customer')),
        Prefetch('orders', queryset=Order.objects.all())
    )
    
    # Apply search filter
    if search_query:
        boards_pos = boards_pos.filter(po_number__icontains=search_query)
    
    # Apply sorting
    valid_sorts = {
        'po_number': 'po_number',
        '-po_number': '-po_number',
    }
    if sort_by in valid_sorts:
        boards_pos = boards_pos.order_by(valid_sorts[sort_by])
    else:
        boards_pos = boards_pos.order_by('po_number')
    
    # Add order count, order types, and received status to each BoardsPO
    filtered_pos = []
    for boards_po in boards_pos:
        boards_po.order_count = boards_po.orders.count()
        boards_po.total_pnx_items = boards_po.pnx_items.count()
        boards_po.received_pnx_items = sum(1 for item in boards_po.pnx_items.all() if item.is_fully_received)
        boards_po.partially_received_items = sum(1 for item in boards_po.pnx_items.all() if item.is_partially_received)
        
        # Count order types
        boards_po.sale_count = boards_po.orders.filter(order_type='sale').count()
        boards_po.remedial_count = boards_po.orders.filter(order_type='remedial').count()
        boards_po.warranty_count = boards_po.orders.filter(order_type='warranty').count()
        
        # Apply status filter
        if status_filter == 'received' and not boards_po.boards_received:
            continue
        elif status_filter == 'partial' and not (boards_po.partially_received_items > 0 and not boards_po.boards_received):
            continue
        elif status_filter == 'not_received' and (boards_po.received_pnx_items > 0):
            continue
        
        # Apply order type filter
        if order_type_filter == 'sale' and boards_po.sale_count == 0:
            continue
        elif order_type_filter == 'remedial' and boards_po.remedial_count == 0:
            continue
        elif order_type_filter == 'warranty' and boards_po.warranty_count == 0:
            continue
        
        filtered_pos.append(boards_po)
    
    # Calculate statistics
    total_pos = len(filtered_pos)
    total_items = sum(po.total_pnx_items for po in filtered_pos)
    total_received = sum(po.received_pnx_items for po in filtered_pos)
    total_orders = sum(po.order_count for po in filtered_pos)
    
    context = {
        'boards_pos': filtered_pos,
        'total_pos': total_pos,
        'total_items': total_items,
        'total_received': total_received,
        'total_orders': total_orders,
        'search_query': search_query,
        'status_filter': status_filter,
        'order_type_filter': order_type_filter,
        'current_sort': sort_by,
    }
    
    return render(request, 'stock_take/boards_summary.html', context)

@login_required
def os_doors_summary(request):
    """Display summary of all OS Doors POs with their received status"""
    # Get distinct OS Doors POs
    os_doors_pos = Order.objects.filter(
        os_doors_required=True, 
        os_doors_po__isnull=False, 
        os_doors_po__gt=''
    ).values('os_doors_po').distinct().order_by('os_doors_po')
    
    # Convert to list and add order/os door data
    os_doors_pos_list = []
    total_os_doors = 0
    total_received = 0
    total_orders = 0
    
    for po_data in os_doors_pos:
        po_number = po_data['os_doors_po']
        orders = Order.objects.filter(os_doors_po=po_number).prefetch_related(
            Prefetch('os_doors', queryset=OSDoor.objects.order_by('door_style', 'style_colour', 'colour'))
        )
        
        po_order_count = orders.count()
        po_total_os_doors = sum(order.os_doors.count() for order in orders)
        po_received_os_doors = sum(
            1 for order in orders for os_door in order.os_doors.all() if os_door.is_fully_received
        )
        
        total_orders += po_order_count
        total_os_doors += po_total_os_doors
        total_received += po_received_os_doors
        
        # Get unique customer names for this PO
        customers = list(set([f"{order.first_name} {order.last_name}".strip() for order in orders if order.first_name or order.last_name]))
        
        po_obj = {
            'po_number': po_number,
            'orders': orders,
            'order_count': po_order_count,
            'total_os_doors': po_total_os_doors,
            'received_os_doors': po_received_os_doors,
            'os_doors_received': po_received_os_doors == po_total_os_doors if po_total_os_doors > 0 else False,
            'customers': customers
        }
        os_doors_pos_list.append(po_obj)
    
    return render(request, 'stock_take/os_doors_summary.html', {
        'os_doors_pos': os_doors_pos_list,
        'total_os_doors': total_os_doors,
        'total_received': total_received,
        'total_orders': total_orders,
    })

@login_required
def stock_items_manager(request):
    """Manage stock items - view and edit multiple items at once"""
    # Get filter parameters
    search = request.GET.get('search', '')
    category_id = request.GET.get('category', '')
    tracking_type = request.GET.get('tracking_type', '')
    
    # Build query
    items = StockItem.objects.select_related('category', 'stock_take_group').all()
    
    if search:
        items = items.filter(
            Q(sku__icontains=search) | 
            Q(name__icontains=search) |
            Q(location__icontains=search)
        )
    
    if category_id:
        items = items.filter(category_id=category_id)
    
    if tracking_type:
        items = items.filter(tracking_type=tracking_type)
    
    # Order by SKU
    items = items.order_by('sku')
    
    # Get categories for filter dropdown
    categories = Category.objects.all().order_by('name')
    stock_take_groups = StockTakeGroup.objects.all().order_by('name')
    
    return render(request, 'stock_take/stock_items_manager.html', {
        'items': items,
        'categories': categories,
        'stock_take_groups': stock_take_groups,
        'search': search,
        'selected_category': category_id,
        'selected_tracking_type': tracking_type,
        'tracking_choices': StockItem.TRACKING_CHOICES,
    })

@login_required
def update_stock_items_batch(request):
    """Update multiple stock items at once via AJAX"""
    if request.method == 'POST':
        try:
            import json
            data = json.loads(request.body)
            items_data = data.get('items', [])
            
            updated_count = 0
            errors = []
            
            for item_data in items_data:
                try:
                    item_id = item_data.get('id')
                    item = StockItem.objects.get(id=item_id)
                    
                    # Update fields
                    if 'sku' in item_data:
                        item.sku = item_data['sku']
                    if 'name' in item_data:
                        item.name = item_data['name']
                    if 'cost' in item_data:
                        item.cost = Decimal(str(item_data['cost']))
                    if 'location' in item_data:
                        item.location = item_data['location']
                    if 'quantity' in item_data:
                        item.quantity = int(item_data['quantity'])
                    if 'tracking_type' in item_data:
                        item.tracking_type = item_data['tracking_type']
                    if 'min_order_qty' in item_data:
                        value = item_data['min_order_qty']
                        item.min_order_qty = int(value) if value else None
                    if 'par_level' in item_data:
                        value = item_data['par_level']
                        item.par_level = int(value) if value else 0
                    if 'category_id' in item_data:
                        value = item_data['category_id']
                        item.category_id = int(value) if value else None
                    if 'stock_take_group_id' in item_data:
                        value = item_data['stock_take_group_id']
                        item.stock_take_group_id = int(value) if value else None
                    
                    item.save()
                    updated_count += 1
                    
                except StockItem.DoesNotExist:
                    errors.append(f"Item {item_id} not found")
                except Exception as e:
                    errors.append(f"Error updating item {item_id}: {str(e)}")
            
            return JsonResponse({
                'success': True,
                'updated_count': updated_count,
                'errors': errors
            })
            
        except Exception as e:
            return JsonResponse({
                'success': False,
                'error': str(e)
            }, status=400)
    
    return JsonResponse({'success': False, 'error': 'Invalid request method'}, status=400)

@login_required
def remedials(request):
    """View to manage remedial orders - create and view remedials"""
    
    # Get all remedial orders
    remedial_orders = Remedial.objects.select_related('original_order', 'boards_po').prefetch_related('accessories').order_by('-created_date')
    
    # Get all orders for selection (to create remedials from)
    available_orders = Order.objects.exclude(
        job_finished=True
    ).select_related('boards_po').order_by('-order_date')[:100]  # Limit to recent 100
    
    if request.method == 'POST':
        # Handle creating a new remedial order
        try:
            original_order_id = request.POST.get('original_order_id')
            remedial_reason = request.POST.get('remedial_notes', '')  # Using 'remedial_notes' from form
            
            if not original_order_id:
                messages.error(request, 'Please select an order to create a remedial for.')
                return redirect('remedials')
            
            original_order = Order.objects.get(id=original_order_id)
            
            # Generate unique remedial number
            latest_remedial = Remedial.objects.order_by('-id').first()
            if latest_remedial and latest_remedial.remedial_number:
                # Extract number from REM-001 format
                try:
                    last_num = int(latest_remedial.remedial_number.split('-')[1])
                    new_num = last_num + 1
                except (IndexError, ValueError):
                    new_num = 1
            else:
                new_num = 1
            
            remedial_number = f"REM-{new_num:03d}"
            
            # Create new remedial order
            remedial = Remedial.objects.create(
                original_order=original_order,
                remedial_number=remedial_number,
                reason=remedial_reason,
                first_name=original_order.first_name,
                last_name=original_order.last_name,
                customer_number=original_order.customer_number,
                address=original_order.address,
                postcode=original_order.postcode,
            )
            
            messages.success(request, f'Remedial {remedial_number} created for {original_order.first_name} {original_order.last_name} (Order: {original_order.sale_number})')
            return redirect('remedials')
            
        except Order.DoesNotExist:
            messages.error(request, 'Original order not found.')
            return redirect('remedials')
        except Exception as e:
            messages.error(request, f'Error creating remedial: {str(e)}')
            return redirect('remedials')
    
    return render(request, 'stock_take/remedials.html', {
        'remedial_orders': remedial_orders,
        'available_orders': available_orders,
    })

@login_required
def remedial_report(request):
    """Generate a report of all remedial orders with statistics"""
    from django.db.models import Count, Q
    
    # Get all remedial orders
    remedial_orders = Remedial.objects.select_related('original_order', 'boards_po').prefetch_related('accessories').order_by('-created_date')
    
    # Statistics
    total_remedials = remedial_orders.count()
    completed_remedials = remedial_orders.filter(is_completed=True).count()
    in_progress_remedials = total_remedials - completed_remedials
    
    # Remedials with boards ordered
    boards_ordered_count = remedial_orders.filter(boards_po__boards_ordered=True).count()
    
    # Remedials by month (last 12 months)
    from datetime import datetime, timedelta
    twelve_months_ago = datetime.now().date() - timedelta(days=365)
    recent_remedials = remedial_orders.filter(created_date__gte=twelve_months_ago)
    
    return render(request, 'stock_take/remedial_report.html', {
        'remedial_orders': remedial_orders,
        'total_remedials': total_remedials,
        'completed_remedials': completed_remedials,
        'in_progress_remedials': in_progress_remedials,
        'boards_ordered_count': boards_ordered_count,
        'recent_remedials': recent_remedials,
    })


@login_required
@login_required
def fit_board(request):
    """Display fit board calendar with appointments and completion tracking"""
    from datetime import datetime, timedelta
    from calendar import monthrange
    import calendar
    
    # Get current date or requested month/year
    today = timezone.now().date()
    current_year = int(request.GET.get('year', today.year))
    current_month = int(request.GET.get('month', today.month))
    
    # Navigation for previous/next month
    first_day = datetime(current_year, current_month, 1).date()
    if current_month == 12:
        next_month = datetime(current_year + 1, 1, 1).date()
    else:
        next_month = datetime(current_year, current_month + 1, 1).date()
    
    if current_month == 1:
        prev_month = datetime(current_year - 1, 12, 1).date()
    else:
        prev_month = datetime(current_year, current_month - 1, 1).date()
    
    # Get all appointments for this month
    appointments = FitAppointment.objects.filter(
        fit_date__year=current_year,
        fit_date__month=current_month
    ).select_related('order')
    
    # Get all orders with fit dates in this month (for quick add)
    orders_this_month = Order.objects.filter(
        fit_date__year=current_year,
        fit_date__month=current_month,
        job_finished=False
    ).order_by('fit_date', 'last_name')
    
    # Create appointments dict by date and fitter
    appointments_by_date = {}
    for appointment in appointments:
        date_key = appointment.fit_date.day
        if date_key not in appointments_by_date:
            appointments_by_date[date_key] = {'R': [], 'G': [], 'S': [], 'P': []}
        appointments_by_date[date_key][appointment.fitter].append(appointment)
    
    # Build calendar structure
    cal = calendar.Calendar(firstweekday=0)  # Monday as first day
    month_days = cal.monthdayscalendar(current_year, current_month)
    
    # Get day names
    day_names = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
    month_name = calendar.month_name[current_month]
    
    # Get all unfinished orders for selection dropdown
    all_orders = Order.objects.filter(
        job_finished=False
    ).order_by('fit_date', 'last_name')
    
    # Fitter choices
    fitters = [('R', 'Ross'), ('G', 'Gavin'), ('S', 'Stuart'), ('P', 'Paddy')]
    
    context = {
        'current_year': current_year,
        'current_month': current_month,
        'month_name': month_name,
        'month_days': month_days,
        'day_names': day_names,
        'appointments_by_date': appointments_by_date,
        'orders_this_month': orders_this_month,
        'all_orders': all_orders,
        'prev_month': prev_month,
        'next_month': next_month,
        'today': today,
        'fitters': fitters,
    }
    
    return render(request, 'stock_take/fit_board.html', context)


@login_required
def workflow(request):
    """Display workflow stages for order management"""
    from .models import WorkflowStage
    
    stages = WorkflowStage.objects.all().prefetch_related('tasks')
    phases = [
        ('enquiry', 'Enquiry'),
        ('lead', 'Lead'),
        ('sale', 'Sale'),
    ]
    
    # Group stages by phase for template iteration
    stages_by_phase = {}
    for phase_code, phase_display in phases:
        stages_by_phase[phase_code] = stages.filter(phase=phase_code)
    
    context = {
        'stages': stages,
        'phases': phases,
        'stages_by_phase': stages_by_phase,
    }
    return render(request, 'stock_take/workflow.html', context)


@login_required
def save_workflow_stage(request):
    """Create or update a workflow stage"""
    from .models import WorkflowStage
    
    if request.method == 'POST':
        stage_id = request.POST.get('stage_id')
        name = request.POST.get('name')
        phase = request.POST.get('phase')
        role = request.POST.get('role')
        description = request.POST.get('description')
        expected_days = request.POST.get('expected_days')
        
        if stage_id:
            # Update existing stage
            stage = get_object_or_404(WorkflowStage, id=stage_id)
            stage.name = name
            stage.phase = phase
            stage.role = role
            stage.description = description
            stage.expected_days = int(expected_days) if expected_days else None
            stage.save()
        else:
            # Create new stage
            # Get the highest order number and add 1
            max_order = WorkflowStage.objects.aggregate(models.Max('order'))['order__max'] or 0
            stage = WorkflowStage.objects.create(
                name=name,
                phase=phase,
                role=role,
                description=description,
                expected_days=int(expected_days) if expected_days else None,
                order=max_order + 1
            )
        
        return redirect('workflow')
    
    return redirect('workflow')


@login_required
def get_workflow_stage(request, stage_id):
    """Get workflow stage details as JSON"""
    from .models import WorkflowStage
    
    stage = get_object_or_404(WorkflowStage, id=stage_id)
    data = {
        'id': stage.id,
        'name': stage.name,
        'phase': stage.phase,
        'role': stage.role,
        'description': stage.description,
        'expected_days': stage.expected_days,
    }
    return JsonResponse(data)


@login_required
def delete_workflow_stage(request, stage_id):
    """Delete a workflow stage"""
    from .models import WorkflowStage
    
    if request.method == 'POST':
        try:
            stage = get_object_or_404(WorkflowStage, id=stage_id)
            stage.delete()
            return JsonResponse({'success': True})
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})
    
    return JsonResponse({'success': False, 'error': 'Invalid request method'})


@login_required
def move_workflow_stage(request, stage_id):
    """Move a workflow stage up or down"""
    from .models import WorkflowStage
    
    if request.method == 'POST':
        direction = request.POST.get('direction')
        stage = get_object_or_404(WorkflowStage, id=stage_id)
        
        if direction == 'up':
            # Get the stage immediately above this one
            prev_stage = WorkflowStage.objects.filter(
                order__lt=stage.order
            ).order_by('-order').first()
            
            if prev_stage:
                # Swap orders
                stage.order, prev_stage.order = prev_stage.order, stage.order
                stage.save()
                prev_stage.save()
        
        elif direction == 'down':
            # Get the stage immediately below this one
            next_stage = WorkflowStage.objects.filter(
                order__gt=stage.order
            ).order_by('order').first()
            
            if next_stage:
                # Swap orders
                stage.order, next_stage.order = next_stage.order, stage.order
                stage.save()
                next_stage.save()
        
        return JsonResponse({'success': True})
    
    return JsonResponse({'success': False})


@login_required
def save_workflow_task(request):
    """Add a task to a workflow stage"""
    from .models import WorkflowTask, WorkflowStage
    
    if request.method == 'POST':
        stage_id = request.POST.get('stage_id')
        description = request.POST.get('description')
        task_type = request.POST.get('task_type', 'record')
        options = request.POST.get('options', '')
        
        stage = get_object_or_404(WorkflowStage, id=stage_id)
        
        # Get the highest order number for tasks in this stage
        max_order = stage.tasks.aggregate(models.Max('order'))['order__max'] or 0
        
        WorkflowTask.objects.create(
            stage=stage,
            description=description,
            task_type=task_type,
            options=options,
            order=max_order + 1
        )
        
        return redirect('workflow')
    
    return redirect('workflow')


@login_required
def delete_workflow_task(request, task_id):
    """Delete a workflow task"""
    from .models import WorkflowTask
    
    if request.method == 'POST':
        try:
            task = get_object_or_404(WorkflowTask, id=task_id)
            task.delete()
            return JsonResponse({'success': True})
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})
    
    return JsonResponse({'success': False, 'error': 'Invalid request method'})


@login_required
def update_order_workflow_stage(request, order_id):
    """Update the workflow stage for an order"""
    from .models import OrderWorkflowProgress, WorkflowStage
    from django.utils import timezone
    
    if request.method == 'POST':
        order = get_object_or_404(Order, id=order_id)
        stage_id = request.POST.get('stage_id')
        
        # Get or create workflow progress
        workflow_progress, created = OrderWorkflowProgress.objects.get_or_create(order=order)
        
        if stage_id:
            stage = get_object_or_404(WorkflowStage, id=stage_id)
            workflow_progress.current_stage = stage
            workflow_progress.stage_started_at = timezone.now()
        else:
            workflow_progress.current_stage = None
        
        workflow_progress.save()
        
        return JsonResponse({'success': True, 'message': 'Stage updated successfully'})
    
    return JsonResponse({'success': False, 'error': 'Invalid request method'}, status=400)


@login_required
def update_task_completion(request, order_id, task_id):
    """Update task completion status for an order"""
    from .models import OrderWorkflowProgress, WorkflowTask, TaskCompletion
    from django.utils import timezone
    
    if request.method == 'POST':
        order = get_object_or_404(Order, id=order_id)
        task = get_object_or_404(WorkflowTask, id=task_id)
        
        # Get or create workflow progress
        workflow_progress, _ = OrderWorkflowProgress.objects.get_or_create(order=order)
        
        # Get or create task completion
        completion, _ = TaskCompletion.objects.get_or_create(
            order_progress=workflow_progress,
            task=task
        )
        
        # Handle different task types
        action = request.POST.get('action')
        
        if action == 'checkbox':
            completed = request.POST.get('completed') == 'true'
            completion.completed = completed
            completion.completed_at = timezone.now() if completed else None
            if request.user.is_authenticated:
                completion.completed_by = request.user.username if completed else ''
        
        elif action == 'radio' or action == 'dropdown' or action == 'decision':
            selected_option = request.POST.get('selected_option', '')
            completion.selected_option = selected_option
            completion.completed = bool(selected_option)
            completion.completed_at = timezone.now() if selected_option else None
            if request.user.is_authenticated:
                completion.completed_by = request.user.username if selected_option else ''
        
        completion.save()
        
        return JsonResponse({'success': True, 'message': 'Task updated successfully'})
    
    return JsonResponse({'success': False, 'error': 'Invalid request method'}, status=400)


@login_required
def progress_to_next_stage(request, order_id):
    """Progress order to the next workflow stage"""
    from .models import OrderWorkflowProgress, WorkflowStage
    from django.utils import timezone
    
    if request.method == 'POST':
        try:
            order = get_object_or_404(Order, id=order_id)
            
            # Get or create workflow progress
            workflow_progress, _ = OrderWorkflowProgress.objects.get_or_create(order=order)
            
            if not workflow_progress.current_stage:
                return JsonResponse({'success': False, 'error': 'No current stage set'}, status=400)
            
            # Check if all requirement tasks are completed before progressing
            if not workflow_progress.can_progress_to_next_stage:
                return JsonResponse({'success': False, 'error': 'Cannot progress: Required tasks must be completed first'}, status=400)
            
            current_stage = workflow_progress.current_stage
            
            # Find next stage (same phase, higher order, or first stage of next phase)
            next_stage = WorkflowStage.objects.filter(
                phase=current_stage.phase,
                order__gt=current_stage.order
            ).order_by('order').first()
            
            # If no next stage in same phase, try next phase
            if not next_stage:
                phase_order = {'enquiry': 0, 'lead': 1, 'sale': 2}
                current_phase_order = phase_order.get(current_stage.phase, -1)
                
                for phase_key, phase_value in phase_order.items():
                    if phase_value > current_phase_order:
                        next_stage = WorkflowStage.objects.filter(phase=phase_key).order_by('order').first()
                        if next_stage:
                            break
            
            if next_stage:
                workflow_progress.current_stage = next_stage
                workflow_progress.stage_started_at = timezone.now()
                workflow_progress.save()
                return JsonResponse({'success': True, 'message': f'Progressed to {next_stage.name}'})
            else:
                return JsonResponse({'success': False, 'error': 'No next stage available'}, status=400)
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)}, status=500)
    
    return JsonResponse({'success': False, 'error': 'Invalid request method'}, status=400)


@login_required
def revert_to_previous_stage(request, order_id):
    """Revert order to the previous workflow stage"""
    from .models import OrderWorkflowProgress, WorkflowStage
    from django.utils import timezone
    
    if request.method == 'POST':
        try:
            order = get_object_or_404(Order, id=order_id)
            
            # Get or create workflow progress
            workflow_progress, _ = OrderWorkflowProgress.objects.get_or_create(order=order)
            
            if not workflow_progress.current_stage:
                return JsonResponse({'success': False, 'error': 'No current stage set'}, status=400)
            
            current_stage = workflow_progress.current_stage
            
            # Find previous stage (same phase, lower order, or last stage of previous phase)
            previous_stage = WorkflowStage.objects.filter(
                phase=current_stage.phase,
                order__lt=current_stage.order
            ).order_by('-order').first()
            
            # If no previous stage in same phase, try previous phase
            if not previous_stage:
                phase_order = {'enquiry': 0, 'lead': 1, 'sale': 2}
                current_phase_order = phase_order.get(current_stage.phase, -1)
                
                for phase_key in reversed(list(phase_order.keys())):
                    if phase_order[phase_key] < current_phase_order:
                        previous_stage = WorkflowStage.objects.filter(phase=phase_key).order_by('-order').first()
                        if previous_stage:
                            break
            
            if previous_stage:
                workflow_progress.current_stage = previous_stage
                workflow_progress.stage_started_at = timezone.now()
                workflow_progress.save()
                return JsonResponse({'success': True, 'message': f'Reverted to {previous_stage.name}'})
            else:
                return JsonResponse({'success': False, 'error': 'No previous stage available'}, status=400)
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)}, status=500)
    
    return JsonResponse({'success': False, 'error': 'Invalid request method'}, status=400)


@login_required
def add_fit_appointment(request):
    """Add or update a fit appointment"""
    if request.method == 'POST':
        entity_id = request.POST.get('entity_id')
        entity_type = request.POST.get('entity_type', 'order')  # 'order' or 'remedial'
        fit_date = request.POST.get('fit_date')
        fitter = request.POST.get('fitter', 'R')  # Default to Ross
        
        try:
            if entity_type == 'order':
                order = Order.objects.get(id=entity_id)
                appointment, created = FitAppointment.objects.get_or_create(
                    order=order,
                    remedial=None,
                    fit_date=fit_date,
                    defaults={'fitter': fitter}
                )
                customer_name = f"{order.first_name} {order.last_name}"
            elif entity_type == 'remedial':
                remedial = Remedial.objects.get(id=entity_id)
                appointment, created = FitAppointment.objects.get_or_create(
                    remedial=remedial,
                    order=None,
                    fit_date=fit_date,
                    defaults={'fitter': fitter}
                )
                customer_name = f"{remedial.remedial_number} - {remedial.first_name} {remedial.last_name}"
            else:
                return JsonResponse({'success': False, 'error': 'Invalid entity type'})
            
            if created:
                messages.success(request, f'Fit appointment added for {customer_name}')
            else:
                messages.info(request, f'Appointment already exists for {customer_name}')
            
            return JsonResponse({'success': True, 'created': created})
        except (Order.DoesNotExist, Remedial.DoesNotExist):
            return JsonResponse({'success': False, 'error': 'Entity not found'})
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})
    
    return JsonResponse({'success': False, 'error': 'Invalid request method'})


@login_required
def update_fit_status(request, appointment_id):
    """Update completion status of a fit appointment"""
    if request.method == 'POST':
        try:
            appointment = get_object_or_404(FitAppointment, id=appointment_id)
            
            # Update status fields
            appointment.interior_completed = request.POST.get('interior_completed') == 'true'
            appointment.door_completed = request.POST.get('door_completed') == 'true'
            appointment.accessories_completed = request.POST.get('accessories_completed') == 'true'
            appointment.materials_completed = request.POST.get('materials_completed') == 'true'
            appointment.save()
            
            return JsonResponse({
                'success': True,
                'is_fully_completed': appointment.is_fully_completed
            })
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})
    
    return JsonResponse({'success': False, 'error': 'Invalid request method'})


@login_required
def delete_fit_appointment(request, appointment_id):
    """Delete a fit appointment"""
    if request.method == 'POST':
        try:
            appointment = get_object_or_404(FitAppointment, id=appointment_id)
            customer_name = appointment.customer_name
            appointment.delete()
            
            return JsonResponse({
                'success': True,
                'message': f'Appointment for {customer_name} deleted'
            })
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})
    
    return JsonResponse({'success': False, 'error': 'Invalid request method'})


@login_required
def move_fit_appointment(request, appointment_id):
    """Move a fit appointment to a new date and/or fitter"""
    if request.method == 'POST':
        try:
            import json
            data = json.loads(request.body)
            appointment = get_object_or_404(FitAppointment, id=appointment_id)
            
            # Update fitter if provided
            if 'fitter' in data:
                appointment.fitter = data['fitter']
            
            # Update fit date if provided
            if 'fit_date' in data:
                from datetime import datetime
                appointment.fit_date = datetime.strptime(data['fit_date'], '%Y-%m-%d').date()
            
            appointment.save()
            
            return JsonResponse({
                'success': True,
                'message': f'Appointment for {appointment.customer_name} moved'
            })
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})
    
    return JsonResponse({'success': False, 'error': 'Invalid request method'})


@login_required
def bulk_import_fit_dates(request):
    """Bulk import all fit dates from unfinished orders as appointments"""
    if request.method == 'POST':
        try:
            # Get all unfinished orders that have a fit date
            orders = Order.objects.filter(
                job_finished=False,
                fit_date__isnull=False
            )
            
            created_count = 0
            for order in orders:
                # Create appointment with Ross as default fitter if it doesn't exist
                appointment, created = FitAppointment.objects.get_or_create(
                    order=order,
                    fit_date=order.fit_date,
                    defaults={'fitter': 'R'}  # Default to Ross
                )
                if created:
                    created_count += 1
            
            return JsonResponse({
                'success': True,
                'created': created_count,
                'message': f'Imported {created_count} appointments'
            })
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})
    
    return JsonResponse({'success': False, 'error': 'Invalid request method'})


@login_required
def search_orders_api(request):
    """Search orders for fit board appointment selection"""
    query = request.GET.get('q', '').strip()
    
    # Get base queryset of unfinished orders
    orders = Order.objects.filter(job_finished=False)
    
    # Filter if query provided
    if query:
        orders = orders.filter(
            models.Q(first_name__icontains=query) |
            models.Q(last_name__icontains=query) |
            models.Q(sale_number__icontains=query) |
            models.Q(customer_number__icontains=query)
        )
    
    # Limit results and order by fit date
    orders = orders.order_by('fit_date', 'last_name')[:20]
    
    results = [{
        'id': order.id,
        'first_name': order.first_name,
        'last_name': order.last_name,
        'sale_number': order.sale_number,
        'customer_number': order.customer_number,
        'fit_date': order.fit_date.strftime('%d/%m/%Y')
    } for order in orders]
    
    return JsonResponse({'orders': results})


@login_required
def search_remedials_api(request):
    """Search remedials for fit board appointment selection"""
    query = request.GET.get('q', '').strip()
    
    # Get base queryset of incomplete remedials
    remedials = Remedial.objects.filter(is_completed=False)
    
    # Filter if query provided
    if query:
        remedials = remedials.filter(
            models.Q(remedial_number__icontains=query) |
            models.Q(first_name__icontains=query) |
            models.Q(last_name__icontains=query) |
            models.Q(customer_number__icontains=query)
        )
    
    # Limit results and order by ID
    remedials = remedials.order_by('-id')[:20]
    
    results = [{
        'id': remedial.id,
        'remedial_number': remedial.remedial_number,
        'first_name': remedial.first_name,
        'last_name': remedial.last_name,
        'customer_number': remedial.customer_number,
        'reason': remedial.reason[:50] + '...' if len(remedial.reason) > 50 else remedial.reason
    } for remedial in remedials]
    
    return JsonResponse({'remedials': results})


@login_required
def update_order_fit_status(request, order_id):
    """Update fit completion status on the Order model"""
    if request.method == 'POST':
        try:
            order = get_object_or_404(Order, id=order_id)
            field = request.POST.get('field')
            value = request.POST.get('value') == 'true'
            
            # Update the appropriate field
            if field == 'interior':
                order.interior_completed = value
            elif field == 'door':
                order.door_completed = value
            elif field == 'accessories':
                order.accessories_completed = value
            elif field == 'materials':
                order.materials_completed = value
            elif field == 'paperwork':
                order.paperwork_completed = value
            else:
                return JsonResponse({'success': False, 'error': 'Invalid field'})
            
            order.save()
            
            return JsonResponse({
                'success': True,
                'field': field,
                'value': value
            })
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})
    
    return JsonResponse({'success': False, 'error': 'Invalid request method'})


@login_required
def generate_and_attach_pnx(request, order_id):
    """Analyze PNX generation and show duplicate detection modal"""
    from material_generator.board_logic import generate_board_order_file
    from django.conf import settings
    import os
    
    order = get_object_or_404(Order, id=order_id)
    
    if not order.customer_number:
        return JsonResponse({'success': False, 'error': 'Order must have a CAD Number to generate PNX.'})
    
    if not order.boards_po:
        return JsonResponse({'success': False, 'error': 'Order must have a Boards PO assigned to attach PNX file.'})
    
    try:
        # Get database path - look in project root directory (where .env is)
        db_path = os.path.join(settings.BASE_DIR, 'cad_data.db')
        
        if not os.path.exists(db_path):
            return JsonResponse({'success': False, 'error': f'CAD database not found at: {db_path}. Please ensure cad_data.db is in the project root directory.'})
        
        # Generate PNX content using customer number (CAD number)
        pnx_content = generate_board_order_file(order.customer_number, db_path)
        
        if not pnx_content or pnx_content.strip() == '':
            return JsonResponse({'success': False, 'error': f'No board data found for CAD Number {order.customer_number}.'})
        
        # Parse PNX and analyze for duplicates
        io_string = io.StringIO(pnx_content)
        reader = csv.DictReader(io_string, delimiter=';')
        
        # Get existing items
        existing_items = {
            (item.barcode, item.matname, float(item.cleng), float(item.cwidth)): item
            for item in order.boards_po.pnx_items.all()
        }
        
        # Collect ALL items from the PNX for review
        all_items = []
        
        for row in reader:
            # Skip empty rows
            if not row.get('BARCODE', '').strip():
                continue
                
            try:
                barcode = row.get('BARCODE', '').strip()
                matname = row.get('MATNAME', '').strip()
                cleng = float(row.get('CLENG', '0').strip() or '0')
                cwidth = float(row.get('CWIDTH', '0').strip() or '0')
                cnt = float(row.get('CNT', '0').strip() or '0')
                customer = row.get('CUSTOMER', '').strip()
                
                key = (barcode, matname, cleng, cwidth)
                
                # Determine status
                status = 'new'
                old_qty = 0
                if key in existing_items:
                    existing_item = existing_items[key]
                    old_qty = float(existing_item.cnt)
                    if old_qty < cnt:
                        status = 'updated'
                    else:
                        status = 'duplicate'
                
                all_items.append({
                    'barcode': barcode,
                    'matname': matname,
                    'cleng': cleng,
                    'cwidth': cwidth,
                    'dimensions': f"{cleng} x {cwidth}",
                    'qty': cnt,
                    'old_qty': old_qty,
                    'status': status,
                    'customer': customer
                })
                    
            except (ValueError, KeyError) as e:
                # Skip rows with invalid data
                continue
        
        # Store the PNX content in session for later use
        request.session[f'pending_pnx_{order_id}'] = pnx_content
        request.session.modified = True  # Ensure session is saved
        
        return JsonResponse({
            'success': True,
            'all_items': all_items,
            'total_items': len(all_items),
            'has_existing': len(existing_items) > 0
        })
        
    except Exception as e:
        import traceback
        error_detail = traceback.format_exc()
        logger.error(f"Error analyzing PNX for order {order_id}: {error_detail}")
        return JsonResponse({'success': False, 'error': f'Error analyzing PNX: {str(e)}'})


@login_required
def confirm_pnx_generation(request, order_id):
    """Actually save the PNX items after user confirmation"""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Invalid request method'})
    
    order = get_object_or_404(Order, id=order_id)
    
    # Get the pending PNX content from session
    pnx_content = request.session.get(f'pending_pnx_{order_id}')
    
    if not pnx_content:
        return JsonResponse({'success': False, 'error': 'No pending PNX data found. Please generate PNX again.'})
    
    # Get force import barcodes from request body
    import json
    force_import_barcodes = []
    try:
        body_data = json.loads(request.body.decode('utf-8'))
        force_import_barcodes = body_data.get('force_import_barcodes', [])
    except (json.JSONDecodeError, ValueError):
        pass
    
    return confirm_pnx_generation_internal(request, order_id, pnx_content, force_import_barcodes)


def confirm_pnx_generation_internal(request, order_id, pnx_content, force_import_barcodes=None):
    """Internal function to save PNX items - can be called directly or via confirm endpoint"""
    order = get_object_or_404(Order, id=order_id)
    
    if force_import_barcodes is None:
        force_import_barcodes = []
    
    try:
        # Save PNX file to boards PO
        filename = f"Boards_Order_{order.boards_po.po_number}.pnx"
        order.boards_po.file.save(filename, io.BytesIO(pnx_content.encode('utf-8')))
        
        # Parse PNX and create/update PNX items
        io_string = io.StringIO(pnx_content)
        reader = csv.DictReader(io_string, delimiter=';')
        
        # Get existing items for update detection
        existing_items = {
            (item.barcode, item.matname, float(item.cleng), float(item.cwidth)): item
            for item in order.boards_po.pnx_items.all()
        }
        
        items_created = 0
        items_updated = 0
        
        for row in reader:
            # Skip empty rows
            if not row.get('BARCODE', '').strip():
                continue
                
            try:
                customer_field = row.get('CUSTOMER', '').strip()
                ordername_field = row.get('ORDERNAME', '').strip()
                
                # Build structured customer value: SITE-DESIGNER-SALENUMBER
                # Site: default to BFS
                site = "BFS"
                
                # Designer: get initials from order's designer
                designer_initials = ""
                if order.designer:
                    name_parts = order.designer.name.split()
                    designer_initials = ''.join([part[0].upper() for part in name_parts if part])
                
                # Sale number from order
                sale_number = order.sale_number
                
                # Format: BFS-SD-413491
                customer_value = f"{site}-{designer_initials}-{sale_number}" if designer_initials else f"{site}-{sale_number}"
                
                barcode = row.get('BARCODE', '').strip()
                matname = row.get('MATNAME', '').strip()
                cleng = Decimal(row.get('CLENG', '0').strip() or '0')
                cwidth = Decimal(row.get('CWIDTH', '0').strip() or '0')
                cnt = Decimal(row.get('CNT', '0').strip() or '0')
                
                # Debug logging
                if items_created == 0 and items_updated == 0:
                    logger.info(f"First PNX item - customer_value: '{customer_value}', order.sale_number: '{order.sale_number}'")
                
                key = (barcode, matname, float(cleng), float(cwidth))
                
                # Check if this barcode should be force-imported (user moved it from duplicates)
                force_import = barcode in force_import_barcodes
                
                # Check if item exists and needs updating
                if key in existing_items:
                    existing_item = existing_items[key]
                    # Update if quantity increased OR if force importing
                    if existing_item.cnt < cnt or force_import:
                        # Update quantity, customer field, and edging fields
                        existing_item.cnt = cnt
                        existing_item.customer = customer_value
                        existing_item.grain = row.get('GRAIN', '').strip()
                        existing_item.articlename = row.get('ARTICLENAME', '').strip()
                        existing_item.partdesc = row.get('PARTDESC', '').strip()
                        existing_item.prfid1 = row.get('PRFID1', '').strip()
                        existing_item.prfid2 = row.get('PRFID2', '').strip()
                        existing_item.prfid3 = row.get('PRFID3', '').strip()
                        existing_item.prfid4 = row.get('PRFID4', '').strip()
                        existing_item.ordername = row.get('ORDERNAME', '').strip()
                        existing_item.save()
                        items_updated += 1
                    # If it's a duplicate and not force importing, skip it
                else:
                    # Create new item
                    PNXItem.objects.create(
                        boards_po=order.boards_po,
                        barcode=barcode,
                        matname=matname,
                        cleng=cleng,
                        cwidth=cwidth,
                        cnt=cnt,
                        customer=customer_value,
                        grain=row.get('GRAIN', '').strip(),
                        articlename=row.get('ARTICLENAME', '').strip(),
                        partdesc=row.get('PARTDESC', '').strip(),
                        prfid1=row.get('PRFID1', '').strip(),
                        prfid2=row.get('PRFID2', '').strip(),
                        prfid3=row.get('PRFID3', '').strip(),
                        prfid4=row.get('PRFID4', '').strip(),
                        ordername=row.get('ORDERNAME', '').strip()
                    )
                    items_created += 1
                    
            except (ValueError, KeyError) as e:
                # Skip rows with invalid data
                continue
        
        order.boards_po.save()
        
        # Generate and save CSV file alongside PNX
        try:
            csv_output = io.StringIO()
            csv_writer = csv.writer(csv_output)
            
            # Write header - exact order as specified
            csv_writer.writerow(['BARCODE', 'MATNAME', 'CLENG', 'CWIDTH', 'CNT', 'GRAIN', 'CUSTOMER', 'ORDERNAME', 'ARTICLENAME', 'PARTDESC', 'PRFID1', 'PRFID3', 'PRFID4', 'PRFID2'])
            
            # Build CUSTOMER field for CSV
            designer_initials = ''
            if order.designer and order.designer.name:
                designer_initials = ''.join(word[0].upper() for word in order.designer.name.split() if word)
            csv_customer_value = f"BFS-{designer_initials}-{order.sale_number}"
            
            # Re-parse PNX for CSV generation (to include all fields)
            io_string_csv = io.StringIO(pnx_content)
            reader_csv = csv.DictReader(io_string_csv, delimiter=';')
            
            for row in reader_csv:
                if not row.get('BARCODE', '').strip():
                    continue
                csv_writer.writerow([
                    row.get('BARCODE', '').strip(),
                    row.get('MATNAME', '').strip(),
                    row.get('CLENG', '').strip(),
                    row.get('CWIDTH', '').strip(),
                    row.get('CNT', '').strip(),
                    row.get('GRAIN', '').strip(),
                    csv_customer_value,
                    row.get('ORDERNAME', '').strip(),
                    row.get('ARTICLENAME', '').strip(),
                    row.get('PARTDESC', '').strip(),
                    row.get('PRFID1', '').strip(),
                    row.get('PRFID3', '').strip(),
                    row.get('PRFID4', '').strip(),
                    row.get('PRFID2', '').strip(),
                ])
            
            csv_filename = f"Boards_Order_{order.boards_po.po_number}.csv"
            order.boards_po.csv_file.save(csv_filename, io.BytesIO(csv_output.getvalue().encode('utf-8')))
            logger.info(f"CSV file generated and saved: {csv_filename}")
        except Exception as csv_error:
            logger.warning(f"Failed to generate CSV file: {csv_error}")
        
        # Clear the session data if it exists
        if f'pending_pnx_{order_id}' in request.session:
            del request.session[f'pending_pnx_{order_id}']
        
        logger.info(f"PNX generation complete for order {order_id} - Created: {items_created}, Updated: {items_updated}")
        logger.info(f"Order sale_number: '{order.sale_number}'")
        
        # Check if items can be found with the filter
        matching_items = order.boards_po.pnx_items.filter(customer__icontains=order.sale_number).count()
        logger.info(f"Items matching filter (customer__icontains='{order.sale_number}'): {matching_items}")
        
        # Show sample of what customer values actually are
        sample_items = order.boards_po.pnx_items.all()[:3]
        for item in sample_items:
            logger.info(f"Sample PNX item - barcode: {item.barcode}, customer: '{item.customer}'")
        
        return JsonResponse({
            'success': True,
            'items_created': items_created,
            'items_updated': items_updated,
            'po_number': order.boards_po.po_number,
            'auto_generated': True  # Flag to indicate this was auto-generated
        })
        
    except Exception as e:
        import traceback
        error_detail = traceback.format_exc()
        logger.error(f"Error confirming PNX for order {order_id}: {error_detail}")
        return JsonResponse({'success': False, 'error': f'Error saving PNX: {str(e)}'})


@login_required
def delete_pnx_items(request):
    """Delete multiple PNX items"""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Invalid request method'})
    
    try:
        import json
        data = json.loads(request.body)
        item_ids = data.get('item_ids', [])
        
        if not item_ids:
            return JsonResponse({'success': False, 'error': 'No items selected for deletion'})
        
        # Delete the items
        deleted_count = PNXItem.objects.filter(id__in=item_ids).delete()[0]
        
        return JsonResponse({
            'success': True,
            'deleted_count': deleted_count
        })
        
    except Exception as e:
        logger.error(f"Error deleting PNX items: {str(e)}")
        return JsonResponse({'success': False, 'error': f'Error deleting items: {str(e)}'})


@login_required
def generate_and_upload_accessories_csv(request, order_id):
    """Generate accessories CSV from CAD database and upload it to the order"""
    from material_generator import workguru_logic
    from django.conf import settings
    import os
    
    order = get_object_or_404(Order, id=order_id)
    
    if not order.customer_number:
        messages.error(request, 'Order must have a CAD Number to generate accessories CSV.')
        return redirect('order_details', order_id=order_id)
    
    try:
        # Get database paths - look in project root directory (where .env is)
        cad_db_path = os.path.join(settings.BASE_DIR, 'cad_data.db')
        products_db_path = os.path.join(settings.BASE_DIR, 'order_generator_files', 'src', 'products.db')
        
        if not os.path.exists(cad_db_path):
            messages.error(request, f'CAD database not found at: {cad_db_path}. Please ensure cad_data.db is in the project root directory.')
            return redirect('order_details', order_id=order_id)
        
        if not os.path.exists(products_db_path):
            messages.error(request, f'Products database not found at: {products_db_path}.')
            return redirect('order_details', order_id=order_id)
        
        # Generate CSV content using customer number (CAD number)
        logger.info(f"Generating accessories CSV for CAD Number: {order.customer_number}")
        csv_content = workguru_logic.generate_workguru_csv(
            int(order.customer_number), 
            cad_db_path, 
            products_db_path
        )
        
        if not csv_content or csv_content.strip() == '':
            messages.warning(request, f'No accessory data found for CAD Number {order.customer_number}.')
            return redirect('order_details', order_id=order_id)
        
        # Save to order
        filename = f"{order.customer_number}_WG_Accessories.csv"
        order.processed_csv.save(filename, io.BytesIO(csv_content.encode('utf-8')))
        order.processed_csv_created_at = timezone.now()
        
        # Parse CSV and create/update accessories
        io_string = io.StringIO(csv_content)
        reader = csv.DictReader(io_string)
        
        accessories_created = 0
        accessories_updated = 0
        missing_items = 0
        substitutions_used = 0
        substitutions_created = 0
        
        # Safe decimal conversion
        def safe_decimal(value, default='0'):
            try:
                cleaned = str(value).strip() or default
                return Decimal(cleaned)
            except (ValueError, TypeError, InvalidOperation):
                return Decimal(default)
        
        # Helper to safely get string values
        def safe_str(value):
            if value is None:
                return ''
            return str(value).strip()
        
        rows_processed = 0
        for row in reader:
            rows_processed += 1
            sku = safe_str(row.get('Sku', ''))
            
            # Log ALL fields from the row to debug
            logger.info(f"Row {rows_processed} raw data: {dict(row)}")
            
            if not sku:
                logger.info(f"Skipping row {rows_processed}: empty SKU")
                continue
            
            # Check if this is an OS Door requirement indicator
            if 'DOR_VNL_OSD_MTM' in sku:
                order.os_doors_required = True
                continue
            
            # Parse billable field early
            billable_str = safe_str(row.get('Billable', '')) or 'TRUE'
            billable = billable_str.upper() == 'TRUE'
            
            # Get initial values from CSV
            description_value = safe_str(row.get('Description', ''))
            quantity_value = safe_str(row.get('Quantity', ''))
            name_value = safe_str(row.get('Name', ''))
            
            # Check if this is a MISSING item (quantity in Description, "MISSING" in CostPrice/SellPrice)
            cost_price_value = safe_str(row.get('CostPrice', ''))
            is_missing_item = cost_price_value.upper() == 'MISSING'
            
            # For MISSING items, quantity is in the Description column
            if is_missing_item:
                # The Description column contains the quantity for MISSING items
                final_quantity = safe_decimal(description_value, '0')
                final_description = name_value  # Name becomes description for MISSING items
                logger.info(f"Row {rows_processed}: MISSING item detected (CostPrice=MISSING), qty from Description: {final_quantity}")
            else:
                final_quantity = safe_decimal(quantity_value, '0')
                final_description = description_value
            
            # Store the quantity from CSV - we'll use this even after substitution
            csv_quantity = final_quantity
            
            # Try to find matching stock item
            stock_item = None
            missing = is_missing_item  # Start with missing flag if detected from CSV format
            substituted = False
            final_sku = sku
            final_name = name_value
            final_cost_price = safe_decimal(cost_price_value, '0') if not is_missing_item else Decimal('0')
            final_sell_price = safe_decimal(row.get('SellPrice', '0'))
            final_billable = billable
            
            # Count missing items from CSV format
            if is_missing_item:
                missing_items += 1
            
            try:
                stock_item = StockItem.objects.get(sku=sku)
            except StockItem.DoesNotExist:
                # Check for substitution
                try:
                    substitution = Substitution.objects.get(missing_sku=sku)
                    replacement_sku = substitution.replacement_sku
                    if replacement_sku and replacement_sku.strip():
                        # Apply substitution - use the REPLACEMENT SKU
                        final_sku = replacement_sku.strip()
                        final_name = substitution.replacement_name or substitution.missing_name or name_value
                        final_description = substitution.description or final_name
                        
                        # Use cost from substitution if available
                        if substitution.cost_price is not None and substitution.cost_price > 0:
                            final_cost_price = substitution.cost_price
                        
                        final_sell_price = Decimal('0')  # Always 0 for substitutions
                        
                        # ALWAYS use the quantity from CSV, not from substitution
                        # (csv_quantity was already extracted correctly for MISSING items)
                        final_quantity = csv_quantity
                        
                        final_billable = False  # Always FALSE for substitutions
                        missing = False  # No longer missing - we have a replacement
                        substituted = True
                        
                        # Try to find the replacement stock item
                        try:
                            stock_item = StockItem.objects.get(sku=final_sku)
                            # Use the stock item's cost
                            if stock_item.cost and stock_item.cost > 0:
                                final_cost_price = stock_item.cost
                            substitutions_used += 1
                            logger.info(f"Substitution applied: {sku} -> {final_sku}, qty={final_quantity}, cost={final_cost_price}")
                        except StockItem.DoesNotExist:
                            stock_item = None
                            # Fall back to substitution cost if no stock item
                            if substitution.cost_price is not None and substitution.cost_price > 0:
                                final_cost_price = substitution.cost_price
                            substitutions_used += 1
                            logger.info(f"Substitution applied but replacement stock item not found: {sku} -> {final_sku}, cost={final_cost_price}")
                    else:
                        # Substitution exists but no replacement SKU defined yet
                        missing = True
                        logger.info(f"Substitution exists but no replacement SKU for: {sku}")
                except Substitution.DoesNotExist:
                    # Create substitution for manual handling - item is missing
                    logger.info(f"Creating substitution for missing SKU: {sku}, qty={csv_quantity}")
                    Substitution.objects.create(
                        missing_sku=sku,
                        missing_name=name_value,
                        replacement_sku='',
                        replacement_name='',
                        description='Auto-created from CSV generation',
                        cost_price=Decimal('0'),
                        sell_price=Decimal('0'),
                        quantity=int(csv_quantity) if csv_quantity else 0,
                        billable=False
                    )
                    missing = True
                    substitutions_created += 1
            
            # Check if accessory already exists
            existing_accessory = Accessory.objects.filter(order=order, sku=final_sku).first()
            
            logger.info(f"Creating/updating accessory: SKU={final_sku}, Name={final_name}, Qty={final_quantity}, Missing={missing}")
            
            try:
                if existing_accessory:
                    # Update existing
                    existing_accessory.name = final_name
                    existing_accessory.description = final_description
                    existing_accessory.cost_price = final_cost_price
                    existing_accessory.sell_price = final_sell_price
                    existing_accessory.quantity = final_quantity
                    existing_accessory.billable = final_billable
                    existing_accessory.stock_item = stock_item
                    existing_accessory.missing = missing
                    existing_accessory.save()
                    accessories_updated += 1
                    logger.info(f"Updated accessory: {final_sku}")
                else:
                    # Create new
                    Accessory.objects.create(
                        order=order,
                        sku=final_sku,
                        name=final_name,
                        description=final_description,
                        cost_price=final_cost_price,
                        sell_price=final_sell_price,
                        quantity=final_quantity,
                        billable=final_billable,
                        stock_item=stock_item,
                        missing=missing
                    )
                    accessories_created += 1
                    logger.info(f"Created accessory: {final_sku}")
            except Exception as acc_error:
                logger.error(f"Error creating/updating accessory {final_sku}: {acc_error}")
                continue
        
        # Set flag if there are missing items
        if substitutions_created > 0:
            order.csv_has_missing_items = True
        
        order.save()
        
        logger.info(f"CSV processing complete: {rows_processed} rows processed, {accessories_created} created, {accessories_updated} updated, {missing_items} missing")
        
        # Count rows (excluding header)
        row_count = csv_content.count('\n') - 1
        
        # Build success message
        msg_parts = [f'Accessories CSV generated with {row_count} items.']
        if accessories_created or accessories_updated:
            msg_parts.append(f'Created: {accessories_created}, Updated: {accessories_updated}')
        if substitutions_used:
            msg_parts.append(f'Substitutions applied: {substitutions_used}')
        if substitutions_created:
            msg_parts.append(f'New substitutions created: {substitutions_created}')
        
        messages.success(request, ' '.join(msg_parts))
        
        # Return file as download
        response = HttpResponse(csv_content.encode('utf-8'), content_type='text/csv')
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response
        
    except Exception as e:
        import traceback
        error_detail = traceback.format_exc()
        logger.error(f"Error generating accessories CSV for order {order_id}: {error_detail}")
        messages.error(request, f'Error generating CSV: {str(e)}')
        return redirect('order_details', order_id=order_id)


# Timesheet API Views
@require_http_methods(["GET"])
def get_fitters(request):
    """Get all active fitters"""
    from .models import Fitter
    fitters = Fitter.objects.filter(active=True).values('id', 'name', 'hourly_rate')
    return JsonResponse({'fitters': list(fitters)})


@require_http_methods(["GET"])
def get_factory_workers(request):
    """Get all active factory workers"""
    from .models import FactoryWorker
    workers = FactoryWorker.objects.filter(active=True).values('id', 'name', 'hourly_rate')
    return JsonResponse({'workers': list(workers)})


@require_http_methods(["POST"])
def add_fitter(request):
    """Add a new fitter"""
    try:
        import json
        from .models import Fitter
        data = json.loads(request.body)
        
        name = data.get('name', '').strip()
        
        if not name:
            return JsonResponse({'success': False, 'error': 'Name is required'})
        
        fitter = Fitter.objects.create(
            name=name,
            email='',
            phone='',
            hourly_rate=0,
            active=True
        )
        
        return JsonResponse({
            'success': True,
            'fitter': {
                'id': fitter.id,
                'name': fitter.name
            }
        })
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@require_http_methods(["POST"])
def add_factory_worker(request):
    """Add a new factory worker"""
    try:
        import json
        from .models import FactoryWorker
        data = json.loads(request.body)
        
        name = data.get('name', '').strip()
        
        if not name:
            return JsonResponse({'success': False, 'error': 'Name is required'})
        
        worker = FactoryWorker.objects.create(
            name=name,
            hourly_rate=data.get('hourly_rate', 0),
            active=True
        )
        
        return JsonResponse({
            'success': True,
            'worker': {
                'id': worker.id,
                'name': worker.name,
                'hourly_rate': str(worker.hourly_rate),
            }
        })
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@require_http_methods(["POST"])
def add_timesheet(request, order_id):
    """Add a new timesheet for an order"""
    try:
        import json
        from .models import Timesheet, Expense, Fitter, FactoryWorker
        
        order = get_object_or_404(Order, id=order_id)
        data = json.loads(request.body)
        
        timesheet_type = data.get('timesheet_type')
        date = data.get('date')
        description = data.get('description', '')
        
        # Auto-set date to today if not provided
        if not date:
            from datetime import date as date_module
            date = date_module.today()
        
        # Create timesheet with different fields based on type
        timesheet = Timesheet(
            order=order,
            timesheet_type=timesheet_type,
            date=date,
            description=description
        )
        
        if timesheet_type == 'installation':
            # Installation uses fixed price (hours optional)
            fitter_id = data.get('fitter_id')
            helper_id = data.get('helper_id')
            installation_factory_worker_id = data.get('installation_factory_worker_id')
            price = data.get('price')
            hours = data.get('hours')  # Optional for installation
            if fitter_id:
                timesheet.fitter = Fitter.objects.get(id=fitter_id)
            if helper_id:
                timesheet.helper = Fitter.objects.get(id=helper_id)
            if installation_factory_worker_id:
                # Factory worker doing installation work
                timesheet.factory_worker = FactoryWorker.objects.get(id=installation_factory_worker_id)
            if price:
                timesheet.price = price
            if hours:
                timesheet.hours = hours
        else:
            # Manufacturing uses hours × hourly_rate (rate from worker)
            worker_id = data.get('factory_worker_id')
            hours = data.get('hours')
            if worker_id:
                factory_worker = FactoryWorker.objects.get(id=worker_id)
                timesheet.factory_worker = factory_worker
                # Always use the worker's current hourly rate
                timesheet.hourly_rate = factory_worker.hourly_rate
            if hours:
                timesheet.hours = hours
        
        timesheet.save()
        
        # Add petrol expense if requested
        if timesheet_type == 'installation' and data.get('petrol_amount'):
            # Use the same date as the timesheet
            if not date:
                from datetime import date as date_module
                date = date_module.today()
            Expense.objects.create(
                order=order,
                fitter=timesheet.fitter,
                expense_type='petrol',
                date=date,
                amount=data.get('petrol_amount'),
                description='Petrol expense'
            )
        
        return JsonResponse({'success': True})
        
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@require_http_methods(["POST"])
def add_multiple_timesheets(request, order_id):
    """Add multiple manufacturing timesheets at once"""
    try:
        import json
        from .models import Timesheet, FactoryWorker
        from datetime import date as date_module
        
        order = get_object_or_404(Order, id=order_id)
        data = json.loads(request.body)
        
        timesheets_data = data.get('timesheets', [])
        
        if not timesheets_data:
            return JsonResponse({'success': False, 'error': 'No timesheets provided'})
        
        created_count = 0
        today = date_module.today()
        
        for ts_data in timesheets_data:
            timesheet_type = ts_data.get('timesheet_type', 'manufacturing')
            worker_id = ts_data.get('factory_worker_id')
            hours = ts_data.get('hours')
            description = ts_data.get('description', '')
            
            if not worker_id or not hours:
                continue  # Skip invalid entries
                
            factory_worker = FactoryWorker.objects.get(id=worker_id)
            
            timesheet = Timesheet(
                order=order,
                timesheet_type=timesheet_type,
                date=today,
                description=description,
                factory_worker=factory_worker,
                hours=hours,
                hourly_rate=factory_worker.hourly_rate
            )
            timesheet.save()
            created_count += 1
        
        return JsonResponse({
            'success': True, 
            'created_count': created_count,
            'message': f'{created_count} timesheets added successfully'
        })
        
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@require_http_methods(["POST"])
def delete_timesheet(request, timesheet_id):
    """Delete a timesheet"""
    try:
        from .models import Timesheet
        timesheet = get_object_or_404(Timesheet, id=timesheet_id)
        timesheet.delete()
        return JsonResponse({'success': True})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@require_http_methods(["POST"])
def delete_expense(request, expense_id):
    """Delete an expense"""
    try:
        from .models import Expense
        expense = get_object_or_404(Expense, id=expense_id)
        expense.delete()
        return JsonResponse({'success': True})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


# ============================================================================
# TIMESHEETS VIEWS
# ============================================================================

@login_required
def timesheets(request):
    """Page to manage timesheets and factory worker rates"""
    from .models import FactoryWorker, Fitter, Timesheet
    
    factory_workers = FactoryWorker.objects.all().order_by('name')
    fitters = Fitter.objects.all().order_by('name')
    timesheets = Timesheet.objects.all().select_related('order', 'fitter', 'factory_worker', 'helper').order_by('-date', '-created_at')[:100]
    
    return render(request, 'stock_take/timesheets.html', {
        'factory_workers': factory_workers,
        'fitters': fitters,
        'timesheets': timesheets,
    })


@require_http_methods(["POST"])
@login_required
def update_factory_worker(request, worker_id):
    """Update a factory worker's details"""
    try:
        import json
        from .models import FactoryWorker
        
        worker = get_object_or_404(FactoryWorker, id=worker_id)
        data = json.loads(request.body)
        
        if 'name' in data:
            worker.name = data['name'].strip()
        if 'hourly_rate' in data:
            worker.hourly_rate = data['hourly_rate']
        if 'active' in data:
            worker.active = data['active']
        
        worker.save()
        
        return JsonResponse({
            'success': True,
            'worker': {
                'id': worker.id,
                'name': worker.name,
                'hourly_rate': str(worker.hourly_rate),
                'active': worker.active,
            }
        })
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@require_http_methods(["POST"])
@login_required
def delete_factory_worker(request, worker_id):
    """Delete a factory worker"""
    try:
        from .models import FactoryWorker
        worker = get_object_or_404(FactoryWorker, id=worker_id)
        worker.delete()
        return JsonResponse({'success': True})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@require_http_methods(["POST"])
@login_required
def update_fitter(request, fitter_id):
    """Update a fitter's details"""
    try:
        import json
        from .models import Fitter
        
        fitter = get_object_or_404(Fitter, id=fitter_id)
        data = json.loads(request.body)
        
        if 'name' in data:
            fitter.name = data['name'].strip()
        if 'hourly_rate' in data:
            fitter.hourly_rate = data['hourly_rate']
        if 'active' in data:
            fitter.active = data['active']
        
        fitter.save()
        
        return JsonResponse({
            'success': True,
            'fitter': {
                'id': fitter.id,
                'name': fitter.name,
                'hourly_rate': str(fitter.hourly_rate),
                'active': fitter.active,
            }
        })
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@require_http_methods(["POST"])
@login_required
def delete_fitter(request, fitter_id):
    """Delete a fitter"""
    try:
        from .models import Fitter
        fitter = get_object_or_404(Fitter, id=fitter_id)
        fitter.delete()
        return JsonResponse({'success': True})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})

# ============================================================================
# COSTING REPORT VIEW
# ============================================================================

@login_required
def costing_report(request):
    """Costing report showing fully and partially costed orders with statistics"""
    from .models import Order, Timesheet
    from django.db.models import Avg, Sum, Count, F, ExpressionWrapper, DecimalField, Q
    from decimal import Decimal
    from datetime import date
    import statistics
    
    # Get all completed orders (job_finished=True OR fit_date has passed) for costing analysis
    today = date.today()
    all_orders = Order.objects.filter(
        Q(job_finished=True) | Q(fit_date__lte=today)
    ).prefetch_related('timesheets').distinct()
    
    # Calculate actual costs from timesheets for each order
    orders_with_costs = []
    for order in all_orders:
        # Calculate installation cost from timesheets, fall back to stored value
        installation_timesheets = order.timesheets.filter(timesheet_type='installation')
        calculated_installation = sum(ts.total_cost for ts in installation_timesheets) or Decimal('0')
        # Use timesheet total if available, otherwise use stored order value
        installation_cost = calculated_installation if calculated_installation > 0 else (order.installation_cost or Decimal('0'))
        
        # Calculate manufacturing cost from timesheets, fall back to stored value
        manufacturing_timesheets = order.timesheets.filter(timesheet_type='manufacturing')
        calculated_manufacturing = sum(ts.total_cost for ts in manufacturing_timesheets) or Decimal('0')
        # Use timesheet total if available, otherwise use stored order value
        manufacturing_cost = calculated_manufacturing if calculated_manufacturing > 0 else (order.manufacturing_cost or Decimal('0'))
        
        # Get materials cost (from order field)
        materials_cost = order.materials_cost or Decimal('0')
        
        # Total costs
        total_cost = materials_cost + installation_cost + manufacturing_cost
        
        # Calculate profit
        revenue = order.total_value_exc_vat or Decimal('0')
        profit = revenue - total_cost
        profit_margin = (profit / revenue * 100) if revenue > 0 else Decimal('0')
        
        # Determine costing status - use the explicit fully_costed checkbox
        has_materials = materials_cost > 0
        has_installation = installation_cost > 0
        has_manufacturing = manufacturing_cost > 0
        is_fully_costed = order.fully_costed  # Use explicit checkbox instead of auto-detection
        
        orders_with_costs.append({
            'order': order,
            'materials_cost': materials_cost,
            'installation_cost': installation_cost,
            'manufacturing_cost': manufacturing_cost,
            'total_cost': total_cost,
            'revenue': revenue,
            'profit': profit,
            'profit_margin': profit_margin,
            'has_materials': has_materials,
            'has_installation': has_installation,
            'has_manufacturing': has_manufacturing,
            'is_fully_costed': is_fully_costed,
        })
    
    # Separate fully costed and partially costed
    fully_costed = [o for o in orders_with_costs if o['is_fully_costed']]
    partially_costed = [o for o in orders_with_costs if not o['is_fully_costed']]
    
    # Calculate statistics for fully costed orders
    stats = {
        'total_orders': len(all_orders),
        'fully_costed_count': len(fully_costed),
        'partially_costed_count': len(partially_costed),
        'costing_completion_rate': (len(fully_costed) / len(all_orders) * 100) if all_orders else 0,
    }
    
    if fully_costed:
        # Profit statistics
        profits = [float(o['profit']) for o in fully_costed]
        profit_margins = [float(o['profit_margin']) for o in fully_costed]
        
        stats['avg_profit'] = sum(profits) / len(profits)
        stats['median_profit'] = statistics.median(profits)
        stats['min_profit'] = min(profits)
        stats['max_profit'] = max(profits)
        
        stats['avg_profit_margin'] = sum(profit_margins) / len(profit_margins)
        stats['median_profit_margin'] = statistics.median(profit_margins)
        
        # Cost breakdowns
        materials_costs = [float(o['materials_cost']) for o in fully_costed]
        installation_costs = [float(o['installation_cost']) for o in fully_costed]
        manufacturing_costs = [float(o['manufacturing_cost']) for o in fully_costed]
        total_costs = [float(o['total_cost']) for o in fully_costed]
        revenues = [float(o['revenue']) for o in fully_costed]
        
        stats['avg_materials_cost'] = sum(materials_costs) / len(materials_costs)
        stats['avg_installation_cost'] = sum(installation_costs) / len(installation_costs)
        stats['avg_manufacturing_cost'] = sum(manufacturing_costs) / len(manufacturing_costs)
        stats['avg_total_cost'] = sum(total_costs) / len(total_costs)
        stats['avg_revenue'] = sum(revenues) / len(revenues)
        
        stats['median_materials_cost'] = statistics.median(materials_costs)
        stats['median_installation_cost'] = statistics.median(installation_costs)
        stats['median_manufacturing_cost'] = statistics.median(manufacturing_costs)
        stats['median_total_cost'] = statistics.median(total_costs)
        stats['median_revenue'] = statistics.median(revenues)
        
        # Cost as percentage of revenue (averages)
        if stats['avg_revenue'] > 0:
            stats['materials_pct_of_revenue'] = (stats['avg_materials_cost'] / stats['avg_revenue']) * 100
            stats['installation_pct_of_revenue'] = (stats['avg_installation_cost'] / stats['avg_revenue']) * 100
            stats['manufacturing_pct_of_revenue'] = (stats['avg_manufacturing_cost'] / stats['avg_revenue']) * 100
        else:
            stats['materials_pct_of_revenue'] = 0
            stats['installation_pct_of_revenue'] = 0
            stats['manufacturing_pct_of_revenue'] = 0
        
        # Cost as percentage of revenue (medians) - calculate percentage per order first, then median
        materials_pcts = [(float(o['materials_cost']) / float(o['revenue'])) * 100 if float(o['revenue']) > 0 else 0 for o in fully_costed]
        installation_pcts = [(float(o['installation_cost']) / float(o['revenue'])) * 100 if float(o['revenue']) > 0 else 0 for o in fully_costed]
        manufacturing_pcts = [(float(o['manufacturing_cost']) / float(o['revenue'])) * 100 if float(o['revenue']) > 0 else 0 for o in fully_costed]
        profit_pcts = [(float(o['profit']) / float(o['revenue'])) * 100 if float(o['revenue']) > 0 else 0 for o in fully_costed]
        
        # Calculate raw medians
        raw_median_materials = statistics.median(materials_pcts)
        raw_median_installation = statistics.median(installation_pcts)
        raw_median_manufacturing = statistics.median(manufacturing_pcts)
        raw_median_profit = statistics.median(profit_pcts)
        
        # Normalize to ensure they add up to 100%
        total_median = raw_median_materials + raw_median_installation + raw_median_manufacturing + raw_median_profit
        if total_median > 0:
            stats['median_materials_pct'] = (raw_median_materials / total_median) * 100
            stats['median_installation_pct'] = (raw_median_installation / total_median) * 100
            stats['median_manufacturing_pct'] = (raw_median_manufacturing / total_median) * 100
            stats['median_profit_pct'] = (raw_median_profit / total_median) * 100
        else:
            stats['median_materials_pct'] = 0
            stats['median_installation_pct'] = 0
            stats['median_manufacturing_pct'] = 0
            stats['median_profit_pct'] = 0
        
        # Total sums
        stats['total_revenue'] = sum(revenues)
        stats['total_costs'] = sum(total_costs)
        stats['total_profit'] = sum(profits)
        stats['total_materials'] = sum(materials_costs)
        stats['total_installation'] = sum(installation_costs)
        stats['total_manufacturing'] = sum(manufacturing_costs)
    else:
        # Set defaults if no fully costed orders
        stats['avg_profit'] = 0
        stats['median_profit'] = 0
        stats['min_profit'] = 0
        stats['max_profit'] = 0
        stats['avg_profit_margin'] = 0
        stats['median_profit_margin'] = 0
        stats['avg_materials_cost'] = 0
        stats['avg_installation_cost'] = 0
        stats['avg_manufacturing_cost'] = 0
        stats['avg_total_cost'] = 0
        stats['avg_revenue'] = 0
        stats['median_materials_cost'] = 0
        stats['median_installation_cost'] = 0
        stats['median_manufacturing_cost'] = 0
        stats['median_total_cost'] = 0
        stats['median_revenue'] = 0
        stats['materials_pct_of_revenue'] = 0
        stats['installation_pct_of_revenue'] = 0
        stats['manufacturing_pct_of_revenue'] = 0
        stats['median_materials_pct'] = 0
        stats['median_installation_pct'] = 0
        stats['median_manufacturing_pct'] = 0
        stats['median_profit_pct'] = 0
        stats['total_revenue'] = 0
        stats['total_costs'] = 0
        stats['total_profit'] = 0
        stats['total_materials'] = 0
        stats['total_installation'] = 0
        stats['total_manufacturing'] = 0
    
    return render(request, 'stock_take/costing_report.html', {
        'fully_costed': fully_costed,
        'partially_costed': partially_costed,
        'stats': stats,
    })