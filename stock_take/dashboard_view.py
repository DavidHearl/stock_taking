from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Sum, Avg
from django.db.models.functions import TruncWeek, TruncMonth
from django.http import JsonResponse
from datetime import datetime, timedelta
from decimal import Decimal
import json
from .models import Order, PurchaseOrder, StockItem


def _get_monthly_sales_data(year, month):
    """Calculate monthly sales stats for a given year/month."""
    from calendar import monthrange
    first_day = datetime(year, month, 1).date()
    last_day = datetime(year, month, monthrange(year, month)[1]).date()

    orders = Order.objects.filter(
        fit_date__gte=first_day,
        fit_date__lte=last_day,
        total_value_exc_vat__gt=0,
    )
    agg = orders.aggregate(
        total=Sum('total_value_exc_vat'),
        avg=Avg('total_value_exc_vat'),
        count=Count('id'),
    )
    return {
        'total': float(agg['total'] or 0),
        'avg': float(agg['avg'] or 0),
        'count': agg['count'] or 0,
    }


@login_required
def dashboard_monthly_sales(request):
    """AJAX endpoint to get monthly sales data for a given year/month."""
    try:
        year = int(request.GET.get('year', datetime.now().year))
        month = int(request.GET.get('month', datetime.now().month))
    except (ValueError, TypeError):
        return JsonResponse({'error': 'Invalid year/month'}, status=400)

    data = _get_monthly_sales_data(year, month)
    return JsonResponse({'success': True, **data})


@login_required
def dashboard(request):
    """Main dashboard page."""

    # Franchise users go straight to claim service
    profile = getattr(request.user, 'profile', None)
    if profile and profile.role and profile.role.name == 'franchise':
        return redirect('claim_service')
    
    # Get fits per week data (past 52 weeks + future 12 weeks to show scheduled fits)
    today = datetime.now().date()
    start_date = today - timedelta(weeks=52)
    future_date = today + timedelta(weeks=12)
    
    # Get all orders with fit dates in the range (past 52 weeks + future 12 weeks)
    orders_in_range = Order.objects.filter(
        fit_date__gte=start_date,
        fit_date__lte=future_date
    )
    
    # Create a dictionary of all weeks with default data
    weeks_data = {}
    current_date = start_date
    while current_date <= future_date:
        # Get Monday of the week
        week_start = current_date - timedelta(days=current_date.weekday())
        weeks_data[week_start] = {'fits': 0, 'sales': Decimal('0.00')}
        current_date += timedelta(weeks=1)
    
    # Fill in actual data by iterating through orders
    for order in orders_in_range:
        week_start = order.fit_date - timedelta(days=order.fit_date.weekday())
        if week_start in weeks_data:
            weeks_data[week_start]['fits'] += 1
            weeks_data[week_start]['sales'] += order.total_value_exc_vat or Decimal('0.00')
    
    # Convert to lists for Chart.js (sorted by week)
    sorted_weeks = sorted(weeks_data.items())
    
    # If we have data, trim to the range where we have actual fits
    if any(data['fits'] > 0 for _, data in sorted_weeks):
        # Find first and last weeks with fits
        first_fit_idx = next(i for i, (_, data) in enumerate(sorted_weeks) if data['fits'] > 0)
        last_fit_idx = len(sorted_weeks) - next(i for i, (_, data) in enumerate(reversed(sorted_weeks)) if data['fits'] > 0) - 1
        
        # Add some padding (4 weeks before and after)
        start_idx = max(0, first_fit_idx - 4)
        end_idx = min(len(sorted_weeks), last_fit_idx + 5)
        sorted_weeks = sorted_weeks[start_idx:end_idx]
    
    labels = [week.strftime('%d %b %Y') for week, _ in sorted_weeks]
    fits_values = [data['fits'] for _, data in sorted_weeks]
    sales_values = [float(data['sales']) for _, data in sorted_weeks]
    
    # Find the index of the current week for the vertical marker line
    this_week_monday = today - timedelta(days=today.weekday())
    current_week_index = None
    for i, (week, _) in enumerate(sorted_weeks):
        if week == this_week_monday:
            current_week_index = i
            break
    
    # This week's sale value
    this_week_start = today - timedelta(days=today.weekday())
    this_week_sales = Order.objects.filter(
        fit_date__gte=this_week_start,
        fit_date__lte=today
    ).aggregate(total=Sum('total_value_exc_vat'))['total'] or Decimal('0.00')
    
    # Get count of approved POs waiting to arrive
    pending_pos = PurchaseOrder.objects.filter(
        status__in=['Approved', 'Ordered', 'Sent']
    ).exclude(
        status__in=['Received', 'Invoiced', 'Cancelled', 'Closed']
    ).count()
    
    # Total stock value
    stock_items = StockItem.objects.filter(tracking_type='stock', quantity__gt=0)
    total_stock_value = sum(item.cost * item.quantity for item in stock_items) or Decimal('0.00')
    stock_item_count = stock_items.count()
    
    # Monthly board costs - aggregate materials_cost by month (past 12 months + future 3 months)
    twelve_months_ago = today.replace(day=1) - timedelta(days=365)
    twelve_months_ago = twelve_months_ago.replace(day=1)  # Start of that month
    three_months_ahead = today.replace(day=1)
    for _ in range(3):
        if three_months_ahead.month == 12:
            three_months_ahead = three_months_ahead.replace(year=three_months_ahead.year + 1, month=1)
        else:
            three_months_ahead = three_months_ahead.replace(month=three_months_ahead.month + 1)
    
    monthly_board_data = (
        Order.objects.filter(
            fit_date__gte=twelve_months_ago,
            fit_date__lte=three_months_ahead,
            materials_cost__gt=0,
        )
        .annotate(month=TruncMonth('fit_date'))
        .values('month')
        .annotate(total_cost=Sum('materials_cost'))
        .order_by('month')
    )
    
    # Build a complete month range (even months with no data)
    board_cost_months = {}
    current_month = twelve_months_ago
    while current_month <= three_months_ahead:
        board_cost_months[current_month] = Decimal('0.00')
        # Advance to next month
        if current_month.month == 12:
            current_month = current_month.replace(year=current_month.year + 1, month=1)
        else:
            current_month = current_month.replace(month=current_month.month + 1)
    
    for entry in monthly_board_data:
        month_key = entry['month']
        if hasattr(month_key, 'date'):
            month_key = month_key.date()
        # Normalize to first of month
        month_key = month_key.replace(day=1)
        if month_key in board_cost_months:
            board_cost_months[month_key] = entry['total_cost']
    
    sorted_board_months = sorted(board_cost_months.items())
    board_cost_labels = [m.strftime('%b %Y') for m, _ in sorted_board_months]
    board_cost_values = [float(v) for _, v in sorted_board_months]

    # Find index of current month for the monthly chart marker
    current_month_start = today.replace(day=1)
    sorted_board_month_keys = [m for m, _ in sorted_board_months]
    current_month_board_index = None
    for i, m in enumerate(sorted_board_month_keys):
        if m == current_month_start:
            current_month_board_index = i
            break

    # Monthly sales totals - aggregate total_value_exc_vat by month (past 12 months + future 3 months)
    monthly_sales_data = (
        Order.objects.filter(
            fit_date__gte=twelve_months_ago,
            fit_date__lte=three_months_ahead,
            total_value_exc_vat__gt=0,
        )
        .annotate(month=TruncMonth('fit_date'))
        .values('month')
        .annotate(total_sales=Sum('total_value_exc_vat'))
        .order_by('month')
    )

    monthly_sales_months = {}
    temp_month = twelve_months_ago
    while temp_month <= three_months_ahead:
        monthly_sales_months[temp_month] = Decimal('0.00')
        if temp_month.month == 12:
            temp_month = temp_month.replace(year=temp_month.year + 1, month=1)
        else:
            temp_month = temp_month.replace(month=temp_month.month + 1)

    for entry in monthly_sales_data:
        month_key = entry['month']
        if hasattr(month_key, 'date'):
            month_key = month_key.date()
        month_key = month_key.replace(day=1)
        if month_key in monthly_sales_months:
            monthly_sales_months[month_key] = entry['total_sales']

    sorted_sales_months = sorted(monthly_sales_months.items())
    monthly_sales_labels = [m.strftime('%b %Y') for m, _ in sorted_sales_months]
    monthly_sales_values = [float(v) for _, v in sorted_sales_months]

    # Find index of current month for the monthly sales chart marker
    sorted_sales_month_keys = [m for m, _ in sorted_sales_months]
    current_month_sales_index = None
    for i, m in enumerate(sorted_sales_month_keys):
        if m == current_month_start:
            current_month_sales_index = i
            break

    # Current month sales data
    current_month_sales = _get_monthly_sales_data(today.year, today.month)
    
    context = {
        'fits_chart_data': json.dumps({
            'labels': labels,
            'values': fits_values,
        }),
        'sales_chart_data': json.dumps({
            'labels': labels,
            'values': sales_values,
        }),
        'board_cost_chart_data': json.dumps({
            'labels': board_cost_labels,
            'values': board_cost_values,
        }),
        'monthly_sales_chart_data': json.dumps({
            'labels': monthly_sales_labels,
            'values': monthly_sales_values,
        }),
        'current_week_index': current_week_index,
        'current_month_board_index': current_month_board_index,
        'current_month_sales_index': current_month_sales_index,
        'pending_pos_count': pending_pos,
        'total_stock_value': '{:,.0f}'.format(total_stock_value),
        'stock_item_count': '{:,}'.format(stock_item_count),
        'this_week_sales': '{:,.0f}'.format(this_week_sales),
        'monthly_sales_total': '{:,.0f}'.format(Decimal(str(current_month_sales['total']))),
        'monthly_sales_avg': '{:,.0f}'.format(Decimal(str(current_month_sales['avg']))),
        'monthly_sales_count': current_month_sales['count'],
        'current_year': today.year,
        'current_month': today.month,
    }
    return render(request, 'stock_take/dashboard.html', context)
