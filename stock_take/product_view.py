from django.contrib.auth.decorators import login_required
from django.shortcuts import render, get_object_or_404
from .models import StockItem, Category, StockTakeGroup, StockHistory, Accessory
import json
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
    
    # Calculate stock trajectory based on order fit dates
    today = timezone.now().date()
    current_stock = product.quantity
    
    # Get all accessories linked to this stock item with fit dates
    accessories = Accessory.objects.filter(
        stock_item=product,
        order__job_finished=False
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
    
    return render(request, 'stock_take/product_detail.html', {
        'product': product,
        'categories': json.dumps(categories),
        'stock_take_groups': json.dumps(stock_take_groups),
        'tracking_choices': json.dumps(list(StockItem.TRACKING_CHOICES)),
        'stock_history': json.dumps(history_data),
        'allocated': allocated,
        'remaining': remaining,
    })
