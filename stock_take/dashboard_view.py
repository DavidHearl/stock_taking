from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Sum, DecimalField, Value, OuterRef, Subquery, F
from django.db.models.functions import TruncWeek, TruncMonth, Coalesce
from django.http import HttpResponse, JsonResponse
from datetime import datetime, timedelta
from decimal import Decimal
import json
from .models import Order, PurchaseOrder, PurchaseOrderProduct, StockItem, StockHistory, AnthillSale, AnthillPayment

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

    # Item shortages — tracked items where quantity is below par level (par_level > 0)
    shortage_items = (
        StockItem.objects
        .filter(tracking_type__in=['stock', 'non-stock'], par_level__gt=0)
        .filter(quantity__lt=F('par_level'))
        .only('id', 'sku', 'name', 'quantity', 'par_level', 'cost')
        .order_by('quantity')
    )

    # Incoming quantities from approved POs (not yet received)
    shortage_skus = [s.sku for s in shortage_items]
    incoming_data = (
        PurchaseOrderProduct.objects
        .filter(sku__in=shortage_skus, purchase_order__status='Approved')
        .values('sku')
        .annotate(total=Sum('order_quantity'))
    )
    incoming_map = {item['sku']: float(item['total'] or 0) for item in incoming_data}

    shortage_items_list = [
        {
            'id': s.id,
            'sku': s.sku,
            'name': s.name,
            'quantity': s.quantity,
            'par_level': s.par_level,
            'shortfall': s.par_level - s.quantity,
            'incoming_qty': incoming_map.get(s.sku, 0),
        }
        for s in shortage_items
    ]
    shortage_count = len(shortage_items_list)

    # Incoming materials — individual product items on pending purchase orders
    # Exclude Carnehill and OS Doors suppliers
    incoming_products = (
        PurchaseOrderProduct.objects
        .filter(
            purchase_order__status__in=['Approved', 'Ordered', 'Sent'],
        )
        .exclude(purchase_order__supplier_name__icontains='carnehill')
        .exclude(purchase_order__supplier_name__icontains='os doors')
        .select_related('purchase_order')
    )
    # Build a dict keyed by SKU to aggregate same items
    incoming_by_sku = {}
    for p in incoming_products:
        outstanding = float((p.order_quantity or 0) - (p.received_quantity or 0))
        if outstanding <= 0:
            continue
        sku_key = p.sku or p.name or str(p.id)
        if sku_key in incoming_by_sku:
            incoming_by_sku[sku_key]['qty_outstanding'] += outstanding
            po_num = p.purchase_order.display_number or str(p.purchase_order.id)
            if po_num not in incoming_by_sku[sku_key]['po_numbers']:
                incoming_by_sku[sku_key]['po_numbers'].append(po_num)
            # Keep the earliest expected date
            new_date = p.purchase_order.expected_date or ''
            existing_date = incoming_by_sku[sku_key]['expected_date']
            if new_date and (not existing_date or existing_date == '-' or new_date < existing_date):
                incoming_by_sku[sku_key]['expected_date'] = new_date
        else:
            incoming_by_sku[sku_key] = {
                'sku': p.sku or '-',
                'name': p.name or '-',
                'supplier': p.purchase_order.supplier_name or '-',
                'po_numbers': [p.purchase_order.display_number or str(p.purchase_order.id)],
                'qty_outstanding': outstanding,
                'expected_date': p.purchase_order.expected_date or '-',
                'status': p.purchase_order.status,
            }
    # Build the final list sorted by name for grouping same items together
    incoming_materials_list = sorted(incoming_by_sku.values(), key=lambda x: x['name'])
    # Convert po_numbers list to comma-separated string
    for item in incoming_materials_list:
        item['po_numbers'] = ', '.join(item['po_numbers'])
    incoming_materials_count = len(incoming_materials_list)

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
        AnthillPayment.objects.filter(sale=OuterRef('pk'), ignored=False)
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
        .values('sale_value', 'discount', 'total_paid')
    )
    if contract_prefix:
        outstanding_qs = outstanding_qs.filter(contract_number__istartswith=contract_prefix)

    outstanding_rows = list(outstanding_qs)
    total_outstanding_balance = sum(
        max((row['sale_value'] or Decimal('0')) - 2 * (row['discount'] or Decimal('0')) - (row['total_paid'] or Decimal('0')), Decimal('0'))
        for row in outstanding_rows
        if (row['sale_value'] or Decimal('0')) - 2 * (row['discount'] or Decimal('0')) > (row['total_paid'] or Decimal('0'))
    ) or Decimal('0')
    outstanding_debtor_count = sum(
        1 for row in outstanding_rows
        if (row['sale_value'] or Decimal('0')) - 2 * (row['discount'] or Decimal('0')) > (row['total_paid'] or Decimal('0'))
    )

    # Remaining balance of fits scheduled this week
    this_week_qs = (
        AnthillSale.objects
        .filter(fit_date__gte=this_week_start, fit_date__lte=this_week_end, sale_value__gt=0)
        .exclude(status__in=['cancelled', 'dead'])
        .annotate(total_paid=Coalesce(payments_subquery, Value(Decimal('0')), output_field=DecimalField(max_digits=14, decimal_places=2)))
        .values('sale_value', 'discount', 'total_paid')
    )
    if contract_prefix:
        this_week_qs = this_week_qs.filter(contract_number__istartswith=contract_prefix)
    expected_this_week = sum(
        max((row['sale_value'] or Decimal('0')) - 2 * (row['discount'] or Decimal('0')) - (row['total_paid'] or Decimal('0')), Decimal('0'))
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
        'shortage_count': shortage_count,
        'shortage_items_json': json.dumps(shortage_items_list),
        'incoming_materials_count': incoming_materials_count,
        'incoming_materials_json': json.dumps(incoming_materials_list),
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

    five_days_ago = datetime.now() - timedelta(days=5)
    history_qs = (
        StockHistory.objects
        .filter(
            stock_item__tracking_type__in=['stock', 'non-stock'],
            created_at__gte=five_days_ago,
        )
        .exclude(change_type__in=['sale', 'purchase'])
        .select_related('stock_item', 'stock_item__category')
        .order_by('-created_at')
    )
    recent_changes = []
    for h in history_qs:
        if not h.change_amount:
            continue
        value_change = float(h.change_amount * h.stock_item.cost)
        if value_change == 0:
            continue
        recent_changes.append({
            'date': h.created_at.strftime('%d/%m/%Y'),
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
            .filter(stock_item__tracking_type__in=['stock', 'non-stock'], created_at__date__gt=as_of_date)
            .values('stock_item_id')
            .annotate(total_change=Sum('change_amount'))
        )
        change_map = {c['stock_item_id']: c['total_change'] for c in changes_after}
        stock_qs = (
            StockItem.objects
            .filter(tracking_type__in=['stock', 'non-stock'])
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
            .filter(tracking_type__in=['stock', 'non-stock'], quantity__gt=0)
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
            stock_item__tracking_type__in=['stock', 'non-stock'],
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
            .filter(stock_item__tracking_type__in=['stock', 'non-stock'], created_at__date__gt=as_of_date)
            .values('stock_item_id')
            .annotate(total_change=Sum('change_amount'))
        )
        change_map = {c['stock_item_id']: c['total_change'] for c in changes_after}
        stock_qs = (
            StockItem.objects
            .filter(tracking_type__in=['stock', 'non-stock'])
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
            .filter(tracking_type__in=['stock', 'non-stock'], quantity__gt=0)
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
def dashboard_monthly_stock_history(request):
    """JSON endpoint — returns total stock value reconstructed for the 1st of each month, starting from the earliest StockHistory record."""
    from datetime import date as date_type

    today = datetime.now().date()

    # Find the earliest StockHistory record to determine where to start
    earliest_record = (
        StockHistory.objects
        .filter(stock_item__tracking_type__in=['stock', 'non-stock'])
        .order_by('created_at')
        .values_list('created_at', flat=True)
        .first()
    )
    if earliest_record:
        start_y, start_m = earliest_record.year, earliest_record.month
    else:
        start_y, start_m = today.year, today.month

    # Build list of 1st-of-month dates from start month up to current month
    months = []
    y, m = start_y, start_m
    while (y, m) <= (today.year, today.month):
        months.append(date_type(y, m, 1))
        m += 1
        if m > 12:
            m = 1
            y += 1

    # Current quantities and costs for all stock/non-stock items
    all_items = list(
        StockItem.objects
        .filter(tracking_type__in=['stock', 'non-stock'])
        .values('pk', 'quantity', 'cost')
    )

    # All StockHistory changes (we need to reconstruct backwards)
    # Fetch all changes after the earliest month start so we can slice per month
    earliest = months[0]
    all_changes = list(
        StockHistory.objects
        .filter(
            stock_item__tracking_type__in=['stock', 'non-stock'],
            created_at__date__gte=earliest,
        )
        .values('stock_item_id', 'change_amount', 'created_at')
    )

    result = []
    for month_start in months:
        # changes that occurred AFTER this month's 1st
        change_map = {}
        for c in all_changes:
            if c['created_at'].date() > month_start:
                pk = c['stock_item_id']
                change_map[pk] = change_map.get(pk, 0) + c['change_amount']

        total_value = 0.0
        for item in all_items:
            qty = item['quantity'] - change_map.get(item['pk'], 0)
            if qty > 0:
                total_value += float(item['cost']) * qty

        result.append({
            'label': month_start.strftime('%b %Y'),
            'value': round(total_value, 2),
        })

    return JsonResponse({'success': True, 'months': result})


@login_required
def dashboard_outstanding_report(request):
    profile = getattr(request.user, 'profile', None)
    raw_location = request.GET.get('location') or (profile.selected_location if profile else '')
    contract_prefix = _contract_prefix_for_location(raw_location)
    time_filter = request.GET.get('period', 'all')  # all, monthly, weekly

    payments_subquery = Subquery(
        AnthillPayment.objects.filter(sale=OuterRef('pk'), ignored=False)
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
                'sale_value', 'discount', 'activity_date', 'location', 'total_paid', 'fit_date')
    )
    if contract_prefix:
        qs = qs.filter(contract_number__istartswith=contract_prefix)

    # Apply time period filter based on fit_date
    if time_filter == 'weekly':
        from datetime import date
        today = date.today()
        week_start = today - timedelta(days=today.weekday())
        week_end = week_start + timedelta(days=6)
        qs = qs.filter(fit_date__gte=week_start, fit_date__lte=week_end)
    elif time_filter == 'monthly':
        from datetime import date
        today = date.today()
        month_start = today.replace(day=1)
        next_month = (month_start + timedelta(days=32)).replace(day=1)
        qs = qs.filter(fit_date__gte=month_start, fit_date__lt=next_month)

    # Collect PKs first, then bulk-fetch payments
    sale_rows = []
    overpaid_rows = []
    for row in qs:
        sv = row['sale_value'] or Decimal('0')
        disc = row['discount'] or Decimal('0')
        total_paid = row['total_paid'] or Decimal('0')
        real_paid = total_paid + disc
        effective_sv = sv - disc
        real_outstanding = effective_sv - real_paid
        if real_outstanding < Decimal('-5'):
            # Overpaid by more than £5
            overpaid_rows.append(row)
        elif real_outstanding >= Decimal('10'):
            sale_rows.append(row)

    # Bulk-fetch payments for all qualifying sales (outstanding + overpaid)
    sale_pks = [r['pk'] for r in sale_rows] + [r['pk'] for r in overpaid_rows]
    payments_by_sale = {}
    if sale_pks:
        for p in AnthillPayment.objects.filter(sale_id__in=sale_pks).order_by('date').values(
            'sale_id', 'amount', 'date', 'payment_type', 'source',
            'xero_invoice_number', 'invoice_status',
        ):
            payments_by_sale.setdefault(p['sale_id'], []).append({
                'amount': float(p['amount'] or 0),
                'date': p['date'].strftime('%d/%m/%Y') if p['date'] else '',
                'type': p['payment_type'] or 'Payment',
                'source': p['source'] or '',
                'invoice': p['xero_invoice_number'] or '',
                'invoice_status': p['invoice_status'] or '',
            })

    results = []
    for row in sale_rows:
        sv = row['sale_value'] or Decimal('0')
        disc = row['discount'] or Decimal('0')
        total_paid = row['total_paid'] or Decimal('0')
        real_paid = total_paid + disc
        effective_sv = sv - disc
        real_outstanding = effective_sv - real_paid
        dt = row['activity_date']
        fd = row['fit_date']
        results.append({
            'pk': row['pk'],
            'sale_number': row['anthill_activity_id'],
            'customer': row['customer_name'] or '-',
            'contract': row['contract_number'] or '',
            'location': row['location'] or '',
            'sale_value': float(sv),
            'paid': float(real_paid),
            'outstanding': float(real_outstanding),
            'year': fd.year if fd else None,
            'fit_date': fd.strftime('%d/%m/%Y') if fd else '',
            'fit_date_iso': fd.isoformat() if fd else '',
            'payments': payments_by_sale.get(row['pk'], []),
        })
    # Sort by fit_date year descending (no fit date last), then outstanding descending
    results.sort(key=lambda x: (0 if x['year'] is None else 1, -(x['year'] or 0), -x['outstanding']))

    overpaid_results = []
    for row in overpaid_rows:
        sv = row['sale_value'] or Decimal('0')
        disc = row['discount'] or Decimal('0')
        total_paid = row['total_paid'] or Decimal('0')
        real_paid = total_paid + disc
        effective_sv = sv - disc
        overpay_amount = real_paid - effective_sv
        fd = row['fit_date']
        overpaid_results.append({
            'pk': row['pk'],
            'sale_number': row['anthill_activity_id'],
            'customer': row['customer_name'] or '-',
            'contract': row['contract_number'] or '',
            'sale_value': float(sv),
            'paid': float(real_paid),
            'overpaid': float(overpay_amount),
            'fit_date': fd.strftime('%d/%m/%Y') if fd else '',
            'payments': payments_by_sale.get(row['pk'], []),
        })
    overpaid_results.sort(key=lambda x: -x['overpaid'])

    return JsonResponse({
        'success': True, 'rows': results, 'count': len(results),
        'overpaid_rows': overpaid_results, 'overpaid_count': len(overpaid_results),
    })


@login_required
def dashboard_outstanding_xero_check(request):
    """
    Check Xero for payments for the sales currently shown in the outstanding
    balance report (respects the same location & period filters).
    Streams newline-delimited JSON so the frontend can show live progress.
    """
    import time
    from django.http import StreamingHttpResponse
    from .services import xero_api

    profile = getattr(request.user, 'profile', None)
    raw_location = request.GET.get('location') or (profile.selected_location if profile else '')
    contract_prefix = _contract_prefix_for_location(raw_location)
    time_filter = request.GET.get('period', 'all')

    # Check Xero connection first
    access_token, _ = xero_api.get_valid_access_token()
    if not access_token:
        return JsonResponse({'success': False, 'error': 'Xero is not connected. Please connect via the Xero settings page first.'})

    # Build the same queryset as dashboard_outstanding_report
    payments_subquery = Subquery(
        AnthillPayment.objects.filter(sale=OuterRef('pk'), ignored=False)
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
        .values('pk', 'anthill_activity_id', 'customer_name', 'contract_number',
                'sale_value', 'discount', 'total_paid', 'fit_date')
    )
    if contract_prefix:
        qs = qs.filter(contract_number__istartswith=contract_prefix)

    if time_filter == 'weekly':
        from datetime import date
        today = date.today()
        week_start = today - timedelta(days=today.weekday())
        week_end = week_start + timedelta(days=6)
        qs = qs.filter(fit_date__gte=week_start, fit_date__lte=week_end)
    elif time_filter == 'monthly':
        from datetime import date
        today = date.today()
        month_start = today.replace(day=1)
        next_month = (month_start + timedelta(days=32)).replace(day=1)
        qs = qs.filter(fit_date__gte=month_start, fit_date__lt=next_month)

    # Filter to only outstanding rows (same £10 threshold)
    sales_to_check = []
    for row in qs:
        sv = row['sale_value'] or Decimal('0')
        disc = row['discount'] or Decimal('0')
        total_paid = row['total_paid'] or Decimal('0')
        real_paid = total_paid + disc
        effective_sv = sv - disc
        if effective_sv - real_paid < Decimal('10'):
            continue
        if not row['contract_number']:
            continue
        sales_to_check.append(row)

    if not sales_to_check:
        return JsonResponse({'success': True, 'message': 'No outstanding sales to check.', 'results': [], 'stats': {}})

    def _stream():
        stats = {
            'total': len(sales_to_check),
            'checked': 0,
            'invoices_found': 0,
            'payments_created': 0,
            'payments_updated': 0,
            'no_invoice': 0,
            'errors': 0,
        }

        yield json.dumps({'type': 'start', 'total': len(sales_to_check)}) + '\n'

        # Track base payment IDs across all sales to prevent double-counting.
        # If a fallback-amount overpayment/prepayment has already been saved
        # to one sale, skip it on subsequent sales.
        seen_fallback_base_pids = set()

        for idx, row in enumerate(sales_to_check):
            sale_pk = row['pk']
            contract_number = row['contract_number']
            customer_name = row['customer_name'] or ''
            activity_id = row['anthill_activity_id']

            yield json.dumps({
                'type': 'checking',
                'index': idx + 1,
                'total': len(sales_to_check),
                'customer': customer_name,
                'contract': contract_number,
            }) + '\n'

            try:
                invoice_data = xero_api.get_sale_payments_from_xero(
                    contract_number=contract_number,
                    contact_name=customer_name or None,
                )
            except Exception as exc:
                stats['errors'] += 1
                yield json.dumps({
                    'type': 'result',
                    'index': idx + 1,
                    'customer': customer_name,
                    'contract': contract_number,
                    'status': 'error',
                    'message': str(exc),
                }) + '\n'
                time.sleep(1)
                continue

            stats['checked'] += 1

            if not invoice_data:
                stats['no_invoice'] += 1
                yield json.dumps({
                    'type': 'result',
                    'index': idx + 1,
                    'customer': customer_name,
                    'contract': contract_number,
                    'status': 'no_invoice',
                    'message': 'No matching invoices in Xero',
                }) + '\n'
                time.sleep(1.0)
                continue

            stats['invoices_found'] += len(invoice_data)

            # Write payments to DB
            try:
                sale = AnthillSale.objects.get(pk=sale_pk)
            except AnthillSale.DoesNotExist:
                continue

            sale_payments_created = 0
            sale_payments_updated = 0
            sale_payments_skipped = 0

            # Payment cap: never credit more than the sale is worth.
            sale_value = (sale.sale_value or Decimal('0')) - (sale.discount or Decimal('0'))
            existing_paid = (
                AnthillPayment.objects.filter(sale=sale)
                .aggregate(total=Sum('amount'))['total']
            ) or Decimal('0')
            running_total = existing_paid

            for inv in invoice_data:
                if inv.get('status', '').upper() in ('CANCELLED', 'VOIDED', 'DELETED'):
                    continue

                for p in inv['payments']:
                    if p.get('status', '').upper() == 'CANCELLED':
                        continue
                    pid = p.get('payment_id') or ''
                    base_pid = p.get('base_payment_id') or ''
                    is_fallback = p.get('is_fallback', False)
                    p_amount = p['amount'] or Decimal('0')

                    # Prevent double-counting: if a fallback-amount payment
                    # (no per-invoice allocation from Xero) has already been
                    # attributed to another sale in this batch, skip it.
                    if is_fallback and base_pid and base_pid in seen_fallback_base_pids:
                        continue
                    # Also check DB for prior runs: same base on a different sale
                    if is_fallback and base_pid:
                        already_on_other = AnthillPayment.objects.filter(
                            anthill_payment_id__startswith=base_pid,
                        ).exclude(sale=sale).exists()
                        if already_on_other:
                            continue

                    # Check if this payment already exists (update won't change total)
                    already_exists = False
                    if pid:
                        already_exists = AnthillPayment.objects.filter(
                            sale=sale, anthill_payment_id=pid).exists()
                    else:
                        already_exists = AnthillPayment.objects.filter(
                            sale=sale, xero_invoice_id=inv['invoice_id'],
                            date=p['date']).exists()

                    # Payment cap: skip NEW payments that would exceed sale value
                    if not already_exists and sale_value > 0:
                        if running_total + p_amount > sale_value + Decimal('0.50'):
                            sale_payments_skipped += 1
                            continue

                    defaults = {
                        'source': 'xero',
                        'xero_invoice_id': inv['invoice_id'],
                        'xero_invoice_number': inv['invoice_number'],
                        'invoice_total': inv['total'],
                        'invoice_amount_due': inv['amount_due'],
                        'invoice_status': inv['status'],
                        'payment_type': p['reference'] or 'Payment',
                        'date': p['date'],
                        'amount': p_amount,
                        'status': p['status'],
                        'location': '',
                        'user_name': '',
                    }

                    if pid:
                        obj, created = AnthillPayment.objects.update_or_create(
                            sale=sale,
                            anthill_payment_id=pid,
                            defaults=defaults,
                        )
                    else:
                        obj, created = AnthillPayment.objects.update_or_create(
                            sale=sale,
                            xero_invoice_id=inv['invoice_id'],
                            date=p['date'],
                            defaults=defaults,
                        )

                    if created:
                        sale_payments_created += 1
                        stats['payments_created'] += 1
                        running_total += p_amount
                    else:
                        sale_payments_updated += 1
                        stats['payments_updated'] += 1

                    # Track fallback base IDs to prevent cross-sale duplication
                    if is_fallback and base_pid:
                        seen_fallback_base_pids.add(base_pid)

                # Invoice-level summary row for invoices with no individual payments
                if not inv['payments']:
                    inv_amount = inv['amount_paid'] or Decimal('0')
                    already_exists = AnthillPayment.objects.filter(
                        sale=sale, xero_invoice_id=inv['invoice_id'],
                        date=None).exists()
                    if not already_exists and sale_value > 0:
                        if running_total + inv_amount > sale_value + Decimal('0.50'):
                            sale_payments_skipped += 1
                            continue

                    defaults = {
                        'source': 'xero',
                        'xero_invoice_id': inv['invoice_id'],
                        'xero_invoice_number': inv['invoice_number'],
                        'invoice_total': inv['total'],
                        'invoice_amount_due': inv['amount_due'],
                        'invoice_status': inv['status'],
                        'payment_type': 'Invoice Payment',
                        'date': None,
                        'amount': inv_amount,
                        'status': inv['status'],
                        'location': '',
                        'user_name': '',
                    }
                    obj, created = AnthillPayment.objects.update_or_create(
                        sale=sale,
                        xero_invoice_id=inv['invoice_id'],
                        date=None,
                        defaults=defaults,
                    )
                    if created:
                        sale_payments_created += 1
                        stats['payments_created'] += 1
                        running_total += inv_amount
                    else:
                        sale_payments_updated += 1
                        stats['payments_updated'] += 1

            inv_nums = ', '.join(inv['invoice_number'] for inv in invoice_data)
            # Clean up old-format payment IDs (plain UUID without _InvoiceID suffix)
            # that were created before the allocation-based parsing fix.
            _cleanup_old_format_payment_ids(sale)
            # Deduplicate manual payments that match Xero payments
            dups_removed = _deduplicate_manual_payments(sale)
            msg = f'{inv_nums} — {sale_payments_created} new, {sale_payments_updated} updated'
            if dups_removed:
                msg += f', {dups_removed} dup removed'
            if sale_payments_skipped:
                msg += f', {sale_payments_skipped} skipped (exceeds sale value)'
            yield json.dumps({
                'type': 'result',
                'index': idx + 1,
                'customer': customer_name,
                'contract': contract_number,
                'status': 'found',
                'message': msg,
            }) + '\n'

            time.sleep(1.5)  # Xero rate limit

        yield json.dumps({'type': 'done', 'stats': stats}) + '\n'

    response = StreamingHttpResponse(_stream(), content_type='application/x-ndjson')
    response['X-Accel-Buffering'] = 'no'
    response['Cache-Control'] = 'no-cache'
    return response


def _deduplicate_manual_payments(sale):
    """
    Remove manual (anthill-sourced) payments that clearly duplicate a Xero payment.
    A manual payment is considered a duplicate if:
      - It has source='anthill' (or empty/non-xero)
      - A Xero payment exists on the same sale with identical amount
    Returns the number of manual payments removed.
    """
    xero_payments = list(
        AnthillPayment.objects.filter(sale=sale, source='xero')
        .values_list('amount', flat=True)
    )
    if not xero_payments:
        return 0

    # Build a list of xero amounts (allow each to match at most once)
    xero_amounts = list(xero_payments)  # mutable copy
    manual_payments = AnthillPayment.objects.filter(sale=sale).exclude(source='xero')
    removed = 0
    for mp in manual_payments:
        mp_amount = mp.amount or Decimal('0')
        # Look for an exact-amount match in the Xero payments
        for i, xa in enumerate(xero_amounts):
            if xa is not None and abs(mp_amount - xa) < Decimal('0.01'):
                mp.delete()
                xero_amounts.pop(i)  # consume this Xero match
                removed += 1
                break
    return removed


def _cleanup_old_format_payment_ids(sale):
    """
    Remove Xero payment records that use the old-format anthill_payment_id
    (plain UUID) when a new-format record (UUID_InvoiceID) now exists
    for the same base payment on this sale. This prevents stale records
    from a previous run doubling up the totals.
    """
    import re
    uuid_re = re.compile(
        r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$',
        re.IGNORECASE,
    )
    xero_payments = AnthillPayment.objects.filter(sale=sale, source='xero')
    # Collect new-format base IDs (anything before the first _InvoiceID)
    new_format_bases = set()
    for ap in xero_payments:
        pid = ap.anthill_payment_id or ''
        if '_' in pid:
            base = pid.split('_', 1)[0]
            new_format_bases.add(base.lower())
    if not new_format_bases:
        return
    # Delete old-format records whose plain UUID matches a new-format base
    for ap in xero_payments:
        pid = ap.anthill_payment_id or ''
        if uuid_re.match(pid) and pid.lower() in new_format_bases:
            ap.delete()


@login_required
def dashboard_outstanding_xero_check_single(request):
    """
    Check Xero for payments for a single sale by PK.
    Returns JSON with the result.
    """
    import time
    from .services import xero_api

    sale_pk = request.GET.get('sale_pk')
    if not sale_pk:
        return JsonResponse({'success': False, 'error': 'Missing sale_pk parameter.'})

    access_token, _ = xero_api.get_valid_access_token()
    if not access_token:
        return JsonResponse({'success': False, 'error': 'Xero is not connected.'})

    try:
        sale = AnthillSale.objects.get(pk=sale_pk)
    except AnthillSale.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Sale not found.'})

    if not sale.contract_number:
        return JsonResponse({'success': False, 'error': 'Sale has no contract number.'})

    try:
        invoice_data = xero_api.get_sale_payments_from_xero(
            contract_number=sale.contract_number,
            contact_name=sale.customer_name or None,
        )
    except Exception as exc:
        return JsonResponse({'success': False, 'error': str(exc)})

    if not invoice_data:
        return JsonResponse({'success': True, 'found': False, 'message': 'No matching invoices found in Xero.'})

    payments_created = 0
    payments_updated = 0
    payments_skipped = 0

    # Payment cap: never credit more than the sale is worth.
    sale_value = (sale.sale_value or Decimal('0')) - (sale.discount or Decimal('0'))
    existing_paid = (
        AnthillPayment.objects.filter(sale=sale)
        .aggregate(total=Sum('amount'))['total']
    ) or Decimal('0')
    running_total = existing_paid

    for inv in invoice_data:
        if inv.get('status', '').upper() in ('CANCELLED', 'VOIDED', 'DELETED'):
            continue
        for p in inv['payments']:
            if p.get('status', '').upper() == 'CANCELLED':
                continue
            pid = p.get('payment_id') or ''
            base_pid = p.get('base_payment_id') or ''
            is_fallback = p.get('is_fallback', False)
            p_amount = p['amount'] or Decimal('0')

            # Prevent double-counting: if a fallback-amount payment
            # already exists on a different sale, skip it.
            if is_fallback and base_pid:
                already_on_other = AnthillPayment.objects.filter(
                    anthill_payment_id__startswith=base_pid,
                ).exclude(sale=sale).exists()
                if already_on_other:
                    continue

            # Check if this payment already exists (update won't change total)
            already_exists = False
            if pid:
                already_exists = AnthillPayment.objects.filter(
                    sale=sale, anthill_payment_id=pid).exists()
            else:
                already_exists = AnthillPayment.objects.filter(
                    sale=sale, xero_invoice_id=inv['invoice_id'],
                    date=p['date']).exists()

            # Payment cap: skip NEW payments that would exceed sale value
            if not already_exists and sale_value > 0:
                if running_total + p_amount > sale_value + Decimal('0.50'):
                    payments_skipped += 1
                    continue

            defaults = {
                'source': 'xero',
                'xero_invoice_id': inv['invoice_id'],
                'xero_invoice_number': inv['invoice_number'],
                'invoice_total': inv['total'],
                'invoice_amount_due': inv['amount_due'],
                'invoice_status': inv['status'],
                'payment_type': p['reference'] or 'Payment',
                'date': p['date'],
                'amount': p_amount,
                'status': p['status'],
                'location': '',
                'user_name': '',
            }
            if pid:
                obj, created = AnthillPayment.objects.update_or_create(
                    sale=sale, anthill_payment_id=pid, defaults=defaults)
            else:
                obj, created = AnthillPayment.objects.update_or_create(
                    sale=sale, xero_invoice_id=inv['invoice_id'], date=p['date'], defaults=defaults)
            if created:
                payments_created += 1
                running_total += p_amount
            else:
                payments_updated += 1

        if not inv['payments']:
            inv_amount = inv['amount_paid'] or Decimal('0')
            already_exists = AnthillPayment.objects.filter(
                sale=sale, xero_invoice_id=inv['invoice_id'],
                date=None).exists()
            if not already_exists and sale_value > 0:
                if running_total + inv_amount > sale_value + Decimal('0.50'):
                    payments_skipped += 1
                    continue

            defaults = {
                'source': 'xero',
                'xero_invoice_id': inv['invoice_id'],
                'xero_invoice_number': inv['invoice_number'],
                'invoice_total': inv['total'],
                'invoice_amount_due': inv['amount_due'],
                'invoice_status': inv['status'],
                'payment_type': 'Invoice Payment',
                'date': None,
                'amount': inv_amount,
                'status': inv['status'],
                'location': '',
                'user_name': '',
            }
            obj, created = AnthillPayment.objects.update_or_create(
                sale=sale, xero_invoice_id=inv['invoice_id'], date=None, defaults=defaults)
            if created:
                payments_created += 1
                running_total += inv_amount
            else:
                payments_updated += 1

    inv_nums = ', '.join(inv['invoice_number'] for inv in invoice_data)
    # Clean up old-format payment IDs
    _cleanup_old_format_payment_ids(sale)
    # Deduplicate: remove manual payments that match Xero payments
    duplicates_removed = _deduplicate_manual_payments(sale)
    msg = f'{inv_nums} — {payments_created} new, {payments_updated} updated'
    if duplicates_removed:
        msg += f', {duplicates_removed} duplicate manual payment{"s" if duplicates_removed != 1 else ""} removed'
    if payments_skipped:
        msg += f', {payments_skipped} skipped (exceeds sale value £{sale_value})'
    return JsonResponse({
        'success': True,
        'found': True,
        'message': msg,
        'payments_created': payments_created,
        'payments_updated': payments_updated,
        'duplicates_removed': duplicates_removed,
        'payments_skipped': payments_skipped,
    })


@login_required
def dashboard_outstanding_pdf(request):
    """Download the outstanding balance report as a PDF."""
    from .pdf_generator import generate_outstanding_report_pdf

    profile = getattr(request.user, 'profile', None)
    raw_location = request.GET.get('location') or (profile.selected_location if profile else '')
    contract_prefix = _contract_prefix_for_location(raw_location)
    location_label = raw_location.title() if raw_location else 'All Locations'

    payments_subquery = Subquery(
        AnthillPayment.objects.filter(sale=OuterRef('pk'), ignored=False)
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
                'sale_value', 'discount', 'activity_date', 'total_paid')
    )
    if contract_prefix:
        qs = qs.filter(contract_number__istartswith=contract_prefix)

    rows = []
    for row in qs:
        sv = row['sale_value'] or Decimal('0')
        disc = row['discount'] or Decimal('0')
        total_paid = row['total_paid'] or Decimal('0')
        real_paid = total_paid + disc
        effective_sv = sv - disc
        real_outstanding = effective_sv - real_paid
        if real_outstanding < Decimal('10'):
            continue
        dt = row['activity_date']
        rows.append({
            'pk': row['pk'],
            'sale_number': row['anthill_activity_id'],
            'customer': row['customer_name'] or '-',
            'contract': row['contract_number'] or '',
            'sale_value': float(sv),
            'paid': float(real_paid),
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
