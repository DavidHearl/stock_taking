"""Accounting → Payments list view.

Aggregates every AnthillPayment record (Xero-linked, manual and scraped) across
all sales and presents them in a single sortable list, filtered by the user's
currently selected location/showroom.
"""

from decimal import Decimal

from django.core.paginator import Paginator
from django.db.models import (
    Q, Sum, Count, Subquery, OuterRef, DecimalField, Value,
)
from django.db.models.functions import Coalesce
from django.shortcuts import render

from .models import AnthillPayment, AnthillSale
from .permissions import page_permission_required
from .services.location_filter import profile_locations, location_q

_PAY_PER_PAGE = 100

# Reconciliation thresholds (mirror the dashboard outstanding report): a sale is
# flagged as overpaid when paid exceeds the effective value by more than £5, and
# underpaid when still owing £10 or more. Anything in between counts as settled.
_RECONCILE_OVERPAID = Decimal('-5')
_RECONCILE_UNDERPAID = Decimal('10')


@page_permission_required('payments')
def payments_list(request):
    """List all payments, filtered by the current location.

    Two views share this page via the ``?view=`` query param:
    ``payments`` (default) is the flat payment ledger; ``reconcile`` lists the
    sales whose payments don't add up (under- or over-paid).
    """
    view = request.GET.get('view', 'payments')
    search_query = request.GET.get('q', '').strip()
    source_filter = request.GET.get('source', 'all')
    try:
        page_num = max(1, int(request.GET.get('page', 1)))
    except (ValueError, TypeError):
        page_num = 1

    # Location filter — match the user's selected showroom(s) (same behaviour as
    # the Invoices list). Falls back to showing all when no location is set.
    profile = getattr(request.user, 'profile', None)
    selected_locations = profile_locations(profile)
    location_filter = ', '.join(selected_locations)

    if view == 'reconcile':
        return _render_reconcile(
            request, search_query, selected_locations, location_filter,
        )

    qs = (
        AnthillPayment.objects
        .select_related('sale', 'sale__customer')
    )

    loc_filter = location_q(
        selected_locations, 'sale__location', 'location', lookup='icontains'
    )
    if loc_filter:
        qs = qs.filter(loc_filter)

    if source_filter == 'xero':
        qs = qs.filter(source='xero')
    elif source_filter == 'manual':
        qs = qs.exclude(source='xero')

    if search_query:
        qs = qs.filter(
            Q(sale__customer_name__icontains=search_query)
            | Q(sale__contract_number__icontains=search_query)
            | Q(payment_type__icontains=search_query)
            | Q(xero_invoice_number__icontains=search_query)
            | Q(user_name__icontains=search_query)
            | Q(status__icontains=search_query)
        )

    qs = qs.order_by('-date', '-id')

    stats = qs.aggregate(
        total_payments=Count('id'),
        total_amount=Sum('amount'),
    )

    paginator = Paginator(qs, _PAY_PER_PAGE)
    page_obj = paginator.get_page(page_num)

    payments = list(page_obj.object_list)
    for p in payments:
        # Showroom: prefer the sale's location; fall back to the payment's own
        # location (ignoring internal "manual-link" placeholders).
        loc = (p.location or '').strip()
        if loc.lower() == 'manual-link':
            loc = ''
        p.showroom_display = (p.sale.location if p.sale else '') or loc or ''
        p.is_split = p.full_amount is not None and p.amount is not None and p.full_amount != p.amount

    context = {
        'view': 'payments',
        'payments': payments,
        'page_obj': page_obj,
        'paginator': paginator,
        'search_query': search_query,
        'source_filter': source_filter,
        'location_filter': location_filter,
        'total_payments': stats['total_payments'] or 0,
        'total_amount': stats['total_amount'] or Decimal('0'),
    }
    return render(request, 'stock_take/payments.html', context)


def _render_reconcile(request, search_query, selected_locations, location_filter):
    """Render the Reconcile tab, anchored on the CUSTOMER (not the sale).

    Payments are pooled across a customer's sales, so a joint payment recorded
    against the "wrong" sale makes individual sales look under/over-paid even
    when the customer as a whole balances. We therefore aggregate every one of a
    customer's sales and classify on the *net* position::

        net = Σ(sale_value − discount) − Σ(paid + discount)

    Customers still owing money are ``underpaid``; customers paid beyond their
    total are ``overpaid``; customers settled within the thresholds drop out.
    The expanded row breaks the net down per sale (with each sale's own balance)
    so payments can be moved to the sale they belong to.
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
        .filter(sale_value__gt=0)
        .exclude(status='cancelled')
        .annotate(total_paid=Coalesce(
            payments_subquery, Value(Decimal('0')),
            output_field=DecimalField(max_digits=14, decimal_places=2),
        ))
    )

    loc_filter = location_q(selected_locations, 'location', lookup='icontains')
    if loc_filter:
        qs = qs.filter(loc_filter)

    qs = qs.values(
        'pk', 'anthill_activity_id', 'customer_id', 'customer_name',
        'customer__name', 'contract_number', 'location', 'sale_value',
        'discount', 'total_paid', 'fit_date', 'activity_date',
        'order__workflow_progress__current_stage__name',
        'order__workflow_progress__current_stage__phase',
        'order__workflow_progress__current_stage__order',
    )

    # ── Pool every sale under its customer ──
    customers = {}
    for r in qs:
        cust_id = r['customer_id']
        cust_name = r['customer__name'] or r['customer_name'] or '-'
        key = cust_id if cust_id is not None else 'name:' + cust_name
        c = customers.get(key)
        if c is None:
            c = customers[key] = {
                'customer_id': cust_id,
                'customer_name': cust_name,
                'sales': [],
                'total_value': Decimal('0'),
                'total_paid': Decimal('0'),
            }
        sv = r['sale_value'] or Decimal('0')
        disc = r['discount'] or Decimal('0')
        paid = r['total_paid'] or Decimal('0')
        effective_sv = sv - disc
        real_paid = paid + disc
        activity_date = r['activity_date']
        c['sales'].append({
            'pk': r['pk'],
            'anthill_activity_id': r['anthill_activity_id'] or '',
            'contract_number': r['contract_number'] or '',
            'showroom': r['location'] or '',
            'sale_value': effective_sv,
            'paid': real_paid,
            'balance': effective_sv - real_paid,
            'abs_balance': abs(effective_sv - real_paid),
            'fit_date': r['fit_date'],
            'activity_date': activity_date,
            'stage_name': r['order__workflow_progress__current_stage__name'],
            'stage_phase': r['order__workflow_progress__current_stage__phase'] or '',
            'stage_order': r['order__workflow_progress__current_stage__order'],
            'payments': [],
            '_sort': activity_date.timestamp() if activity_date else 0.0,
        })
        c['total_value'] += effective_sv
        c['total_paid'] += real_paid

    # Optional text search — keep a customer if the query matches its name or any
    # of its sales' contract numbers (filtered after pooling so the net stays
    # correct across every sale).
    if search_query:
        q = search_query.lower()
        customers = {
            k: c for k, c in customers.items()
            if q in (c['customer_name'] or '').lower()
            or any(q in (s['contract_number'] or '').lower() for s in c['sales'])
        }

    # ── Classify each customer on its NET position ──
    rows = []
    for i, c in enumerate(customers.values()):
        net = c['total_value'] - c['total_paid']
        if net < _RECONCILE_OVERPAID:
            status = 'overpaid'
            difference = -net
        elif net >= _RECONCILE_UNDERPAID:
            status = 'underpaid'
            difference = net
        else:
            continue
        c['sales'].sort(key=lambda s: s['_sort'], reverse=True)
        newest = c['sales'][0]
        cust_id = c['customer_id']
        rows.append({
            **c,
            'row_id': 'c{}'.format(cust_id) if cust_id is not None else 'x{}'.format(i),
            'difference': difference,
            'status': status,
            'sale_count': len(c['sales']),
            'showroom': newest['showroom'],
            'activity_date': newest['activity_date'],
            # Represent the customer by their newest sale's workflow stage.
            'stage_name': newest['stage_name'],
            'stage_phase': newest['stage_phase'],
            'stage_order': newest['stage_order'],
            '_sort': newest['_sort'],
        })

    # Preload payments for every sale of the flagged customers so the reconcile
    # tools can show the breakdown without an extra request.
    sale_index = {}
    for row in rows:
        for s in row['sales']:
            sale_index[s['pk']] = s
    if sale_index:
        payments = (
            AnthillPayment.objects
            .filter(sale_id__in=sale_index.keys())
            .order_by('date', 'id')
            .values('id', 'sale_id', 'amount', 'date', 'payment_type',
                    'source', 'ignored')
        )
        for p in payments:
            sale_index[p['sale_id']]['payments'].append({
                'id': p['id'],
                'amount': p['amount'] or Decimal('0'),
                'date': p['date'],
                'type': p['payment_type'] or 'Payment',
                'source': p['source'] or 'manual',
                'ignored': p['ignored'],
            })

    # ── Group customers by their newest sale's workflow stage ──
    _NO_STAGE = 'No workflow stage'
    groups = {}
    for row in rows:
        name = row['stage_name'] or _NO_STAGE
        g = groups.get(name)
        if g is None:
            g = groups[name] = {
                'stage_name': name,
                'stage_phase': row['stage_phase'],
                # Real stages sort by their defined order; unstaged customers last.
                'stage_order': row['stage_order'] if row['stage_order'] is not None else 10_000,
                'rows': [],
                'count': 0,
            }
        g['rows'].append(row)
        g['count'] += 1

    for g in groups.values():
        g['rows'].sort(key=lambda x: x['_sort'], reverse=True)

    reconcile_groups = sorted(
        groups.values(), key=lambda g: (g['stage_order'], g['stage_name'])
    )

    context = {
        'view': 'reconcile',
        'reconcile_groups': reconcile_groups,
        'reconcile_count': len(rows),
        'search_query': search_query,
        'location_filter': location_filter,
    }
    return render(request, 'stock_take/payments.html', context)
