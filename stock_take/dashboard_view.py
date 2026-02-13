from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Sum
from django.db.models.functions import TruncWeek, TruncMonth
from datetime import datetime, timedelta
from decimal import Decimal
import json
from .models import Order, PurchaseOrder


@login_required
def dashboard(request):
    """Main dashboard page."""
    
    # Get fits per week data (max 52 weeks, or less if not enough data)
    today = datetime.now().date()
    start_date = today - timedelta(weeks=52)
    
    # Get all orders with fit dates in the last 52 weeks
    orders_in_range = Order.objects.filter(
        fit_date__gte=start_date,
        fit_date__lte=today
    )
    
    # Create a dictionary of all weeks with default data
    weeks_data = {}
    current_date = start_date
    while current_date <= today:
        # Get Monday of the week
        week_start = current_date - timedelta(days=current_date.weekday())
        weeks_data[week_start] = {'fits': 0, 'profit': Decimal('0.00')}
        current_date += timedelta(weeks=1)
    
    # Fill in actual data by iterating through orders
    for order in orders_in_range:
        week_start = order.fit_date - timedelta(days=order.fit_date.weekday())
        if week_start in weeks_data:
            weeks_data[week_start]['fits'] += 1
            weeks_data[week_start]['profit'] += order.profit
    
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
    profit_values = [float(data['profit']) for _, data in sorted_weeks]
    
    # Get count of approved POs waiting to arrive
    pending_pos = PurchaseOrder.objects.filter(
        status__in=['Approved', 'Ordered', 'Sent']
    ).exclude(
        status__in=['Received', 'Invoiced', 'Cancelled', 'Closed']
    ).count()
    
    # Monthly board costs - aggregate materials_cost by month over last 12 months
    twelve_months_ago = today.replace(day=1) - timedelta(days=365)
    twelve_months_ago = twelve_months_ago.replace(day=1)  # Start of that month
    
    monthly_board_data = (
        Order.objects.filter(
            fit_date__gte=twelve_months_ago,
            fit_date__lte=today,
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
    while current_month <= today.replace(day=1):
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
    
    context = {
        'fits_chart_data': json.dumps({
            'labels': labels,
            'values': fits_values,
        }),
        'profit_chart_data': json.dumps({
            'labels': labels,
            'values': profit_values,
        }),
        'board_cost_chart_data': json.dumps({
            'labels': board_cost_labels,
            'values': board_cost_values,
        }),
        'pending_pos_count': pending_pos,
    }
    return render(request, 'stock_take/dashboard.html', context)
