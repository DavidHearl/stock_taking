from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Sum, DecimalField, Value, OuterRef, Subquery
from django.db.models.functions import TruncWeek, TruncMonth, Coalesce
from django.http import HttpResponse, JsonResponse
from datetime import datetime, timedelta
from decimal import Decimal
import json
from .models import Order, PurchaseOrder, StockItem, StockHistory, AnthillSale, AnthillPayment

# Maps the user's selected_location value to the contract number prefix used by Anthill.
# Contract numbers are the authoritative location signal — the location field in
# AnthillSale contains dirty/incorrect data for some historical records.
_LOCATION_CONTRACT_PREFIX = {
    'belfast':     'BFS',
    'dublin':      'DUB',
    'nottingham':  'NTG',
    'wyedean':     'WYE',
    'midlands':    'MDE',
}


def _contract_prefix_for_location(location: str) -> str:
    """Return the contract number prefix for a given selected_location value, or ''."""
    return _LOCATION_CONTRACT_PREFIX.get(location.strip().lower(), '')


def _get_monthly_sales_data(year, month, contract_prefix=''):
    """Calculate monthly sales stats for a given year/month using fit_date."""
    from calendar import monthrange
    first_day = datetime(year, month, 1).date()
    last_day = datetime(year, month, monthrange(year, month)[1]).date()

    sales_qs = AnthillSale.objects.filter(
        fit_date__gte=first_day,
        fit_date__lte=last_day,
        sale_value__gt=0,
    ).exclude(status__in=['cancelled', 'dead'])
    if contract_prefix:
        sales_qs = sales_qs.filter(contract_number__istartswith=contract_prefix)
    agg = sales_qs.aggregate(
        total=Sum('sale_value'),
        count=Count('id'),
    )
    return {
        'total': float(agg['total'] or 0),
        'count': agg['count'] or 0,
    }


@login_required
def dashboard_sales_after(request):
    """AJAX endpoint – return total value and count of sales whose fit_date >= given date."""
    date_str = request.GET.get('date', '')
    try:
        cutoff = datetime.strptime(date_str, '%Y-%m-%d').date()
    except (ValueError, TypeError):
        cutoff = datetime.now().date()

    sales_qs = AnthillSale.objects.filter(
        fit_date__gte=cutoff,
        sale_value__gt=0,
    ).exclude(status__in=['cancelled', 'dead'])
    agg = sales_qs.aggregate(
        total=Sum('sale_value'),
        count=Count('id'),
    )
    return JsonResponse({
        'success': True,
        'total': float(agg['total'] or 0),
        'count': agg['count'] or 0,
    })


def _build_sales_after_rows(cutoff):
    """Return a list of sale row dicts for fit_date >= cutoff, with payment totals."""
    qs = (
        AnthillSale.objects
        .filter(fit_date__gte=cutoff, sale_value__gt=0)
        .exclude(status__in=['cancelled', 'dead'])
        .annotate(payments_total=Coalesce(
            Sum('payments__amount'), Value(Decimal('0')),
            output_field=DecimalField(max_digits=14, decimal_places=2),
        ))
        .select_related('customer')
        .order_by('fit_date')
        .values(
            'pk', 'anthill_activity_id', 'contract_number', 'customer_name',
            'fit_date', 'activity_date', 'sale_value', 'assigned_to_name',
            'payments_total', 'location',
        )
    )
    rows = []
    for s in qs:
        sv = float(s['sale_value'] or 0)
        paid = float(s['payments_total'] or 0)
        remaining = max(sv - paid, 0)
        rows.append({
            'pk': s['pk'],
            'customer': s['customer_name'] or '-',
            'sale_number': s['contract_number'] or s['anthill_activity_id'] or '',
            'order_date': s['activity_date'].strftime('%d/%m/%Y') if s['activity_date'] else '',
            'fit_date': s['fit_date'].strftime('%d/%m/%Y') if s['fit_date'] else '',
            'sale_value': sv,
            'paid': paid,
            'remaining': remaining,
            'designer': s['assigned_to_name'] or '-',
        })
    return rows


@login_required
def dashboard_sales_after_report(request):
    """JSON endpoint – returns per-order breakdown for orders with fit_date >= given date."""
    date_str = request.GET.get('date', '')
    try:
        cutoff = datetime.strptime(date_str, '%Y-%m-%d').date()
    except (ValueError, TypeError):
        cutoff = datetime.now().date()

    rows = _build_sales_after_rows(cutoff)
    total = sum(r['sale_value'] for r in rows)
    return JsonResponse({
        'success': True,
        'rows': rows,
        'count': len(rows),
        'total': total,
        'cutoff': cutoff.strftime('%d/%m/%Y'),
    })


@login_required
def dashboard_sales_after_pdf(request):
    """Download the sales-after-date report as a PDF."""
    from .pdf_generator import generate_sales_after_pdf
    from datetime import date as date_type

    date_str = request.GET.get('date', '')
    try:
        cutoff = datetime.strptime(date_str, '%Y-%m-%d').date()
    except (ValueError, TypeError):
        cutoff = datetime.now().date()

    rows = _build_sales_after_rows(cutoff)
    buffer = generate_sales_after_pdf(rows, cutoff_date=cutoff)
    filename = f'Sales_After_{cutoff.strftime("%Y%m%d")}_{date_type.today().strftime("%Y%m%d")}.pdf'
    response = HttpResponse(buffer.getvalue(), content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


@login_required
def dashboard_monthly_sales(request):
    """AJAX endpoint to get monthly sales data for a given year/month."""
    try:
        year = int(request.GET.get('year', datetime.now().year))
        month = int(request.GET.get('month', datetime.now().month))
    except (ValueError, TypeError):
        return JsonResponse({'error': 'Invalid year/month'}, status=400)

    profile = getattr(request.user, 'profile', None)
    contract_prefix = _contract_prefix_for_location(profile.selected_location if profile else '')
    data = _get_monthly_sales_data(year, month, contract_prefix)

    # Previous month for delta
    if month == 1:
        prev_year, prev_month = year - 1, 12
    else:
        prev_year, prev_month = year, month - 1
    prev_data = _get_monthly_sales_data(prev_year, prev_month, contract_prefix)
    delta = data['total'] - prev_data['total']

    return JsonResponse({
        'success': True,
        'total': data['total'],
        'count': data['count'],
        'prev_total': prev_data['total'],
        'delta': delta,
        'delta_str': '{:,.0f}'.format(abs(delta)),
        'delta_sign': 1 if delta > 0 else (-1 if delta < 0 else 0),
    })


@login_required
def dashboard(request):
    """Main dashboard page."""

    # Franchise users go straight to claim service
    profile = getattr(request.user, 'profile', None)
    if profile and profile.role and profile.role.name == 'franchise':
        return redirect('claim_service')

    selected_location = profile.selected_location if profile else ''
    contract_prefix = _contract_prefix_for_location(selected_location)

    # Get fits per week data (past 52 weeks + future 12 weeks to show scheduled fits)
    today = datetime.now().date()
    start_date = today - timedelta(weeks=52)
    future_date = today + timedelta(weeks=12)

    # Get all AnthillSale records with fit dates in the range
    sales_in_range = AnthillSale.objects.filter(
        fit_date__gte=start_date,
        fit_date__lte=future_date,
        sale_value__gt=0,
    ).exclude(status__in=['cancelled', 'dead'])
    if contract_prefix:
        sales_in_range = sales_in_range.filter(contract_number__istartswith=contract_prefix)
    sales_in_range = sales_in_range.values('fit_date', 'sale_value')
    
    # Create a dictionary of all weeks with default data
    weeks_data = {}
    current_date = start_date
    while current_date <= future_date:
        # Get Monday of the week
        week_start = current_date - timedelta(days=current_date.weekday())
        weeks_data[week_start] = {'fits': 0, 'sales': Decimal('0.00')}
        current_date += timedelta(weeks=1)
    
    # Fill in actual data by iterating through AnthillSale records
    for sale in sales_in_range:
        week_start = sale['fit_date'] - timedelta(days=sale['fit_date'].weekday())
        if week_start in weeks_data:
            weeks_data[week_start]['fits'] += 1
            weeks_data[week_start]['sales'] += sale['sale_value'] or Decimal('0.00')
    
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
    
    # This week's fits scheduled (AnthillSale by fit_date — full Mon–Sun week)
    this_week_start = today - timedelta(days=today.weekday())
    this_week_end = this_week_start + timedelta(days=6)
    this_week_sales_qs = AnthillSale.objects.filter(
        fit_date__gte=this_week_start,
        fit_date__lte=this_week_end,
        sale_value__gt=0,
    ).exclude(status__in=['cancelled', 'dead'])
    if contract_prefix:
        this_week_sales_qs = this_week_sales_qs.filter(contract_number__istartswith=contract_prefix)
    this_week_sales = this_week_sales_qs.aggregate(total=Sum('sale_value'))['total'] or Decimal('0.00')

    # Last week for week-on-week comparison
    last_week_start = this_week_start - timedelta(days=7)
    last_week_end = last_week_start + timedelta(days=6)
    last_week_sales_qs = AnthillSale.objects.filter(
        fit_date__gte=last_week_start,
        fit_date__lte=last_week_end,
        sale_value__gt=0,
    ).exclude(status__in=['cancelled', 'dead'])
    if contract_prefix:
        last_week_sales_qs = last_week_sales_qs.filter(contract_number__istartswith=contract_prefix)
    last_week_sales = last_week_sales_qs.aggregate(total=Sum('sale_value'))['total'] or Decimal('0.00')
    week_delta = this_week_sales - last_week_sales
    
    # Sales after today (future pipeline — from AnthillSale.fit_date)
    sales_after_qs = AnthillSale.objects.filter(
        fit_date__gte=today,
        sale_value__gt=0,
    ).exclude(status__in=['cancelled', 'dead'])
    if contract_prefix:
        sales_after_qs = sales_after_qs.filter(contract_number__istartswith=contract_prefix)
    sales_after = sales_after_qs.aggregate(
        total=Sum('sale_value'),
        count=Count('id'),
    )

    # Get count of approved POs waiting to arrive
    pending_pos = PurchaseOrder.objects.filter(
        status__in=['Approved', 'Ordered', 'Sent']
    ).exclude(
        status__in=['Received', 'Invoiced', 'Cancelled', 'Closed']
    ).count()
    
    # Total stock value — includes both 'stock' and 'non-stock' items with quantity > 0
    # Non-stock items are still physical warehouse stock and count for accounting purposes
    stock_items = StockItem.objects.filter(tracking_type__in=['stock', 'non-stock'], quantity__gt=0)
    total_stock_value = sum(item.cost * item.quantity for item in stock_items) or Decimal('0.00')
    stock_item_count = stock_items.count()

    # 7-day stock value change
    seven_days_ago = today - timedelta(days=7)
    recent_history = (
        StockHistory.objects
        .filter(created_at__date__gte=seven_days_ago, stock_item__tracking_type__in=['stock', 'non-stock'])
        .exclude(change_type__in=['sale', 'purchase'])
        .select_related('stock_item')
    )
    stock_value_7day_delta = sum(h.change_amount * h.stock_item.cost for h in recent_history) or Decimal('0.00')
    
    # Monthly board costs - aggregate materials_cost by month (past 12 months + future 3 months)
    twelve_months_ago = today.replace(day=1) - timedelta(days=365)
    twelve_months_ago = twelve_months_ago.replace(day=1)  # Start of that month
    # Sales chart looks back 36 months to show meaningful historical data
    thirty_six_months_ago = today.replace(day=1)
    for _ in range(36):
        if thirty_six_months_ago.month == 1:
            thirty_six_months_ago = thirty_six_months_ago.replace(year=thirty_six_months_ago.year - 1, month=12)
        else:
            thirty_six_months_ago = thirty_six_months_ago.replace(month=thirty_six_months_ago.month - 1)
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

    # Monthly sales totals from AnthillSale - aggregate sale_value by fit_date month
    # Using fit_date (not activity_date) so future scheduled fits appear in upcoming months
    monthly_sales_qs = AnthillSale.objects.filter(
        fit_date__gte=thirty_six_months_ago,
        fit_date__lte=three_months_ahead,
        sale_value__gt=0,
    ).exclude(status__in=['cancelled', 'dead'])
    if contract_prefix:
        monthly_sales_qs = monthly_sales_qs.filter(contract_number__istartswith=contract_prefix)
    monthly_sales_data = (
        monthly_sales_qs
        .annotate(month=TruncMonth('fit_date'))
        .values('month')
        .annotate(total_sales=Sum('sale_value'))
        .order_by('month')
    )

    monthly_sales_months = {}
    temp_month = thirty_six_months_ago
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

    # Current month sales data (by fit_date)
    current_month_sales = _get_monthly_sales_data(today.year, today.month, contract_prefix)

    # Previous month for initial month-on-month delta
    prev_month_num = today.month - 1 if today.month > 1 else 12
    prev_month_year = today.year if today.month > 1 else today.year - 1
    prev_month_sales = _get_monthly_sales_data(prev_month_year, prev_month_num, contract_prefix)
    month_delta = Decimal(str(current_month_sales['total'])) - Decimal(str(prev_month_sales['total']))

    # Average daily sale value — last 365 days total / 365
    one_year_ago = today - timedelta(days=365)
    avg_12m_qs = AnthillSale.objects.filter(
        fit_date__gte=one_year_ago,
        fit_date__lte=today,
        sale_value__gt=0,
    ).exclude(status__in=['cancelled', 'dead'])
    if contract_prefix:
        avg_12m_qs = avg_12m_qs.filter(contract_number__istartswith=contract_prefix)
    avg_12m_total = avg_12m_qs.aggregate(total=Sum('sale_value'))['total'] or Decimal('0')
    avg_daily_sale = avg_12m_total / 365

    # Total outstanding balance: sum balance_payable directly from Anthill.
    # Filter by contract number prefix (authoritative) rather than the location
    # field which contains dirty data for some historic records.

    # Compute real outstanding dynamically: sale_value - sum(all payments)
    payments_subquery = Subquery(
        AnthillPayment.objects.filter(sale=OuterRef('pk'))
        .values('sale')
        .annotate(total=Sum('amount'))
        .values('total'),
        output_field=DecimalField(max_digits=14, decimal_places=2),
    )
    outstanding_qs = (
        AnthillSale.objects
        .filter(sale_value__gt=0)
        .exclude(status='cancelled')
        .annotate(total_paid=Coalesce(payments_subquery, Value(Decimal('0')), output_field=DecimalField(max_digits=14, decimal_places=2)))
        .values('sale_value', 'total_paid')
    )
    if contract_prefix:
        outstanding_qs = outstanding_qs.filter(contract_number__istartswith=contract_prefix)

    outstanding_rows = list(outstanding_qs)
    total_outstanding_balance = sum(
        max((row['sale_value'] or Decimal('0')) - (row['total_paid'] or Decimal('0')), Decimal('0'))
        for row in outstanding_rows
        if (row['sale_value'] or Decimal('0')) > (row['total_paid'] or Decimal('0'))
    ) or Decimal('0')
    outstanding_debtor_count = sum(
        1 for row in outstanding_rows
        if (row['sale_value'] or Decimal('0')) > (row['total_paid'] or Decimal('0'))
    )

    # Remaining balance of fits scheduled this week
    this_week_qs = (
        AnthillSale.objects
        .filter(fit_date__gte=this_week_start, fit_date__lte=this_week_end, sale_value__gt=0)
        .exclude(status__in=['cancelled', 'dead'])
        .annotate(total_paid=Coalesce(payments_subquery, Value(Decimal('0')), output_field=DecimalField(max_digits=14, decimal_places=2)))
        .values('sale_value', 'total_paid')
    )
    if contract_prefix:
        this_week_qs = this_week_qs.filter(contract_number__istartswith=contract_prefix)
    expected_this_week = sum(
        max((row['sale_value'] or Decimal('0')) - (row['total_paid'] or Decimal('0')), Decimal('0'))
        for row in this_week_qs
    ) or Decimal('0')
    
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
        'stock_value_7day_delta': float(stock_value_7day_delta),
        'stock_value_7day_delta_str': '{:,.0f}'.format(abs(stock_value_7day_delta)),
        'this_week_sales': '{:,.0f}'.format(this_week_sales),
        'week_delta': float(week_delta),
        'week_delta_str': '{:,.0f}'.format(abs(week_delta)),
        'monthly_sales_total': '{:,.0f}'.format(Decimal(str(current_month_sales['total']))),
        'monthly_sales_count': current_month_sales['count'],
        'month_delta': float(month_delta),
        'month_delta_str': '{:,.0f}'.format(abs(month_delta)),
        'avg_daily_sale': '{:,.0f}'.format(avg_daily_sale),
        'current_year': today.year,
        'current_month': today.month,
        'today_str': today.strftime('%Y-%m-%d'),
        'sales_after_total': '{:,.0f}'.format(sales_after['total'] or Decimal('0.00')),
        'sales_after_count': sales_after['count'] or 0,
        'total_outstanding_balance': '{:,.0f}'.format(total_outstanding_balance),
        'outstanding_debtor_count': outstanding_debtor_count,
        'expected_this_week': '{:,.0f}'.format(expected_this_week),
    }
    return render(request, 'stock_take/dashboard.html', context)


@login_required
def dashboard_stock_report(request):
    """JSON endpoint — returns recent stock changes and current stock levels."""
    date_str = request.GET.get('date', '')
    as_of_date = None
    if date_str:
        try:
            as_of_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        except (ValueError, TypeError):
            pass

    CHANGE_TYPE_LABELS = {
        'stock_take': 'Stock Take',
        'purchase': 'Purchase',
        'sale': 'Sale/Usage',
        'adjustment': 'Adjustment',
        'initial': 'Initial Stock',
    }

    three_days_ago = datetime.now() - timedelta(days=3)
    history_qs = (
        StockHistory.objects
        .filter(
            stock_item__tracking_type='stock',
            created_at__gte=three_days_ago,
        )
        .exclude(change_type__in=['sale', 'purchase'])
        .select_related('stock_item', 'stock_item__category')
        .order_by('-created_at')
    )
    recent_changes = []
    for h in history_qs:
        value_change = float(h.change_amount * h.stock_item.cost)
        recent_changes.append({
            'date': h.created_at.strftime('%d/%m/%Y %H:%M'),
            'sku': h.stock_item.sku,
            'name': h.stock_item.name,
            'change_type': CHANGE_TYPE_LABELS.get(h.change_type, h.change_type),
            'change_amount': h.change_amount,
            'unit_cost': float(h.stock_item.cost),
            'value_change': value_change,
            'reference': h.reference or '',
            'notes': h.notes or '',
        })

    if as_of_date:
        # Reconstruct by taking current quantity and subtracting all changes that
        # occurred AFTER the target date.  This is reliable even for items that
        # have no StockHistory records before the target date.
        changes_after = (
            StockHistory.objects
            .filter(stock_item__tracking_type='stock', created_at__date__gt=as_of_date)
            .values('stock_item_id')
            .annotate(total_change=Sum('change_amount'))
        )
        change_map = {c['stock_item_id']: c['total_change'] for c in changes_after}
        stock_qs = (
            StockItem.objects
            .filter(tracking_type='stock')
            .select_related('category')
        )
        current_stock = []
        for item in stock_qs:
            qty = item.quantity - change_map.get(item.pk, 0)
            if qty <= 0:
                continue
            current_stock.append({
                'sku': item.sku,
                'name': item.name,
                'category': item.category.name if item.category else '—',
                'location': item.location or '—',
                'unit_cost': float(item.cost),
                'quantity': qty,
                'total_value': float(item.cost) * qty,
            })
    else:
        stock_qs = (
            StockItem.objects
            .filter(tracking_type='stock', quantity__gt=0)
            .select_related('category')
        )
        current_stock = []
        for item in stock_qs:
            current_stock.append({
                'sku': item.sku,
                'name': item.name,
                'category': item.category.name if item.category else '—',
                'location': item.location or '—',
                'unit_cost': float(item.cost),
                'quantity': item.quantity,
                'total_value': float(item.cost * item.quantity),
            })

    current_stock.sort(key=lambda i: i['total_value'], reverse=True)
    total_value = sum(i['total_value'] for i in current_stock)
    return JsonResponse({
        'success': True,
        'recent_changes': recent_changes,
        'current_stock': current_stock,
        'total_value': total_value,
        'stock_count': len(current_stock),
        'as_of_date': as_of_date.strftime('%d %b %Y') if as_of_date else None,
    })


@login_required
def dashboard_stock_pdf(request):
    """Download the stock report as a PDF."""
    from .pdf_generator import generate_stock_report_pdf
    from datetime import date as date_type

    date_str = request.GET.get('date', '')
    as_of_date = None
    if date_str:
        try:
            as_of_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        except (ValueError, TypeError):
            pass

    CHANGE_TYPE_LABELS = {
        'stock_take': 'Stock Take',
        'purchase': 'Purchase',
        'sale': 'Sale/Usage',
        'adjustment': 'Adjustment',
        'initial': 'Initial Stock',
    }

    three_days_ago = datetime.now() - timedelta(days=3)
    history_qs = (
        StockHistory.objects
        .filter(
            stock_item__tracking_type='stock',
            created_at__gte=three_days_ago,
        )
        .exclude(change_type__in=['sale', 'purchase'])
        .select_related('stock_item', 'stock_item__category')
        .order_by('-created_at')
    )
    recent_changes = []
    for h in history_qs:
        recent_changes.append({
            'date': h.created_at.strftime('%d/%m/%Y'),
            'sku': h.stock_item.sku,
            'name': h.stock_item.name,
            'change_type': CHANGE_TYPE_LABELS.get(h.change_type, h.change_type),
            'change_amount': h.change_amount,
            'unit_cost': float(h.stock_item.cost),
            'value_change': float(h.change_amount * h.stock_item.cost),
            'reference': h.reference or '',
        })

    if as_of_date:
        changes_after = (
            StockHistory.objects
            .filter(stock_item__tracking_type='stock', created_at__date__gt=as_of_date)
            .values('stock_item_id')
            .annotate(total_change=Sum('change_amount'))
        )
        change_map = {c['stock_item_id']: c['total_change'] for c in changes_after}
        stock_qs = (
            StockItem.objects
            .filter(tracking_type='stock')
            .select_related('category')
        )
        current_stock = []
        for item in stock_qs:
            qty = item.quantity - change_map.get(item.pk, 0)
            if qty <= 0:
                continue
            current_stock.append({
                'sku': item.sku,
                'name': item.name,
                'category': item.category.name if item.category else '—',
                'location': item.location or '—',
                'unit_cost': float(item.cost),
                'quantity': qty,
                'total_value': float(item.cost) * qty,
            })
    else:
        stock_qs = (
            StockItem.objects
            .filter(tracking_type='stock', quantity__gt=0)
            .select_related('category')
        )
        current_stock = []
        for item in stock_qs:
            current_stock.append({
                'sku': item.sku,
                'name': item.name,
                'category': item.category.name if item.category else '—',
                'location': item.location or '—',
                'unit_cost': float(item.cost),
                'quantity': item.quantity,
                'total_value': float(item.cost * item.quantity),
            })
    current_stock.sort(key=lambda i: i['total_value'], reverse=True)

    buffer = generate_stock_report_pdf(recent_changes, current_stock, as_of_date=as_of_date)
    date_suffix = as_of_date.strftime('%Y%m%d') if as_of_date else date_type.today().strftime('%Y%m%d')
    filename = f'Stock_Report_{date_suffix}.pdf'
    response = HttpResponse(buffer.getvalue(), content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


@login_required
def dashboard_outstanding_report(request):
    profile = getattr(request.user, 'profile', None)
    raw_location = request.GET.get('location') or (profile.selected_location if profile else '')
    contract_prefix = _contract_prefix_for_location(raw_location)

    payments_subquery = Subquery(
        AnthillPayment.objects.filter(sale=OuterRef('pk'))
        .values('sale')
        .annotate(total=Sum('amount'))
        .values('total'),
        output_field=DecimalField(max_digits=14, decimal_places=2),
    )
    qs = (
        AnthillSale.objects
        .filter(sale_value__gt=0)
        .exclude(status='cancelled')
        .annotate(total_paid=Coalesce(payments_subquery, Value(Decimal('0')), output_field=DecimalField(max_digits=14, decimal_places=2)))
        .order_by('-activity_date')
        .values('pk', 'anthill_activity_id', 'customer_name', 'contract_number',
                'sale_value', 'activity_date', 'location', 'total_paid')
    )
    if contract_prefix:
        qs = qs.filter(contract_number__istartswith=contract_prefix)

    results = []
    for row in qs:
        sv = row['sale_value'] or Decimal('0')
        total_paid = row['total_paid'] or Decimal('0')
        real_outstanding = sv - total_paid
        if real_outstanding <= Decimal('0'):
            continue
        dt = row['activity_date']
        results.append({
            'pk': row['pk'],
            'sale_number': row['anthill_activity_id'],
            'customer': row['customer_name'] or '-',
            'contract': row['contract_number'] or '',
            'location': row['location'] or '',
            'sale_value': float(sv),
            'paid': float(total_paid),
            'outstanding': float(real_outstanding),
            'year': dt.year if dt else None,
            'date': dt.strftime('%d/%m/%Y') if dt else '',
        })
    results.sort(key=lambda x: (-(x['year'] or 0), x['outstanding'] * -1))
    return JsonResponse({'success': True, 'rows': results, 'count': len(results)})


@login_required
def dashboard_outstanding_pdf(request):
    """Download the outstanding balance report as a PDF."""
    from .pdf_generator import generate_outstanding_report_pdf

    profile = getattr(request.user, 'profile', None)
    raw_location = request.GET.get('location') or (profile.selected_location if profile else '')
    contract_prefix = _contract_prefix_for_location(raw_location)
    location_label = raw_location.title() if raw_location else 'All Locations'

    payments_subquery = Subquery(
        AnthillPayment.objects.filter(sale=OuterRef('pk'))
        .values('sale')
        .annotate(total=Sum('amount'))
        .values('total'),
        output_field=DecimalField(max_digits=14, decimal_places=2),
    )
    qs = (
        AnthillSale.objects
        .filter(sale_value__gt=0)
        .annotate(total_paid=Coalesce(payments_subquery, Value(Decimal('0')), output_field=DecimalField(max_digits=14, decimal_places=2)))
        .order_by('-activity_date')
        .values('pk', 'anthill_activity_id', 'customer_name', 'contract_number',
                'sale_value', 'activity_date', 'total_paid')
    )
    if contract_prefix:
        qs = qs.filter(contract_number__istartswith=contract_prefix)

    rows = []
    for row in qs:
        sv = row['sale_value'] or Decimal('0')
        total_paid = row['total_paid'] or Decimal('0')
        real_outstanding = sv - total_paid
        if real_outstanding <= Decimal('0'):
            continue
        dt = row['activity_date']
        rows.append({
            'pk': row['pk'],
            'sale_number': row['anthill_activity_id'],
            'customer': row['customer_name'] or '-',
            'contract': row['contract_number'] or '',
            'sale_value': float(sv),
            'paid': float(total_paid),
            'outstanding': float(real_outstanding),
            'year': dt.year if dt else None,
            'date': dt.strftime('%d/%m/%Y') if dt else '',
        })
    rows.sort(key=lambda x: (-(x['year'] or 0), -x['outstanding']))

    from datetime import date
    buffer = generate_outstanding_report_pdf(rows, location_label=location_label)
    filename = f'Outstanding_Balance_{location_label}_{date.today().strftime("%Y%m%d")}.pdf'
    response = HttpResponse(buffer.getvalue(), content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


def _build_fit_sales_rows(qs):
    """Shared row builder for fit-date-based sales reports (week / monthly)."""
    rows = []
    for s in qs.values('pk', 'anthill_activity_id', 'contract_number', 'customer_name',
                        'fit_date', 'activity_date', 'sale_value', 'assigned_to_name'):
        sv = float(s['sale_value'] or 0)
        rows.append({
            'pk': s['pk'],
            'customer': s['customer_name'] or '-',
            'sale_number': s['contract_number'] or s['anthill_activity_id'] or '',
            'order_date': s['activity_date'].strftime('%d/%m/%Y') if s['activity_date'] else '',
            'fit_date': s['fit_date'].strftime('%d/%m/%Y') if s['fit_date'] else '',
            'sale_value': sv,
            'designer': s['assigned_to_name'] or '-',
        })
    return rows


@login_required
def dashboard_week_report(request):
    """JSON endpoint — sales with fit_date in the current Mon–Sun week."""
    profile = getattr(request.user, 'profile', None)
    contract_prefix = _contract_prefix_for_location(profile.selected_location if profile else '')
    today = datetime.now().date()
    week_start = today - timedelta(days=today.weekday())
    week_end = week_start + timedelta(days=6)
    qs = (
        AnthillSale.objects
        .filter(fit_date__gte=week_start, fit_date__lte=week_end, sale_value__gt=0)
        .exclude(status__in=['cancelled', 'dead'])
        .order_by('fit_date')
    )
    if contract_prefix:
        qs = qs.filter(contract_number__istartswith=contract_prefix)
    rows = _build_fit_sales_rows(qs)
    total = sum(r['sale_value'] for r in rows)
    return JsonResponse({
        'success': True,
        'rows': rows,
        'count': len(rows),
        'total': total,
        'week_start': week_start.strftime('%d %b %Y'),
        'week_end': week_end.strftime('%d %b %Y'),
    })


@login_required
def dashboard_week_pdf(request):
    """Download the current week fits as a PDF."""
    from .pdf_generator import generate_fit_sales_pdf
    from datetime import date as date_type
    profile = getattr(request.user, 'profile', None)
    contract_prefix = _contract_prefix_for_location(profile.selected_location if profile else '')
    today = datetime.now().date()
    week_start = today - timedelta(days=today.weekday())
    week_end = week_start + timedelta(days=6)
    qs = (
        AnthillSale.objects
        .filter(fit_date__gte=week_start, fit_date__lte=week_end, sale_value__gt=0)
        .exclude(status__in=['cancelled', 'dead'])
        .order_by('fit_date')
    )
    if contract_prefix:
        qs = qs.filter(contract_number__istartswith=contract_prefix)
    rows = _build_fit_sales_rows(qs)
    title = f"This Week\u2019s Fits  {week_start.strftime('%d %b')} \u2013 {week_end.strftime('%d %b %Y')}"
    buffer = generate_fit_sales_pdf(rows, title=title)
    filename = f'Fits_Week_{week_start.strftime("%Y%m%d")}.pdf'
    response = HttpResponse(buffer.getvalue(), content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


@login_required
def dashboard_monthly_report(request):
    """JSON endpoint — sales with fit_date in the selected month/year."""
    from calendar import monthrange
    profile = getattr(request.user, 'profile', None)
    contract_prefix = _contract_prefix_for_location(profile.selected_location if profile else '')
    try:
        year = int(request.GET.get('year', datetime.now().year))
        month = int(request.GET.get('month', datetime.now().month))
    except (ValueError, TypeError):
        return JsonResponse({'error': 'Invalid year/month'}, status=400)
    first_day = datetime(year, month, 1).date()
    last_day = datetime(year, month, monthrange(year, month)[1]).date()
    qs = (
        AnthillSale.objects
        .filter(fit_date__gte=first_day, fit_date__lte=last_day, sale_value__gt=0)
        .exclude(status__in=['cancelled', 'dead'])
        .order_by('fit_date')
    )
    if contract_prefix:
        qs = qs.filter(contract_number__istartswith=contract_prefix)
    rows = _build_fit_sales_rows(qs)
    total = sum(r['sale_value'] for r in rows)
    return JsonResponse({
        'success': True,
        'rows': rows,
        'count': len(rows),
        'total': total,
        'month_label': first_day.strftime('%B %Y'),
    })


@login_required
def dashboard_monthly_pdf(request):
    """Download a monthly fits report as a PDF."""
    from .pdf_generator import generate_fit_sales_pdf
    from calendar import monthrange
    from datetime import date as date_type
    profile = getattr(request.user, 'profile', None)
    contract_prefix = _contract_prefix_for_location(profile.selected_location if profile else '')
    try:
        year = int(request.GET.get('year', datetime.now().year))
        month = int(request.GET.get('month', datetime.now().month))
    except (ValueError, TypeError):
        year, month = datetime.now().year, datetime.now().month
    first_day = datetime(year, month, 1).date()
    last_day = datetime(year, month, monthrange(year, month)[1]).date()
    qs = (
        AnthillSale.objects
        .filter(fit_date__gte=first_day, fit_date__lte=last_day, sale_value__gt=0)
        .exclude(status__in=['cancelled', 'dead'])
        .order_by('fit_date')
    )
    if contract_prefix:
        qs = qs.filter(contract_number__istartswith=contract_prefix)
    rows = _build_fit_sales_rows(qs)
    title = f'Monthly Fits \u2014 {first_day.strftime("%B %Y")}'
    buffer = generate_fit_sales_pdf(rows, title=title)
    filename = f'Fits_{first_day.strftime("%Y_%m")}.pdf'
    response = HttpResponse(buffer.getvalue(), content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


@login_required
def dashboard_avg_report(request):
    """JSON endpoint — last 365-day monthly breakdown for the average sale value card."""
    from django.db.models.functions import TruncMonth
    profile = getattr(request.user, 'profile', None)
    contract_prefix = _contract_prefix_for_location(profile.selected_location if profile else '')
    today = datetime.now().date()
    one_year_ago = today - timedelta(days=365)
    qs = (
        AnthillSale.objects
        .filter(fit_date__gte=one_year_ago, fit_date__lte=today, sale_value__gt=0)
        .exclude(status__in=['cancelled', 'dead'])
    )
    if contract_prefix:
        qs = qs.filter(contract_number__istartswith=contract_prefix)
    monthly = (
        qs.annotate(month=TruncMonth('fit_date'))
        .values('month')
        .annotate(count=Count('id'), total=Sum('sale_value'))
        .order_by('month')
    )
    rows = []
    for m in monthly:
        month_dt = m['month']
        if hasattr(month_dt, 'date'):
            month_dt = month_dt.date()
        total = float(m['total'] or 0)
        count = m['count'] or 0
        rows.append({
            'month': month_dt.strftime('%B %Y'),
            'count': count,
            'total': total,
            'avg': round(total / count, 0) if count else 0,
        })
    grand_total = sum(r['total'] for r in rows)
    grand_count = sum(r['count'] for r in rows)
    return JsonResponse({
        'success': True,
        'rows': rows,
        'grand_total': grand_total,
        'grand_count': grand_count,
        'daily_avg': round(grand_total / 365, 0),
        'period': f'{one_year_ago.strftime("%d %b %Y")} \u2013 {today.strftime("%d %b %Y")}',
    })


@login_required
def dashboard_avg_pdf(request):
    """Download the 12-month average sale breakdown as a PDF."""
    from .pdf_generator import generate_avg_sales_pdf
    from django.db.models.functions import TruncMonth
    from datetime import date as date_type
    profile = getattr(request.user, 'profile', None)
    contract_prefix = _contract_prefix_for_location(profile.selected_location if profile else '')
    today = datetime.now().date()
    one_year_ago = today - timedelta(days=365)
    qs = (
        AnthillSale.objects
        .filter(fit_date__gte=one_year_ago, fit_date__lte=today, sale_value__gt=0)
        .exclude(status__in=['cancelled', 'dead'])
    )
    if contract_prefix:
        qs = qs.filter(contract_number__istartswith=contract_prefix)
    monthly = (
        qs.annotate(month=TruncMonth('fit_date'))
        .values('month')
        .annotate(count=Count('id'), total=Sum('sale_value'))
        .order_by('month')
    )
    rows = []
    for m in monthly:
        month_dt = m['month']
        if hasattr(month_dt, 'date'):
            month_dt = month_dt.date()
        total = float(m['total'] or 0)
        count = m['count'] or 0
        rows.append({
            'month': month_dt.strftime('%B %Y'),
            'count': count,
            'total': total,
            'avg': round(total / count, 0) if count else 0,
        })
    grand_total = sum(r['total'] for r in rows)
    grand_count = sum(r['count'] for r in rows)
    period = f'{one_year_ago.strftime("%d %b %Y")} \u2013 {today.strftime("%d %b %Y")}'
    buffer = generate_avg_sales_pdf(rows, grand_total=grand_total, grand_count=grand_count, period=period)
    filename = f'Avg_Sale_Value_12m_{date_type.today().strftime("%Y%m%d")}.pdf'
    response = HttpResponse(buffer.getvalue(), content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response
