from django.contrib.auth.decorators import login_required
from django.shortcuts import render, get_object_or_404, redirect
from django.http import JsonResponse, StreamingHttpResponse, HttpResponse
from django.db.models import Count, Max, Sum, Q, Exists, OuterRef, F
from django.db import ProgrammingError, DatabaseError, transaction
from django.contrib import messages
from .models import Customer, Order, PurchaseOrder, AnthillSale, AnthillPayment, Invoice, SyncLog, Lead, SaleCoverSheet, SaleCoverSheetHistory, ClaimDocument, Designer
import logging
import json
import time
import os
import re
from datetime import date
from decimal import Decimal, InvalidOperation

logger = logging.getLogger(__name__)


@login_required
def customers_list(request):
    """Display list of all customers with date-bracket filters and cascading search.
    Location is taken from the user's profile (site-wide selector in the top navbar).
    Also handles the 'contacts' view mode (Leads) via ?view=contacts.
    """
    from django.utils import timezone
    from django.core.paginator import Paginator
    from datetime import timedelta

    view_mode = request.GET.get('view', 'customers')  # 'customers', 'contacts', or 'events'

    # ── Events (AnthillSale) view ─────────────────────────────────────────
    if view_mode == 'events':
        from django.utils import timezone
        from django.core.paginator import Paginator
        from datetime import timedelta

        search_query = request.GET.get('q', '').strip()
        category_filter = request.GET.get('cat', '').strip()

        profile = getattr(request.user, 'profile', None)
        location_filter = profile.selected_location if profile else ''

        events_base = AnthillSale.objects.select_related('customer', 'order').order_by('-activity_date')

        if location_filter and not search_query:
            events_base = events_base.filter(location__iexact=location_filter)

        if category_filter:
            events_base = events_base.filter(category=category_filter)

        if search_query:
            terms = search_query.split()
            per_term_qs = []
            for term in terms:
                per_term_qs.append(
                    Q(customer_name__icontains=term) |
                    Q(anthill_activity_id__icontains=term) |
                    Q(activity_type__icontains=term) |
                    Q(status__icontains=term) |
                    Q(assigned_to_name__icontains=term) |
                    Q(contract_number__icontains=term) |
                    Q(source__icontains=term) |
                    Q(customer__name__icontains=term) |
                    Q(customer__first_name__icontains=term) |
                    Q(customer__last_name__icontains=term)
                )
            search_q = per_term_qs[0]
            for extra in per_term_qs[1:]:
                search_q &= extra
            events_base = events_base.filter(search_q)

        page_number = request.GET.get('page', 1)
        paginator = Paginator(events_base, 100)
        page_obj = paginator.get_page(page_number)

        context = {
            'view_mode': 'events',
            'events': page_obj,
            'page_obj': page_obj,
            'filtered_count': paginator.count,
            'search_query': search_query,
            'category_filter': category_filter,
            'location_filter': location_filter,
            'source_choices': Lead.SOURCE_CHOICES,
            'status_choices': Lead.STATUS_CHOICES,
        }
        return render(request, 'stock_take/customers_list.html', context)

    # ── Contacts (Leads) view ────────────────────────────────────────────
    if view_mode == 'contacts':
        search_query = request.GET.get('q', '').strip()
        status_filter = request.GET.get('status', 'all')

        leads = Lead.objects.all()

        if status_filter == 'active':
            leads = leads.exclude(status__in=['converted', 'lost'])
        elif status_filter == 'converted':
            leads = leads.filter(status='converted')
        elif status_filter == 'lost':
            leads = leads.filter(status='lost')
        elif status_filter != 'all':
            leads = leads.filter(status=status_filter)

        if search_query:
            terms = search_query.split()
            per_term_qs = []
            for term in terms:
                per_term_qs.append(
                    Q(name__icontains=term) |
                    Q(email__icontains=term) |
                    Q(phone__icontains=term) |
                    Q(city__icontains=term) |
                    Q(postcode__icontains=term) |
                    Q(source__icontains=term) |
                    Q(anthill_customer_id__icontains=term)
                )
            search_q = per_term_qs[0]
            for extra in per_term_qs[1:]:
                search_q &= extra
            leads = leads.filter(search_q)

        total_count = Lead.objects.count()
        active_count = Lead.objects.exclude(status__in=['converted', 'lost']).count()
        converted_count = Lead.objects.filter(status='converted').count()
        lost_count = Lead.objects.filter(status='lost').count()
        filtered_count = leads.count()

        paginator = Paginator(leads, 100)
        page_number = request.GET.get('page', 1)
        page_obj = paginator.get_page(page_number)

        context = {
            'view_mode': 'contacts',
            'leads': page_obj,
            'page_obj': page_obj,
            'total_count': total_count,
            'active_count': active_count,
            'converted_count': converted_count,
            'lost_count': lost_count,
            'filtered_count': filtered_count,
            'search_query': search_query,
            'status_filter': status_filter,
            'status_choices': Lead.STATUS_CHOICES,
            'source_choices': Lead.SOURCE_CHOICES,
        }
        return render(request, 'stock_take/customers_list.html', context)

    # ── Customers view (default) ─────────────────────────────────────────
    search_query = request.GET.get('q', '').strip()

    # Location comes from the user's profile (site-wide setting)
    profile = getattr(request.user, 'profile', None)
    location_filter = profile.selected_location if profile else ''

    from django.db.models import Prefetch
    customers_base = Customer.objects.prefetch_related(
        Prefetch('orders', queryset=Order.objects.order_by('sale_number', 'id').distinct('sale_number'))
    ).order_by('name', 'last_name', 'first_name')

    # Apply location filter from profile (skip when searching so results
    # include customers whose record has no location set)
    if location_filter and not search_query:
        customers_base = customers_base.filter(location__iexact=location_filter)

    # Build search Q filter
    if search_query:
        terms = search_query.split()
        per_term_qs = []
        for term in terms:
            per_term_qs.append(
                Q(name__icontains=term) |
                Q(first_name__icontains=term) |
                Q(last_name__icontains=term) |
                Q(email__icontains=term) |
                Q(phone__icontains=term) |
                Q(code__icontains=term) |
                Q(city__icontains=term) |
                Q(postcode__icontains=term) |
                Q(anthill_customer_id__icontains=term)
            )
        search_q = per_term_qs[0]
        for extra in per_term_qs[1:]:
            search_q &= extra
        customers = customers_base.filter(search_q)
    else:
        customers = customers_base

    # Pagination
    page_number = request.GET.get('page', 1)
    paginator = Paginator(customers, 100)
    page_obj = paginator.get_page(page_number)

    # Last Anthill customer sync log entry
    last_anthill_sync = SyncLog.objects.filter(script_name='sync_anthill_customers').order_by('-ran_at').first()

    context = {
        'view_mode': 'customers',
        'customers': page_obj,
        'page_obj': page_obj,
        'filtered_count': paginator.count if paginator else 0,
        'search_query': search_query,
        'location_filter': location_filter,
        'last_anthill_sync': last_anthill_sync,
        'source_choices': Lead.SOURCE_CHOICES,
        'status_choices': Lead.STATUS_CHOICES,
    }

    return render(request, 'stock_take/customers_list.html', context)


@login_required
def customer_detail(request, pk):
    """Display detailed view of a single customer"""
    customer = get_object_or_404(Customer, pk=pk)

    # Get linked orders
    orders = Order.objects.filter(customer=customer).order_by('-order_date')

    # Get Anthill sales for this customer — annotated with payment totals, prefetch linked order
    from datetime import datetime, timezone as dt_timezone
    all_anthill_sales = list(
        customer.anthill_sales
        .select_related('order', 'order__designer')
        .annotate(
            payments_total=Sum('payments__amount'),
            payments_count=Count('payments'),
        )
        .order_by('activity_date')  # ascending for lead-matching; reversed later
    )

    # Separate "lead-type" activities (e.g. "Product Lead") from regular sales
    lead_type_sales = [s for s in all_anthill_sales if 'lead' in (s.activity_type or '').lower()]
    non_lead_sales  = [s for s in all_anthill_sales if 'lead' not in (s.activity_type or '').lower()]

    # Match each lead activity to the most recent preceding sale (greedy, oldest sale first)
    _min_dt = datetime(1970, 1, 1, tzinfo=dt_timezone.utc)
    used_lead_pks = set()
    sales_data = []
    for sale in non_lead_sales:  # already sorted ascending
        best_lead = None
        for lead_act in lead_type_sales:
            if lead_act.pk in used_lead_pks:
                continue
            if lead_act.activity_date and sale.activity_date and lead_act.activity_date <= sale.activity_date:
                if best_lead is None or lead_act.activity_date > best_lead.activity_date:
                    best_lead = lead_act
        if best_lead:
            used_lead_pks.add(best_lead.pk)
        sales_data.append({'sale': sale, 'lead_activity': best_lead})

    # Any unmatched lead activities appear as standalone top-level entries
    for lead_act in lead_type_sales:
        if lead_act.pk not in used_lead_pks:
            sales_data.append({'sale': lead_act, 'lead_activity': None})

    # Sort descending by date for display
    sales_data.sort(key=lambda x: x['sale'].activity_date or _min_dt, reverse=True)

    # Compute per-row outstanding and overall payment totals
    from decimal import Decimal
    total_sale_value = Decimal('0')
    total_paid = Decimal('0')
    for entry in sales_data:
        sale = entry['sale']
        sv = Decimal(str(sale.sale_value or 0))
        pt = Decimal(str(sale.payments_total or 0))
        entry['outstanding'] = sv - pt
        entry['paid_percent'] = round(float(pt) / float(sv) * 100) if sv else 0
        total_sale_value += sv
        total_paid += pt
    total_outstanding = total_sale_value - total_paid
    payment_percent = round(float(total_paid) / float(total_sale_value) * 100) if total_sale_value else 0

    # Get invoices linked to this customer
    invoices = Invoice.objects.filter(customer=customer).order_by('-date')

    # Get contacts from raw_data
    contacts = []
    if customer.raw_data and isinstance(customer.raw_data, dict):
        contacts = customer.raw_data.get('contacts', [])

    # Find associated lead — first by anthill_customer_id match, then by converted_to_customer FK
    from .models import Lead
    lead = None
    if customer.anthill_customer_id:
        lead = Lead.objects.filter(anthill_customer_id=customer.anthill_customer_id).first()
    if lead is None:
        lead = customer.source_leads.first()

    context = {
        'customer': customer,
        'orders': orders,
        'order_count': orders.count(),
        'contacts': contacts,
        'sales_data': sales_data,
        'anthill_sales_count': len(all_anthill_sales),
        'invoices': invoices,
        'invoice_count': invoices.count(),
        'lead': lead,
        'total_sale_value': total_sale_value,
        'total_paid': total_paid,
        'total_outstanding': total_outstanding,
        'payment_percent': payment_percent,
    }

    return render(request, 'stock_take/customer_detail.html', context)


@login_required
def customer_save(request, pk):
    """Save edited customer details via AJAX POST"""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    customer = get_object_or_404(Customer, pk=pk)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    # Map of editable fields
    editable_fields = [
        'name', 'code', 'email', 'phone', 'fax', 'website', 'abn',
        'address_1', 'address_2', 'city', 'suburb', 'state', 'postcode', 'country',
        'currency', 'price_tier', 'credit_terms_type', 'credit_days', 'credit_limit',
        'billing_client',
    ]

    old_activity_id = None
    update_fields = []
    for field in editable_fields:
        if field in data:
            val = data[field]
            if field == 'credit_limit':
                try:
                    val = float(val) if val else 0
                except (ValueError, TypeError):
                    val = 0
            elif field == 'email':
                val = val if val and '@' in val else None
            elif field == 'website':
                if val and not val.startswith(('http://', 'https://')):
                    val = f'https://{val}' if val and '.' in val else None
                elif not val:
                    val = None
            else:
                # For non-nullable fields (no null=True), keep empty string rather than
                # setting None, which would raise an IntegrityError on save.
                field_instance = Customer._meta.get_field(field)
                allows_null = getattr(field_instance, 'null', False)
                val = (val or None) if allows_null else (val or '')
            setattr(customer, field, val)
            update_fields.append(field)

    # Handle is_active toggle
    if 'is_active' in data:
        customer.is_active = data['is_active']
        update_fields.append('is_active')

    if update_fields:
        try:
            customer.save(update_fields=update_fields)
        except Exception as e:
            logger.error('Error saving customer %s: %s', pk, e)
            return JsonResponse({'error': str(e)}, status=400)

    return JsonResponse({'success': True, 'url_name': customer.url_name})


@login_required
def customer_delete(request, pk):
    """Delete a customer"""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    customer = get_object_or_404(Customer, pk=pk)
    customer.delete()
    return JsonResponse({'success': True})


@login_required
def customers_bulk_delete(request):
    """Bulk delete customers by list of IDs"""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    try:
        data = json.loads(request.body)
        ids = data.get('ids', [])
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    if not ids:
        return JsonResponse({'error': 'No IDs provided'}, status=400)

    deleted_count, _ = Customer.objects.filter(pk__in=ids).delete()
    return JsonResponse({'success': True, 'deleted': deleted_count})


@login_required
def customer_merge(request):
    """Merge two customers: transfer orders/data from remove_id into keep_id, then delete remove_id. Identify and display conflicts for user resolution."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    try:
        data = json.loads(request.body)
        keep_id = data.get('keep_id')
        remove_id = data.get('remove_id')
        resolve_conflicts = data.get('resolve_conflicts', {})
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    if not keep_id or not remove_id:
        return JsonResponse({'error': 'Both keep_id and remove_id are required'}, status=400)

    try:
        keep_customer = Customer.objects.get(pk=keep_id)
        remove_customer = Customer.objects.get(pk=remove_id)
    except Customer.DoesNotExist:
        return JsonResponse({'error': 'Customer not found'}, status=404)

    try:
        # Identify sales linked to both customers
        sales_keep = set(AnthillSale.objects.filter(customer=keep_customer).values_list('anthill_activity_id', flat=True))
        sales_remove = set(AnthillSale.objects.filter(customer=remove_customer).values_list('anthill_activity_id', flat=True))
        conflicting_sales = sales_keep.intersection(sales_remove)

        # If conflicts exist and not resolved, return them for user decision
        if conflicting_sales and not resolve_conflicts:
            conflicts = list(AnthillSale.objects.filter(anthill_activity_id__in=conflicting_sales))
            return JsonResponse({
                'success': False,
                'conflicts': [
                    {
                        'id': sale.pk,
                        'anthill_activity_id': sale.anthill_activity_id,
                        'activity_type': sale.activity_type,
                        'status': sale.status,
                        'customer_name': sale.customer_name,
                        'keep_customer_id': keep_customer.pk,
                        'remove_customer_id': remove_customer.pk,
                    }
                    for sale in conflicts
                ],
                'message': 'Sales conflict detected. Please resolve before merging.'
            })

        # Transfer sales from remove to keep (if not conflicting or resolved)
        for sale_id in sales_remove:
            if sale_id not in conflicting_sales or (resolve_conflicts and resolve_conflicts.get(str(sale_id)) == 'keep'):
                AnthillSale.objects.filter(anthill_activity_id=sale_id, customer=remove_customer).update(customer=keep_customer)
            elif resolve_conflicts and resolve_conflicts.get(str(sale_id)) == 'remove':
                AnthillSale.objects.filter(anthill_activity_id=sale_id, customer=remove_customer).delete()

        # Transfer orders from remove to keep
        orders_moved = Order.objects.filter(customer=remove_customer).update(customer=keep_customer)

        # Transfer purchase orders if the model has a customer FK
        try:
            pos_moved = PurchaseOrder.objects.filter(customer=remove_customer).update(customer=keep_customer)
        except Exception:
            pos_moved = 0

        # Fill in any blank fields on keep_customer from remove_customer
        fill_fields = [
            'email', 'phone', 'fax', 'website', 'abn',
            'address_1', 'address_2', 'city', 'state', 'suburb', 'postcode', 'country',
            'code', 'currency', 'credit_days', 'credit_terms_type', 'price_tier',
            'workguru_id', 'anthill_customer_id',
        ]
        updated_fields = []
        for field in fill_fields:
            keep_val = getattr(keep_customer, field)
            remove_val = getattr(remove_customer, field)
            if not keep_val and remove_val:
                setattr(keep_customer, field, remove_val)
                updated_fields.append(field)

        if updated_fields:
            keep_customer.save(update_fields=updated_fields)

        # Delete the merged-away customer
        remove_name = str(remove_customer)
        remove_customer.delete()

        return JsonResponse({
            'success': True,
            'orders_moved': orders_moved,
            'fields_filled': len(updated_fields),
            'removed': remove_name,
        })
    except (ProgrammingError, DatabaseError):
        logger.exception('Database error during customer merge')
        return JsonResponse({'error': 'Database schema is out of sync. Please run migrations and try again.'}, status=500)
    except Exception:
        logger.exception('Unexpected error during customer merge')
        return JsonResponse({'error': 'Unexpected server error while merging customers.'}, status=500)


@login_required
def customer_create(request):
    """Create a new customer manually"""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    
    from django.contrib import messages
    from django.shortcuts import redirect
    
    first_name = request.POST.get('first_name', '').strip()
    last_name = request.POST.get('last_name', '').strip()
    title = request.POST.get('title', '').strip()

    if not first_name and not last_name:
        messages.error(request, 'First name or last name is required.')
        return redirect('customers_list')

    # Build a combined name for legacy compatibility
    name = f'{first_name} {last_name}'.strip()

    # Check for duplicate — match against both first/last fields and legacy name field
    if Customer.objects.filter(
        Q(first_name__iexact=first_name, last_name__iexact=last_name) |
        Q(name__iexact=name)
    ).exists():
        messages.error(request, f'A customer named "{name}" already exists.')
        return redirect('customers_list')

    # Generate a unique positive workguru_id for manually created customers
    # Use 700000+ range to avoid collisions with real WorkGuru IDs
    max_id = Customer.objects.filter(workguru_id__gte=700000).order_by('-workguru_id').values_list('workguru_id', flat=True).first() or 699999
    manual_id = max(max_id + 1, 700000)
    # Safety loop in case of gaps or race conditions
    while Customer.objects.filter(workguru_id=manual_id).exists():
        manual_id += 1

    customer = Customer.objects.create(
        workguru_id=manual_id,
        title=title or None,
        first_name=first_name,
        last_name=last_name,
        name=name,
        email=request.POST.get('email', '').strip() or None,
        phone=request.POST.get('phone', '').strip() or None,
        website=request.POST.get('website', '').strip() or None,
        address_1=request.POST.get('address_1', '').strip() or None,
        city=request.POST.get('city', '').strip() or None,
        postcode=request.POST.get('postcode', '').strip() or None,
        country=request.POST.get('country', '').strip() or None,
        is_active=True,
    )

    messages.success(request, f'Customer "{customer.name}" created successfully.')
    return redirect('customer_detail', pk=customer.pk)


# ════════════════════════════════════════════════════════════════════════
# Events / Sales views
# ════════════════════════════════════════════════════════════════════════

@login_required
def events_list(request):
    """Display list of ALL Anthill events (all categories) with search, category filter, and date-bracket filters."""
    from django.utils import timezone
    from django.core.paginator import Paginator
    from datetime import timedelta

    search_query = request.GET.get('q', '').strip()
    age_filter = request.GET.get('age', '1y')
    category_filter = request.GET.get('cat', '').strip()

    # Location from user profile
    profile = getattr(request.user, 'profile', None)
    location_filter = profile.selected_location if profile else ''

    now = timezone.now()
    cutoff_1y = now - timedelta(days=365)
    cutoff_2y = now - timedelta(days=730)

    # All events
    events_base = AnthillSale.objects.select_related('customer', 'order').order_by('-activity_date')

    if location_filter and not search_query:
        events_base = events_base.filter(location__iexact=location_filter)

    # Category filter
    if category_filter:
        events_base = events_base.filter(category=category_filter)

    # Search — split into individual terms so multi-word searches work
    search_q = None
    if search_query:
        terms = search_query.split()
        per_term_qs = []
        for term in terms:
            per_term_qs.append(
                Q(customer_name__icontains=term) |
                Q(anthill_activity_id__icontains=term) |
                Q(activity_type__icontains=term) |
                Q(status__icontains=term) |
                Q(assigned_to_name__icontains=term) |
                Q(contract_number__icontains=term) |
                Q(source__icontains=term) |
                Q(customer__name__icontains=term) |
                Q(customer__first_name__icontains=term) |
                Q(customer__last_name__icontains=term)
            )
        search_q = per_term_qs[0]
        for extra in per_term_qs[1:]:
            search_q &= extra

    # Date bracket filters
    def bracket_filter(qs, bracket):
        if bracket == '1y':
            return qs.exclude(activity_type__istartswith='Historic').filter(activity_date__gte=cutoff_1y)
        elif bracket == '1_2y':
            return qs.exclude(activity_type__istartswith='Historic').filter(activity_date__gte=cutoff_2y, activity_date__lt=cutoff_1y)
        elif bracket == '2_10y':
            return qs.exclude(activity_type__istartswith='Historic').filter(activity_date__lt=cutoff_2y).exclude(activity_date__isnull=True)
        elif bracket == 'historic':
            return qs.filter(activity_type__istartswith='Historic')
        return qs

    count_1y = bracket_filter(events_base, '1y').count()
    count_1_2y = bracket_filter(events_base, '1_2y').count()
    count_2_10y = bracket_filter(events_base, '2_10y').count()
    count_historic = bracket_filter(events_base, 'historic').count()

    events = bracket_filter(events_base, age_filter)

    search_expanded_from = None
    if search_q:
        filtered = events.filter(search_q)
        if filtered.exists():
            events = filtered
        else:
            bracket_order = ['1y', '1_2y', '2_10y', 'historic']
            try:
                start_idx = bracket_order.index(age_filter) + 1
            except ValueError:
                start_idx = 0
            remaining = bracket_order[start_idx:] + bracket_order[:bracket_order.index(age_filter)]
            for bracket in remaining:
                expanded_qs = bracket_filter(events_base, bracket).filter(search_q)
                if expanded_qs.exists():
                    events = expanded_qs
                    search_expanded_from = bracket
                    break
            else:
                events = filtered

    page_number = request.GET.get('page', 1)
    paginator = Paginator(events, 100)
    page_obj = paginator.get_page(page_number)

    context = {
        'events': page_obj,
        'page_obj': page_obj,
        'filtered_count': paginator.count,
        'search_query': search_query,
        'age_filter': age_filter,
        'category_filter': category_filter,
        'location_filter': location_filter,
        'count_1y': count_1y,
        'count_1_2y': count_1_2y,
        'count_2_10y': count_2_10y,
        'count_historic': count_historic,
        'search_expanded_from': search_expanded_from,
    }

    return render(request, 'stock_take/events_list.html', context)


def _duplicate_sale_pks_to_drop(sales_qs):
    """Return the set of AnthillSale PKs to hide so each sale appears only once.

    The same underlying sale can show up twice: once with a full contract number
    (e.g. "BFS-PO-425301") and once with only the bare sale number ("425301", where
    ``contract_number`` is empty and the activity id is displayed instead). Keying on
    the raw ``contract_number`` misses these, so we normalise every row to its trailing
    sale number — the digits at the end of the contract number, or the activity id when
    no contract number is present. Within a duplicate group the most complete record
    wins, preferring (in order): a linked order, a fit date, a real contract number,
    the latest fit date, then the latest activity date. The losers are returned for
    exclusion.
    """
    rows = list(
        sales_qs.values(
            'pk', 'contract_number', 'anthill_activity_id',
            'order_id', 'fit_date', 'activity_date',
        )
    )

    def _sale_key(r):
        contract = (r['contract_number'] or '').strip()
        if contract:
            m = re.search(r'(\d{4,})\s*$', contract)
            if m:
                return m.group(1)
        return (r['anthill_activity_id'] or '').strip()

    groups = {}
    for r in rows:
        key = _sale_key(r)
        if not key:
            continue
        groups.setdefault(key, []).append(r)

    def _rank(r):
        return (
            1 if r['order_id'] else 0,
            1 if r['fit_date'] else 0,
            1 if (r['contract_number'] or '').strip() else 0,
            r['fit_date'].toordinal() if r['fit_date'] else 0,
            r['activity_date'].timestamp() if r['activity_date'] else 0,
            r['pk'],
        )

    drop = set()
    for records in groups.values():
        if len(records) < 2:
            continue
        keep = max(records, key=_rank)
        for r in records:
            if r['pk'] != keep['pk']:
                drop.add(r['pk'])
    return drop


@login_required
def sales_list(request):
    """Display list of Anthill sales (Category 3 only) with search and status filters."""
    from django.core.paginator import Paginator

    search_query = request.GET.get('q', '').strip()
    status_filter = request.GET.get('status', 'open')

    # Location from user profile
    profile = getattr(request.user, 'profile', None)
    location_filter = profile.selected_location if profile else ''

    # Only Category 3 = actual sales (Room Sale + Historic Sale), exclude Cancelled
    sales_base = AnthillSale.objects.select_related('customer', 'order', 'order__designer').filter(
        category='3'
    ).exclude(
        status__iexact='cancelled'
    ).order_by(F('fit_date').asc(nulls_last=True), '-activity_date')

    # Collapse duplicate sales that share the same contract number (the value shown
    # in the "Sale Number" column). Anthill can hold more than one activity for the
    # same contract — keep the most complete record and exclude the rest so each
    # sale number appears only once.
    duplicate_pks = _duplicate_sale_pks_to_drop(sales_base)
    if duplicate_pks:
        sales_base = sales_base.exclude(pk__in=duplicate_pks)

    if location_filter:
        # The location field on AnthillSale holds dirty/incorrect data for many
        # historical records, so the contract number prefix is the authoritative branch
        # signal (e.g. "BFS-..." = Belfast, "DUB-..." = Dublin). Keep a sale when either:
        #   * its contract number starts with this branch's prefix, or
        #   * it has no recognisable branch prefix (bare number / non-standard ref) AND
        #     its location field matches the selected branch.
        # Sales with a *different* branch prefix, or prefix-less sales whose location
        # points elsewhere, are excluded. This is applied even while searching so the
        # selected showroom always scopes the results.
        from .dashboard_view import _contract_prefix_for_location
        prefix = _contract_prefix_for_location(location_filter)
        if prefix:
            no_branch_prefix = ~Q(contract_number__iregex=r'^[A-Za-z]{3}-')
            sales_base = sales_base.filter(
                Q(contract_number__istartswith=prefix)
                | (no_branch_prefix & Q(location__iexact=location_filter))
            )
        else:
            sales_base = sales_base.filter(location__iexact=location_filter)

    # Search — split into individual terms so multi-word searches work
    search_q = None
    if search_query:
        terms = search_query.split()
        per_term_qs = []
        for term in terms:
            per_term_qs.append(
                Q(customer_name__icontains=term) |
                Q(anthill_activity_id__icontains=term) |
                Q(activity_type__icontains=term) |
                Q(status__icontains=term) |
                Q(assigned_to_name__icontains=term) |
                Q(contract_number__icontains=term) |
                Q(source__icontains=term) |
                Q(customer__name__icontains=term) |
                Q(customer__first_name__icontains=term) |
                Q(customer__last_name__icontains=term)
            )
        search_q = per_term_qs[0]
        for extra in per_term_qs[1:]:
            search_q &= extra

    # Status-based filters
    from .models import Remedial
    complete_statuses = ['complete', 'completed', 'won']
    complete_re = r'^(' + '|'.join(complete_statuses) + ')$'

    # Orders whose job has been marked finished count as complete sales even if the
    # Anthill status string was never updated. The order can be linked either by the
    # explicit FK (order_id) or by sale_number == anthill_activity_id (fallback link).
    finished_order_ids = set(
        Order.objects.filter(job_finished=True).values_list('id', flat=True)
    )
    finished_sale_numbers = set(
        Order.objects.filter(job_finished=True)
        .exclude(sale_number__isnull=True).exclude(sale_number='')
        .values_list('sale_number', flat=True)
    )
    complete_q = (
        Q(status__iregex=complete_re)
        | Q(order_id__in=finished_order_ids)
        | Q(anthill_activity_id__in=finished_sale_numbers)
    )

    # Orders that have at least one open (not completed) remedial — these sales
    # belong in the "Remedial" bracket rather than the plain "Open" bracket.
    open_remedial_order_ids = set(
        Remedial.objects.filter(is_completed=False)
        .exclude(original_order__isnull=True)
        .values_list('original_order_id', flat=True)
    )

    def status_bracket(qs, bracket):
        if bracket == 'complete':
            return qs.filter(complete_q)
        # Open and Remedial are both "not complete"
        non_complete = qs.exclude(complete_q)
        if bracket == 'remedial':
            return non_complete.filter(order_id__in=open_remedial_order_ids)
        elif bracket == 'open':
            return non_complete.exclude(order_id__in=open_remedial_order_ids)
        return qs

    count_open = status_bracket(sales_base, 'open').count()
    count_remedial = status_bracket(sales_base, 'remedial').count()
    count_complete = status_bracket(sales_base, 'complete').count()

    sales = status_bracket(sales_base, status_filter)

    if search_q:
        sales = sales.filter(search_q)

    page_number = request.GET.get('page', 1)

    # ---- Ordering status per sale (Required / Short / Ordered / Validated) ----
    from .models import OrderValidationRequest
    from .ordering_status import get_short_sale_numbers

    def _attach_ordering_status(sale_list):
        """Resolve each sale's linked order and set .ordering_status. Returns the
        anthill_activity_id -> Order fallback map used by the template."""
        activity_ids = [s.anthill_activity_id for s in sale_list]
        omap = {
            o.sale_number: o
            for o in Order.objects.filter(sale_number__in=activity_ids).only('id', 'sale_number')
        }

        sale_order_ids = {}
        for s in sale_list:
            linked = s.order or omap.get(s.anthill_activity_id)
            if linked:
                sale_order_ids[s.pk] = linked.id

        status_orders = {}
        if sale_order_ids:
            status_orders = {
                o.id: o for o in Order.objects.filter(id__in=set(sale_order_ids.values()))
                .select_related('boards_po')
                .prefetch_related('additional_boards_pos', 'accessories')
            }

        validated_order_ids = set(
            OrderValidationRequest.objects.filter(is_dismissed=True)
            .values_list('order_id', flat=True)
        )
        short_sale_numbers = get_short_sale_numbers()

        for s in sale_list:
            order = status_orders.get(sale_order_ids.get(s.pk))
            if not order:
                s.ordering_status = None
            elif order.id in validated_order_ids:
                s.ordering_status = 'validated'
            elif order.all_materials_ordered:
                s.ordering_status = 'ordered'
            elif order.sale_number in short_sale_numbers:
                s.ordering_status = 'short'
            else:
                s.ordering_status = 'required'
        return omap

    # PFP sales (no fit date) are collapsed into a single group shown only on page 1.
    pfp_sales = list(sales.filter(fit_date__isnull=True))

    if status_filter == 'complete':
        # The "complete" bracket is huge and status grouping is irrelevant there, so
        # just order by most-recent fit date and compute status for the visible page.
        dated_qs = sales.filter(fit_date__isnull=False).order_by(F('fit_date').desc(nulls_last=True))
        paginator = Paginator(dated_qs, 100)
        page_obj = paginator.get_page(page_number)
        dated_sales = list(page_obj)
        order_map = _attach_ordering_status(
            dated_sales + (pfp_sales if page_obj.number == 1 else [])
        )
    else:
        # Order strictly by fit date descending (furthest-away date first, past dates
        # last) so the "Today" divider lands on the single upcoming -> past boundary.
        dated_qs = sales.filter(fit_date__isnull=False).order_by(F('fit_date').desc(nulls_last=True))
        dated_all = list(dated_qs)
        order_map = _attach_ordering_status(dated_all + pfp_sales)
        paginator = Paginator(dated_all, 100)
        page_obj = paginator.get_page(page_number)
        dated_sales = list(page_obj)

    # PFP collapsed group only appears on the first page.
    if page_obj.number != 1:
        pfp_sales = []

    # ---- Payment status per visible sale (fully paid vs outstanding) ----
    def _attach_payment_status(sale_list):
        """Set .is_fully_paid on each sale using the same credit-matching logic as the
        sale detail page. Payments are fetched in one query for all visible sales."""
        if not sale_list:
            return
        sale_pks = [s.pk for s in sale_list]
        payments_by_sale = {}
        for p in AnthillPayment.objects.filter(sale_id__in=sale_pks, ignored=False):
            payments_by_sale.setdefault(p.sale_id, []).append(p)
        for s in sale_list:
            total_paid, discount = _match_credits_to_payments(payments_by_sale.get(s.pk, []))
            sale_value = s.sale_value or Decimal('0')
            effective_value = sale_value - discount
            # Only a sale with a positive value that is fully covered counts as paid.
            s.is_fully_paid = effective_value > 0 and total_paid >= effective_value
            # Amount still owed (never negative).
            s.outstanding_value = max(effective_value - total_paid, Decimal('0'))

    _attach_payment_status(dated_sales + pfp_sales)

    # Designer report — only for David Hearl
    is_david_hearl = request.user.email.lower() == 'david.hearl@sliderobes.com'
    available_locations = []
    if is_david_hearl:
        available_locations = list(
            AnthillSale.objects
            .filter(category='3')
            .exclude(location__isnull=True)
            .exclude(location='')
            .values_list('location', flat=True)
            .distinct()
            .order_by('location')
        )

    # ---- Ordering toolbar context (validator bell, Orders-to-Place, Add Order) ----
    from django.urls import reverse as _reverse
    from .forms import OrderForm
    from .models import UserSiteRole, AnthillOrderToPlace

    order_form = OrderForm(initial={'order_type': 'sale'})

    # Validator bell + modal
    is_validator = UserSiteRole.objects.filter(user=request.user, role_name='validator').exists()
    validation_requests = list(
        OrderValidationRequest.objects.filter(recipient=request.user, is_dismissed=False)
        .select_related('order', 'order__boards_po')
    )
    validation_pending_count = sum(
        1 for vr in validation_requests
        if not (
            (vr.boards_checked or vr.order.boards_not_required) and
            (vr.accessories_checked or vr.order.accessories_not_required) and
            (vr.os_doors_checked or not vr.order.os_doors_required) and
            vr.glass_checked
        )
    )

    # Detail-link map for orders shown in the validator modal
    order_href_map = {}
    if validation_requests:
        vr_order_numbers = [vr.order.sale_number for vr in validation_requests if vr.order.sale_number]
        vr_sale_pk_map = dict(
            AnthillSale.objects.filter(anthill_activity_id__in=vr_order_numbers)
            .values_list('anthill_activity_id', 'pk')
        )
        for vr in validation_requests:
            sale_pk = vr_sale_pk_map.get(vr.order.sale_number)
            if sale_pk:
                order_href_map[vr.order.id] = _reverse('sale_detail', kwargs={'pk': sale_pk}) + '?tab=order'
            else:
                order_href_map[vr.order.id] = _reverse('order_details', kwargs={'order_id': vr.order.id})

    # Anthill "Orders to Place" count + sale-number map for the modal
    anthill_orders_to_place_count = AnthillOrderToPlace.objects.filter(resolved=False).count()
    all_existing_sale_numbers = dict(
        Order.objects.exclude(sale_number__isnull=True).exclude(sale_number='')
        .values_list('sale_number', 'id')
    )
    from .views import _build_anthill_sale_pk_map
    anthill_sale_pk_map = _build_anthill_sale_pk_map()

    context = {
        'sales': page_obj,
        'page_obj': page_obj,
        'dated_sales': dated_sales,
        'pfp_sales': pfp_sales,
        'filtered_count': paginator.count,
        'search_query': search_query,
        'status_filter': status_filter,
        'location_filter': location_filter,
        'count_open': count_open,
        'count_remedial': count_remedial,
        'count_complete': count_complete,
        'order_map': order_map,
        'is_david_hearl': is_david_hearl,
        'available_locations': available_locations,
        'form': order_form,
        'is_validator': is_validator,
        'validation_requests': validation_requests,
        'validation_pending_count': validation_pending_count,
        'order_href_map': order_href_map,
        'anthill_orders_to_place_count': anthill_orders_to_place_count,
        'all_existing_sale_numbers': all_existing_sale_numbers,
        'anthill_sale_pk_map': anthill_sale_pk_map,
    }

    return render(request, 'stock_take/sales_list.html', context)


@login_required
def sales_by_designer_api(request):
    """Return sale totals grouped by designer. David Hearl only."""
    if request.user.email.lower() != 'david.hearl@sliderobes.com':
        return JsonResponse({'error': 'Forbidden'}, status=403)

    from django.db.models import Sum, Count

    location = request.GET.get('location', '').strip()
    date_from = request.GET.get('date_from', '').strip()
    date_to = request.GET.get('date_to', '').strip()

    qs = (
        AnthillSale.objects
        .filter(category='3')
        .exclude(status__iexact='cancelled')
        .exclude(sale_value__isnull=True)
    )

    if location:
        qs = qs.filter(location__iexact=location)
    if date_from:
        qs = qs.filter(activity_date__date__gte=date_from)
    if date_to:
        qs = qs.filter(activity_date__date__lte=date_to)

    rows = list(
        qs.values('location', 'assigned_to_name')
        .annotate(total=Sum('sale_value'), count=Count('pk'))
        .order_by('location', 'assigned_to_name')
    )
    # Decimal isn't JSON-serialisable — convert to float
    for r in rows:
        r['total'] = float(r['total'])

    return JsonResponse({'rows': rows})


@login_required
def sale_save(request, pk):
    """Save edited sale details via AJAX POST."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    sale = get_object_or_404(AnthillSale, pk=pk)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    from decimal import Decimal, InvalidOperation

    editable_fields = [
        'contract_number', 'location', 'assigned_to_name', 'source',
        'status', 'range_name', 'door_type', 'products_included',
        'fit_from_date', 'goods_due_in', 'anthill_activity_id',
    ]
    decimal_fields = ['sale_value', 'profit', 'deposit_required', 'balance_payable']

    update_fields = []
    for field in editable_fields:
        if field in data:
            val = data[field] or ''

            if field == 'anthill_activity_id':
                val = str(val).strip()
                if not val:
                    return JsonResponse({'success': False, 'error': 'Activity ID cannot be blank.'}, status=400)
                exists = AnthillSale.objects.exclude(pk=sale.pk).filter(anthill_activity_id=val).exists()
                if exists:
                    return JsonResponse({'success': False, 'error': f'Activity ID "{val}" already exists.'}, status=400)

            setattr(sale, field, val)
            update_fields.append(field)

    for field in decimal_fields:
        if field in data:
            raw = str(data[field]).replace('£', '').replace(',', '').strip()
            try:
                val = Decimal(raw) if raw else None
            except InvalidOperation:
                val = None
            setattr(sale, field, val)
            update_fields.append(field)

    if update_fields:
        sale.save(update_fields=update_fields)

        # Keep linked order.sale_number aligned when the activity ID changes.
        if 'anthill_activity_id' in update_fields and sale.order:
            if sale.order.sale_number == old_activity_id or not sale.order.sale_number:
                sale.order.sale_number = sale.anthill_activity_id
                sale.order.save(update_fields=['sale_number'])

    return JsonResponse({'success': True})


@login_required
def sale_complete(request, pk):
    """Mark a sale as complete (status='completed').

    Normally only allowed when every material on the linked order has been
    ordered/allocated; the readiness check is re-validated server-side so a client
    cannot bypass the disabled menu option.

    A ``force`` flag bypasses the allocation guard for legacy sales that were added
    before the order workflow existed (no linked order to validate against).
    """
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required'}, status=405)

    sale = get_object_or_404(AnthillSale, pk=pk)

    force = False
    if request.body:
        try:
            force = bool(json.loads(request.body).get('force'))
        except json.JSONDecodeError:
            force = False

    if not force:
        # Resolve the linked order the same way the sales list does: the explicit FK
        # first, then a fallback match on sale_number == anthill_activity_id.
        order = sale.order
        if not order and sale.anthill_activity_id:
            order = Order.objects.filter(sale_number=sale.anthill_activity_id).first()

        if not order or not order.all_materials_ordered:
            return JsonResponse(
                {'success': False, 'error': 'All items must be allocated before completing the sale.'},
                status=400,
            )

    sale.status = 'completed'
    sale.save(update_fields=['status'])

    return JsonResponse({'success': True})


@login_required
def sale_merge(request, pk):
    """Merge another sale into this sale (pk), then delete the source sale."""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required'}, status=405)

    keep_sale = get_object_or_404(AnthillSale, pk=pk)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Invalid JSON'}, status=400)

    source_pk = data.get('source_sale_id')
    if not source_pk:
        return JsonResponse({'success': False, 'error': 'source_sale_id is required'}, status=400)

    try:
        source_sale = AnthillSale.objects.select_related('customer', 'order').get(pk=source_pk)
    except AnthillSale.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Source sale not found'}, status=404)

    if source_sale.pk == keep_sale.pk:
        return JsonResponse({'success': False, 'error': 'Cannot merge a sale into itself.'}, status=400)

    # Safety: only allow merge within the same customer context.
    if keep_sale.customer_id != source_sale.customer_id:
        return JsonResponse({'success': False, 'error': 'Sales belong to different customers.'}, status=400)

    # If both sales are linked to different orders, avoid destructive ambiguity.
    if keep_sale.order_id and source_sale.order_id and keep_sale.order_id != source_sale.order_id:
        return JsonResponse({
            'success': False,
            'error': 'Both sales are linked to different orders. Unlink one order first, then merge.'
        }, status=400)

    try:
        with transaction.atomic():
            # Move all payments from source to keep.
            moved_payments = AnthillPayment.objects.filter(sale=source_sale).update(sale=keep_sale)

            updated_fields = []

            # Preserve order link if keep sale has none.
            if not keep_sale.order_id and source_sale.order_id:
                keep_sale.order = source_sale.order
                updated_fields.append('order')

            # Fill key empty fields on keep sale from source sale.
            fill_fields = [
                'contract_number', 'anthill_customer_id', 'customer_name', 'location',
                'assigned_to_id', 'assigned_to_name', 'activity_type', 'category', 'status',
                'source', 'sale_type_id', 'range_name', 'door_type', 'products_included',
                'fit_from_date', 'goods_due_in',
            ]
            for field in fill_fields:
                keep_val = getattr(keep_sale, field)
                source_val = getattr(source_sale, field)
                if (keep_val is None or keep_val == '') and source_val not in (None, ''):
                    setattr(keep_sale, field, source_val)
                    updated_fields.append(field)

            # Fill empty numeric/date fields from source.
            copy_if_empty = [
                'sale_value', 'profit', 'deposit_required', 'balance_payable',
                'discount', 'activity_date', 'fit_date',
            ]
            for field in copy_if_empty:
                keep_val = getattr(keep_sale, field)
                source_val = getattr(source_sale, field)
                if keep_val in (None, '') and source_val not in (None, ''):
                    setattr(keep_sale, field, source_val)
                    updated_fields.append(field)

            if updated_fields:
                keep_sale.save(update_fields=list(dict.fromkeys(updated_fields)))

            # Keep linked order's sale number aligned.
            if keep_sale.order and keep_sale.anthill_activity_id:
                if keep_sale.order.sale_number != keep_sale.anthill_activity_id:
                    keep_sale.order.sale_number = keep_sale.anthill_activity_id
                    keep_sale.order.save(update_fields=['sale_number'])

            removed_sale_pk = source_sale.pk
            source_sale.delete()

        return JsonResponse({
            'success': True,
            'merged_into': keep_sale.pk,
            'removed_sale': removed_sale_pk,
            'moved_payments': moved_payments,
        })
    except Exception:
        logger.exception('Unexpected error during sale merge')
        return JsonResponse({'success': False, 'error': 'Unexpected server error while merging sales.'}, status=500)


@login_required
def sale_link_order(request, pk):
    """Link or unlink an Order to/from an AnthillSale (AJAX POST).

    Accepts JSON body: { "order_id": <int|null> }
    When linking, also propagates fit_date between sale and order.
    """
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required'}, status=405)

    sale = get_object_or_404(AnthillSale, pk=pk)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Invalid JSON'}, status=400)

    order_id = data.get('order_id')

    if order_id is None:
        # Unlink
        sale.order = None
        sale.save(update_fields=['order'])
        return JsonResponse({'success': True, 'unlinked': True})

    order = Order.objects.filter(id=order_id).first()
    if not order:
        return JsonResponse({'success': False, 'error': 'Order not found'})

    sale.order = order
    sale.save(update_fields=['order'])

    # Propagate fit_date: sale → order (always update order to match the sale)
    if sale.fit_date:
        order.fit_date = sale.fit_date
        order.save(update_fields=['fit_date'])
    # Or order → sale (if sale doesn't have one)
    elif order.fit_date and not sale.fit_date:
        sale.fit_date = order.fit_date
        sale.save(update_fields=['fit_date'])

    return JsonResponse({'success': True, 'linked': True, 'sale_number': order.sale_number})


def _build_default_installation_address(sale):
    if not sale.customer:
        return ''

    parts = [
        sale.customer.address_1,
        sale.customer.address_2,
        sale.customer.city,
        sale.customer.postcode,
    ]
    cleaned = [p.strip() for p in parts if p and str(p).strip()]
    return ', '.join(cleaned)


def _designer_initials(name):
    parts = [p for p in re.split(r'\s+', (name or '').strip()) if p]
    return ''.join(p[0].upper() for p in parts if p)


def _extract_contract_initials(contract_number):
    contract = (contract_number or '').strip().upper()
    if not contract:
        return ''

    # Handles patterns like BFS-SD-420522 or NTG-KW-420968.
    m = re.match(r'^[A-Z]{2,4}-([A-Z]{2,4})-\d+', contract)
    if m:
        return m.group(1)

    tokens = re.findall(r'[A-Z]+', contract)
    if len(tokens) >= 2 and 2 <= len(tokens[1]) <= 4:
        return tokens[1]
    return ''


def _infer_designer_name_from_contract(sale):
    initials = _extract_contract_initials(sale.contract_number)
    if not initials:
        return ''

    for designer in Designer.objects.only('name').order_by('name'):
        if _designer_initials(designer.name) == initials:
            return designer.name
    return ''


def _get_or_create_sale_coversheet(sale, user=None):
    inferred_designer = _infer_designer_name_from_contract(sale)
    user_default = (user.get_full_name() or user.username) if user and user.is_authenticated else ''

    defaults = {
        'prepared_by': inferred_designer or user_default,
        'customer_on_site_name': (sale.customer.name if sale.customer else sale.customer_name) or '',
        'customer_on_site_phone': (sale.customer.phone if sale.customer and sale.customer.phone else ''),
        'installation_address': _build_default_installation_address(sale),
        'fit_date': sale.fit_date or (sale.order.fit_date if sale.order and sale.order.fit_date else None),
    }
    coversheet, created = SaleCoverSheet.objects.get_or_create(sale=sale, defaults=defaults)

    # If an older coversheet still has the user-default value, replace with the contract-inferred designer.
    if not created and inferred_designer and coversheet.prepared_by in ('', user_default):
        coversheet.prepared_by = inferred_designer
        coversheet.save(update_fields=['prepared_by'])

    return coversheet


def _sale_claim_group_key(sale):
    job = (sale.order.sale_number if sale.order and sale.order.sale_number else sale.anthill_activity_id or '').strip()
    customer = (sale.customer_name or (sale.customer.name if sale.customer else '') or 'customer').strip()
    customer_token = re.sub(r'[^A-Za-z0-9]+', '', customer)[:24] or 'customer'
    sale_token = re.sub(r'[^A-Za-z0-9]+', '', (sale.anthill_activity_id or 'sale'))[:24] or 'sale'
    return f'{job}_{customer_token}_{sale_token}'


def _claim_doc_type_from_filename(filename):
    base = os.path.splitext(os.path.basename(filename or ''))[0]
    parts = base.rsplit('_', 1)
    return parts[-1] if len(parts) > 1 else ''


def _important_claim_doc_types(limit=3):
    from django.core.cache import cache as _cache
    cache_key = f'important_claim_doc_types_{limit}'
    result = _cache.get(cache_key)
    if result is not None:
        return result
    defaults = ['ProductionDrawings', 'Survey', 'Checklist']
    docs = ClaimDocument.objects.only('file').order_by('-uploaded_at')[:500]
    counts = {}
    for d in docs:
        dt = _claim_doc_type_from_filename(d.file.name)
        if not dt:
            continue
        counts[dt] = counts.get(dt, 0) + 1

    ranked = [k for k, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]
    combined = ranked + [d for d in defaults if d not in ranked]
    result = combined[:limit]
    _cache.set(cache_key, result, 3600)
    return result


@login_required
def sale_coversheet_save(request, pk):
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required'}, status=405)

    sale = get_object_or_404(AnthillSale.objects.select_related('customer', 'order'), pk=pk)
    coversheet = _get_or_create_sale_coversheet(sale, request.user)

    def parse_date(val):
        val = (val or '').strip()
        if not val:
            return None
        try:
            return date.fromisoformat(val)
        except ValueError:
            return None

    def parse_fit_days(val):
        val = (val or '').strip()
        if not val:
            return None
        try:
            parsed = Decimal(val)
        except (InvalidOperation, TypeError):
            return None
        if parsed < Decimal('0.5') or parsed > Decimal('5.0'):
            return None
        return parsed.quantize(Decimal('0.1'))

    def norm_for_compare(val):
        if val is None:
            return ''
        if isinstance(val, Decimal):
            return str(val)
        if hasattr(val, 'isoformat'):
            return val.isoformat()
        if isinstance(val, bool):
            return val
        return str(val).strip()

    tracked_fields = [
        'prepared_by', 'cad_number', 'customer_on_site_name', 'customer_on_site_phone',
        'installation_address', 'survey_date', 'fit_date', 'products_scope',
        'measurements_notes', 'access_notes', 'health_safety_notes', 'special_instructions',
        'two_man_lift_required', 'access_check_required', 'rip_out_required',
        'remeasure_required', 'remeasure_date', 'new_build_property', 'parking_situation',
        'design_check_passed_date', 'pfp_passed_date', 'ordering_passed_date',
        'goods_due_in_date', 'fit_days', 'fit_days_decided_by', 'door_type', 'door_details', 'track_type',
        'track_colour', 'handle_details', 'lighting_details', 'installation_products_included',
        'installation_design_type', 'measured_on', 'fit_on', 'electrics_utilities_required',
        'electrics_utilities_notes', 'underfloor_heating', 'board_colour_exterior',
        'board_colour_interior', 'board_colour_backs', 'board_colour_fronts', 'is_final',
    ]
    old_values = {f: getattr(coversheet, f) for f in tracked_fields}

    coversheet.prepared_by = (request.POST.get('prepared_by') or '').strip()
    coversheet.cad_number = (request.POST.get('cad_number') or '').strip()
    coversheet.customer_on_site_name = (request.POST.get('customer_on_site_name') or '').strip()
    coversheet.customer_on_site_phone = (request.POST.get('customer_on_site_phone') or '').strip()
    coversheet.installation_address = (request.POST.get('installation_address') or '').strip()
    coversheet.survey_date = parse_date(request.POST.get('survey_date'))
    coversheet.fit_date = parse_date(request.POST.get('fit_date'))
    coversheet.products_scope = (request.POST.get('products_scope') or '').strip()
    coversheet.measurements_notes = (request.POST.get('measurements_notes') or '').strip()
    coversheet.access_notes = (request.POST.get('access_notes') or '').strip()
    coversheet.health_safety_notes = (request.POST.get('health_safety_notes') or '').strip()
    coversheet.special_instructions = (request.POST.get('special_instructions') or '').strip()
    coversheet.two_man_lift_required = request.POST.get('two_man_lift_required') == 'on'
    coversheet.access_check_required = request.POST.get('access_check_required') == 'on'
    coversheet.rip_out_required = request.POST.get('rip_out_required') == 'on'
    coversheet.remeasure_required = request.POST.get('remeasure_required') == 'on'
    coversheet.remeasure_date = parse_date(request.POST.get('remeasure_date')) if coversheet.remeasure_required else None
    coversheet.new_build_property = request.POST.get('new_build_property') == 'on'
    coversheet.parking_situation = (request.POST.get('parking_situation') or '').strip()
    coversheet.design_check_passed_date = parse_date(request.POST.get('design_check_passed_date'))
    coversheet.pfp_passed_date = parse_date(request.POST.get('pfp_passed_date'))
    coversheet.ordering_passed_date = parse_date(request.POST.get('ordering_passed_date'))
    coversheet.goods_due_in_date = parse_date(request.POST.get('goods_due_in_date'))
    coversheet.fit_days = parse_fit_days(request.POST.get('fit_days'))
    coversheet.fit_days_decided_by = (request.POST.get('fit_days_decided_by') or '').strip()
    coversheet.door_type = (request.POST.get('door_type') or '').strip()
    coversheet.door_details = (request.POST.get('door_details') or '').strip()
    coversheet.track_type = (request.POST.get('track_type') or '').strip()
    coversheet.track_colour = (request.POST.get('track_colour') or '').strip()
    coversheet.handle_details = (request.POST.get('handle_details') or '').strip()
    coversheet.lighting_details = (request.POST.get('lighting_details') or '').strip()
    coversheet.installation_products_included = (request.POST.get('installation_products_included') or '').strip()
    coversheet.installation_design_type = (request.POST.get('installation_design_type') or '').strip()
    coversheet.measured_on = (request.POST.get('measured_on') or '').strip()
    coversheet.fit_on = (request.POST.get('fit_on') or '').strip()
    coversheet.electrics_utilities_required = request.POST.get('electrics_utilities_required') == 'on'
    coversheet.electrics_utilities_notes = (request.POST.get('electrics_utilities_notes') or '').strip()
    coversheet.underfloor_heating = request.POST.get('underfloor_heating') == 'on'
    coversheet.board_colour_exterior = (request.POST.get('board_colour_exterior') or '').strip()
    coversheet.board_colour_interior = (request.POST.get('board_colour_interior') or '').strip()
    coversheet.board_colour_backs = (request.POST.get('board_colour_backs') or '').strip()
    coversheet.board_colour_fronts = (request.POST.get('board_colour_fronts') or '').strip()
    coversheet.is_final = request.POST.get('is_final') == 'on'

    changes = {}
    for field in tracked_fields:
        old_v = norm_for_compare(old_values[field])
        new_v = norm_for_compare(getattr(coversheet, field))
        if old_v != new_v:
            changes[field] = {'from': old_v, 'to': new_v}

    if changes:
        coversheet.revision_number = (coversheet.revision_number or 1) + 1
        coversheet.updated_by = request.user
        coversheet.save()
        SaleCoverSheetHistory.objects.create(
            coversheet=coversheet,
            revision_number=coversheet.revision_number,
            changed_by=request.user,
            changes=changes,
        )

    messages.success(request, 'Coversheet saved successfully.')
    return redirect('sale_detail', pk=pk)


@login_required
def sale_coversheet_pdf(request, pk):
    sale = get_object_or_404(AnthillSale.objects.select_related('customer', 'order'), pk=pk)
    coversheet = _get_or_create_sale_coversheet(sale, request.user)

    from .sale_coversheet_pdf_generator import generate_sale_coversheet_pdf
    buffer = generate_sale_coversheet_pdf(sale, coversheet)

    filename = f'Sale_Coversheet_{sale.anthill_activity_id}.pdf'
    response = HttpResponse(buffer.getvalue(), content_type='application/pdf')
    response['Content-Disposition'] = f'inline; filename="{filename}"'
    return response


@login_required
def sale_claim_document_upload(request, pk):
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required'}, status=405)

    sale = get_object_or_404(AnthillSale.objects.select_related('customer', 'order'), pk=pk)
    uploaded = request.FILES.get('file')
    doc_type = (request.POST.get('doc_type') or '').strip()

    if not uploaded:
        messages.error(request, 'Please choose a file to upload.')
        return redirect('sale_detail', pk=pk)
    if not doc_type:
        messages.error(request, 'Document type is required.')
        return redirect('sale_detail', pk=pk)

    group_key = _sale_claim_group_key(sale)
    ext = os.path.splitext(uploaded.name)[1] or '.pdf'
    clean_type = re.sub(r'[^A-Za-z0-9]+', '', doc_type) or 'Document'
    uploaded.name = f'{group_key}_{clean_type}{ext}'

    # Replace any existing document of the same type (Update semantics) so each
    # document slot only ever holds one current file.
    for d in ClaimDocument.objects.filter(group_key=group_key):
        if _claim_doc_type_from_filename(d.file.name) == clean_type:
            if d.file:
                d.file.delete(save=False)
            d.delete()

    ClaimDocument.objects.create(
        title=f'{clean_type} - {(sale.contract_number or sale.anthill_activity_id)}',
        file=uploaded,
        customer_name=(sale.customer_name or (sale.customer.name if sale.customer else '')),
        group_key=group_key,
        uploaded_by=request.user,
    )

    messages.success(request, f'{clean_type} uploaded.')
    return redirect('sale_detail', pk=pk)


@login_required
def sale_claim_document_delete(request, pk):
    """Delete a single document attached to this sale (file + record)."""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required'}, status=405)

    sale = get_object_or_404(AnthillSale.objects.select_related('order'), pk=pk)
    doc_id = (request.POST.get('doc_id') or '').strip()
    group_key = _sale_claim_group_key(sale)

    try:
        doc = ClaimDocument.objects.get(pk=doc_id, group_key=group_key)
    except (ClaimDocument.DoesNotExist, ValueError):
        return JsonResponse({'success': False, 'error': 'Document not found.'}, status=404)

    if doc.file:
        doc.file.delete(save=False)
    doc.delete()
    return JsonResponse({'success': True})


@login_required
def sale_claim_document_attach(request, pk):
    """Attach one or more existing Claim Service documents to this job by re-homing
    them under the sale's canonical group key, so they appear in the Job Info docs."""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required'}, status=405)

    sale = get_object_or_404(AnthillSale.objects.select_related('customer', 'order'), pk=pk)

    raw_ids = request.POST.get('doc_ids') or request.POST.get('doc_id') or ''
    doc_ids = [i.strip() for i in str(raw_ids).split(',') if i.strip().isdigit()]
    if not doc_ids:
        return JsonResponse({'success': False, 'error': 'No documents selected.'}, status=400)

    group_key = _sale_claim_group_key(sale)
    docs = ClaimDocument.objects.filter(id__in=doc_ids)
    attached = 0
    for doc in docs:
        if doc.group_key != group_key:
            doc.group_key = group_key
            doc.save(update_fields=['group_key'])
        attached += 1

    if not attached:
        return JsonResponse({'success': False, 'error': 'No matching documents found.'}, status=404)

    return JsonResponse({'success': True, 'attached': attached})


def _build_sale_context(sale, request_user):
    """Compute context dict for a single AnthillSale. Used by both
    sale_detail (standalone) and order_details (sale tab)."""
    from decimal import Decimal
    coversheet = _get_or_create_sale_coversheet(sale, request_user)
    claim_group_key = _sale_claim_group_key(sale)
    claim_docs = list(ClaimDocument.objects.filter(group_key=claim_group_key).order_by('-uploaded_at'))
    docs_by_type = {}
    for d in claim_docs:
        dt = _claim_doc_type_from_filename(d.file.name)
        if dt and dt not in docs_by_type:
            docs_by_type[dt] = d
    # Fixed set of main job documents shown on the sale. The coversheet is
    # rendered separately; these four are matched against attached claim docs by
    # their cleaned type token (see _claim_doc_type_from_filename).
    # 'claim' marks documents that originate from the Claim Service (and so get a
    # search button). The Contract is a simple uploaded/generated PDF, not a
    # claim document, so it has no search action.
    important_documents = [
        {'doc_type': key, 'label': label, 'claim': claim, 'doc': docs_by_type.get(key)}
        for key, label, claim in (
            ('Contract', 'Contract', False),
            ('A3ProductionDrawings', 'A3 Production Drawings', True),
            ('BillOfMaterials', 'Bill Of Materials', True),
            ('ProductionDrawings', 'Production Drawings', True),
        )
    ]
    coversheet_history = list(coversheet.history_entries.select_related('changed_by').all()[:25])

    cad = (coversheet.cad_number or '').strip().lower()
    cad_matches_claim_docs = False
    cad_expected_numbers = []
    if cad:
        # Claim service documents often reference the CAD number without its
        # leading zero(s) (e.g. "23359" instead of "023359"), so compare the
        # numeric value with leading zeros stripped rather than relying on an
        # exact substring match. The CAD number lives in the document's
        # group key / filename (e.g. "1111_Radley_022115"), so search those too.
        cad_digits = re.sub(r'\D', '', cad)
        cad_norm = cad_digits.lstrip('0')
        # The contract number also appears in the document names/group keys, so
        # ignore it when collecting CAD candidates — it is not a CAD reference.
        contract_norm = re.sub(r'\D', '', sale.contract_number or '').lstrip('0')
        seen_numbers = set()
        for d in claim_docs:
            haystack = (
                f"{d.title or ''} {d.group_key or ''} "
                f"{os.path.basename(d.file.name or '')}"
            ).lower()
            if cad and cad in haystack:
                cad_matches_claim_docs = True
                break
            tokens = re.findall(r'\d+', haystack)
            if cad_norm and any(t.lstrip('0') == cad_norm for t in tokens):
                cad_matches_claim_docs = True
                break
            # Collect CAD-like numbers from the docs so the user can see what
            # value the documents actually reference, excluding the contract
            # number which is not a CAD reference.
            for t in tokens:
                tnorm = t.lstrip('0')
                if contract_norm and tnorm == contract_norm:
                    continue
                if 5 <= len(t) <= 6 and t not in seen_numbers:
                    seen_numbers.add(t)
                    cad_expected_numbers.append(t)
    # Only flag a mismatch when the documents actually contain a CAD-like number
    # that differs from the entered value. If the docs only reference the
    # contract number (or no CAD candidate at all), there is nothing to flag.
    cad_mismatch = bool(cad) and not cad_matches_claim_docs and bool(cad_expected_numbers)

    gallery_images = []
    if sale.order:
        from .models import GalleryImage, Remedial
        gallery_images = GalleryImage.objects.filter(order=sale.order).order_by('-uploaded_at')
        remedials = list(Remedial.objects.filter(original_order=sale.order).prefetch_related('accessories').order_by('-created_date'))
    else:
        remedials = []
    related_sales = []
    if sale.customer:
        related_sales = sale.customer.anthill_sales.exclude(pk=sale.pk).order_by('-activity_date')

    sale_invoices = []
    if sale.contract_number:
        sale_invoices = list(
            Invoice.objects.filter(contract_number=sale.contract_number)
            .prefetch_related('line_items', 'payments', 'purchase_orders')
            .order_by('-date')
        )

    all_payments = list(sale.payments.all().order_by('date'))

    # Both Xero and manual payments are shown customer-wide: every payment across
    # all of the customer's sales appears here. Xero rows are deduplicated so an
    # invoice linked to several sales is only listed once (with its full,
    # un-split value).
    if sale.customer:
        customer_payment_qs = (
            AnthillPayment.objects
            .filter(sale__customer=sale.customer)
            .select_related('sale')
            .order_by('date', 'id')
        )
    else:
        customer_payment_qs = (
            AnthillPayment.objects.filter(sale=sale).order_by('date', 'id')
        )
    customer_payments = list(customer_payment_qs)

    manual_payments = []
    for p in customer_payments:
        if p.source != 'manual':
            continue
        p.belongs_to_current = (p.sale_id == sale.pk)
        manual_payments.append(p)
    manual_payments_total = sum(p.amount for p in manual_payments if p.amount) or None

    xero_payments = []
    _seen_xero = set()
    for p in customer_payments:
        if p.source == 'manual':
            continue
        # Deduplicate only TRUE duplicates — the same invoice linked to several
        # sales (via the invoice split) produces one row per sale sharing the
        # same (invoice, payment id). A single batch Xero payment can apply to
        # several *different* invoices with the same PaymentID, so the invoice id
        # must be part of the key to keep those distinct.
        if p.anthill_payment_id:
            key = (p.xero_invoice_id, p.anthill_payment_id)
        else:
            key = (p.xero_invoice_id, p.date, p.full_amount if p.full_amount is not None else p.amount)
        if key in _seen_xero:
            continue
        _seen_xero.add(key)
        # Display the real (un-split) amount on the customer-wide list.
        p.display_amount = p.full_amount if p.full_amount is not None else p.amount
        p.belongs_to_current = (p.sale_id == sale.pk)
        xero_payments.append(p)
    xero_payments_total = sum(p.display_amount for p in xero_payments if p.display_amount) or None

    active_payments = [p for p in all_payments if not p.ignored]
    total_paid, discount = _match_credits_to_payments(active_payments)
    sale_value = sale.sale_value or Decimal('0')
    effective_value = sale_value - discount

    is_cancelled = sale.status in ('dead', 'cancelled')
    is_completed = (sale.status or '').lower() in ('complete', 'completed', 'won')
    if is_cancelled:
        outstanding = Decimal('0')
        overpayment = max(total_paid - effective_value, Decimal('0'))
        payment_pct = 100 if effective_value > 0 else 0
    else:
        outstanding = max(effective_value - total_paid, Decimal('0'))
        overpayment = max(total_paid - effective_value, Decimal('0'))
        payment_pct = int(min(total_paid / effective_value * 100, 100)) if effective_value > 0 else 0
    overpayment_pct = int(overpayment / effective_value * 100) if effective_value > 0 and overpayment > 0 else 0
    adjusted_profit = (sale.profit or Decimal('0')) - discount if sale.profit else None

    # ── Customer payment pool ───────────────────────────────────────────────
    # Treat every eligible sale + payment for this customer as a single pool and
    # allocate it oldest-sale-first, so a customer is never shown overpaid on one
    # sale while outstanding on another. When the current sale participates, its
    # pooled allocation replaces the in-isolation outstanding/overpayment figures.
    payment_pool = _build_customer_payment_pool(sale)
    if payment_pool and payment_pool['current'] and not is_cancelled:
        cur = payment_pool['current']
        outstanding = cur['outstanding']
        overpayment = cur['credit']
        payment_pct = cur['pct']
        overpayment_pct = int(overpayment / effective_value * 100) if effective_value > 0 and overpayment > 0 else 0
        hero_value_paid = cur['allocated']
        hero_value_total = cur['effective_value']
    else:
        hero_value_paid = total_paid
        hero_value_total = effective_value

    return {
        'sale': sale,
        'coversheet': coversheet,
        'designers': Designer.objects.order_by('name'),
        'coversheet_history': coversheet_history,
        'cad_mismatch': cad_mismatch,
        'cad_expected_numbers': cad_expected_numbers,
        'claim_group_key': claim_group_key,
        'important_documents': important_documents,
        'related_sales': related_sales,
        'payments': all_payments,
        'xero_payments': xero_payments,
        'manual_payments': manual_payments,
        'payments_total': total_paid,
        'xero_payments_total': xero_payments_total,
        'manual_payments_total': manual_payments_total,
        'effective_value': effective_value,
        'discount': discount,
        'outstanding': outstanding,
        'overpayment': overpayment,
        'payment_pct': payment_pct,
        'overpayment_pct': overpayment_pct,
        'payment_pool': payment_pool,
        'hero_value_paid': hero_value_paid,
        'hero_value_total': hero_value_total,
        'adjusted_profit': adjusted_profit,
        'gallery_images': gallery_images,
        'remedials': remedials,
        'is_cancelled': is_cancelled,
        'is_completed': is_completed,
        'sale_invoices': sale_invoices,
    }


def _sale_alloc_date(sale):
    """Return the date used to order a sale for payment allocation.

    Prefers the sale's own fit date, then the linked order's fit date. Returns
    ``None`` when no fit date is known — such sales are treated as the newest
    (allocated last).
    """
    import datetime as _dt
    d = getattr(sale, 'fit_date', None)
    if not d and getattr(sale, 'order', None) is not None:
        d = getattr(sale.order, 'fit_date', None)
    if isinstance(d, _dt.datetime):
        return d.date()
    return d


def _within_six_months(d1, d2):
    """True when ``d2`` (assumed >= ``d1``) falls within six calendar months of
    ``d1``. Returns ``False`` if either date is missing."""
    import datetime as _dt
    if d1 is None or d2 is None:
        return False
    month_index = d1.month - 1 + 6
    year = d1.year + month_index // 12
    month = month_index % 12 + 1
    if month == 12:
        next_month_first = _dt.date(year + 1, 1, 1)
    else:
        next_month_first = _dt.date(year, month + 1, 1)
    last_day = (next_month_first - _dt.timedelta(days=1)).day
    limit = _dt.date(year, month, min(d1.day, last_day))
    return d2 <= limit


def _fit_date_groups(entries):
    """Chain ``entries`` (already sorted oldest -> newest by alloc date) into
    groups where each sale joins the current group only if its fit date is
    within six months of the previous sale in that group. Sales without a fit
    date never chain, so each starts (and ends) its own group at the tail."""
    groups = []
    current = []
    prev = None
    for e in entries:
        d = e['alloc_date']
        if not current:
            current = [e]
        elif _within_six_months(prev, d):
            current.append(e)
        else:
            groups.append(current)
            current = [e]
        prev = d
    if current:
        groups.append(current)
    return groups


def _split_proportional(caps, avail):
    """Distribute ``avail`` across slots proportional to each slot's value, so
    every slot reaches the same percentage of its value (a percentage-based, not
    equal-nominal, split).

    Any amount beyond the total of all caps is left undistributed so it can
    cascade to the next group. Returns a list of per-slot allocations, each
    <= its cap.
    """
    from decimal import Decimal, ROUND_HALF_UP
    n = len(caps)
    total = sum(caps, Decimal('0'))
    if total <= 0:
        return [Decimal('0')] * n
    target = avail if avail < total else total
    result = []
    allocated = Decimal('0')
    cent = Decimal('0.01')
    for i, c in enumerate(caps):
        if i == n - 1:
            # Last slot soaks up the rounding remainder so the shares always sum
            # exactly to ``target``.
            share = target - allocated
        else:
            share = (c / total * target).quantize(cent, rounding=ROUND_HALF_UP)
        if share < 0:
            share = Decimal('0')
        if share > c:
            share = c
        result.append(share)
        allocated += share
    return result


def _allocate_fit_date(entries, pool_paid):
    """Allocate ``pool_paid`` across ``entries`` (sorted oldest -> newest) using
    the fit-date grouping rule: fill the oldest group first, splitting each group
    percentage-wise (pro-rata to value) and cascading any overflow. Mutates each
    entry's ``allocated`` and returns the leftover credit after every sale is
    filled.
    """
    from decimal import Decimal
    for e in entries:
        e['allocated'] = Decimal('0')
    remaining = pool_paid
    for group in _fit_date_groups(entries):
        caps = [e['effective_value'] for e in group]
        allocated = _split_proportional(caps, remaining)
        for e, a in zip(group, allocated):
            e['allocated'] = a
            remaining -= a
    return max(remaining, Decimal('0'))


def _build_customer_payment_pool(current_sale):
    """Pool a customer's eligible sales + payments into one balance.

    Real-world payments rarely map 1:1 to a single sale — a customer with
    several contracts often pays a lump sum that should fill the whole account.
    Calculating each sale in isolation produces nonsense like one sale being
    "overpaid" while another is "outstanding". Instead we sum every payment into
    a pool and allocate it by fit date: sales are grouped so that any two within
    six months share proportionally (by value), and groups more than six months
    apart are filled oldest-first.

    Excludes cancelled/dead sales, leads and enquiries. Returns ``None`` when
    there's no customer; otherwise a dict with pool totals, per-sale allocation
    entries and the entry for ``current_sale`` (``None`` when the current sale
    isn't eligible, e.g. it's cancelled).
    """
    import datetime as _dt
    from decimal import Decimal

    customer = current_sale.customer
    if not customer:
        return None

    eligible = list(
        customer.anthill_sales
        .select_related('order')
        .prefetch_related('payments')
        .exclude(activity_type__icontains='lead')
        .exclude(activity_type__icontains='enquir')
        .exclude(status__in=['dead', 'cancelled'])
        .order_by('activity_date', 'id')
    )

    entries = []
    pool_value = Decimal('0')
    pool_paid = Decimal('0')
    for s in eligible:
        active = [p for p in s.payments.all() if not p.ignored]
        paid, discount = _match_credits_to_payments(active)
        # The sale value isn't always populated on the AnthillSale; fall back to
        # the linked order's inc-VAT total (the figure shown in the Financial
        # Summary) so the pool still reflects the real contract value.
        base_value = s.sale_value or (s.order.total_value_inc_vat if s.order else None) or Decimal('0')
        eff = base_value - discount
        if eff < 0:
            eff = Decimal('0')
        entries.append({
            'sale': s,
            'alloc_date': _sale_alloc_date(s),
            'effective_value': eff,
            'received': paid,
            'discount': discount,
        })
        pool_value += eff
        pool_paid += paid

    # Order by fit date (oldest first); sales without a fit date are treated as
    # the newest and allocated last.
    entries.sort(key=lambda e: (e['alloc_date'] is None, e['alloc_date'] or _dt.date.min))

    # Fit-date allocation: sales within six months of each other share
    # proportionally (percentage-based); groups more than six months apart are
    # filled oldest-group-first.
    leftover = _allocate_fit_date(entries, pool_paid)
    for e in entries:
        eff = e['effective_value']
        alloc = e.get('allocated', Decimal('0'))
        e['outstanding'] = max(eff - alloc, Decimal('0'))
        e['credit'] = Decimal('0')
        e['pct'] = int(min(alloc / eff * 100, 100)) if eff > 0 else (100 if alloc > 0 else 0)
        e['is_current'] = (e['sale'].pk == current_sale.pk)
    # Money left after filling every sale is the customer's credit — attribute it
    # to the newest sale (the last entry after fit-date ordering).
    if entries and leftover > 0:
        entries[-1]['credit'] = leftover

    net_outstanding = max(pool_value - pool_paid, Decimal('0'))
    net_credit = max(pool_paid - pool_value, Decimal('0'))
    pool_pct = int(min(pool_paid / pool_value * 100, 100)) if pool_value > 0 else (100 if pool_paid > 0 else 0)

    current = next((e for e in entries if e['is_current']), None)

    return {
        'entries': entries,
        'pool_value': pool_value,
        'pool_paid': pool_paid,
        'net_outstanding': net_outstanding,
        'net_credit': net_credit,
        'pool_pct': pool_pct,
        'sale_count': len(entries),
        'current': current,
        'multi': len(entries) > 1,
    }


def _orders_financials(orders):
    """Single source of truth for per-order fit financials.

    Shared by the fit calendar and the weekly operations report so the two can
    never disagree. Given an iterable of ``Order`` objects, returns a dict keyed
    by ``order.sale_number`` whose values are::

        {'sale_value': effective (post-discount) value,
         'payments_total': amount paid,
         'outstanding': balance still owed,
         'value_estimated': bool}

    The figures are derived exactly the way the sale-detail page is: the stored
    ``AnthillSale.balance_payable`` when it is reliable, otherwise the live
    customer payment pool (which handles cross-sale credits), otherwise the
    order's own inc-VAT total. ``None`` is returned for an order with no value.
    """
    orders = [o for o in orders if o is not None]
    result = {}
    if not orders:
        return result

    sale_numbers = [o.sale_number for o in orders if o.sale_number]

    # Stored Anthill figures, keyed by sale number. Keep the highest-value row
    # when several sale records share an Anthill order id so a populated value
    # isn't clobbered by a zero.
    _fin_map = {}
    if sale_numbers:
        for row in (AnthillSale.objects
                    .filter(anthill_activity_id__in=sale_numbers)
                    .values('anthill_activity_id', 'sale_value', 'discount',
                            'balance_payable', 'paid_in_full')):
            key = row['anthill_activity_id']
            sv = row['sale_value'] or Decimal('0')
            existing = _fin_map.get(key)
            if existing is not None and sv <= existing['sale_value']:
                continue
            _fin_map[key] = {
                'sale_value': sv,
                'discount': row['discount'] or Decimal('0'),
                'balance_payable': row['balance_payable'],
                'paid_in_full': row['paid_in_full'],
            }

    # When a sale's stored value is missing/zero its balance was computed against
    # zero and is unreliable — recompute via the customer payment pool instead.
    _unreliable_nums = {
        o.sale_number for o in orders
        if o.sale_number and (
            _fin_map.get(o.sale_number) is None
            or _fin_map[o.sale_number]['sale_value'] <= 0
        )
    }
    _pool_fin = {}
    if _unreliable_nums:
        _pool_cache = {}
        for _s in (AnthillSale.objects
                   .filter(anthill_activity_id__in=_unreliable_nums)
                   .select_related('customer', 'order')):
            if not _s.customer_id:
                continue
            if _s.customer_id not in _pool_cache:
                _pool_cache[_s.customer_id] = _build_customer_payment_pool(_s) or False
            pool = _pool_cache[_s.customer_id]
            if not pool:
                continue
            for e in pool['entries']:
                aid = e['sale'].anthill_activity_id
                if aid and e['effective_value'] > 0:
                    _pool_fin[aid] = {
                        'effective_value': e['effective_value'],
                        'outstanding': e['outstanding'],
                    }

    for o in orders:
        sn = o.sale_number
        order_total = o.total_value_inc_vat or Decimal('0')

        pooled = _pool_fin.get(sn)
        if pooled is not None:
            result[sn] = {
                'sale_value': pooled['effective_value'],
                'payments_total': pooled['effective_value'] - pooled['outstanding'],
                'outstanding': pooled['outstanding'],
                'value_estimated': False,
            }
            continue

        row = _fin_map.get(sn)
        if row is None:
            result[sn] = {
                'sale_value': order_total,
                'payments_total': Decimal('0'),
                'outstanding': order_total,
                'value_estimated': False,
            } if order_total > 0 else None
            continue

        base_value = row['sale_value'] or order_total
        effective_value = base_value - row['discount']
        balance = row['balance_payable']
        outstanding = effective_value if balance is None else balance
        paid = effective_value - outstanding
        result[sn] = {
            'sale_value': effective_value,
            'payments_total': paid,
            'outstanding': outstanding,
            'value_estimated': base_value <= 0,
        }

    return result


@login_required
def sale_create_order(request, pk):
    """Create a new Order pre-filled from an AnthillSale, link it, then redirect to the order tab."""
    from django.urls import reverse as _reverse
    from .forms import OrderForm

    sale = get_object_or_404(AnthillSale, pk=pk)

    # Already has an order — just redirect
    if sale.order:
        return redirect(_reverse('sale_detail', kwargs={'pk': pk}) + '?tab=order')

    if request.method == 'POST':
        # Use the submitted POST data directly — the template already rendered all
        # fields (visible or hidden) with the correct pre-filled values, so the
        # browser sends everything we need.
        form = OrderForm(request.POST)
        if form.is_valid():
            order = form.save()
            sale.order = order
            sale.save(update_fields=['order'])
            return redirect(_reverse('sale_detail', kwargs={'pk': pk}) + '?tab=order')
        else:
            # Pass form errors back through sale_detail context
            context = _build_sale_context(sale, request.user)
            context['active_tab'] = 'order'
            context['create_order_form'] = form
            # Surface all fields that have errors, plus always show designer
            context['missing_order_fields'] = set(form.errors.keys()) | {'designer'}
            return render(request, 'stock_take/sale_detail.html', context)


@login_required
def sale_save_order_fields(request, pk):
    """Save order-level fields (CAD/system number, designer) edited from the Sale tab.

    When the sale already has an Order, the provided fields are updated directly.
    When it has none, an Order is created and linked — but only when there's enough
    information to build a valid Order (a 6-digit sale number plus a valid CAD number).
    Otherwise a helpful error is returned describing what's still required.
    """
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required'})

    sale = get_object_or_404(AnthillSale.objects.select_related('customer', 'order'), pk=pk)

    try:
        import json
        data = json.loads(request.body or '{}')

        order = sale.order
        if order is not None:
            if 'customer_number' in data:
                order.customer_number = (data.get('customer_number') or '').strip()
            if 'designer_id' in data:
                designer_id = data.get('designer_id')
                order.designer = Designer.objects.get(id=designer_id) if designer_id else None
            order.save()
            return JsonResponse({'success': True})

        # No order yet — try to build one from the sale data plus the submitted edits.
        from .forms import OrderForm
        initial = _order_initial_from_sale(sale)
        form_data = {k: ('' if v is None else v) for k, v in initial.items()}
        if data.get('customer_number'):
            form_data['customer_number'] = data['customer_number'].strip()
        if data.get('designer_id'):
            form_data['designer'] = data['designer_id']
        # Total value may be edited in the Financial Summary and sent here directly,
        # so order creation doesn't depend on the parallel sale-save committing first.
        total_value = data.get('total_value_inc_vat')
        if total_value not in (None, ''):
            cleaned = str(total_value).replace('£', '').replace(',', '').strip()
            if cleaned:
                form_data['total_value_inc_vat'] = cleaned

        form = OrderForm(form_data)
        if not form.is_valid():
            parts = []
            for field, errs in form.errors.items():
                label = form.fields[field].label or field.replace('_', ' ').title()
                parts.append(f"{label}: {' '.join(errs)}")
            return JsonResponse({
                'success': False,
                'error': 'Not enough information to create an order yet — ' + ' | '.join(parts),
            })

        order = form.save()
        sale.order = order
        sale.save(update_fields=['order'])
        return JsonResponse({'success': True, 'order_created': True})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@login_required
def sale_set_anthill_id(request, pk):
    """Save a missing Anthill customer/sale ID on a sale and return the built URL.

    Used by the Anthill icon column on the sales list: clicking a greyed-out icon
    pops a small modal asking for the number, which is saved here so the link
    becomes permanent.
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    sale = get_object_or_404(AnthillSale, pk=pk)
    field = (request.POST.get('field') or '').strip()
    number = (request.POST.get('number') or '').strip()

    if not number:
        return JsonResponse({'success': False, 'error': 'Please enter a number.'}, status=400)

    if field == 'customer':
        sale.anthill_customer_id = number
        sale.save(update_fields=['anthill_customer_id'])
        url = f'https://sliderobes.anthillcrm.com/system/Customers/ViewCustomer.aspx?CustomerID={number}'
    elif field == 'sale':
        if AnthillSale.objects.exclude(pk=sale.pk).filter(anthill_activity_id=number).exists():
            return JsonResponse({'success': False, 'error': 'That sale ID is already in use.'}, status=400)
        sale.anthill_activity_id = number
        sale.save(update_fields=['anthill_activity_id'])
        url = f'https://sliderobes.anthillcrm.com/system/Orders/ViewOrder.aspx?OrderID={number}'
    else:
        return JsonResponse({'success': False, 'error': 'Invalid field.'}, status=400)

    return JsonResponse({'success': True, 'url': url})

    return redirect(_reverse('sale_detail', kwargs={'pk': pk}) + '?tab=order')


def _order_initial_from_sale(sale):
    """Derive OrderForm initial data from an AnthillSale. Shared by the create-order
    form and the auto-create-on-save flow for order-level fields edited on the Sale tab."""
    customer = sale.customer
    raw_cnum = (
        (customer.code if customer else None)
        or sale.anthill_customer_id
        or ''
    ).strip()
    if len(raw_cnum) == 5 and raw_cnum.isdigit():
        raw_cnum = '0' + raw_cnum
    customer_number = raw_cnum if (len(raw_cnum) == 6 and raw_cnum.startswith('0')) else ''

    first_name = (customer.first_name if customer else '') or ''
    last_name = (customer.last_name if customer else '') or ''
    if not first_name and not last_name and sale.customer_name:
        parts = sale.customer_name.strip().split(' ', 1)
        first_name = parts[0]
        last_name = parts[1] if len(parts) > 1 else ''

    # Auto-assign the designer from the contract number initials (e.g. BFS-NR-424804 -> NR -> Neil Robb)
    designer_id = ''
    inferred_designer_name = _infer_designer_name_from_contract(sale)
    if inferred_designer_name:
        matched_designer = Designer.objects.filter(name=inferred_designer_name).first()
        if matched_designer:
            designer_id = matched_designer.pk

    return {
        'customer': customer.pk if customer else '',
        'first_name': first_name,
        'last_name': last_name,
        'sale_number': sale.anthill_activity_id or '',
        'customer_number': customer_number,
        'order_date': sale.activity_date.date() if sale.activity_date else None,
        'fit_date': sale.fit_date,
        'address': (customer.address_1 or customer.address if customer else '') or '',
        'postcode': (customer.postcode if customer else '') or '',
        'anthill_id': (customer.anthill_customer_id if customer else '') or sale.anthill_customer_id or '',
        'total_value_inc_vat': sale.sale_value or '',
        'order_type': 'sale',
        'designer': designer_id,
    }


@login_required
def sale_detail(request, pk):
    """Display detailed view of a single Anthill event. Sale is the anchor page —
    the combined sale+order tabbed view always lives here."""
    sale = get_object_or_404(AnthillSale.objects.select_related('customer', 'order'), pk=pk)
    active_tab = request.GET.get('tab', 'sale')

    context = _build_sale_context(sale, request.user)
    context['active_tab'] = active_tab

    # Load order context into the combined page when an order is linked
    if sale.order:
        from .views import _build_order_context
        order_ctx = _build_order_context(sale.order, request)
        # Merge order context but don't overwrite sale keys (sale, coversheet, etc.)
        for key, val in order_ctx.items():
            if key not in context:
                context[key] = val

        # Build remedial "action required" suggestions from the original order's
        # items so the open/edit-remedial form can hint what likely needs ordering.
        # Each suggestion is a dict: {'label', 'sku', 'detail'} — sku/detail optional.
        def _dedupe_suggestions(rows, cap=20):
            out = []
            seen = set()
            for row in rows:
                label = (row.get('label') or '').strip()
                if not label:
                    continue
                key = (label, row.get('sku') or '')
                if key in seen:
                    continue
                seen.add(key)
                out.append({
                    'label': label,
                    'sku': (row.get('sku') or '').strip(),
                    'detail': (row.get('detail') or '').strip(),
                })
                if len(out) >= cap:
                    break
            return out

        context['rem_suggest_boards'] = _dedupe_suggestions(
            {'label': m} for m in context.get('order_materials', [])
        )
        context['rem_suggest_glass'] = _dedupe_suggestions(
            {'label': g.name, 'sku': g.sku} for g in context.get('glass_items', [])
        )
        _rem_accs = list(context.get('non_glass_accessories', [])) + list(context.get('raumplus_accessories', []))
        context['rem_suggest_accessories'] = _dedupe_suggestions(
            {
                'label': a.name,
                'sku': a.sku,
                'detail': (f"Stock {a.available_quantity:.0f}" if a.stock_item else ''),
            }
            for a in _rem_accs
        )
        context['rem_suggest_osdoors'] = _dedupe_suggestions(
            {
                'label': ' '.join(p for p in [(d.door_style or '').strip(), (d.style_colour or d.colour or '').strip()] if p),
                'detail': (f"{d.width:.0f}×{d.height:.0f}" if d.width and d.height else ''),
            }
            for d in sale.order.os_doors.all()
        )
    else:
        # No order yet — supply a pre-filled OrderForm so the order tab can render a create form
        from .forms import OrderForm
        initial_data = _order_initial_from_sale(sale)
        context['create_order_form'] = context.get('create_order_form') or OrderForm(initial=initial_data)

        # Fields the user must fill in: always show designer; show any field where we have no data
        _never_visible = {'order_type', 'customer', 'anthill_id', 'address', 'postcode', 'os_doors_required', 'all_items_ordered', 'job_finished'}
        missing = {'designer'}  # designer is never derivable from sale data
        for field_name, value in initial_data.items():
            if field_name not in _never_visible and not value:
                missing.add(field_name)
        # If the form is bound (POST validation failed), also surface all errored fields
        bound_form = context['create_order_form']
        if bound_form.is_bound:
            missing.update(bound_form.errors.keys())
        context['missing_order_fields'] = missing

    # Collect all purchase orders linked to this sale for the Purchase Orders tab
    if sale.order:
        _seen_po_ids = set()
        _sale_pos = []
        def _add_sale_po(po, label):
            if po is not None and po.pk not in _seen_po_ids:
                _seen_po_ids.add(po.pk)
                _sale_pos.append({'po': po, 'type': label})
        _add_sale_po(context.get('boards_purchase_order'), 'Boards')
        for _bpd in context.get('boards_po_list', []):
            _add_sale_po(_bpd.get('purchase_order'), 'Boards')
        _add_sale_po(context.get('os_doors_purchase_order'), 'OS Doors')
        for _apo in context.get('additional_os_doors_pos_list', []):
            _add_sale_po(_apo, 'OS Doors')
        for _proj in sale.order.po_projects.select_related('purchase_order').all():
            if _proj.purchase_order:
                _add_sale_po(_proj.purchase_order, 'Project')
        context['sale_purchase_orders'] = _sale_pos
    else:
        context['sale_purchase_orders'] = []

    _status = (sale.status or '').lower()
    context['hero_sale_badge_text'] = (sale.status or 'unknown').upper()
    context['hero_sale_badge_class'] = 'active' if _status in ('open', 'won') else ('danger' if _status in ('dead', 'cancelled') else 'inactive')

    return render(request, 'stock_take/sale_detail.html', context)



def _match_credits_to_payments(payments_list):
    """Match credit payments to positive payments by amount.

    ALL negative amounts count as discount (reduce the sale obligation).
    Credits that mirror a positive payment (same absolute amount) also
    remove that positive from total_paid — those entries represent the
    credit being applied, not real cash received.

    Returns (total_paid, discount).
    """
    from decimal import Decimal
    credits = []
    positives = []
    other_negatives = Decimal('0')  # non-credit negatives (adjustments, write-offs)
    for p in payments_list:
        amt = getattr(p, 'amount', None) or Decimal('0')
        ptype = getattr(p, 'payment_type', '') or ''
        if 'credit' in ptype.lower() and amt < 0:
            credits.append(abs(amt))
        elif amt > 0:
            positives.append(amt)
        elif amt < 0:
            other_negatives += amt

    # ALL credits are discount; other negatives (write-offs, final balance
    # adjustments, etc.) also reduce the sale obligation rather than
    # subtracting from total paid.
    discount = sum(credits, Decimal('0')) + abs(other_negatives)

    # Match credits to positives — matched positives are removed from paid
    # (they represent the credit being applied, not real cash)
    unmatched_positives = list(positives)
    for credit_amt in credits:
        for i, pay_amt in enumerate(unmatched_positives):
            if abs(pay_amt - credit_amt) < Decimal('0.50'):
                unmatched_positives.pop(i)
                break

    total_paid = sum(unmatched_positives, Decimal('0'))
    return max(total_paid, Decimal('0')), discount


def _refresh_xero_cache_linked_sales(customer):
    """Update linked_sales in the saved xero_invoices_data cache to reflect current DB state."""
    if not customer.xero_invoices_data:
        return
    invoices = customer.xero_invoices_data.get('invoices', [])
    if not invoices:
        return
    sales = list(
        customer.anthill_sales
        .exclude(activity_type__icontains='lead')
        .exclude(activity_type__icontains='enquir')
    )
    sale_map = {s.pk: s.contract_number or str(s.anthill_activity_id or s.pk) for s in sales}
    linked_payments = (
        AnthillPayment.objects
        .filter(sale__customer=customer)
        .exclude(xero_invoice_id='')
        .values_list('xero_invoice_id', 'sale_id')
        .distinct()
    )
    invoice_sale_map = {}
    for xid, sale_id in linked_payments:
        label = sale_map.get(sale_id, str(sale_id))
        invoice_sale_map.setdefault(xid, []).append(label)
    for inv in invoices:
        inv['linked_sales'] = invoice_sale_map.get(inv.get('invoice_id', ''), [])
    customer.xero_invoices_data['invoices'] = invoices
    customer.save(update_fields=['xero_invoices_data'])


def _recalculate_sale_financials(sale):
    """Recompute discount, balance_payable and paid_in_full using credit-payment matching."""
    from decimal import Decimal
    payments = list(sale.payments.filter(ignored=False))
    total_paid, discount = _match_credits_to_payments(payments)
    sale.discount = discount
    sale_value = sale.sale_value or Decimal('0')
    effective_value = sale_value - discount
    new_balance = max(effective_value - total_paid, Decimal('0'))
    sale.balance_payable = new_balance
    sale.paid_in_full = new_balance <= Decimal('0')
    sale.save(update_fields=['discount', 'balance_payable', 'paid_in_full'])

    # When the customer has more than one eligible sale, the in-isolation balance
    # above is only provisional — re-allocate the whole customer pool by fit date
    # and persist each sale's pooled balance so the figures stay consistent.
    if sale.customer_id:
        _recalculate_customer_financials(sale.customer)


def _recalculate_customer_financials(customer):
    """Allocate the customer's whole payment pool across their sales by the
    fit-date rule and persist each sale's balance.

    This is the customer-wide counterpart to ``_recalculate_sale_financials``:
    it writes every eligible sale's ``discount``/``balance_payable``/
    ``paid_in_full`` from the shared allocation so no sale is shown overpaid
    while another is outstanding.
    """
    from decimal import Decimal
    if not customer:
        return
    anchor = customer.anthill_sales.first()
    if not anchor:
        return
    pool = _build_customer_payment_pool(anchor)
    if not pool:
        return
    for e in pool['entries']:
        s = e['sale']
        new_balance = e['outstanding']
        new_paid = new_balance <= Decimal('0')
        if s.discount != e['discount'] or s.balance_payable != new_balance or s.paid_in_full != new_paid:
            s.discount = e['discount']
            s.balance_payable = new_balance
            s.paid_in_full = new_paid
            s.save(update_fields=['discount', 'balance_payable', 'paid_in_full'])



def _rebalance_invoice_split(invoice_id):
    """Split each Xero payment equally across every sale linked to the same invoice.

    When a single Xero invoice is linked to N sales, the payment value should be
    shared equally between them — e.g. a £2,000 invoice linked to 2 sales gives
    each sale £1,000. The un-split value is preserved in ``full_amount`` so the
    split can be recomputed whenever a sale is linked or unlinked.
    """
    from decimal import Decimal, ROUND_HALF_UP
    if not invoice_id:
        return
    rows = list(
        AnthillPayment.objects.filter(xero_invoice_id=invoice_id, source='xero')
    )
    if not rows:
        return
    sale_count = len({r.sale_id for r in rows})
    if sale_count < 1:
        return

    affected_sale_ids = set()
    for r in rows:
        base = r.full_amount if r.full_amount is not None else (r.amount or Decimal('0'))
        share = (base / sale_count).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        if r.full_amount != base or r.amount != share:
            r.full_amount = base
            r.amount = share
            r.save(update_fields=['full_amount', 'amount'])
        affected_sale_ids.add(r.sale_id)

    for sale in AnthillSale.objects.filter(pk__in=affected_sale_ids):
        _recalculate_sale_financials(sale)



@login_required
def xero_search_invoices(request, pk):
    """Search Xero for invoices matching this sale's customer name."""
    import re as _re
    from decimal import Decimal
    from .services.xero_api import find_contact_by_name, get_invoices_for_contact, search_contacts_by_name

    sale = get_object_or_404(AnthillSale.objects.select_related('customer'), pk=pk)
    customer_name = (sale.customer.name if sale.customer else sale.customer_name) or ''
    if not customer_name:
        return JsonResponse({'success': False, 'error': 'No customer name on this sale'}, status=400)

    # Find Xero contact
    contact_id = find_contact_by_name(customer_name)
    if not contact_id:
        # Try partial search and return candidates
        candidates = search_contacts_by_name(customer_name)
        if not candidates:
            return JsonResponse({'success': True, 'invoices': [], 'message': f'No Xero contact found for "{customer_name}"'})
        # Use first candidate as best match
        contact_id = candidates[0].get('ContactID', '')
        if not contact_id:
            return JsonResponse({'success': True, 'invoices': [], 'message': f'No Xero contact found for "{customer_name}"'})

    invoices = get_invoices_for_contact(contact_id)

    # Already-linked invoice IDs for this sale
    linked_ids = set(sale.payments.values_list('xero_invoice_id', flat=True))

    # Invoices linked to OTHER sales — map invoice_id -> sale info
    other_links = (
        AnthillPayment.objects
        .exclude(sale=sale)
        .exclude(xero_invoice_id='')
        .values_list('xero_invoice_id', 'sale__contract_number', 'sale__anthill_activity_id')
        .distinct()
    )
    other_sale_map = {}
    for xid, contract, activity_id in other_links:
        label = contract or str(activity_id or '')
        if xid not in other_sale_map:
            other_sale_map[xid] = label

    # Map each of this customer's sale contract numbers to its pk so an invoice
    # can be routed to the sale whose contract matches the invoice reference
    # (used by "Link All" to sync every invoice to the correct sale, not just
    # the current one).
    cust_sales = []
    if sale.customer:
        cust_sales = list(
            sale.customer.anthill_sales
            .exclude(activity_type__icontains='lead')
            .exclude(activity_type__icontains='enquir')
        )
    contract_to_pk = {}
    for s in cust_sales:
        cn = (s.contract_number or '').strip().upper()
        if cn:
            contract_to_pk[cn] = s.pk
    sale_label_map = {
        s.pk: (s.contract_number or str(s.anthill_activity_id or s.pk)) for s in cust_sales
    }

    def _match_sale(reference, inv_number):
        ref = (reference or '').strip().upper()
        num = (inv_number or '').strip().upper()
        if ref and ref in contract_to_pk:
            return contract_to_pk[ref]
        if ref:
            for cn, spk in contract_to_pk.items():
                if cn in ref or ref in cn:
                    return spk
        if num:
            for cn, spk in contract_to_pk.items():
                if cn in num or num in cn:
                    return spk
        # Fall back to the current sale when nothing matches.
        return sale.pk

    def _parse_date(raw):
        if not raw:
            return None
        ms = _re.search(r'/Date\((\d+)', raw)
        if ms:
            import datetime
            ts = int(ms.group(1)) / 1000
            return datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc).strftime('%Y-%m-%d')
        return raw[:10] if len(raw) >= 10 else raw

    results = []
    for inv in invoices:
        inv_id = inv.get('InvoiceID', '')
        status = inv.get('Status', '')
        if status.upper() in ('DELETED', 'DRAFT'):
            continue
        total = Decimal(str(inv.get('Total', 0)))
        amount_due = Decimal(str(inv.get('AmountDue', 0)))
        amount_paid = Decimal(str(inv.get('AmountPaid', 0)))
        match_pk = _match_sale(inv.get('Reference', ''), inv.get('InvoiceNumber', ''))
        results.append({
            'invoice_id': inv_id,
            'invoice_number': inv.get('InvoiceNumber', ''),
            'reference': inv.get('Reference', ''),
            'date': _parse_date(inv.get('DateString') or inv.get('Date')),
            'due_date': _parse_date(inv.get('DueDateString') or inv.get('DueDate')),
            'total': str(total),
            'amount_paid': str(amount_paid),
            'amount_due': str(amount_due),
            'status': status,
            'already_linked': inv_id in linked_ids,
            'linked_to_other': other_sale_map.get(inv_id, ''),
            'match_sale_pk': match_pk,
            'match_sale_label': sale_label_map.get(match_pk, ''),
        })

    # Sort: authorised first, then by date desc
    results.sort(key=lambda x: (0 if x['status'].upper() == 'AUTHORISED' else 1, x['date'] or ''), reverse=False)

    return JsonResponse({'success': True, 'invoices': results, 'customer_name': customer_name})


@login_required
def xero_link_invoice(request, pk):
    """Fetch a Xero invoice's payments and link them to this sale."""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required'}, status=405)

    from decimal import Decimal
    from .services.xero_api import get_sale_payments_from_xero, get_invoice_with_payments
    import re as _re, datetime, logging
    logger = logging.getLogger(__name__)

    sale = get_object_or_404(AnthillSale.objects.select_related('customer'), pk=pk)

    try:
        data = json.loads(request.body)
        invoice_id = data.get('invoice_id', '').strip()
    except (json.JSONDecodeError, KeyError):
        return JsonResponse({'success': False, 'error': 'Invalid JSON'}, status=400)

    if not invoice_id:
        return JsonResponse({'success': False, 'error': 'No invoice_id provided'}, status=400)

    # Check if already linked
    if sale.payments.filter(xero_invoice_id=invoice_id).exists():
        return JsonResponse({'success': False, 'error': 'Invoice already linked to this sale'}, status=400)

    try:
        # Fetch full invoice detail with payments
        inv = get_invoice_with_payments(invoice_id)
        if not inv:
            return JsonResponse({'success': False, 'error': 'Could not fetch invoice from Xero'}, status=400)

        def _parse_xero_date(raw):
            if not raw:
                return None
            ms = _re.search(r'/Date\((\d+)', raw)
            if ms:
                ts = int(ms.group(1)) / 1000
                return datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc)
            for fmt in ('%Y-%m-%dT%H:%M:%S', '%Y-%m-%d'):
                try:
                    return datetime.datetime.strptime(raw[:10], fmt)
                except ValueError:
                    pass
            return None

        inv_id = inv.get('InvoiceID', '')
        inv_number = inv.get('InvoiceNumber', '')
        inv_total = Decimal(str(inv.get('Total', 0)))
        inv_amount_due = Decimal(str(inv.get('AmountDue', 0)))
        inv_status = inv.get('Status', '')
        payments_list = inv.get('Payments', [])
        created_count = 0

        if payments_list:
            for p in payments_list:
                if (p.get('Status', '') or '').upper() == 'CANCELLED':
                    continue
                pid = p.get('PaymentID', '')
                amount = Decimal(str(p.get('Amount', 0)))
                date = _parse_xero_date(p.get('Date'))
                ref = p.get('Reference', '') or 'Payment'

                defaults = {
                    'source': 'xero',
                    'xero_invoice_id': inv_id,
                    'xero_invoice_number': inv_number,
                    'invoice_total': inv_total,
                    'invoice_amount_due': inv_amount_due,
                    'invoice_status': inv_status,
                    'payment_type': ref,
                    'date': date,
                    'amount': amount,
                    'status': inv_status,
                    'location': 'manual-link',
                    'user_name': '',
                }

                if pid:
                    AnthillPayment.objects.update_or_create(
                        sale=sale, anthill_payment_id=pid, defaults=defaults)
                else:
                    AnthillPayment.objects.update_or_create(
                        sale=sale, xero_invoice_id=inv_id, date=date, defaults=defaults)
                created_count += 1
        else:
            # No individual payments — create invoice-level summary
            amount_paid = Decimal(str(inv.get('AmountPaid', 0)))
            AnthillPayment.objects.update_or_create(
                sale=sale,
                xero_invoice_id=inv_id,
                date=None,
                defaults={
                    'source': 'xero',
                    'xero_invoice_id': inv_id,
                    'xero_invoice_number': inv_number,
                    'invoice_total': inv_total,
                    'invoice_amount_due': inv_amount_due,
                    'invoice_status': inv_status,
                    'payment_type': 'Invoice Payment',
                    'date': None,
                    'amount': amount_paid,
                    'status': inv_status,
                    'location': 'manual-link',
                    'user_name': '',
                })
            created_count = 1

        # Split this invoice equally across every sale it is linked to.
        _rebalance_invoice_split(inv_id)

        return JsonResponse({
            'success': True,
            'message': f'Linked {inv_number} — {created_count} payment(s) imported',
            'payments_created': created_count,
        })
    except Exception as e:
        logger.exception('xero_link_invoice failed for sale %s, invoice %s', pk, invoice_id)
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@login_required
def add_manual_payment(request, pk):
    """Add one or more manual payments to a sale (POST, JSON body)."""
    from django.views.decorators.http import require_POST
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required'}, status=405)

    sale = get_object_or_404(AnthillSale, pk=pk)
    try:
        data = json.loads(request.body)
        payments_data = data.get('payments', [])
    except (json.JSONDecodeError, KeyError):
        return JsonResponse({'success': False, 'error': 'Invalid JSON'}, status=400)

    from decimal import Decimal, InvalidOperation
    from datetime import datetime
    from django.utils import timezone

    created_ids = []
    errors = []
    for i, p in enumerate(payments_data):
        # Parse date — accept "dd/mm/yy HH:MM", "dd/mm/yyyy HH:MM", "dd/mm/yy", "dd/mm/yyyy"
        date_val = None
        raw_date = str(p.get('date', '')).strip()
        for fmt in ('%d/%m/%y %H:%M', '%d/%m/%Y %H:%M', '%d/%m/%y', '%d/%m/%Y'):
            try:
                date_val = timezone.make_aware(datetime.strptime(raw_date, fmt))
                break
            except ValueError:
                continue

        amount_str = str(p.get('amount', '')).replace('£', '').replace(',', '').strip()
        try:
            amount = Decimal(amount_str) if amount_str else None
        except InvalidOperation:
            errors.append(f'Row {i + 1}: invalid amount "{amount_str}"')
            continue

        payment = AnthillPayment.objects.create(
            sale=sale,
            source='manual',
            payment_type=str(p.get('type', '')).strip(),
            date=date_val,
            location=str(p.get('location', '')).strip(),
            user_name=str(p.get('user', '')).strip(),
            amount=amount,
            status=str(p.get('status', '')).strip(),
        )
        created_ids.append(payment.pk)

    if errors and not created_ids:
        return JsonResponse({'success': False, 'errors': errors}, status=400)

    _recalculate_sale_financials(sale)
    return JsonResponse({'success': True, 'created': len(created_ids), 'errors': errors})


@login_required
def delete_manual_payment(request, pk, payment_pk):
    """Delete a manual payment (POST)."""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required'}, status=405)

    payment = get_object_or_404(AnthillPayment, pk=payment_pk, sale__pk=pk, source='manual')
    sale = payment.sale
    payment.delete()
    _recalculate_sale_financials(sale)
    return JsonResponse({'success': True})


@login_required
def delete_xero_payment(request, pk, payment_pk):
    """Delete a Xero payment record (POST). Used when a payment was
    incorrectly matched to this sale (e.g. reference substring collision)."""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required'}, status=405)

    payment = get_object_or_404(AnthillPayment, pk=payment_pk, sale__pk=pk, source='xero')
    sale = payment.sale
    invoice_id = payment.xero_invoice_id
    payment.delete()
    _recalculate_sale_financials(sale)
    # Re-split the invoice across its remaining linked sales so each gets a
    # larger share now that this sale no longer counts.
    _rebalance_invoice_split(invoice_id)
    return JsonResponse({'success': True})


@login_required
def toggle_payment_ignored(request, pk, payment_pk):
    """Toggle the 'ignored' flag on a payment (POST). Returns new state."""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required'}, status=405)

    payment = get_object_or_404(AnthillPayment, pk=payment_pk, sale__pk=pk)
    payment.ignored = not payment.ignored
    payment.save(update_fields=['ignored'])
    _recalculate_sale_financials(payment.sale)
    return JsonResponse({'success': True, 'ignored': payment.ignored})


@login_required
def split_payment(request, pk, payment_pk):
    """Split a payment between the current sale and another sale (POST).

    Expects JSON body:
        {
            "target_sale_pk": int,   # AnthillSale.pk to receive the split portion
            "amount": "123.45"       # Amount to move to the target sale
        }

    The original payment is reduced by `amount` and a new payment record
    is created on the target sale for `amount`, preserving all Xero metadata.
    """
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required'}, status=405)

    import json
    from decimal import Decimal, InvalidOperation

    payment = get_object_or_404(AnthillPayment, pk=payment_pk, sale__pk=pk)
    source_sale = payment.sale

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'success': False, 'error': 'Invalid JSON'}, status=400)

    target_sale_pk = data.get('target_sale_pk')
    if not target_sale_pk:
        return JsonResponse({'success': False, 'error': 'target_sale_pk required'}, status=400)

    target_sale = get_object_or_404(AnthillSale, pk=target_sale_pk)

    try:
        move_amount = Decimal(str(data.get('amount', '0')))
    except (InvalidOperation, TypeError):
        return JsonResponse({'success': False, 'error': 'Invalid amount'}, status=400)

    if move_amount <= 0:
        return JsonResponse({'success': False, 'error': 'Amount must be positive'}, status=400)

    if move_amount >= payment.amount:
        return JsonResponse({
            'success': False,
            'error': f'Amount must be less than the full payment (£{payment.amount:.2f})'
        }, status=400)

    # Reduce the original payment
    payment.amount -= move_amount
    payment.save(update_fields=['amount'])

    # Create the split portion on the target sale
    AnthillPayment.objects.create(
        sale=target_sale,
        source=payment.source,
        xero_invoice_id=payment.xero_invoice_id,
        xero_invoice_number=payment.xero_invoice_number,
        invoice_total=payment.invoice_total,
        invoice_amount_due=payment.invoice_amount_due,
        invoice_status=payment.invoice_status,
        anthill_payment_id=(
            f'{payment.anthill_payment_id}_split' if payment.anthill_payment_id else ''
        ),
        payment_type=payment.payment_type,
        date=payment.date,
        amount=move_amount,
        status=payment.status,
        location=payment.location,
        user_name=payment.user_name,
    )

    # Recalculate financials for both sales
    _recalculate_sale_financials(source_sale)
    _recalculate_sale_financials(target_sale)

    return JsonResponse({
        'success': True,
        'kept_amount': str(payment.amount),
        'moved_amount': str(move_amount),
    })


@login_required
def scrape_anthill_payments(request, pk):
    """
    Server-side scrape of the paymentsTable from the Anthill CRM activity page.

    Uses only the Python standard library (html.parser) — no third-party packages
    required.  Authenticates using ANTHILL_USERNAME / ANTHILL_PASSWORD env vars,
    fetches the sale's activity page, parses the ``paymentsTable`` HTML table, and
    returns the rows as JSON so the client can review and selectively import them.
    """
    import os
    import re
    import requests as req_lib
    from html.parser import HTMLParser

    # ── Minimal HTML parser using stdlib only ─────────────────────────────────
    class _TableParser(HTMLParser):
        """Extract rows from the first <table class="paymentsTable">."""

        def __init__(self):
            super().__init__(convert_charrefs=True)
            self._in_target = False   # inside the paymentsTable
            self._depth = 0           # nesting depth inside the table
            self._in_row = False
            self._in_cell = False
            self._current_row = []
            self._current_cell_parts = []
            self.rows = []            # [[cell_text, ...], ...]
            self.found = False

        def handle_starttag(self, tag, attrs):
            attr_dict = dict(attrs)
            if tag == 'table':
                classes = attr_dict.get('class', '')
                if 'paymentsTable' in classes.split():
                    self._in_target = True
                    self._depth = 1
                    return
            if self._in_target:
                if tag == 'table':
                    self._depth += 1
                elif tag == 'tr':
                    self._in_row = True
                    self._current_row = []
                elif tag in ('td', 'th'):
                    self._in_cell = True
                    self._current_cell_parts = []

        def handle_endtag(self, tag):
            if not self._in_target:
                return
            if tag == 'table':
                self._depth -= 1
                if self._depth == 0:
                    self._in_target = False
                    self.found = True
            elif tag == 'tr' and self._in_row:
                self._in_row = False
                self.rows.append(self._current_row)
                self._current_row = []
            elif tag in ('td', 'th') and self._in_cell:
                self._in_cell = False
                text = ' '.join(self._current_cell_parts).strip()
                # Collapse internal whitespace
                text = re.sub(r'\s+', ' ', text).strip()
                self._current_row.append(text)
                self._current_cell_parts = []

        def handle_data(self, data):
            if self._in_cell:
                stripped = data.strip()
                if stripped:
                    self._current_cell_parts.append(stripped)

    # ──────────────────────────────────────────────────────────────────────────

    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required'}, status=405)

    sale = get_object_or_404(AnthillSale, pk=pk)

    username = os.getenv('ANTHILL_USER_USERNAME')
    password = os.getenv('ANTHILL_USER_PASSWORD')
    subdomain = os.getenv('ANTHILL_SUBDOMAIN', 'sliderobes')

    if not username or not password:
        return JsonResponse({
            'success': False,
            'error': 'ANTHILL_USER_USERNAME / ANTHILL_USER_PASSWORD are not set in the environment.',
        })

    base_url = f'https://{subdomain}.anthillcrm.com'
    sale_url = f'{base_url}/system/Orders/ViewOrder.aspx?OrderID={sale.anthill_activity_id}'

    session = req_lib.Session()
    session.headers['User-Agent'] = (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'
    )

    try:
        # ── Step 1: attempt to load the sale page ─────────────────────────────
        resp = session.get(sale_url, timeout=20, allow_redirects=True)

        # ── Step 2: if redirected to login/signin, authenticate then retry ──────
        def _is_auth_page(url):
            u = url.lower()
            return 'login' in u or 'signin' in u or '/sign-in' in u

        if _is_auth_page(resp.url) or resp.status_code in (401, 403):
            # Parse the login form with the same stdlib parser
            class _FormParser(HTMLParser):
                def __init__(self):
                    super().__init__(convert_charrefs=True)
                    self._in_form = False
                    self.action = ''
                    self.fields = {}

                def handle_starttag(self, tag, attrs):
                    attr_dict = dict(attrs)
                    if tag == 'form':
                        self._in_form = True
                        self.action = attr_dict.get('action', '')
                    elif tag == 'input' and self._in_form:
                        name = attr_dict.get('name', '').strip()
                        value = attr_dict.get('value', '')
                        if name:
                            self.fields[name] = value

                def handle_endtag(self, tag):
                    if tag == 'form':
                        self._in_form = False

            fp = _FormParser()
            fp.feed(resp.text)

            payload = dict(fp.fields)
            login_post_url = resp.url

            if fp.action:
                action = fp.action.strip()
                if action.startswith('http'):
                    login_post_url = action
                elif action.startswith('/'):
                    login_post_url = base_url + action
                else:
                    login_post_url = resp.url.rsplit('/', 1)[0] + '/' + action

            # Fill username and password by matching common field-name patterns
            u_filled = p_filled = False
            for name in list(payload.keys()):
                nl = name.lower()
                if not u_filled and any(k in nl for k in ('user', 'email', 'login')) \
                        and 'view' not in nl and 'event' not in nl:
                    payload[name] = username
                    u_filled = True
                elif not p_filled and 'pass' in nl:
                    payload[name] = password
                    p_filled = True

            if not u_filled:
                payload['username'] = username
            if not p_filled:
                payload['password'] = password

            login_resp = session.post(login_post_url, data=payload, timeout=20, allow_redirects=True)

            if _is_auth_page(login_resp.url):
                return JsonResponse({
                    'success': False,
                    'error': 'Anthill login failed — please check ANTHILL_USERNAME and ANTHILL_PASSWORD.',
                })

            resp = session.get(sale_url, timeout=20)

    except req_lib.exceptions.RequestException as exc:
        return JsonResponse({'success': False, 'error': f'Network error contacting Anthill: {exc}'})

    # ── Step 3: parse the paymentsTable from the main page ──────────────────
    # The Payments tab uses onclick="return switchTo(this, '#pnlPayments');"
    # with href="#" — it is a pure client-side visibility toggle, not a
    # separate URL.  The paymentsTable is already embedded in the original
    # page HTML; we just need to parse it directly from resp.text.
    try:
        parser = _TableParser()
        parser.feed(resp.text)

        diag = {
            'final_url': resp.url,
            'http_status': resp.status_code,
            'raw_has_payments_table': 'paymentsTable' in resp.text,
            'html_sample': resp.text[:5000].replace('\n', ' ').replace('\r', ''),
        }
        title_match = re.search(r'<title[^>]*>([^<]{0,120})</title>', resp.text, re.IGNORECASE)
        if title_match:
            diag['page_title'] = title_match.group(1).strip()

        if not parser.found:
            return JsonResponse({
                'success': False,
                'error': (
                    'Payments table not found on the Anthill page. '
                    'The page may still require login, or the table structure may have changed.'
                ),
                'diag': diag,
            })

        payments = []
        for row in parser.rows[1:]:   # skip the header row (th cells)
            if len(row) < 5:
                continue
            # Skip rows that are only action links (edit / delete / receipt etc.)
            action_words = {'edit', 'delete', 'receipt', 'unconfirm', 'confirm', 'view'}
            non_empty = [c for c in row if c]
            if non_empty and all(c.lower() in action_words for c in non_empty):
                continue

            payment_type = row[0]
            date_str     = row[1]
            location     = row[2]
            user         = row[3]
            amount_str   = row[4]
            status       = row[5].strip() if len(row) > 5 else ''

            amount_clean = amount_str.replace('£', '').replace(',', '').strip()

            if not payment_type or not amount_clean:
                continue

            payments.append({
                'type': payment_type,
                'date': date_str,
                'location': location,
                'user': user,
                'amount': amount_clean,
                'status': status,
            })

        return JsonResponse({'success': True, 'payments': payments})

    except Exception as exc:
        logger.exception('scrape_anthill_payments failed for sale pk=%s', pk)
        return JsonResponse({
            'success': False,
            'error': f'Error processing Anthill response: {exc}',
        }, status=500)


@login_required
def customer_manage_payments(request, pk):
    """Cross-sale payment management for a customer.

    Shows all sales with their payments, allows moving payments between
    sales and splitting payments across multiple sales.
    """
    from decimal import Decimal

    customer = get_object_or_404(Customer, pk=pk)

    # Get sales only (exclude leads, enquiries, and other non-sale activity types)
    sales = list(
        customer.anthill_sales
        .select_related('order')
        .exclude(activity_type__icontains='lead')
        .exclude(activity_type__icontains='enquir')
        .order_by('-activity_date')
    )

    all_customer_payments = list(
        AnthillPayment.objects
        .filter(sale__customer=customer)
        .order_by('date', 'id')
    )

    def _payment_split_group_key(payment):
        # Group split/distributed payments so UI can show they belong together.
        if (payment.payment_type or '').strip() == 'Xero Distribution' or (payment.location or '').strip().lower() == 'distribute':
            ts = payment.created_at.strftime('%Y%m%d%H%M%S') if payment.created_at else ''
            return f'distribute:{ts}:{payment.source}:{payment.date}'

        # For split payments: extract base ID (strip any _split/_copy suffix)
        anthill_pid = (payment.anthill_payment_id or '').strip()
        if not anthill_pid:
            return None
        
        # Extract base UUID (everything before first _split or _copy)
        base_id = anthill_pid.split('_split')[0].split('_copy')[0]
        if base_id != anthill_pid:
            # This payment has a suffix, so it's definitely a split/copy
            return f'split:{base_id}'
        
        # Check if there are other payments with this base_id + suffix
        if any(p.anthill_payment_id and (p.anthill_payment_id.startswith(base_id + '_split') or p.anthill_payment_id.startswith(base_id + '_copy')) for p in all_customer_payments if p.pk != payment.pk):
            return f'split:{base_id}'
        
        return None

    payments_by_sale = {}
    for payment in all_customer_payments:
        payments_by_sale.setdefault(payment.sale_id, []).append(payment)

    split_groups = {}
    for payment in all_customer_payments:
        group_key = _payment_split_group_key(payment)
        if not group_key:
            continue
        split_groups.setdefault(group_key, []).append(payment.pk)

    # Only mark a group as truly split if payments are on different sales
    split_group_meta = {}
    for group_key, payment_ids in split_groups.items():
        if len(payment_ids) < 2:
            continue
        # Get the sales for these payments
        payment_objs = [p for p in all_customer_payments if p.pk in payment_ids]
        sales_in_group = set(p.sale_id for p in payment_objs)
        # Only mark as split if payments span multiple sales
        if len(sales_in_group) >= 2:
            label = 'Distributed' if group_key.startswith('distribute:') else 'Split'
            for pid in payment_ids:
                split_group_meta[pid] = {'label': label, 'count': len(payment_ids)}

    sales_data = []
    grand_total_value = Decimal('0')
    grand_total_paid = Decimal('0')
    grand_total_outstanding = Decimal('0')

    for sale in sales:
        all_payments = list(payments_by_sale.get(sale.pk, []))
        for payment in all_payments:
            meta = split_group_meta.get(payment.pk)
            payment.split_group_label = meta['label'] if meta else ''
            payment.split_group_count = meta['count'] if meta else 0
        active_payments = [p for p in all_payments if not p.ignored]
        total_paid, discount = _match_credits_to_payments(active_payments)
        sale_value = sale.sale_value or Decimal('0')
        effective_value = sale_value - discount
        outstanding = max(effective_value - total_paid, Decimal('0'))
        overpayment = max(total_paid - effective_value, Decimal('0'))
        payment_pct = int(min(total_paid / effective_value * 100, 100)) if effective_value > 0 else 0

        grand_total_value += effective_value
        grand_total_paid += total_paid
        grand_total_outstanding += outstanding

        sales_data.append({
            'sale': sale,
            'payments': all_payments,
            'total_paid': total_paid,
            'discount': discount,
            'effective_value': effective_value,
            'outstanding': outstanding,
            'overpayment': overpayment,
            'payment_pct': payment_pct,
        })

    # Refresh linked_sales in cached Xero data to reflect current DB state
    _refresh_xero_cache_linked_sales(customer)

    context = {
        'customer': customer,
        'sales_data': sales_data,
        'grand_total_value': grand_total_value,
        'grand_total_paid': grand_total_paid,
        'grand_total_outstanding': grand_total_outstanding,
        'saved_xero_data': customer.xero_invoices_data,
        'saved_anthill_data': customer.anthill_payments_data,
        'last_payment_search': customer.last_payment_search,
    }

    return render(request, 'stock_take/customer_manage_payments.html', context)


@login_required
def customer_xero_search(request, pk):
    """Search Xero for all invoices belonging to this customer's contact."""
    import re as _re
    from decimal import Decimal
    from .services.xero_api import find_contact_by_name, get_invoices_for_contact, search_contacts_by_name

    customer = get_object_or_404(Customer, pk=pk)
    customer_name = customer.name or ''
    if not customer_name:
        return JsonResponse({'success': False, 'error': 'No customer name'}, status=400)

    # Find Xero contact
    contact_id = find_contact_by_name(customer_name)
    if not contact_id:
        candidates = search_contacts_by_name(customer_name)
        if not candidates:
            return JsonResponse({'success': True, 'invoices': [], 'message': f'No Xero contact found for "{customer_name}"'})
        contact_id = candidates[0].get('ContactID', '')
        if not contact_id:
            return JsonResponse({'success': True, 'invoices': [], 'message': f'No Xero contact found for "{customer_name}"'})

    invoices = get_invoices_for_contact(contact_id)

    # Build map: invoice_id -> sale contract_number for ALL of this customer's sales
    sales = list(customer.anthill_sales.all())
    sale_map = {s.pk: s.contract_number or str(s.anthill_activity_id or s.pk) for s in sales}

    linked_payments = (
        AnthillPayment.objects
        .filter(sale__customer=customer)
        .exclude(xero_invoice_id='')
        .values_list('xero_invoice_id', 'sale_id')
        .distinct()
    )
    # invoice_id -> list of sale labels
    invoice_sale_map = {}
    for xid, sale_id in linked_payments:
        label = sale_map.get(sale_id, str(sale_id))
        invoice_sale_map.setdefault(xid, []).append(label)

    def _parse_date(raw):
        if not raw:
            return None
        ms = _re.search(r'/Date\((\d+)', raw)
        if ms:
            import datetime
            ts = int(ms.group(1)) / 1000
            return datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc).strftime('%Y-%m-%d')
        return raw[:10] if len(raw) >= 10 else raw

    results = []
    for inv in invoices:
        inv_id = inv.get('InvoiceID', '')
        status = inv.get('Status', '')
        if status.upper() in ('DELETED', 'DRAFT'):
            continue
        total = Decimal(str(inv.get('Total', 0)))
        amount_due = Decimal(str(inv.get('AmountDue', 0)))
        amount_paid = Decimal(str(inv.get('AmountPaid', 0)))
        linked_sales = invoice_sale_map.get(inv_id, [])
        results.append({
            'invoice_id': inv_id,
            'invoice_number': inv.get('InvoiceNumber', ''),
            'reference': inv.get('Reference', ''),
            'date': _parse_date(inv.get('DateString') or inv.get('Date')),
            'due_date': _parse_date(inv.get('DueDateString') or inv.get('DueDate')),
            'total': str(total),
            'amount_paid': str(amount_paid),
            'amount_due': str(amount_due),
            'status': status,
            'linked_sales': linked_sales,
        })

    results.sort(key=lambda x: (0 if x['status'].upper() == 'AUTHORISED' else 1, x['date'] or ''), reverse=False)

    # Auto-match: try to suggest the best sale for each unlinked invoice
    # by matching the invoice Reference against sale contract numbers
    contract_to_pk = {}
    for s in sales:
        cn = (s.contract_number or '').strip().upper()
        if cn:
            contract_to_pk[cn] = s.pk
    for r in results:
        if r['linked_sales']:
            r['suggested_sale_pk'] = None
            continue
        ref = (r.get('reference') or '').strip().upper()
        inv_num = (r.get('invoice_number') or '').strip().upper()
        matched_pk = None
        # Exact match on reference
        if ref and ref in contract_to_pk:
            matched_pk = contract_to_pk[ref]
        # Reference contains contract number or vice versa
        if not matched_pk and ref:
            for cn, spk in contract_to_pk.items():
                if cn in ref or ref in cn:
                    matched_pk = spk
                    break
        # Try invoice number against contract
        if not matched_pk and inv_num:
            for cn, spk in contract_to_pk.items():
                if cn in inv_num or inv_num in cn:
                    matched_pk = spk
                    break
        r['suggested_sale_pk'] = matched_pk

    # Return sales list so the JS can build a dropdown
    sales_list_json = [{'pk': s.pk, 'label': sale_map[s.pk]} for s in sales]

    # Persist to Customer for page-load display
    from django.utils import timezone as _tz
    customer.xero_invoices_data = {'invoices': results, 'customer_name': customer_name, 'sales': sales_list_json}
    customer.last_payment_search = _tz.now()
    customer.save(update_fields=['xero_invoices_data', 'last_payment_search'])

    return JsonResponse({'success': True, 'invoices': results, 'customer_name': customer_name, 'sales': sales_list_json})


@login_required
def customer_xero_link(request, pk):
    """Link a Xero invoice to a specific sale under this customer."""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required'}, status=405)

    from decimal import Decimal
    from .services.xero_api import get_invoice_with_payments
    import re as _re, datetime, logging
    logger = logging.getLogger(__name__)

    customer = get_object_or_404(Customer, pk=pk)

    try:
        data = json.loads(request.body)
        invoice_id = data.get('invoice_id', '').strip()
        sale_pk = data.get('sale_pk')
    except (json.JSONDecodeError, KeyError):
        return JsonResponse({'success': False, 'error': 'Invalid JSON'}, status=400)

    if not invoice_id or not sale_pk:
        return JsonResponse({'success': False, 'error': 'invoice_id and sale_pk required'}, status=400)

    sale = get_object_or_404(AnthillSale, pk=sale_pk, customer=customer)

    if sale.payments.filter(xero_invoice_id=invoice_id).exists():
        return JsonResponse({'success': False, 'error': 'Invoice already linked to this sale'}, status=400)

    try:
        inv = get_invoice_with_payments(invoice_id)
        if not inv:
            return JsonResponse({'success': False, 'error': 'Could not fetch invoice from Xero'}, status=400)

        def _parse_xero_date(raw):
            if not raw:
                return None
            ms = _re.search(r'/Date\((\d+)', raw)
            if ms:
                ts = int(ms.group(1)) / 1000
                return datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc)
            for fmt in ('%Y-%m-%dT%H:%M:%S', '%Y-%m-%d'):
                try:
                    return datetime.datetime.strptime(raw[:10], fmt)
                except ValueError:
                    pass
            return None

        inv_id = inv.get('InvoiceID', '')
        inv_number = inv.get('InvoiceNumber', '')
        inv_total = Decimal(str(inv.get('Total', 0)))
        inv_amount_due = Decimal(str(inv.get('AmountDue', 0)))
        inv_status = inv.get('Status', '')
        payments_list = inv.get('Payments', [])
        created_count = 0

        if payments_list:
            for p in payments_list:
                if (p.get('Status', '') or '').upper() == 'CANCELLED':
                    continue
                pid = p.get('PaymentID', '')
                amount = Decimal(str(p.get('Amount', 0)))
                date = _parse_xero_date(p.get('Date'))
                ref = p.get('Reference', '') or 'Payment'

                defaults = {
                    'source': 'xero',
                    'xero_invoice_id': inv_id,
                    'xero_invoice_number': inv_number,
                    'invoice_total': inv_total,
                    'invoice_amount_due': inv_amount_due,
                    'invoice_status': inv_status,
                    'payment_type': ref,
                    'date': date,
                    'amount': amount,
                    'status': inv_status,
                    'location': 'manual-link',
                    'user_name': '',
                }

                if pid:
                    AnthillPayment.objects.update_or_create(
                        sale=sale, anthill_payment_id=pid, defaults=defaults)
                else:
                    AnthillPayment.objects.update_or_create(
                        sale=sale, xero_invoice_id=inv_id, date=date, defaults=defaults)
                created_count += 1
        else:
            amount_paid = Decimal(str(inv.get('AmountPaid', 0)))
            AnthillPayment.objects.update_or_create(
                sale=sale,
                xero_invoice_id=inv_id,
                date=None,
                defaults={
                    'source': 'xero',
                    'xero_invoice_id': inv_id,
                    'xero_invoice_number': inv_number,
                    'invoice_total': inv_total,
                    'invoice_amount_due': inv_amount_due,
                    'invoice_status': inv_status,
                    'payment_type': 'Invoice Payment',
                    'date': None,
                    'amount': amount_paid,
                    'status': inv_status,
                    'location': 'manual-link',
                    'user_name': '',
                })
            created_count = 1

        # Split this invoice equally across every sale it is linked to.
        _rebalance_invoice_split(inv_id)

        sale_label = sale.contract_number or str(sale.anthill_activity_id or sale.pk)
        return JsonResponse({
            'success': True,
            'message': f'Linked {inv_number} to {sale_label} — {created_count} payment(s) imported',
            'payments_created': created_count,
        })
    except Exception as e:
        logger.exception('customer_xero_link failed for customer %s, invoice %s', pk, invoice_id)
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@login_required
def customer_anthill_scrape(request, pk):
    """Scrape Anthill CRM for payments across all of this customer's sales."""
    import os
    import re
    import requests as req_lib
    from html.parser import HTMLParser

    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required'}, status=405)

    customer = get_object_or_404(Customer, pk=pk)
    sales = list(
        customer.anthill_sales
        .exclude(activity_type__icontains='lead')
        .exclude(activity_type__icontains='enquir')
        .filter(anthill_activity_id__isnull=False)
        .order_by('-activity_date')
    )

    if not sales:
        return JsonResponse({'success': True, 'sales': []})

    username = os.getenv('ANTHILL_USER_USERNAME')
    password = os.getenv('ANTHILL_USER_PASSWORD')
    subdomain = os.getenv('ANTHILL_SUBDOMAIN', 'sliderobes')

    if not username or not password:
        return JsonResponse({
            'success': False,
            'error': 'ANTHILL_USER_USERNAME / ANTHILL_USER_PASSWORD are not set in the environment.',
        })

    base_url = f'https://{subdomain}.anthillcrm.com'

    # Reusable HTML parser class
    class _TableParser(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self._in_target = False
            self._depth = 0
            self._in_row = False
            self._in_cell = False
            self._current_row = []
            self._current_cell_parts = []
            self.rows = []
            self.found = False

        def handle_starttag(self, tag, attrs):
            attr_dict = dict(attrs)
            if tag == 'table':
                classes = attr_dict.get('class', '')
                if 'paymentsTable' in classes.split():
                    self._in_target = True
                    self._depth = 1
                    return
            if self._in_target:
                if tag == 'table':
                    self._depth += 1
                elif tag == 'tr':
                    self._in_row = True
                    self._current_row = []
                elif tag in ('td', 'th'):
                    self._in_cell = True
                    self._current_cell_parts = []

        def handle_endtag(self, tag):
            if not self._in_target:
                return
            if tag == 'table':
                self._depth -= 1
                if self._depth == 0:
                    self._in_target = False
                    self.found = True
            elif tag == 'tr' and self._in_row:
                self._in_row = False
                self.rows.append(self._current_row)
                self._current_row = []
            elif tag in ('td', 'th') and self._in_cell:
                self._in_cell = False
                text = ' '.join(self._current_cell_parts).strip()
                text = re.sub(r'\s+', ' ', text).strip()
                self._current_row.append(text)
                self._current_cell_parts = []

        def handle_data(self, data):
            if self._in_cell:
                stripped = data.strip()
                if stripped:
                    self._current_cell_parts.append(stripped)

    class _FormParser(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self._in_form = False
            self.action = ''
            self.fields = {}

        def handle_starttag(self, tag, attrs):
            attr_dict = dict(attrs)
            if tag == 'form':
                self._in_form = True
                self.action = attr_dict.get('action', '')
            elif tag == 'input' and self._in_form:
                name = attr_dict.get('name', '').strip()
                value = attr_dict.get('value', '')
                if name:
                    self.fields[name] = value

        def handle_endtag(self, tag):
            if tag == 'form':
                self._in_form = False

    # Create a session and authenticate once
    session = req_lib.Session()
    session.headers['User-Agent'] = (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'
    )

    def _is_auth_page(url):
        u = url.lower()
        return 'login' in u or 'signin' in u or '/sign-in' in u

    authenticated = False

    def ensure_authenticated(resp):
        nonlocal authenticated
        if authenticated:
            return resp
        if not (_is_auth_page(resp.url) or resp.status_code in (401, 403)):
            authenticated = True
            return resp

        fp = _FormParser()
        fp.feed(resp.text)

        payload = dict(fp.fields)
        login_post_url = resp.url

        if fp.action:
            action = fp.action.strip()
            if action.startswith('http'):
                login_post_url = action
            elif action.startswith('/'):
                login_post_url = base_url + action
            else:
                login_post_url = resp.url.rsplit('/', 1)[0] + '/' + action

        u_filled = p_filled = False
        for name in list(payload.keys()):
            nl = name.lower()
            if not u_filled and any(k in nl for k in ('user', 'email', 'login')) \
                    and 'view' not in nl and 'event' not in nl:
                payload[name] = username
                u_filled = True
            elif not p_filled and 'pass' in nl:
                payload[name] = password
                p_filled = True

        if not u_filled:
            payload['username'] = username
        if not p_filled:
            payload['password'] = password

        login_resp = session.post(login_post_url, data=payload, timeout=20, allow_redirects=True)

        if _is_auth_page(login_resp.url):
            return None  # Login failed
        authenticated = True
        return login_resp

    results = []
    action_words = {'edit', 'delete', 'receipt', 'unconfirm', 'confirm', 'view'}

    try:
        for sale in sales:
            if not sale.anthill_activity_id:
                continue
            sale_url = f'{base_url}/system/Orders/ViewOrder.aspx?OrderID={sale.anthill_activity_id}'
            resp = session.get(sale_url, timeout=20, allow_redirects=True)

            if not authenticated:
                auth_result = ensure_authenticated(resp)
                if auth_result is None:
                    return JsonResponse({'success': False, 'error': 'Anthill login failed'})
                resp = session.get(sale_url, timeout=20)

            parser = _TableParser()
            parser.feed(resp.text)

            if not parser.found:
                continue

            sale_label = sale.contract_number or str(sale.anthill_activity_id)
            payments = []
            for row in parser.rows[1:]:
                if len(row) < 5:
                    continue
                non_empty = [c for c in row if c]
                if non_empty and all(c.lower() in action_words for c in non_empty):
                    continue

                payment_type = row[0]
                date_str = row[1]
                location = row[2]
                user = row[3]
                amount_str = row[4]
                status = row[5].strip() if len(row) > 5 else ''

                amount_clean = amount_str.replace('£', '').replace(',', '').strip()
                if not payment_type or not amount_clean:
                    continue

                payments.append({
                    'type': payment_type,
                    'date': date_str,
                    'location': location,
                    'user': user,
                    'amount': amount_clean,
                    'status': status,
                })

            results.append({
                'sale_pk': sale.pk,
                'sale_label': sale_label,
                'payments': payments,
            })

    except req_lib.exceptions.RequestException as exc:
        return JsonResponse({'success': False, 'error': f'Network error contacting Anthill: {exc}'})

    # Persist to Customer for page-load display
    from django.utils import timezone as _tz
    customer.anthill_payments_data = {'sales': results}
    customer.last_payment_search = _tz.now()
    customer.save(update_fields=['anthill_payments_data', 'last_payment_search'])

    return JsonResponse({'success': True, 'sales': results})


@login_required
def customer_distribute_payments(request, pk):
    """Distribute a total amount across multiple sales, creating payment records.

    Expects JSON body:
        {
            "invoice_ids": [str, ...],     # Xero invoice IDs (for reference)
            "distributions": [
                {"sale_pk": int, "amount": str},
                ...
            ]
        }
    """
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required'}, status=405)

    customer = get_object_or_404(Customer, pk=pk)

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'success': False, 'error': 'Invalid JSON'}, status=400)

    distributions = data.get('distributions', [])
    invoice_ids = data.get('invoice_ids', [])
    if not distributions:
        return JsonResponse({'success': False, 'error': 'No distributions provided'}, status=400)

    from decimal import Decimal, InvalidOperation
    from django.utils import timezone as _tz

    invoice_ref = ', '.join(invoice_ids[:5])
    if len(invoice_ids) > 5:
        invoice_ref += f' (+{len(invoice_ids) - 5} more)'

    created = 0
    errors = []

    for dist in distributions:
        sale_pk = dist.get('sale_pk')
        amount_str = dist.get('amount', '0')

        try:
            amount = Decimal(str(amount_str))
        except (InvalidOperation, ValueError):
            errors.append(f'Invalid amount for sale {sale_pk}')
            continue

        if amount <= 0:
            continue

        try:
            sale = AnthillSale.objects.get(pk=sale_pk, customer=customer)
        except AnthillSale.DoesNotExist:
            errors.append(f'Sale {sale_pk} not found')
            continue

        AnthillPayment.objects.create(
            sale=sale,
            source='xero',
            payment_type='Xero Distribution',
            amount=amount,
            date=_tz.now().date(),
            location='distribute',
            status='Confirmed',
        )
        created += 1
        _recalculate_sale_financials(sale)

    if errors:
        return JsonResponse({
            'success': created > 0,
            'created': created,
            'error': '; '.join(errors),
        })

    return JsonResponse({'success': True, 'created': created})


@login_required
def move_payment(request, pk, payment_pk):
    """Move an entire payment from one sale to another (POST).

    Expects JSON body:
        {
            "target_sale_pk": int   # AnthillSale.pk to receive the payment
        }

    The payment's sale FK is updated to the target sale. Both the source
    and target sale financials are recalculated.
    """
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required'}, status=405)

    customer = get_object_or_404(Customer, pk=pk)
    payment = get_object_or_404(AnthillPayment, pk=payment_pk, sale__customer=customer)
    source_sale = payment.sale

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'success': False, 'error': 'Invalid JSON'}, status=400)

    target_sale_pk = data.get('target_sale_pk')
    if not target_sale_pk:
        return JsonResponse({'success': False, 'error': 'target_sale_pk required'}, status=400)

    target_sale = get_object_or_404(AnthillSale, pk=target_sale_pk, customer=customer)

    if target_sale.pk == source_sale.pk:
        return JsonResponse({'success': False, 'error': 'Cannot move payment to the same sale'}, status=400)

    # Move the payment
    payment.sale = target_sale
    payment.save(update_fields=['sale'])

    # Recalculate financials for both sales
    _recalculate_sale_financials(source_sale)
    _recalculate_sale_financials(target_sale)

    return JsonResponse({
        'success': True,
        'payment_pk': payment.pk,
        'source_sale_pk': source_sale.pk,
        'target_sale_pk': target_sale.pk,
    })


@login_required
def cross_sale_split_payment(request, pk, payment_pk):
    """Split a payment from the manage-payments page (POST).

    Expects JSON body:
        {
            "target_sale_pk": int,
            "amount": "123.45"
        }
    """
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required'}, status=405)

    from decimal import Decimal, InvalidOperation

    customer = get_object_or_404(Customer, pk=pk)
    payment = get_object_or_404(AnthillPayment, pk=payment_pk, sale__customer=customer)
    source_sale = payment.sale

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'success': False, 'error': 'Invalid JSON'}, status=400)

    target_sale_pk = data.get('target_sale_pk')
    if not target_sale_pk:
        return JsonResponse({'success': False, 'error': 'target_sale_pk required'}, status=400)

    target_sale = get_object_or_404(AnthillSale, pk=target_sale_pk, customer=customer)

    if target_sale.pk == source_sale.pk:
        return JsonResponse({'success': False, 'error': 'Cannot split to the same sale'}, status=400)

    try:
        move_amount = Decimal(str(data.get('amount', '0')))
    except (InvalidOperation, TypeError):
        return JsonResponse({'success': False, 'error': 'Invalid amount'}, status=400)

    if move_amount <= 0:
        return JsonResponse({'success': False, 'error': 'Amount must be positive'}, status=400)

    if move_amount >= abs(payment.amount):
        return JsonResponse({
            'success': False,
            'error': f'Amount must be less than the full payment (£{abs(payment.amount):.2f}). Use Move instead.'
        }, status=400)

    # Reduce the original payment
    if payment.amount >= 0:
        payment.amount -= move_amount
        new_amount = move_amount
    else:
        payment.amount += move_amount
        new_amount = -move_amount
    payment.save(update_fields=['amount'])

    # Create the split portion on the target sale
    AnthillPayment.objects.create(
        sale=target_sale,
        source=payment.source,
        xero_invoice_id=payment.xero_invoice_id,
        xero_invoice_number=payment.xero_invoice_number,
        invoice_total=payment.invoice_total,
        invoice_amount_due=payment.invoice_amount_due,
        invoice_status=payment.invoice_status,
        anthill_payment_id=(
            f'{payment.anthill_payment_id}_split' if payment.anthill_payment_id else ''
        ),
        payment_type=payment.payment_type,
        date=payment.date,
        amount=new_amount,
        status=payment.status,
        location=payment.location,
        user_name=payment.user_name,
    )

    # Recalculate financials for both sales
    _recalculate_sale_financials(source_sale)
    _recalculate_sale_financials(target_sale)

    return JsonResponse({
        'success': True,
        'kept_amount': str(payment.amount),
        'moved_amount': str(move_amount),
    })


@login_required
def delete_payment_from_manage(request, pk, payment_pk):
    """Delete a payment from the manage-payments page (POST).

    Works for both manual and xero payments, scoped to the customer.
    """
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required'}, status=405)

    customer = get_object_or_404(Customer, pk=pk)
    payment = get_object_or_404(AnthillPayment, pk=payment_pk, sale__customer=customer)
    sale = payment.sale
    payment.delete()
    _recalculate_sale_financials(sale)
    _refresh_xero_cache_linked_sales(customer)
    return JsonResponse({'success': True})


@login_required
def bulk_delete_payments(request, pk):
    """Delete multiple payments at once from the manage-payments page (POST).

    Expects JSON body:
        {"payment_pks": [int, ...]}
    """
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required'}, status=405)

    customer = get_object_or_404(Customer, pk=pk)

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'success': False, 'error': 'Invalid JSON'}, status=400)

    pks = data.get('payment_pks', [])
    if not pks:
        return JsonResponse({'success': False, 'error': 'No payments specified'}, status=400)

    payments = AnthillPayment.objects.filter(pk__in=pks, sale__customer=customer)
    affected_sales = set(payments.values_list('sale_id', flat=True))
    deleted_count = payments.count()
    payments.delete()

    for sale in AnthillSale.objects.filter(pk__in=affected_sales):
        _recalculate_sale_financials(sale)

    _refresh_xero_cache_linked_sales(customer)
    return JsonResponse({'success': True, 'deleted': deleted_count})


@login_required
def bulk_copy_payments(request, pk):
    """Copy selected payments from manage-payments page (POST).

    Expects JSON body:
        {"payment_pks": [int, ...]}
    """
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required'}, status=405)

    customer = get_object_or_404(Customer, pk=pk)

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'success': False, 'error': 'Invalid JSON'}, status=400)

    pks = data.get('payment_pks', [])
    if not pks:
        return JsonResponse({'success': False, 'error': 'No payments specified'}, status=400)

    payments = list(
        AnthillPayment.objects
        .filter(pk__in=pks, sale__customer=customer)
        .select_related('sale')
    )

    if not payments:
        return JsonResponse({'success': False, 'error': 'No valid payments found'}, status=400)

    created = 0
    affected_sale_ids = set()

    with transaction.atomic():
        for payment in payments:
            copy_pid = f"{payment.anthill_payment_id}_copy" if payment.anthill_payment_id else ''
            AnthillPayment.objects.create(
                sale=payment.sale,
                source=payment.source,
                xero_invoice_id=payment.xero_invoice_id,
                xero_invoice_number=payment.xero_invoice_number,
                invoice_total=payment.invoice_total,
                invoice_amount_due=payment.invoice_amount_due,
                invoice_status=payment.invoice_status,
                anthill_payment_id=copy_pid,
                payment_type=payment.payment_type,
                date=payment.date,
                location=payment.location,
                user_name=payment.user_name,
                amount=payment.amount,
                status=payment.status,
                ignored=payment.ignored,
            )
            created += 1
            affected_sale_ids.add(payment.sale_id)

    for sale in AnthillSale.objects.filter(pk__in=affected_sale_ids):
        _recalculate_sale_financials(sale)

    _refresh_xero_cache_linked_sales(customer)
    return JsonResponse({'success': True, 'created': created})


@login_required
def edit_payment_from_manage(request, pk, payment_pk):
    """Edit payment fields from manage-payments page (POST)."""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required'}, status=405)

    customer = get_object_or_404(Customer, pk=pk)
    payment = get_object_or_404(AnthillPayment, pk=payment_pk, sale__customer=customer)

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'success': False, 'error': 'Invalid JSON'}, status=400)

    update_fields = []

    if 'payment_type' in data:
        payment.payment_type = str(data.get('payment_type', '')).strip()[:100]
        update_fields.append('payment_type')

    if 'location' in data:
        payment.location = str(data.get('location', '')).strip()[:100]
        update_fields.append('location')

    if 'user_name' in data:
        payment.user_name = str(data.get('user_name', '')).strip()[:150]
        update_fields.append('user_name')

    if 'status' in data:
        payment.status = str(data.get('status', '')).strip()[:50]
        update_fields.append('status')

    if 'amount' in data:
        try:
            payment.amount = Decimal(str(data.get('amount', '0')))
        except (InvalidOperation, ValueError):
            return JsonResponse({'success': False, 'error': 'Invalid amount'}, status=400)
        update_fields.append('amount')

    if 'date' in data:
        raw_date = str(data.get('date', '')).strip()
        if raw_date:
            from datetime import datetime as _dt
            try:
                payment.date = _dt.fromisoformat(raw_date)
            except ValueError:
                try:
                    payment.date = _dt.strptime(raw_date, '%Y-%m-%d')
                except ValueError:
                    return JsonResponse({'success': False, 'error': 'Invalid date format'}, status=400)
        else:
            payment.date = None
        update_fields.append('date')

    if 'ignored' in data:
        payment.ignored = bool(data.get('ignored'))
        update_fields.append('ignored')

    if not update_fields:
        return JsonResponse({'success': False, 'error': 'No fields provided'}, status=400)

    update_fields.append('updated_at')
    payment.save(update_fields=update_fields)

    _recalculate_sale_financials(payment.sale)
    _refresh_xero_cache_linked_sales(customer)

    return JsonResponse({
        'success': True,
        'payment': {
            'pk': payment.pk,
            'payment_type': payment.payment_type,
            'amount': str(payment.amount) if payment.amount is not None else '',
            'date': payment.date.date().isoformat() if payment.date else '',
            'status': payment.status,
            'location': payment.location,
            'user_name': payment.user_name,
            'ignored': payment.ignored,
        }
    })

