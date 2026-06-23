"""Accounting → Payments list view.

Aggregates every AnthillPayment record (Xero-linked, manual and scraped) across
all sales and presents them in a single sortable list, filtered by the user's
currently selected location/showroom.
"""

from decimal import Decimal

from django.core.paginator import Paginator
from django.db.models import Q, Sum, Count
from django.shortcuts import render

from .models import AnthillPayment
from .permissions import page_permission_required

_PAY_PER_PAGE = 100


@page_permission_required('payments')
def payments_list(request):
    """List all payments, filtered by the current location."""
    search_query = request.GET.get('q', '').strip()
    source_filter = request.GET.get('source', 'all')
    try:
        page_num = max(1, int(request.GET.get('page', 1)))
    except (ValueError, TypeError):
        page_num = 1

    qs = (
        AnthillPayment.objects
        .select_related('sale', 'sale__customer')
    )

    # Location filter — match the user's selected showroom (same behaviour as
    # the Invoices list). Falls back to showing all when no location is set.
    location_filter = ''
    profile = getattr(request.user, 'profile', None)
    if profile:
        location_filter = (profile.selected_location or '').strip()
    if location_filter:
        qs = qs.filter(
            Q(sale__location__icontains=location_filter)
            | Q(location__icontains=location_filter)
        )

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
