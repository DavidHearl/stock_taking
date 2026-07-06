from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Sum, DecimalField, Value, OuterRef, Subquery, F, ExpressionWrapper
from django.db.models.functions import TruncWeek, TruncMonth, Coalesce
from django.http import HttpResponse, JsonResponse
from datetime import datetime, timedelta
from decimal import Decimal
import json
import logging
from .models import Order, PurchaseOrder, PurchaseOrderProduct, StockItem, StockHistory, AnthillSale, AnthillPayment

logger = logging.getLogger(__name__)

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


# ── Sales targets ──────────────────────────────────────────────────────────
# Fixed company sales objectives, measured against fits scheduled in the period
# (AnthillSale.sale_value bucketed by fit_date, same source as the sales cards).
WEEKLY_SALES_TARGET = Decimal('25000')
MONTHLY_SALES_TARGET = Decimal('100000')
YEARLY_SALES_TARGET = Decimal('1250000')


def _get_target_breakdown(start_date, end_date, target, contract_prefix=''):
	"""Break sales fitting in [start_date, end_date] into paid / outstanding vs a target.

	``actual`` is gross ``sale_value`` (matches the "This Week's Sales" / "Monthly
	Sales" cards). ``paid`` is confirmed payments capped at each sale's value, and
	``outstanding`` is the remainder, so paid + outstanding == actual. ``remaining``
	is the shortfall against the target (0 once the target is met or beaten).
	"""
	payments_subquery = Subquery(
		AnthillPayment.objects.filter(sale=OuterRef('pk'), ignored=False)
		.values('sale')
		.annotate(total=Sum('amount'))
		.values('total'),
		output_field=DecimalField(max_digits=14, decimal_places=2),
	)
	qs = (
		AnthillSale.objects
		.filter(fit_date__gte=start_date, fit_date__lte=end_date, sale_value__gt=0)
		.exclude(status__in=['cancelled', 'dead'])
		.annotate(total_paid=Coalesce(payments_subquery, Value(Decimal('0')), output_field=DecimalField(max_digits=14, decimal_places=2)))
		.values('sale_value', 'total_paid')
	)
	if contract_prefix:
		qs = qs.filter(contract_number__istartswith=contract_prefix)

	actual = Decimal('0')
	paid = Decimal('0')
	count = 0
	for row in qs:
		value = row['sale_value'] or Decimal('0')
		row_paid = min(row['total_paid'] or Decimal('0'), value)
		actual += value
		paid += row_paid
		count += 1
	outstanding = actual - paid
	target = Decimal(str(target))
	remaining = max(target - actual, Decimal('0'))
	pct = float(actual / target * 100) if target else 0.0
	return {
		'target': float(target),
		'actual': float(actual),
		'paid': float(paid),
		'outstanding': float(outstanding),
		'remaining': float(remaining),
		'count': count,
		'pct': round(pct, 1),
	}


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

    # Get approved POs waiting to arrive
    pending_pos_qs = PurchaseOrder.objects.filter(
        status__in=['Approved', 'Ordered', 'Sent']
    ).exclude(
        status__in=['Received', 'Invoiced', 'Cancelled', 'Closed']
    ).order_by('-issue_date')
    pending_pos = pending_pos_qs.count()
    pending_pos_list = [
        {
            'number': po.display_number or po.number or str(po.workguru_id),
            'supplier': po.supplier_name or '—',
            'total': float(po.total or 0),
            'status': po.status or '',
        }
        for po in pending_pos_qs[:20]
    ]

    # Item shortages — tracked items where quantity is below par level (par_level > 0)
    shortage_items = (
        StockItem.objects
        .filter(tracking_type__in=['stock', 'non-stock'], par_level__gt=0)
        .filter(quantity__lt=F('par_level'))
        .only('id', 'sku', 'name', 'quantity', 'par_level', 'cost')
        .order_by('quantity')
    )

    # Incoming quantities from open/partially-received POs (not yet fully received)
    shortage_skus = [s.sku for s in shortage_items]
    incoming_data = (
        PurchaseOrderProduct.objects
        .filter(
            sku__in=shortage_skus,
            purchase_order__status__in=['Approved', 'Ordered', 'Sent', 'Partially Received'],
        )
        .filter(order_quantity__gt=Coalesce(F('received_quantity'), Value(0, output_field=DecimalField())))
        .values('sku')
        .annotate(total=Sum(
            ExpressionWrapper(
                (F('order_quantity') - Coalesce(F('received_quantity'), Value(0, output_field=DecimalField())))
                * Coalesce(F('stock_item__pack_size'), Value(1, output_field=DecimalField())),
                output_field=DecimalField()
            )
        ))
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
            purchase_order__status__in=['Approved', 'Ordered', 'Sent', 'Partially Received'],
        )
        .exclude(purchase_order__supplier_name__icontains='carnehill')
        .exclude(purchase_order__supplier_name__icontains='os doors')
        .select_related('purchase_order', 'stock_item')
    )
    # Build a dict keyed by SKU to aggregate same items
    incoming_by_sku = {}
    for p in incoming_products:
        pack_size = int(getattr(p.stock_item, 'pack_size', 1) or 1)
        outstanding = float(((p.order_quantity or 0) - (p.received_quantity or 0)) * pack_size)
        if outstanding <= 0:
            continue
        effective_date = p.purchase_order.expected_date or ''
        sku_key = p.sku or p.name or str(p.id)
        if sku_key in incoming_by_sku:
            incoming_by_sku[sku_key]['qty_outstanding'] += outstanding
            po_num = p.purchase_order.display_number or str(p.purchase_order.id)
            if po_num not in incoming_by_sku[sku_key]['po_numbers']:
                incoming_by_sku[sku_key]['po_numbers'].append(po_num)
            # Keep the earliest expected date
            existing_date = incoming_by_sku[sku_key]['expected_date']
            if effective_date and (not existing_date or existing_date == '-' or effective_date < existing_date):
                incoming_by_sku[sku_key]['expected_date'] = effective_date
        else:
            incoming_by_sku[sku_key] = {
                'sku': p.sku or '-',
                'name': p.name or '-',
                'supplier': p.purchase_order.supplier_name or '-',
                'po_numbers': [p.purchase_order.display_number or str(p.purchase_order.id)],
                'qty_outstanding': outstanding,
                'expected_date': effective_date or '-',
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
        .values('sale_value', 'discount', 'total_paid', 'customer_id', 'customer_name')
    )
    if contract_prefix:
        outstanding_qs = outstanding_qs.filter(contract_number__istartswith=contract_prefix)

    outstanding_rows = list(outstanding_qs)
    total_outstanding_balance = sum(
        max((row['sale_value'] or Decimal('0')) - 2 * (row['discount'] or Decimal('0')) - (row['total_paid'] or Decimal('0')), Decimal('0'))
        for row in outstanding_rows
        if (row['sale_value'] or Decimal('0')) - 2 * (row['discount'] or Decimal('0')) > (row['total_paid'] or Decimal('0'))
    ) or Decimal('0')
    outstanding_debtor_customers = set()
    for row in outstanding_rows:
        if (row['sale_value'] or Decimal('0')) - 2 * (row['discount'] or Decimal('0')) > (row['total_paid'] or Decimal('0')):
            outstanding_debtor_customers.add(row['customer_id'] or row['customer_name'])
    outstanding_debtor_count = len(outstanding_debtor_customers)

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

    # ── Preview data for stat-card "Detailed" variant tables ──

    # Outstanding: top 10 debtors by outstanding balance
    debtor_map = {}
    for row in outstanding_rows:
        sv = row['sale_value'] or Decimal('0')
        disc = row['discount'] or Decimal('0')
        paid = row['total_paid'] or Decimal('0')
        owed = sv - 2 * disc - paid
        if owed >= Decimal('10'):
            name = row['customer_name'] or '-'
            if name not in debtor_map:
                debtor_map[name] = {'sale_value': Decimal('0'), 'outstanding': Decimal('0')}
            debtor_map[name]['sale_value'] += sv
            debtor_map[name]['outstanding'] += owed
    outstanding_preview = sorted(
        [{'customer': n, 'sale_value': float(v['sale_value']), 'outstanding': float(v['outstanding'])}
         for n, v in debtor_map.items()],
        key=lambda x: -x['outstanding'],
    )[:10]

    # Sales After: next 10 upcoming fits by date
    sales_after_preview_qs = (
        AnthillSale.objects
        .filter(fit_date__gte=today, sale_value__gt=0)
        .exclude(status__in=['cancelled', 'dead'])
        .order_by('fit_date')
    )
    if contract_prefix:
        sales_after_preview_qs = sales_after_preview_qs.filter(contract_number__istartswith=contract_prefix)
    sales_after_preview = [
        {'customer': s['customer_name'] or '-',
         'fit_date': s['fit_date'].strftime('%d/%m') if s['fit_date'] else '',
         'value': float(s['sale_value'] or 0)}
        for s in sales_after_preview_qs.values('customer_name', 'fit_date', 'sale_value')[:10]
    ]

    # Stock Value: top 10 items by total value (qty * cost)
    stock_preview = sorted(
        [{'name': i.name, 'qty': i.quantity, 'value': float(i.cost * i.quantity)}
         for i in stock_items if i.cost and i.quantity],
        key=lambda x: -x['value'],
    )[:10]

    # Week Sales: this week's fits (up to 10)
    week_preview_qs = (
        AnthillSale.objects
        .filter(fit_date__gte=this_week_start, fit_date__lte=this_week_end, sale_value__gt=0)
        .exclude(status__in=['cancelled', 'dead'])
        .order_by('fit_date')
    )
    if contract_prefix:
        week_preview_qs = week_preview_qs.filter(contract_number__istartswith=contract_prefix)
    week_preview = [
        {'customer': s['customer_name'] or '-',
         'fit_date': s['fit_date'].strftime('%a %d') if s['fit_date'] else '',
         'value': float(s['sale_value'] or 0)}
        for s in week_preview_qs.values('customer_name', 'fit_date', 'sale_value')[:10]
    ]

    # Monthly Sales: this month's fits (up to 10)
    from calendar import monthrange
    month_start = today.replace(day=1)
    month_end = today.replace(day=monthrange(today.year, today.month)[1])
    monthly_preview_qs = (
        AnthillSale.objects
        .filter(fit_date__gte=month_start, fit_date__lte=month_end, sale_value__gt=0)
        .exclude(status__in=['cancelled', 'dead'])
        .order_by('fit_date')
    )
    if contract_prefix:
        monthly_preview_qs = monthly_preview_qs.filter(contract_number__istartswith=contract_prefix)
    monthly_preview = [
        {'customer': s['customer_name'] or '-',
         'fit_date': s['fit_date'].strftime('%d/%m') if s['fit_date'] else '',
         'value': float(s['sale_value'] or 0)}
        for s in monthly_preview_qs.values('customer_name', 'fit_date', 'sale_value')[:10]
    ]

    # Avg Sale: last 6 months breakdown
    from django.db.models.functions import TruncMonth as _TruncMonth
    avg_preview_qs = AnthillSale.objects.filter(
        fit_date__gte=today - timedelta(days=180), fit_date__lte=today, sale_value__gt=0,
    ).exclude(status__in=['cancelled', 'dead'])
    if contract_prefix:
        avg_preview_qs = avg_preview_qs.filter(contract_number__istartswith=contract_prefix)
    avg_monthly = (
        avg_preview_qs.annotate(month=_TruncMonth('fit_date'))
        .values('month')
        .annotate(count=Count('id'), total=Sum('sale_value'))
        .order_by('-month')
    )
    avg_preview = [
        {'month': (m['month'].date() if hasattr(m['month'], 'date') else m['month']).strftime('%b %Y'),
         'count': m['count'],
         'avg': round(float(m['total'] or 0) / m['count'], 0) if m['count'] else 0}
        for m in avg_monthly
    ][:6]

    # ── Workflow stages grouped by role ──
    from .models import WorkflowStage, OrderWorkflowProgress
    from collections import OrderedDict
    
    all_wf_stages = WorkflowStage.objects.all().order_by('order', 'phase')
    
    # Count orders at each stage (active orders only)
    wf_counts = dict(
        OrderWorkflowProgress.objects.filter(
            order__isnull=False,
            order__job_finished=False,
        )
        .values('current_stage_id')
        .annotate(count=Count('id'))
        .values_list('current_stage_id', 'count')
    )
    
    # Build role groups
    role_colours = {
        'customer-support': '#6366f1',
        'design': '#3b82f6',
        'fitter': '#10b981',
        'operations': '#f59e0b',
        'manufacturing': '#ef4444',
        'enquiry': '#8b5cf6',
        'waiting': '#94a3b8',
    }
    role_display = dict(WorkflowStage.ROLE_CHOICES)
    workflow_by_role = OrderedDict()
    for stage in all_wf_stages:
        # Normalise legacy underscore roles to hyphenated form
        role = stage.role.replace('_', '-') if stage.role else stage.role
        if role not in workflow_by_role:
            workflow_by_role[role] = {
                'role': role,
                'role_display': role_display.get(role, role),
                'colour': role_colours.get(role, '#94a3b8'),
                'stages': [],
                'total_orders': 0,
            }
        count = wf_counts.get(stage.id, 0)
        workflow_by_role[role]['stages'].append({
            'id': stage.id,
            'name': stage.name,
            'phase': stage.get_phase_display(),
            'count': count,
        })
        workflow_by_role[role]['total_orders'] += count
    
    workflow_roles = list(workflow_by_role.values())

    # Stock take status — has any stock_take been recorded today?
    stock_take_done_today = StockHistory.objects.filter(
        change_type='stock_take',
        created_at__date=today,
    ).exists()

    # Hide the pulsing stock-take widget on weekends and before 1 PM
    now = datetime.now()
    hide_stock_take_widget = (
        not stock_take_done_today
        and (now.weekday() >= 5 or now.hour < 13)
    )

    # ── Sales targets (paid/outstanding vs fixed objectives) ──
    # Weekly pies for last / this / next week, plus this month and this year.
    from calendar import monthrange as _monthrange
    next_week_start = this_week_start + timedelta(days=7)
    next_week_end = next_week_start + timedelta(days=6)
    month_start = today.replace(day=1)
    month_end = today.replace(day=_monthrange(today.year, today.month)[1])
    year_start = today.replace(month=1, day=1)
    year_end = today.replace(month=12, day=31)

    def _target_period(key, label, sublabel, start, end, target):
        data = _get_target_breakdown(start, end, target, contract_prefix)
        data.update({'key': key, 'label': label, 'sublabel': sublabel})
        return data

    _wk_range = lambda s, e: f"{s.strftime('%d %b')} – {e.strftime('%d %b')}"
    targets_data = [
        _target_period('last_week', 'Last Week', _wk_range(last_week_start, last_week_end),
                       last_week_start, last_week_end, WEEKLY_SALES_TARGET),
        _target_period('this_week', 'This Week', _wk_range(this_week_start, this_week_end),
                       this_week_start, this_week_end, WEEKLY_SALES_TARGET),
        _target_period('next_week', 'Next Week', _wk_range(next_week_start, next_week_end),
                       next_week_start, next_week_end, WEEKLY_SALES_TARGET),
        _target_period('this_month', 'This Month', today.strftime('%B %Y'),
                       month_start, month_end, MONTHLY_SALES_TARGET),
        _target_period('this_year', 'This Year', str(today.year),
                       year_start, year_end, YEARLY_SALES_TARGET),
    ]

    context = {
        'targets_json': json.dumps(targets_data),
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
        'pending_pos_json': json.dumps(pending_pos_list),
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
        'outstanding_preview_json': json.dumps(outstanding_preview),
        'sales_after_preview_json': json.dumps(sales_after_preview),
        'stock_preview_json': json.dumps(stock_preview),
        'week_preview_json': json.dumps(week_preview),
        'monthly_preview_json': json.dumps(monthly_preview),
        'avg_preview_json': json.dumps(avg_preview),
        'workflow_roles_json': json.dumps(workflow_roles),
        'stock_take_done_today': stock_take_done_today,
        'hide_stock_take_widget': hide_stock_take_widget,
    }
    return render(request, 'stock_take/dashboard.html', context)


def _write_xero_payments_for_sale(sale, invoice_data, seen_fallback_base_pids=None, anthill_payments=None):
    """Persist Xero invoice/payment data onto a single AnthillSale.

    Mirrors the linking logic used by the outstanding-balance Xero check
    endpoints (payment cap, fallback de-duplication, cleanup). Returns a
    ``(created, updated, skipped)`` tuple.

    ``anthill_payments`` — optional list of confirmed Anthill payments for this
    sale's contract (``[{'amount': Decimal, 'date': date, 'payment_type': str}]``).
    When a Xero invoice is still awaiting payment but Anthill has a matching
    confirmed payment, it is counted now rather than waiting for Xero to reconcile.
    """
    if seen_fallback_base_pids is None:
        seen_fallback_base_pids = set()

    created = 0
    updated = 0
    skipped = 0

    # Confirmed Anthill payments for this contract, consumed as we match them so
    # the same payment is never credited to two invoices.
    anthill_pool = [dict(ap) for ap in (anthill_payments or [])]

    def _consume_anthill(target):
        """Pop and return a confirmed Anthill payment matching ``target``."""
        if not target or target <= 0:
            return None
        for i, ap in enumerate(anthill_pool):
            amt = ap.get('amount') or Decimal('0')
            if abs(amt - target) < Decimal('0.50'):
                return anthill_pool.pop(i)
        return None

    # Payment cap: never credit more than the sale is worth.
    sale_value = (sale.sale_value or Decimal('0')) - (sale.discount or Decimal('0'))
    running_total = (
        AnthillPayment.objects.filter(sale=sale).aggregate(total=Sum('amount'))['total']
    ) or Decimal('0')

    for inv in invoice_data:
        if inv.get('status', '').upper() in ('CANCELLED', 'VOIDED', 'DELETED'):
            continue

        if inv['payments']:
            # A real Xero payment now represents this invoice — drop any earlier
            # invoice-summary placeholder (including an Anthill-confirmed credit)
            # so the placeholder and the real payment are never counted together.
            AnthillPayment.objects.filter(
                sale=sale, xero_invoice_id=inv['invoice_id'], date=None,
            ).delete()

        for p in inv['payments']:
            if p.get('status', '').upper() == 'CANCELLED':
                continue
            pid = p.get('payment_id') or ''
            base_pid = p.get('base_payment_id') or ''
            is_fallback = p.get('is_fallback', False)
            p_amount = p['amount'] or Decimal('0')

            # Prevent double-counting fallback (un-allocated) payments across sales
            if is_fallback and base_pid and base_pid in seen_fallback_base_pids:
                continue
            if is_fallback and base_pid:
                already_on_other = AnthillPayment.objects.filter(
                    anthill_payment_id__startswith=base_pid,
                ).exclude(sale=sale).exists()
                if already_on_other:
                    continue

            already_exists = False
            if pid:
                already_exists = AnthillPayment.objects.filter(
                    sale=sale, anthill_payment_id=pid).exists()
            else:
                already_exists = AnthillPayment.objects.filter(
                    sale=sale, xero_invoice_id=inv['invoice_id'],
                    date=p['date']).exists()

            if not already_exists and sale_value > 0:
                if running_total + p_amount > sale_value + Decimal('0.50'):
                    skipped += 1
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
                _obj, was_created = AnthillPayment.objects.update_or_create(
                    sale=sale, anthill_payment_id=pid, defaults=defaults)
            else:
                _obj, was_created = AnthillPayment.objects.update_or_create(
                    sale=sale, xero_invoice_id=inv['invoice_id'], date=p['date'], defaults=defaults)

            if was_created:
                created += 1
                running_total += p_amount
            else:
                updated += 1

            # This Xero payment already accounts for an Anthill-recorded payment
            # of the same amount — consume it so it can't also be matched to an
            # unpaid invoice below.
            _consume_anthill(p_amount)

            if is_fallback and base_pid:
                seen_fallback_base_pids.add(base_pid)

        # Invoice-level summary row for invoices with no individual payments
        if not inv['payments']:
            inv_amount = inv['amount_paid'] or Decimal('0')
            inv_status = inv['status']
            payment_label = 'Invoice Payment'

            # Xero hasn't reconciled a payment for this invoice yet. If Anthill
            # has a matching confirmed payment, count it now so the calendar /
            # balances don't have to wait for Xero to catch up.
            inv_due = inv.get('amount_due') or Decimal('0')
            if inv_due > Decimal('0.50'):
                match = _consume_anthill(inv_due) or _consume_anthill(inv.get('total') or Decimal('0'))
                if match:
                    inv_amount = match['amount']
                    payment_label = match.get('payment_type') or 'Anthill Payment'
                    inv_status = 'Confirmed (Anthill)'

            already_exists = AnthillPayment.objects.filter(
                sale=sale, xero_invoice_id=inv['invoice_id'], date=None).exists()
            if not already_exists and sale_value > 0:
                if running_total + inv_amount > sale_value + Decimal('0.50'):
                    skipped += 1
                    continue

            defaults = {
                'source': 'xero',
                'xero_invoice_id': inv['invoice_id'],
                'xero_invoice_number': inv['invoice_number'],
                'invoice_total': inv['total'],
                'invoice_amount_due': inv['amount_due'],
                'invoice_status': inv['status'],
                'payment_type': payment_label,
                'date': None,
                'amount': inv_amount,
                'status': inv_status,
                'location': '',
                'user_name': '',
            }
            _obj, was_created = AnthillPayment.objects.update_or_create(
                sale=sale, xero_invoice_id=inv['invoice_id'], date=None, defaults=defaults)
            if was_created:
                created += 1
                running_total += inv_amount
            else:
                updated += 1

    _cleanup_old_format_payment_ids(sale)
    _deduplicate_manual_payments(sale)
    return created, updated, skipped


def _sync_xero_payments_for_sales(sales, progress_callback=None, anthill_payments_by_contract=None):
    """Link Xero payments for the given AnthillSale objects.

    Used by the weekly report to keep payment data correct. Silently no-ops if
    Xero is not connected, and tolerates per-sale Xero/API errors so the report
    always renders. Returns the total number of payments created.

    ``progress_callback(done, total, created)`` — when supplied, is invoked once
    per sale (after each is processed) so callers can report progress. Any
    exception it raises is swallowed so it can never break the sync.

    ``anthill_payments_by_contract`` — optional ``{contract_number: [payments]}``
    map of confirmed Anthill payments. When present, invoices still awaiting
    payment in Xero are credited from a matching confirmed Anthill payment so the
    figures don't have to wait for Xero to reconcile.
    """
    from .services import xero_api

    total = len(sales)

    def _report(done, created):
        if progress_callback:
            try:
                progress_callback(done, total, created)
            except Exception:
                pass

    _report(0, 0)

    try:
        access_token, _ = xero_api.get_valid_access_token()
    except Exception:
        access_token = None
    if not access_token:
        return 0

    seen_fallback_base_pids = set()
    created_total = 0
    for idx, sale in enumerate(sales, start=1):
        try:
            if not sale.contract_number:
                continue
            try:
                invoice_data = xero_api.get_sale_payments_from_xero(
                    contract_number=sale.contract_number,
                    contact_name=sale.customer_name or None,
                )
            except Exception:
                continue
            if not invoice_data:
                continue
            try:
                anthill_for_sale = None
                if anthill_payments_by_contract:
                    anthill_for_sale = anthill_payments_by_contract.get(
                        (sale.contract_number or '').strip())
                created, _updated, _skipped = _write_xero_payments_for_sale(
                    sale, invoice_data, seen_fallback_base_pids,
                    anthill_payments=anthill_for_sale)
                created_total += created
            except Exception:
                continue
        finally:
            _report(idx, created_total)

    # Refresh the stored pooled balances (balance_payable / paid_in_full) for
    # every affected customer so the figures surfaced elsewhere (e.g. the fit
    # calendar) reflect the newly synced payments. The sale-detail pool logic is
    # the single source of truth — we just persist its result here rather than
    # re-deriving paid/outstanding wherever the values are displayed.
    try:
        from .customer_views import _recalculate_customer_financials
        _seen_customers = set()
        for sale in sales:
            cid = sale.customer_id
            if cid and cid not in _seen_customers:
                _seen_customers.add(cid)
                try:
                    _recalculate_customer_financials(sale.customer)
                except Exception:
                    continue
    except Exception:
        pass
    return created_total


def _sync_calendar_payments(sales, progress=None):
    """Calendar "Check Payments": link Xero payments and, for invoices still
    awaiting payment in Xero, credit matching confirmed Anthill payments.

    Flow (matches the way a person would reconcile):
      1. For each sale, look up its invoices in Xero.
      2. If every invoice is paid, it's done — nothing else to do.
      3. The first time a sale has an invoice still awaiting payment, kick off a
         single Anthill payments scrape in a **background thread** so it runs in
         parallel while the remaining sales are still being checked in Xero.
      4. Once all the Xero look-ups are done, wait for the Anthill scrape and
         credit each awaiting invoice from a matching confirmed Anthill payment.

    ``progress(done, total, created, message)`` reports rich status to the UI and
    is best-effort (never allowed to raise). Returns the number of payments
    created.
    """
    import threading
    from .services import xero_api

    total = len(sales)

    def _p(done, created, message=None):
        if progress:
            try:
                progress(done, total, created, message)
            except Exception:
                pass

    _p(0, 0, 'Connecting to Xero…')
    try:
        access_token, _ = xero_api.get_valid_access_token()
    except Exception:
        access_token = None
    if not access_token:
        _p(0, 0, 'Xero is not connected — cannot check payments.')
        return 0

    # ── Lazy + parallel Anthill scrape ────────────────────────────────────
    anthill_state = {'map': None, 'thread': None, 'started': False}

    def _start_anthill():
        if anthill_state['started']:
            return
        anthill_state['started'] = True
        _p(_done_holder['n'], created_total_holder['n'],
           'Awaiting-payment invoice found — fetching Anthill payments in the background…')

        def _worker():
            from django.db import connection as _conn
            try:
                from .invoice_views import confirmed_anthill_payments_by_contract
                anthill_state['map'] = confirmed_anthill_payments_by_contract(
                    period='last_12_months', location_filter='')
            except Exception:
                logger.exception('calendar Anthill scrape failed')
                anthill_state['map'] = {}
            finally:
                _conn.close()

        t = threading.Thread(target=_worker, daemon=True)
        t.start()
        anthill_state['thread'] = t

    def _invoice_awaiting(invoice_data):
        """True when a sale has an invoice with money still due in Xero and no
        individual payments recorded — exactly the case Anthill can fill in."""
        for inv in invoice_data:
            if inv.get('status', '').upper() in ('CANCELLED', 'VOIDED', 'DELETED'):
                continue
            due = inv.get('amount_due') or Decimal('0')
            if due > Decimal('0.50') and not inv['payments']:
                return True
        return False

    # Mutable holders so _start_anthill can report the current counters.
    _done_holder = {'n': 0}
    created_total_holder = {'n': 0}

    seen_fallback_base_pids = set()
    deferred = []   # (sale, invoice_data) awaiting Anthill matching

    # ── Pass 1: check Xero per sale; settle fully-paid sales immediately ───
    for idx, sale in enumerate(sales, start=1):
        _done_holder['n'] = idx
        name = sale.customer_name or sale.contract_number or f'Sale {sale.pk}'
        _p(idx - 1, created_total_holder['n'], f'Checking Xero — {name} ({idx}/{total})')
        try:
            if not sale.contract_number:
                _p(idx, created_total_holder['n'], f'{name}: no contract number — skipped')
                continue
            try:
                invoice_data = xero_api.get_sale_payments_from_xero(
                    contract_number=sale.contract_number,
                    contact_name=sale.customer_name or None,
                )
            except Exception:
                _p(idx, created_total_holder['n'], f'{name}: Xero look-up failed — skipped')
                continue
            if not invoice_data:
                _p(idx, created_total_holder['n'], f'{name}: no Xero invoices found')
                continue

            inv_count = len([i for i in invoice_data
                             if i.get('status', '').upper() not in ('CANCELLED', 'VOIDED', 'DELETED')])

            if _invoice_awaiting(invoice_data):
                # Start the Anthill scrape in parallel (once) and defer the write
                # until the scraped data is available.
                _start_anthill()
                deferred.append((sale, invoice_data))
                _p(idx, created_total_holder['n'],
                   f'{name}: {inv_count} Xero invoice(s), awaiting payment — will check Anthill')
            else:
                try:
                    created, _u, _s = _write_xero_payments_for_sale(
                        sale, invoice_data, seen_fallback_base_pids)
                    created_total_holder['n'] += created
                    _p(idx, created_total_holder['n'],
                       f'{name}: paid in Xero — {created} payment(s) linked')
                except Exception:
                    logger.exception('calendar payment write failed for sale %s', sale.pk)
                    _p(idx, created_total_holder['n'], f'{name}: write failed')
        except Exception:
            logger.exception('calendar payment sync: sale %s failed', sale.pk)

    # ── Wait for the parallel Anthill scrape (started during pass 1) ──────
    if anthill_state['thread'] is not None:
        _p(total, created_total_holder['n'],
           'Waiting for Anthill payments to finish loading…')
        anthill_state['thread'].join(timeout=180)
    anthill_map = anthill_state['map'] or {}

    # ── Pass 2: match Anthill confirmed payments to awaiting invoices ─────
    deferred_total = len(deferred)
    for j, (sale, invoice_data) in enumerate(deferred, start=1):
        name = sale.customer_name or sale.contract_number or f'Sale {sale.pk}'
        anthill_for_sale = anthill_map.get((sale.contract_number or '').strip())
        n_anthill = len(anthill_for_sale or [])
        _p(total, created_total_holder['n'],
           f'Matching Anthill — {name} ({j}/{deferred_total}): {n_anthill} confirmed payment(s)')
        try:
            created, _u, _s = _write_xero_payments_for_sale(
                sale, invoice_data, seen_fallback_base_pids,
                anthill_payments=anthill_for_sale)
            created_total_holder['n'] += created
            if anthill_for_sale:
                _p(total, created_total_holder['n'],
                   f'{name}: credited {created} payment(s) from Anthill')
            elif created:
                _p(total, created_total_holder['n'],
                   f'{name}: {created} payment(s) linked')
            else:
                _p(total, created_total_holder['n'],
                   f'{name}: no matching Anthill payment found')
        except Exception:
            logger.exception('calendar Anthill match failed for sale %s', sale.pk)

    # ── Refresh stored pooled balances for affected customers ────────────
    _p(total, created_total_holder['n'], 'Updating customer balances…')
    try:
        from .customer_views import _recalculate_customer_financials
        _seen_customers = set()
        for sale in sales:
            cid = sale.customer_id
            if cid and cid not in _seen_customers:
                _seen_customers.add(cid)
                try:
                    _recalculate_customer_financials(sale.customer)
                except Exception:
                    continue
    except Exception:
        pass

    return created_total_holder['n']


@login_required
def dashboard_weekly_summary(request):
    """JSON endpoint — comprehensive three-week operational summary for B2C fitting company."""
    from .models import Remedial, BoardsPO as _BoardsPO

    profile = getattr(request.user, 'profile', None)
    contract_prefix = _contract_prefix_for_location(profile.selected_location if profile else '')
    today = datetime.now().date()

    # ── Week boundaries ──────────────────────────────────────────────────────
    this_week_start = today - timedelta(days=today.weekday())
    this_week_end   = this_week_start + timedelta(days=6)
    last_week_start = this_week_start - timedelta(days=7)
    last_week_end   = last_week_start + timedelta(days=6)
    next_week_start = this_week_start + timedelta(days=7)
    next_week_end   = next_week_start + timedelta(days=6)

    from .models import FitAppointment as _FitAppointment
    from .customer_views import _orders_financials
    _fitter_name_map = dict(_FitAppointment.FITTER_CHOICES)

    def _fitters_for_orders(order_ids):
        """Map order ids -> comma-joined fitter display names (multi-fitter jobs)."""
        result = {}
        if not order_ids:
            return result
        for appt in (_FitAppointment.objects
                     .filter(order_id__in=order_ids)
                     .values('order_id', 'fitter')):
            name = _fitter_name_map.get(appt['fitter'], appt['fitter'])
            names = result.setdefault(appt['order_id'], [])
            if name and name not in names:
                names.append(name)
        return {oid: ', '.join(v) for oid, v in result.items()}

    # ── Source of truth: the fit calendar ────────────────────────────────────
    # Every fit in the report comes from a FitAppointment (the calendar) — NOT
    # from Order.fit_date / AnthillSale.fit_date, which can drift. One job == one
    # order; multi-fitter / multi-day appointments collapse to a single job, so
    # nothing is ever double-counted.
    window_appts = (_FitAppointment.objects
                    .filter(fit_date__gte=last_week_start, fit_date__lte=next_week_end,
                            order__isnull=False)
                    .select_related('order'))
    jobs_by_order = {}
    for appt in window_appts:
        order = appt.order
        if not order:
            continue
        rec = jobs_by_order.get(order.id)
        fitter_name = _fitter_name_map.get(appt.fitter, appt.fitter)
        if rec is None:
            jobs_by_order[order.id] = {
                'order': order,
                'fit_date': appt.fit_date,
                'fitters': [fitter_name] if fitter_name else [],
            }
        else:
            if appt.fit_date < rec['fit_date']:
                rec['fit_date'] = appt.fit_date
            if fitter_name and fitter_name not in rec['fitters']:
                rec['fitters'].append(fitter_name)

    # Contract numbers (for the location filter + the table "Contract" column),
    # keyed by the order's Anthill sale number.
    _job_sale_numbers = [r['order'].sale_number for r in jobs_by_order.values() if r['order'].sale_number]
    contract_by_sale = {}
    if _job_sale_numbers:
        for row in (AnthillSale.objects
                    .filter(anthill_activity_id__in=_job_sale_numbers)
                    .values('anthill_activity_id', 'contract_number')):
            cn = row['contract_number'] or ''
            if row['anthill_activity_id'] not in contract_by_sale or cn:
                contract_by_sale[row['anthill_activity_id']] = cn

    # Location scoping: keep only jobs whose linked Anthill contract matches the
    # selected location's prefix (when one is configured).
    if contract_prefix:
        _pref = contract_prefix.upper()
        jobs_by_order = {
            oid: rec for oid, rec in jobs_by_order.items()
            if (contract_by_sale.get(rec['order'].sale_number, '') or '').upper().startswith(_pref)
        }

    # ── Xero sync for the visible jobs ───────────────────────────────────────
    # Pull the latest Xero payments for the jobs on the calendar and link any not
    # yet recorded, so balances are fresh before we read them. _sync_xero_…
    # recalculates each affected customer's stored balance. No-op if Xero offline.
    _sales_to_sync = [
        s for s in (AnthillSale.objects
                    .filter(order_id__in=jobs_by_order.keys(), sale_value__gt=0)
                    .exclude(status__in=['cancelled', 'dead']))
        if (s.balance_payable is None or s.balance_payable >= Decimal('1'))
    ]
    payments_linked = _sync_xero_payments_for_sales(_sales_to_sync) if _sales_to_sync else 0

    # ── Per-job financials (single source of truth, identical to the calendar) ─
    _job_orders = [r['order'] for r in jobs_by_order.values()]
    fin_by_sale = _orders_financials(_job_orders)

    remedial_order_ids = set(
        Remedial.objects
        .filter(original_order_id__in=jobs_by_order.keys())
        .values_list('original_order_id', flat=True)
    ) if jobs_by_order else set()

    def _week_of(d):
        if d < this_week_start:
            return 'last'
        if d <= this_week_end:
            return 'this'
        return 'next'

    jobs = []
    for oid, rec in jobs_by_order.items():
        order = rec['order']
        fin = fin_by_sale.get(order.sale_number) if order.sale_number else None
        if fin:
            value = float(fin['sale_value'] or 0)
            owed = max(float(fin['outstanding'] or 0), 0.0)
            paid = min(max(float(fin['payments_total'] or 0), 0.0), value)
        else:
            value = float(order.total_value_inc_vat or 0)
            owed = value
            paid = 0.0
        if owed < 10:
            pay_key, pay_label = 'paid', 'Paid in Full'
        elif paid >= 10:
            pay_key, pay_label = 'part', 'Part Paid'
        else:
            pay_key, pay_label = 'unpaid', 'Unpaid'
        jobs.append({
            'order_id':    oid,
            'order':       order,
            'customer':    order.customer_name or '-',
            'contract':    contract_by_sale.get(order.sale_number, '') or (order.sale_number or '-'),
            'date':        rec['fit_date'],
            'week':        _week_of(rec['fit_date']),
            'fitter':      ', '.join(rec['fitters']) or '-',
            'value':       value,
            'paid':        paid,
            'outstanding': owed,
            'remedial':    oid in remedial_order_ids,
            'pay_key':     pay_key,
            'pay_label':   pay_label,
        })
    jobs.sort(key=lambda j: j['date'])

    def _week_summary(week_key):
        total = collected = outstanding = 0.0
        count = 0
        for j in jobs:
            if j['week'] != week_key:
                continue
            count += 1
            total += j['value']
            collected += j['paid']
            outstanding += j['outstanding']
        return {'total': total, 'count': count, 'collected': collected, 'outstanding': outstanding}

    last_week_sales = _week_summary('last')
    this_week_sales = _week_summary('this')
    next_week_sales = _week_summary('next')

    # Per-day breakdown (this week + last week), bucketed from the same jobs.
    day_labels = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
    day_sales = {
        i: {'label': day_labels[i],
            'date': (this_week_start + timedelta(days=i)).strftime('%d %b'),
            'total': 0.0, 'count': 0, 'jobs': []}
        for i in range(7)
    }
    last_day_sales = {
        i: {'label': day_labels[i],
            'date': (last_week_start + timedelta(days=i)).strftime('%d %b'),
            'total': 0.0, 'count': 0}
        for i in range(7)
    }
    for j in jobs:
        idx = j['date'].weekday()
        if j['week'] == 'this':
            day_sales[idx]['total'] += j['value']
            day_sales[idx]['count'] += 1
            day_sales[idx]['jobs'].append({
                'customer': j['customer'], 'contract': j['contract'], 'value': j['value'],
            })
        elif j['week'] == 'last':
            last_day_sales[idx]['total'] += j['value']
            last_day_sales[idx]['count'] += 1

    # ── Stock ─────────────────────────────────────────────────────────────────
    stock_items_qs = StockItem.objects.filter(tracking_type__in=['stock', 'non-stock'], quantity__gt=0)
    total_stock_value = sum(item.cost * item.quantity for item in stock_items_qs) or Decimal('0')
    seven_days_ago = today - timedelta(days=7)
    recent_hist = (StockHistory.objects
                   .filter(created_at__date__gte=seven_days_ago,
                           stock_item__tracking_type__in=['stock', 'non-stock'])
                   .exclude(change_type__in=['sale', 'purchase'])
                   .select_related('stock_item'))
    stock_7d_delta = sum(h.change_amount * h.stock_item.cost for h in recent_hist) or Decimal('0')
    shortage_count = (StockItem.objects
                      .filter(tracking_type__in=['stock', 'non-stock'], par_level__gt=0)
                      .filter(quantity__lt=F('par_level'))
                      .count())

    # ── Outstanding payments ──────────────────────────────────────────────────
    # Bulletproof: read the stored balance_payable — the SAME figure the sale
    # page and calendar persist (it accounts for cross-sale credits). Never
    # re-derive sale_value − payments here, which ignored credits and was wrong.
    outstanding_qs = (AnthillSale.objects
                      .filter(sale_value__gt=0)
                      .exclude(status='cancelled'))
    if contract_prefix:
        outstanding_qs = outstanding_qs.filter(contract_number__istartswith=contract_prefix)
    total_outstanding = Decimal('0')
    outstanding_count = 0
    top_debtors_all = []
    for row in outstanding_qs.values('order_id', 'customer_name', 'contract_number', 'fit_date',
                                     'sale_value', 'discount', 'balance_payable', 'paid_in_full'):
        if row['paid_in_full']:
            owed = Decimal('0')
        elif row['balance_payable'] is not None:
            owed = max(row['balance_payable'], Decimal('0'))
        else:
            # No stored balance yet — treat the effective (post-discount) value
            # as fully outstanding rather than guessing from payment sums.
            owed = max((row['sale_value'] or Decimal('0')) - (row['discount'] or Decimal('0')), Decimal('0'))
        if owed >= Decimal('10'):
            total_outstanding += owed
            outstanding_count += 1
            top_debtors_all.append({
                'order_id':    row['order_id'],
                'customer':    row['customer_name'] or '-',
                'contract':    row['contract_number'] or '-',
                'fit_date':    row['fit_date'].strftime('%d %b %Y') if row['fit_date'] else 'TBC',
                'sale_value':  float((row['sale_value'] or Decimal('0')) - (row['discount'] or Decimal('0'))),
                'outstanding': float(owed),
            })
    top_debtors = sorted(top_debtors_all, key=lambda x: x['outstanding'], reverse=True)[:10]
    # Attach fitter names for just the top-10 debtor jobs
    _debtor_fitters = _fitters_for_orders([d['order_id'] for d in top_debtors if d['order_id']])
    for d in top_debtors:
        d['fitter'] = _debtor_fitters.get(d['order_id'], '-') or '-'

    # Collections due this week (jobs fitting this week with a remaining balance),
    # sourced from the same calendar-driven job list as everything else.
    this_week_outstanding = Decimal('0')
    this_week_collections = []
    for j in jobs:
        if j['week'] != 'this' or j['outstanding'] < 1:
            continue
        this_week_outstanding += Decimal(str(j['outstanding']))
        this_week_collections.append({
            'customer':    j['customer'],
            'contract':    j['contract'],
            'fit_date':    j['date'].strftime('%a %d %b'),
            'sale_value':  j['value'],
            'outstanding': j['outstanding'],
        })

    # ── Purchase orders ───────────────────────────────────────────────────────
    pending_qs = PurchaseOrder.objects.filter(status__in=['Approved', 'Ordered', 'Sent'])
    pending_po_total = pending_qs.aggregate(total=Sum('total'))['total'] or Decimal('0')
    pending_po_count = pending_qs.count()
    four_weeks_ago = today - timedelta(weeks=4)
    recent_po_spend = (PurchaseOrder.objects
                       .filter(issue_date__gte=four_weeks_ago)
                       .aggregate(total=Sum('total'))['total'] or Decimal('0'))
    po_status_breakdown = [
        {'status': r['status'], 'count': r['count'], 'total': float(r['total'] or 0)}
        for r in (PurchaseOrder.objects
                  .filter(status__in=['Approved', 'Ordered', 'Sent'])
                  .values('status')
                  .annotate(count=Count('id'), total=Sum('total'))
                  .order_by('status'))
    ]
    # Received POs: only those received in the last 7 days (received_date is a
    # free-text string in mixed formats, so parse it in Python).
    def _parse_po_date(raw):
        if not raw:
            return None
        s = str(raw)[:19]
        for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%Y-%m-%dT%H:%M:%S'):
            try:
                return datetime.strptime(s, fmt).date()
            except ValueError:
                continue
        return None

    recv_week_start = today - timedelta(days=7)
    recv_count = 0
    recv_total = Decimal('0')
    for po in PurchaseOrder.objects.filter(status='Received').values('received_date', 'total'):
        rd = _parse_po_date(po['received_date'])
        if rd and recv_week_start <= rd <= today:
            recv_count += 1
            recv_total += (po['total'] or Decimal('0'))
    po_status_breakdown.append({'status': 'Received', 'count': recv_count, 'total': float(recv_total)})

    # ── Materials readiness (this + next week fits, from the calendar) ────────
    # Driven by the same calendar jobs as the rest of the report; we just join in
    # each order's boards / OS-doors PO status. Finished jobs need no chasing.
    _mat_order_ids = [j['order_id'] for j in jobs if j['week'] in ('this', 'next')]
    _mat_orders = {
        o.id: o for o in (Order.objects
                          .filter(id__in=_mat_order_ids)
                          .select_related('boards_po'))
    }
    materials_rows = []
    for j in jobs:
        if j['week'] not in ('this', 'next'):
            continue
        order = _mat_orders.get(j['order_id'])
        if not order or order.job_finished:
            continue
        # Boards
        boards_status = 'not_required' if order.boards_not_required else 'no_po'
        if not order.boards_not_required:
            bpo = order.boards_po
            if bpo:
                linked_po = PurchaseOrder.objects.filter(display_number=bpo.po_number).first()
                if linked_po:
                    if linked_po.status in ('Received', 'Invoiced', 'Closed'):
                        boards_status = 'received'
                    elif linked_po.status in ('Approved', 'Ordered', 'Sent'):
                        boards_status = 'ordered'
                    else:
                        boards_status = 'pending'
                else:
                    boards_status = 'no_po'
        # OS Doors
        os_doors_status = 'not_required'
        if order.os_doors_po:
            os_po = PurchaseOrder.objects.filter(display_number=order.os_doors_po).first()
            if os_po:
                if os_po.status in ('Received', 'Invoiced', 'Closed'):
                    os_doors_status = 'received'
                elif os_po.status in ('Approved', 'Ordered', 'Sent'):
                    os_doors_status = 'ordered'
                else:
                    os_doors_status = 'pending'
            else:
                os_doors_status = 'no_po'
        ready = order.all_items_ordered or (
            boards_status in ('not_required', 'received')
            and os_doors_status in ('not_required', 'received')
        )
        materials_rows.append({
            'order_id':        order.id,
            'sale_number':     order.sale_number or '-',
            'customer':        j['customer'],
            'fit_date':        j['date'].strftime('%a %d %b'),
            'fit_week':        j['week'],
            'boards_status':   boards_status,
            'os_doors_status': os_doors_status,
            'all_items_ordered': order.all_items_ordered,
            'ready':           ready,
            'value':           j['value'],
            'fitter':          j['fitter'],
            'money_due':       j['outstanding'] if j['outstanding'] > 0 else None,
        })
    materials_not_ready = sum(1 for r in materials_rows if not r['ready'])

    # ── Remedials ─────────────────────────────────────────────────────────────
    open_remedials      = Remedial.objects.filter(is_completed=False).count()
    scheduled_remedials = Remedial.objects.filter(
        is_completed=False,
        scheduled_date__gte=this_week_start,
        scheduled_date__lte=next_week_end,
    ).count()

    # Detailed list of open remedials (with status)
    remedials_list = []
    open_remedials_qs = (Remedial.objects
                         .filter(is_completed=False)
                         .order_by('scheduled_date', '-created_date'))
    for rem in open_remedials_qs:
        if rem.scheduled_date and rem.scheduled_date < today:
            status_key, status_label = 'overdue', 'Overdue'
        elif not rem.all_items_ordered:
            status_key, status_label = 'awaiting', 'Awaiting Materials'
        elif rem.scheduled_date:
            status_key, status_label = 'scheduled', 'Scheduled'
        else:
            status_key, status_label = 'unscheduled', 'Unscheduled'
        remedials_list.append({
            'number':       rem.remedial_number,
            'customer':     f'{rem.first_name} {rem.last_name}'.strip() or '-',
            'reason':       (rem.reason or '').strip()[:80] or '-',
            'scheduled':    rem.scheduled_date.strftime('%a %d %b') if rem.scheduled_date else 'Not scheduled',
            'days_open':    rem.days_since_created,
            'items_ordered': rem.all_items_ordered,
            'status_key':   status_key,
            'status_label': status_label,
        })

    # ── Payment status (the calendar's last/this/next-week fits) ─────────────
    # Built from the same job list as everything else, so the figures here match
    # the chapter stat chips, the day charts and the calendar exactly.
    payment_status_rows = []
    paid_in_full = 0
    part_paid = 0
    unpaid = 0
    for j in jobs:
        if j['pay_key'] == 'paid':
            paid_in_full += 1
        elif j['pay_key'] == 'part':
            part_paid += 1
        else:
            unpaid += 1
        payment_status_rows.append({
            'customer':    j['customer'],
            'contract':    j['contract'],
            'fit_date':    j['date'].strftime('%a %d %b'),
            'week':        j['week'],
            'sale_value':  j['value'],
            'paid':        j['paid'],
            'outstanding': j['outstanding'],
            'pay_key':     j['pay_key'],
            'pay_label':   j['pay_label'],
            'fitter':      j['fitter'],
            'remedial':    j['remedial'],
        })

    # ── Auto-generated insights ────────────────────────────────────────────────
    insights = []
    lw = last_week_sales['total']
    tw = this_week_sales['total']
    delta_pct = ((tw - lw) / lw * 100) if lw else 0
    if delta_pct >= 15:
        insights.append({'type': 'positive', 'icon': 'bi-graph-up-arrow',
                         'text': f'Revenue up {delta_pct:.0f}% vs last week — strong performance.'})
    elif delta_pct <= -15:
        insights.append({'type': 'danger', 'icon': 'bi-graph-down-arrow',
                         'text': f'Revenue down {abs(delta_pct):.0f}% vs last week. Review fitting schedule.'})
    if materials_not_ready > 0:
        insights.append({'type': 'danger', 'icon': 'bi-exclamation-triangle',
                         'text': f'{materials_not_ready} job(s) fitting this/next week have materials not confirmed — chase suppliers immediately.'})
    if shortage_count > 0:
        insights.append({'type': 'warning', 'icon': 'bi-box-seam',
                         'text': f'{shortage_count} stock item(s) below par level. Raise purchase orders to replenish.'})
    if len(this_week_collections) > 0:
        insights.append({'type': 'info', 'icon': 'bi-cash-coin',
                         'text': f'£{float(this_week_outstanding):,.0f} balance outstanding across {len(this_week_collections)} job(s) fitting this week — ensure collection on installation day.'})
    if next_week_sales['count'] == 0:
        insights.append({'type': 'warning', 'icon': 'bi-calendar-x',
                         'text': 'No fits currently scheduled for next week. Pipeline may need attention.'})
    elif next_week_sales['count'] <= 2:
        insights.append({'type': 'info', 'icon': 'bi-calendar-week',
                         'text': f'Only {next_week_sales["count"]} fit(s) scheduled next week — capacity available for new bookings.'})
    if float(total_outstanding) > 50000:
        insights.append({'type': 'danger', 'icon': 'bi-wallet2',
                         'text': f'Total outstanding balance is £{float(total_outstanding):,.0f}. Consider a proactive collections review.'})
    if pending_po_count > 20:
        insights.append({'type': 'warning', 'icon': 'bi-receipt',
                         'text': f'{pending_po_count} purchase orders currently open/pending. Review for any overdue deliveries.'})
    if open_remedials > 5:
        insights.append({'type': 'warning', 'icon': 'bi-tools',
                         'text': f'{open_remedials} open remedials outstanding. Schedule outstanding work to protect customer satisfaction.'})
    if not insights:
        insights.append({'type': 'positive', 'icon': 'bi-check-circle',
                         'text': 'All systems healthy — no urgent actions required.'})

    return JsonResponse({
        'success': True,
        'generated_at': today.strftime('%A %d %B %Y'),
        'payments_linked': payments_linked,
        'last_week':    {'start': last_week_start.strftime('%d %b'), 'end': last_week_end.strftime('%d %b %Y')},
        'this_week':    {'start': this_week_start.strftime('%d %b'), 'end': this_week_end.strftime('%d %b %Y')},
        'next_week':    {'start': next_week_start.strftime('%d %b'), 'end': next_week_end.strftime('%d %b %Y')},
        'last_week_sales':  last_week_sales,
        'this_week_sales':  this_week_sales,
        'next_week_sales':  next_week_sales,
        'day_sales':        [day_sales[i] for i in range(7)],
        'last_day_sales':   [last_day_sales[i] for i in range(7)],
        'stock_value':      float(total_stock_value),
        'stock_delta':      float(stock_7d_delta),
        'shortage_count':   shortage_count,
        'total_outstanding':       float(total_outstanding),
        'outstanding_count':       outstanding_count,
        'this_week_outstanding':   float(this_week_outstanding),
        'this_week_collections':   this_week_collections,
        'pending_po_total':     float(pending_po_total),
        'pending_po_count':     pending_po_count,
        'recent_po_spend':      float(recent_po_spend),
        'po_status_breakdown':  po_status_breakdown,
        'materials_rows':       materials_rows,
        'materials_not_ready':  materials_not_ready,
        'open_remedials':       open_remedials,
        'scheduled_remedials':  scheduled_remedials,
        'remedials_list':       remedials_list,
        'payment_status_rows':  payment_status_rows,
        'payment_summary':      {'paid': paid_in_full, 'part': part_paid, 'unpaid': unpaid},
        'top_debtors':          top_debtors,
        'insights':             insights,
    })


@login_required
def dashboard_save_layout(request):
    """Save the user's dashboard widget layout."""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Method not allowed'}, status=405)
    import json as _json
    try:
        layout = _json.loads(request.body)
    except (ValueError, TypeError):
        return JsonResponse({'success': False, 'error': 'Invalid JSON'}, status=400)
    profile = getattr(request.user, 'profile', None)
    if not profile:
        from .models import UserProfile
        profile = UserProfile.objects.create(user=request.user)
    profile.dashboard_layout = layout
    profile.save(update_fields=['dashboard_layout'])
    return JsonResponse({'success': True})


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
                'sale_value', 'discount', 'activity_date', 'location', 'total_paid', 'fit_date',
                'customer_id', 'customer__name')
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

    # ── Group by customer ──
    def _build_sale_dict(row):
        sv = row['sale_value'] or Decimal('0')
        disc = row['discount'] or Decimal('0')
        total_paid = row['total_paid'] or Decimal('0')
        real_paid = total_paid + disc
        effective_sv = sv - disc
        real_outstanding = effective_sv - real_paid
        fd = row['fit_date']
        return {
            'pk': row['pk'],
            'sale_number': row['anthill_activity_id'],
            'contract': row['contract_number'] or '',
            'location': row['location'] or '',
            'sale_value': float(sv),
            'paid': float(real_paid),
            'outstanding': float(real_outstanding),
            'fit_date': fd.strftime('%d/%m/%Y') if fd else '',
            'fit_date_iso': fd.isoformat() if fd else '',
            'year': fd.year if fd else None,
            'payments': payments_by_sale.get(row['pk'], []),
        }

    customers = {}  # customer_key -> customer data
    for row in sale_rows:
        cust_id = row['customer_id']
        cust_name = row['customer__name'] or row['customer_name'] or '-'
        key = cust_id or cust_name  # group by customer PK if available, else name
        if key not in customers:
            customers[key] = {
                'customer_id': cust_id,
                'customer': cust_name,
                'total_sale_value': 0,
                'total_paid': 0,
                'total_outstanding': 0,
                'sales': [],
            }
        sale_dict = _build_sale_dict(row)
        customers[key]['sales'].append(sale_dict)
        customers[key]['total_sale_value'] += sale_dict['sale_value']
        customers[key]['total_paid'] += sale_dict['paid']
        customers[key]['total_outstanding'] += sale_dict['outstanding']

    results = list(customers.values())
    results.sort(key=lambda x: -x['total_outstanding'])

    overpaid_customers = {}
    for row in overpaid_rows:
        cust_id = row['customer_id']
        cust_name = row['customer__name'] or row['customer_name'] or '-'
        key = cust_id or cust_name
        sv = row['sale_value'] or Decimal('0')
        disc = row['discount'] or Decimal('0')
        total_paid = row['total_paid'] or Decimal('0')
        real_paid = total_paid + disc
        effective_sv = sv - disc
        overpay_amount = real_paid - effective_sv
        fd = row['fit_date']
        if key not in overpaid_customers:
            overpaid_customers[key] = {
                'customer_id': cust_id,
                'customer': cust_name,
                'total_sale_value': 0,
                'total_paid': 0,
                'total_overpaid': 0,
                'sales': [],
            }
        sale_dict = {
            'pk': row['pk'],
            'sale_number': row['anthill_activity_id'],
            'contract': row['contract_number'] or '',
            'sale_value': float(sv),
            'paid': float(real_paid),
            'overpaid': float(overpay_amount),
            'fit_date': fd.strftime('%d/%m/%Y') if fd else '',
            'payments': payments_by_sale.get(row['pk'], []),
        }
        overpaid_customers[key]['sales'].append(sale_dict)
        overpaid_customers[key]['total_sale_value'] += sale_dict['sale_value']
        overpaid_customers[key]['total_paid'] += sale_dict['paid']
        overpaid_customers[key]['total_overpaid'] += sale_dict['overpaid']

    overpaid_results = list(overpaid_customers.values())
    overpaid_results.sort(key=lambda x: -x['total_overpaid'])

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
    """Download the outstanding balance report as a PDF (customer-grouped)."""
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
        .exclude(status='cancelled')
        .annotate(total_paid=Coalesce(payments_subquery, Value(Decimal('0')), output_field=DecimalField(max_digits=14, decimal_places=2)))
        .order_by('-activity_date')
        .values('pk', 'anthill_activity_id', 'customer_name', 'contract_number',
                'sale_value', 'discount', 'activity_date', 'total_paid',
                'customer_id', 'customer__name')
    )
    if contract_prefix:
        qs = qs.filter(contract_number__istartswith=contract_prefix)

    # Build customer-grouped structure
    customers = {}
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
        cust_id = row['customer_id']
        cust_name = row['customer__name'] or row['customer_name'] or '-'
        key = cust_id or cust_name
        if key not in customers:
            customers[key] = {
                'customer': cust_name,
                'total_sale_value': 0.0,
                'total_paid': 0.0,
                'total_outstanding': 0.0,
                'sales': [],
            }
        sale_dict = {
            'sale_number': row['anthill_activity_id'],
            'contract': row['contract_number'] or '',
            'sale_value': float(sv),
            'paid': float(real_paid),
            'outstanding': float(real_outstanding),
            'date': dt.strftime('%d/%m/%Y') if dt else '',
        }
        customers[key]['sales'].append(sale_dict)
        customers[key]['total_sale_value'] += sale_dict['sale_value']
        customers[key]['total_paid'] += sale_dict['paid']
        customers[key]['total_outstanding'] += sale_dict['outstanding']

    customer_rows = list(customers.values())
    customer_rows.sort(key=lambda x: -x['total_outstanding'])

    from datetime import date
    buffer = generate_outstanding_report_pdf(customer_rows, location_label=location_label)
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


# ============================================================================
# Standalone report pages
# These render full pages that reuse the JSON report endpoints above. They are
# linked from the Reports index and open in a new tab.
# ============================================================================

@login_required
def report_outstanding_page(request):
    return render(request, 'stock_take/reports/outstanding_report_page.html')


@login_required
def report_sales_after_page(request):
    return render(request, 'stock_take/reports/sales_after_report_page.html', {
        'today_str': datetime.now().date().strftime('%Y-%m-%d'),
    })


@login_required
def report_stock_page(request):
    return render(request, 'stock_take/reports/stock_report_page.html', {
        'today_str': datetime.now().date().strftime('%Y-%m-%d'),
    })


@login_required
def report_week_page(request):
    return render(request, 'stock_take/reports/week_report_page.html')


@login_required
def report_monthly_page(request):
    import calendar
    now = datetime.now()
    months = [{'num': i, 'name': calendar.month_name[i]} for i in range(1, 13)]
    years = list(range(now.year, now.year - 6, -1))
    return render(request, 'stock_take/reports/monthly_report_page.html', {
        'months': months,
        'years': years,
        'current_month': now.month,
        'current_year': now.year,
    })


@login_required
def report_avg_page(request):
    return render(request, 'stock_take/reports/avg_report_page.html')
