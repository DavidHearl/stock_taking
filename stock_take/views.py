import csv
import io
from decimal import Decimal
from django.shortcuts import render, redirect, get_object_or_404
from django.http import HttpResponse, JsonResponse
from django.contrib import messages
from django.utils import timezone
from django.db.models import Sum, F, Count, Q
from django.db import models
from .models import StockItem, ImportHistory, Category, Schedule


def stock_list(request):
    """Display all stock items in a table"""
    items = StockItem.objects.select_related('category').all()
    
    # Apply filters
    search = request.GET.get('search', '')
    category_filter = request.GET.get('category', '')
    location_filter = request.GET.get('location', '')
    
    if search:
        items = items.filter(
            Q(sku__icontains=search) | 
            Q(name__icontains=search) |
            Q(serial_or_batch__icontains=search)
        )
    
    if category_filter:
        items = items.filter(category_id=category_filter)
    
    if location_filter:
        items = items.filter(location__icontains=location_filter)
    
    latest_import = ImportHistory.objects.first()
    
    # Calculate statistics
    all_items = StockItem.objects.select_related('category').all()
    total_value = all_items.aggregate(
        total=Sum(F('cost') * F('quantity'))
    )['total'] or Decimal('0')
    
    # Separate items by stock status
    in_stock_items = items.filter(quantity__gt=5).select_related('category')
    low_stock_items = items.filter(quantity__lte=5, quantity__gt=0).select_related('category')
    zero_quantity_items = items.filter(quantity=0).select_related('category')
    
    # Get counts for all items (not just filtered)
    total_items_count = all_items.count()
    zero_quantity_count = all_items.filter(quantity=0).count()
    low_stock_count = all_items.filter(quantity__lte=5, quantity__gt=0).count()
    in_stock_count = all_items.filter(quantity__gt=5).count()
    
    # Get filter options
    categories = Category.objects.all()
    locations = all_items.values_list('location', flat=True).distinct().order_by('location')
    
    return render(request, 'stock_take/stock_list.html', {
        'in_stock_items': in_stock_items,
        'low_stock_items': low_stock_items,
        'zero_quantity_items': zero_quantity_items,
        'latest_import': latest_import,
        'total_value': total_value,
        'total_items_count': total_items_count,
        'zero_quantity_count': zero_quantity_count,
        'low_stock_count': low_stock_count,
        'in_stock_count': in_stock_count,
        'categories': categories,
        'locations': locations,
        'current_filters': {
            'search': search,
            'category': category_filter,
            'location': location_filter,
        }
    })


def import_csv(request):
    """Import CSV file and clear existing data"""
    if request.method == 'POST':
        csv_file = request.FILES.get('csv_file')
        
        if not csv_file:
            messages.error(request, 'Please select a CSV file.')
            return redirect('stock_list')
        
        if not csv_file.name.endswith('.csv'):
            messages.error(request, 'Please upload a CSV file.')
            return redirect('stock_list')
        
        try:
            # Clear existing data
            StockItem.objects.all().delete()
            
            # Read CSV file
            data_set = csv_file.read().decode('UTF-8')
            io_string = io.StringIO(data_set)
            reader = csv.DictReader(io_string)
            
            items_created = 0
            for row in reader:
                # Handle quantity conversion from decimal to integer
                quantity_str = row.get('Quantity', '0')
                try:
                    quantity = int(float(quantity_str)) if quantity_str else 0
                except (ValueError, TypeError):
                    quantity = 0
                
                # Handle cost conversion
                cost_str = row.get('Cost', '0')
                try:
                    cost = float(cost_str) if cost_str else 0
                except (ValueError, TypeError):
                    cost = 0
                
                # Handle category
                category_name = row.get('Category', '')
                category = None
                if category_name:
                    category, created = Category.objects.get_or_create(
                        name=category_name,
                        defaults={'description': f'Auto-created from CSV import'}
                    )
                
                StockItem.objects.create(
                    sku=row.get('Sku', ''),
                    name=row.get('Name', ''),
                    cost=cost,
                    category=category,
                    category_name=category_name,
                    location=row.get('Location', ''),
                    quantity=quantity,
                    serial_or_batch=row.get('SerialOrBatch', '')
                )
                items_created += 1
            
            # Record import history
            ImportHistory.objects.create(
                filename=csv_file.name,
                record_count=items_created
            )
            
            messages.success(request, f'Successfully imported {items_created} items from {csv_file.name}')
            
        except Exception as e:
            messages.error(request, f'Error importing CSV: {str(e)}')
    
    return redirect('stock_list')

def export_csv(request):
    """Export current database as CSV"""
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
                # Remove £ symbol if present
                cost_str = cost_str.replace('£', '').strip()
                item.cost = float(cost_str)
            except (ValueError, TypeError):
                item.cost = 0
            
            item.location = request.POST.get('location', item.location)
            
            # Handle quantity conversion
            quantity_str = request.POST.get('quantity', str(item.quantity))
            try:
                item.quantity = int(float(quantity_str))
            except (ValueError, TypeError):
                item.quantity = 0
            
            item.serial_or_batch = request.POST.get('serial_or_batch', item.serial_or_batch)
            
            item.save()
            
            return HttpResponse('Success')
        except Exception as e:
            return HttpResponse(f'Error: {str(e)}', status=400)
    
    return HttpResponse('Method not allowed', status=405)


def category_list(request):
    """Display all categories with subcategories"""
    categories = Category.objects.filter(parent=None).prefetch_related(
        'subcategories__stockitem_set'
    ).annotate(
        item_count=models.Count('stockitem')
    ).order_by('name')
    
    return render(request, 'stock_take/categories.html', {
        'categories': categories,
    })

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

def schedule_list(request):
    """Display all schedules grouped by status"""
    all_schedules = Schedule.objects.prefetch_related('categories').all()
    
    pending_schedules = all_schedules.filter(status='pending').order_by('scheduled_date')
    in_progress_schedules = all_schedules.filter(status='in_progress').order_by('scheduled_date')
    completed_schedules = all_schedules.filter(status='completed').order_by('-scheduled_date')
    
    categories = Category.objects.filter(parent=None)
    
    context = {
        'schedules': all_schedules,
        'pending_schedules': pending_schedules,
        'in_progress_schedules': in_progress_schedules,
        'completed_schedules': completed_schedules,
        'pending_count': pending_schedules.count(),
        'in_progress_count': in_progress_schedules.count(),
        'completed_count': completed_schedules.count(),
        'categories': categories,
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
            
            # Add categories
            category_ids = request.POST.getlist('categories')
            if category_ids:
                schedule.categories.set(category_ids)
            
            messages.success(request, 'Schedule created successfully!')
        except Exception as e:
            messages.error(request, f'Error creating schedule: {str(e)}')
    
    return redirect('schedule_list')

def schedule_update_status(request, schedule_id):
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