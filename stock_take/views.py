from .forms import OrderForm, BoardsPOForm, OSDoorForm, AccessoryCSVForm, Accessory, SubstitutionForm, CSVSkipItemForm
from .models import Order, BoardsPO, PNXItem, StockItem, Accessory

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
from django.db.models import Sum, F, Count, Q
from django.db import models
from .models import StockItem, ImportHistory, Category, Schedule, StockTakeGroup, Substitution, CSVSkipItem
from django.template.loader import render_to_string
import datetime
from django.utils import timezone

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
    
    # Sort by job_finished first (incomplete first), then by boards_po.po_number (nulls last), then by order_date descending
    orders = Order.objects.all().order_by(
        'job_finished',  # Incomplete orders first
        models.F('boards_po__po_number').asc(nulls_last=True),
        '-order_date'
    )
    boards_pos = BoardsPO.objects.all().order_by('po_number')
    form = OrderForm(request.POST or None, initial={'order_type': 'sale'})
    po_form = BoardsPOForm()
    accessories_csv_form = AccessoryCSVForm()
    
    # Add PNX items to each order object for template access
    for order in orders:
        if order.boards_po:
            order.order_pnx_items = order.boards_po.pnx_items.filter(customer__icontains=order.sale_number)
            # Calculate cost for each item and total
            for item in order.order_pnx_items:
                item.calculated_cost = item.get_cost(price_per_sqm)
            order.pnx_total_cost = sum(item.calculated_cost for item in order.order_pnx_items)
        else:
            order.order_pnx_items = []
            order.pnx_total_cost = 0
    
    if request.method == 'POST' and form.is_valid():
        form.save()
        return redirect('ordering')
    
    return render(request, 'stock_take/ordering.html', {
        'orders': orders,
        'boards_pos': boards_pos,
        'form': form,
        'po_form': po_form,
        'accessories_csv_form': accessories_csv_form,
        'price_per_sqm': price_per_sqm,
    })

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
        boards_pos_in_range = BoardsPO.objects.filter(orders__in=orders_in_range).distinct()
        pnx_query = PNXItem.objects.filter(boards_po__in=boards_pos_in_range)
    else:
        pnx_query = PNXItem.objects.all()
    
    for item in pnx_query:
        key = f"PNX-{item.matname}"
        materials[key]['quantity'] += float(item.cnt)
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
    
    for accessory in accessory_query:
        key = f"ACC-{accessory.sku}-{accessory.name}"
        materials[key]['quantity'] += float(accessory.quantity)
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
def create_boards_po(request):
    """Create a new BoardsPO entry and parse PNX file for items"""
    if request.method == 'POST':
        form = BoardsPOForm(request.POST, request.FILES)
        if form.is_valid():
            boards_po = form.save()
            
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
                            PNXItem.objects.create(
                                boards_po=boards_po,
                                barcode=row.get('BARCODE', '').strip(),
                                matname=row.get('MATNAME', '').strip(),
                                cleng=Decimal(row.get('CLENG', '0').strip() or '0'),
                                cwidth=Decimal(row.get('CWIDTH', '0').strip() or '0'),
                                cnt=Decimal(row.get('CNT', '0').strip() or '0'),
                                customer=row.get('CUSTOMER', '').strip()
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
                    PNXItem.objects.create(
                        boards_po=boards_po,
                        barcode=row.get('BARCODE', '').strip(),
                        matname=row.get('MATNAME', '').strip(),
                        cleng=Decimal(row.get('CLENG', '0').strip() or '0'),
                        cwidth=Decimal(row.get('CWIDTH', '0').strip() or '0'),
                        cnt=Decimal(row.get('CNT', '0').strip() or '0'),
                        customer=row.get('CUSTOMER', '').strip()
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
def order_details(request, order_id):
    """Display and edit order details, including boards PO assignment"""
    order = get_object_or_404(Order, id=order_id)
    
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
    
    return render(request, 'stock_take/order_details.html', {
        'order': order,
        'form': form,
        'os_door_form': os_door_form,
        'other_orders': other_orders,
        'order_pnx_items': order_pnx_items,
        'pnx_total_cost': pnx_total_cost,
        'has_os_door_accessories': has_os_door_accessories,
        'price_per_sqm': price_per_sqm,
    })

def completed_stock_takes(request):
    """Display only completed stock takes"""
    completed_schedules = Schedule.objects.filter(
        status='completed',
        completed_date__isnull=False
    ).order_by('-completed_date')
    
    return render(request, 'stock_take/completed_stock_takes.html', {
        'completed_schedules': completed_schedules
    })

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
            for row in reader:
                sku = row.get('Sku', '').strip()
                name = row.get('Name', '').strip()
                cost_str = row.get('Cost', '0')
                location = row.get('Location', '').strip()
                quantity_str = row.get('Quantity', '0')
                serial_or_batch = row.get('SerialOrBatch', '').strip()
                category_name = row.get('Category', '').strip()

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
                        'serial_or_batch': serial_or_batch
                    }
                )
                if not created:
                    # Update fields
                    item.name = name
                    item.cost = cost
                    item.category = category
                    item.category_name = category_name
                    item.location = location
                    item.quantity = quantity
                    item.serial_or_batch = serial_or_batch
                    item.save()
                    items_updated += 1
                else:
                    items_created += 1

            # Record import history
            ImportHistory.objects.create(
                filename=csv_file.name,
                record_count=items_created + items_updated
            )

            # Reset pending schedules and re-populate
            reset_and_populate_schedules()

            messages.success(request, f'Imported {items_created} new items, updated {items_updated} items from {csv_file.name}')
        except Exception as e:
            messages.error(request, f'Error importing CSV: {str(e)}')

    return redirect('stock_list')

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
    """Display all import history records"""
    imports = ImportHistory.objects.all().order_by('-imported_at')
    return render(request, 'stock_take/import_history.html', {
        'imports': imports
    })

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
    # Auto-create schedules first
    auto_create_stock_take_schedules()
    
    items = StockItem.objects.select_related('category', 'stock_take_group').all()
    
    # Apply filters
    search = request.GET.get('search', '')
    category_filter = request.GET.get('category', '')
    stock_take_group_filter = request.GET.get('stock_take_group', '')
    
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
    
    latest_import = ImportHistory.objects.first()
    import_history = ImportHistory.objects.all()[:10]  # Last 10 imports
    
    # Calculate statistics
    all_items = StockItem.objects.select_related('category', 'stock_take_group').all()
    total_value = all_items.aggregate(
        total=Sum(F('cost') * F('quantity'))
    )['total'] or Decimal('0')
    
    # Separate items by stock status
    in_stock_items = items.filter(quantity__gte=10).select_related('category', 'stock_take_group')
    low_stock_items = items.filter(quantity__gte=1, quantity__lt=10).select_related('category', 'stock_take_group')
    zero_quantity_items = items.filter(quantity=0).select_related('category', 'stock_take_group')
    
    # Items needing stock takes
    items_needing_stock_take = items.filter(
        stock_take_group__isnull=False
    ).filter(
        Q(quantity__lte=F('stock_take_group__auto_schedule_threshold')) |
        Q(last_checked__lt=timezone.now() - timezone.timedelta(days=30))
    ).select_related('category', 'stock_take_group')
    
    # Get counts for all items (not just filtered)
    total_items_count = all_items.count()
    zero_quantity_count = all_items.filter(quantity=0).count()
    low_stock_count = all_items.filter(quantity__lt=10, quantity__gt=0).count()
    in_stock_count = all_items.filter(quantity__gt=5).count()
    
    # Get filter options
    categories = Category.objects.all()
    stock_take_groups = StockTakeGroup.objects.select_related('category').all()
    
    return render(request, 'stock_take/stock_list.html', {
        'in_stock_items': in_stock_items,
        'low_stock_items': low_stock_items,
        'zero_quantity_items': zero_quantity_items,
        'items_needing_stock_take': items_needing_stock_take,
        'latest_import': latest_import,
        'import_history': import_history,
        'total_value': total_value,
        'total_items_count': total_items_count,
        'zero_quantity_count': zero_quantity_count,
        'low_stock_count': low_stock_count,
        'in_stock_count': in_stock_count,
        'categories': categories,
        'stock_take_groups': stock_take_groups,
        'current_filters': {
            'search': search,
            'category': category_filter,
            'stock_take_group': stock_take_group_filter,
        }
    })

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
            
            item.save()
            
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

