from .forms import OrderForm, BoardsPOForm
from .models import Order, BoardsPO

import csv
import io
from decimal import Decimal
from django.shortcuts import render, redirect, get_object_or_404
from django.http import HttpResponse, JsonResponse
from django.contrib import messages
from django.utils import timezone
from django.db.models import Sum, F, Count, Q
from django.db import models
from .models import StockItem, ImportHistory, Category, Schedule, StockTakeGroup
from django.template.loader import render_to_string
import datetime
from django.utils import timezone

def ordering(request):
    orders = Order.objects.all().order_by('-order_date')
    form = OrderForm(request.POST or None)
    po_form = BoardsPOForm()
    
    if request.method == 'POST' and form.is_valid():
        form.save()
        return redirect('ordering')
    
    return render(request, 'stock_take/ordering.html', {
        'orders': orders,
        'form': form,
        'po_form': po_form
    })

def create_boards_po(request):
    """Create a new BoardsPO entry"""
    if request.method == 'POST':
        form = BoardsPOForm(request.POST, request.FILES)
        if form.is_valid():
            form.save()
            messages.success(request, f'Boards PO {form.cleaned_data["po_number"]} created successfully.')
            return redirect('ordering')
        else:
            messages.error(request, 'Error creating Boards PO. Please check the form.')
    return redirect('ordering')

def update_boards_ordered(request, order_id):
    """Update the boards_ordered status for an order"""
    if request.method == 'POST':
        import json
        try:
            order = get_object_or_404(Order, id=order_id)
            data = json.loads(request.body)
            order.boards_ordered = data.get('boards_ordered', False)
            order.save()
            return JsonResponse({'success': True})
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})
    return JsonResponse({'success': False, 'error': 'Invalid request method'})

def order_details(request, order_id):
    """Display and edit order details, including boards PO assignment"""
    order = get_object_or_404(Order, id=order_id)
    
    if request.method == 'POST':
        form = OrderForm(request.POST, instance=order)
        if form.is_valid():
            form.save()
            messages.success(request, f'Order {order.sale_number} updated successfully.')
            return redirect('order_details', order_id=order_id)
    else:
        form = OrderForm(instance=order)
    
    # Get other orders with the same boards PO (excluding current order)
    other_orders = []
    if order.boards_po:
        other_orders = Order.objects.filter(boards_po=order.boards_po).exclude(id=order.id)
    
    return render(request, 'stock_take/order_details.html', {
        'order': order,
        'form': form,
        'other_orders': other_orders,
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