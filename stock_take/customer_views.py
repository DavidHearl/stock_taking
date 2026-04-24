from django.contrib.auth.decorators import login_required
from django.shortcuts import render, get_object_or_404, redirect
from django.http import JsonResponse, StreamingHttpResponse, HttpResponse
from django.db.models import Count, Max, Sum, Q, Exists, OuterRef
from django.db import ProgrammingError, DatabaseError, transaction
from django.contrib import messages
from .models import Customer, Order, PurchaseOrder, AnthillSale, AnthillPayment, Invoice, SyncLog, Lead, SaleCoverSheet, ClaimDocument, Designer
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
    """
    from django.utils import timezone
    from django.core.paginator import Paginator
    from datetime import timedelta

    search_query = request.GET.get('q', '').strip()
    age_filter = request.GET.get('age', '1y')  # default to <1 year

    # Location comes from the user's profile (site-wide setting)
    profile = getattr(request.user, 'profile', None)
    location_filter = profile.selected_location if profile else ''

    now = timezone.now()

    # Define date bracket cutoffs
    cutoff_1y = now - timedelta(days=365)    # ~1 year
    cutoff_2y = now - timedelta(days=730)    # ~2 years

    # Annotate whether each customer has at least one historic sale
    historic_sale_subquery = AnthillSale.objects.filter(
        customer=OuterRef('pk'),
        activity_type__istartswith='Historic'
    )

    # Use the most recent sale date (if available) to determine age bracket.
    # If a customer had a sale 3 months ago, they are in the <1y bracket
    # even if their account was created 10 years ago.
    from django.db.models.functions import Coalesce, Greatest
    customers_base = Customer.objects.prefetch_related('orders').annotate(
        latest_sale=Max('anthill_sales__activity_date'),
        base_date=Coalesce('creation_time', 'anthill_created_date'),
        has_historic_sale=Exists(historic_sale_subquery),
    ).annotate(
        effective_date=Coalesce(
            Greatest('base_date', 'latest_sale'),
            'latest_sale',
            'base_date',
        )
    ).order_by('name', 'last_name', 'first_name')

    # Apply location filter from profile (skip when searching so results
    # include customers whose record has no location set)
    if location_filter and not search_query:
        customers_base = customers_base.filter(location__iexact=location_filter)

    # Build search Q filter
    search_q = None
    if search_query:
        # Split into individual terms so "Lewis McKee" matches
        # first_name="Lewis" + last_name="McKee"
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
        # All terms must match (AND), but each term can match any field
        search_q = per_term_qs[0]
        for extra in per_term_qs[1:]:
            search_q &= extra

    # Date bracket filters
    # Historic = customer has at least one sale with activity_type starting with 'Historic'
    # The date-based brackets exclude historic customers
    def bracket_filter(qs, bracket):
        if bracket == '1y':
            return qs.filter(has_historic_sale=False, effective_date__gte=cutoff_1y)
        elif bracket == '1_2y':
            return qs.filter(has_historic_sale=False, effective_date__gte=cutoff_2y, effective_date__lt=cutoff_1y)
        elif bracket == '2_10y':
            return qs.filter(has_historic_sale=False, effective_date__lt=cutoff_2y).exclude(effective_date__isnull=True)
        elif bracket == 'historic':
            return qs.filter(has_historic_sale=True)
        return qs

    # Compute counts for each bracket (before search, after location filter)
    count_1y = bracket_filter(customers_base, '1y').count()
    count_1_2y = bracket_filter(customers_base, '1_2y').count()
    count_2_10y = bracket_filter(customers_base, '2_10y').count()
    count_historic = bracket_filter(customers_base, 'historic').count()

    # Apply date filter to get the current bracket's queryset
    customers = bracket_filter(customers_base, age_filter)

    # When searching, show results from ALL brackets grouped by bracket
    search_expanded_from = None
    search_by_bracket = None  # List of (label, bracket_key, queryset) when searching across all
    if search_q:
        # Search across ALL brackets and group results
        bracket_defs = [
            ('< 1 Year', '1y'),
            ('1-2 Years', '1_2y'),
            ('2+ Years', '2_10y'),
            ('Historic', 'historic'),
        ]
        search_by_bracket = []
        total_search_count = 0
        for label, key in bracket_defs:
            bracket_qs = bracket_filter(customers_base, key).filter(search_q)
            count = bracket_qs.count()
            if count > 0:
                search_by_bracket.append({
                    'label': label,
                    'key': key,
                    'customers': bracket_qs[:100],  # cap per bracket
                    'count': count,
                })
                total_search_count += count

        if not search_by_bracket:
            # No results anywhere — show empty for current bracket
            customers = customers.filter(search_q)
        else:
            # We'll display grouped results instead of the normal paginated list
            customers = None  # signal to template to use search_by_bracket
    else:
        search_by_bracket = None

    # When searching, also look for matching leads so users can find people
    # who exist only in the Lead table (not yet converted to Customer)
    matching_leads = None
    if search_query:
        lead_terms = search_query.split()
        lead_per_term = []
        for term in lead_terms:
            lead_per_term.append(
                Q(name__icontains=term) |
                Q(email__icontains=term) |
                Q(phone__icontains=term) |
                Q(city__icontains=term) |
                Q(postcode__icontains=term) |
                Q(anthill_customer_id__icontains=term)
            )
        lead_q = lead_per_term[0]
        for extra in lead_per_term[1:]:
            lead_q &= extra
        matching_leads = Lead.objects.filter(lead_q).order_by('-created_at')[:20]

    # Pagination (only when not showing grouped search results)
    if customers is not None:
        page_number = request.GET.get('page', 1)
        paginator = Paginator(customers, 100)
        page_obj = paginator.get_page(page_number)
    else:
        page_obj = None
        paginator = None

    # Last Anthill customer sync log entry
    last_anthill_sync = SyncLog.objects.filter(script_name='sync_anthill_customers').order_by('-ran_at').first()

    context = {
        'customers': page_obj,
        'page_obj': page_obj,
        'filtered_count': paginator.count if paginator else 0,
        'search_query': search_query,
        'age_filter': age_filter,
        'location_filter': location_filter,
        'count_1y': count_1y,
        'count_1_2y': count_1_2y,
        'count_2_10y': count_2_10y,
        'count_historic': count_historic,
        'search_expanded_from': search_expanded_from,
        'search_by_bracket': search_by_bracket,
        'last_anthill_sync': last_anthill_sync,
        'matching_leads': matching_leads,
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

    old_activity_id = sale.anthill_activity_id
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
                val = val or None
            setattr(customer, field, val)
            update_fields.append(field)

    # Handle is_active toggle
    if 'is_active' in data:
        customer.is_active = data['is_active']
        update_fields.append('is_active')

    if update_fields:
        customer.save(update_fields=update_fields)

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
    sales_base = AnthillSale.objects.select_related('customer', 'order').filter(
        category='3'
    ).exclude(
        status__iexact='cancelled'
    ).order_by('-activity_date')

    if location_filter and not search_query:
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
    complete_statuses = ['complete', 'completed', 'won']

    def status_bracket(qs, bracket):
        if bracket == 'open':
            return qs.exclude(status__iregex=r'^(' + '|'.join(complete_statuses) + ')$')
        elif bracket == 'complete':
            return qs.filter(status__iregex=r'^(' + '|'.join(complete_statuses) + ')$')
        return qs

    count_open = status_bracket(sales_base, 'open').count()
    count_complete = status_bracket(sales_base, 'complete').count()

    sales = status_bracket(sales_base, status_filter)

    if search_q:
        sales = sales.filter(search_q)

    page_number = request.GET.get('page', 1)
    paginator = Paginator(sales, 100)
    page_obj = paginator.get_page(page_number)

    # Build a fallback map: anthill_activity_id -> Order (via Order.sale_number match)
    page_activity_ids = [s.anthill_activity_id for s in page_obj]
    matched_orders = Order.objects.filter(sale_number__in=page_activity_ids).only('id', 'sale_number')
    order_map = {o.sale_number: o for o in matched_orders}

    context = {
        'sales': page_obj,
        'page_obj': page_obj,
        'filtered_count': paginator.count,
        'search_query': search_query,
        'status_filter': status_filter,
        'location_filter': location_filter,
        'count_open': count_open,
        'count_complete': count_complete,
        'order_map': order_map,
    }

    return render(request, 'stock_take/sales_list.html', context)


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
    return combined[:limit]


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

    coversheet.prepared_by = (request.POST.get('prepared_by') or '').strip()
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
    coversheet.new_build_property = request.POST.get('new_build_property') == 'on'
    coversheet.parking_situation = (request.POST.get('parking_situation') or '').strip()
    coversheet.design_check_passed_date = parse_date(request.POST.get('design_check_passed_date'))
    coversheet.pfp_passed_date = parse_date(request.POST.get('pfp_passed_date'))
    coversheet.ordering_passed_date = parse_date(request.POST.get('ordering_passed_date'))
    coversheet.goods_due_in_date = parse_date(request.POST.get('goods_due_in_date'))
    coversheet.fit_days = parse_fit_days(request.POST.get('fit_days'))
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
    coversheet.updated_by = request.user
    coversheet.save()

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
def sale_detail(request, pk):
    """Display detailed view of a single Anthill event."""
    sale = get_object_or_404(AnthillSale.objects.select_related('customer', 'order'), pk=pk)
    coversheet = _get_or_create_sale_coversheet(sale, request.user)
    claim_group_key = _sale_claim_group_key(sale)
    claim_docs = list(ClaimDocument.objects.filter(group_key=claim_group_key).order_by('-uploaded_at'))
    docs_by_type = {}
    for d in claim_docs:
        dt = _claim_doc_type_from_filename(d.file.name)
        if dt and dt not in docs_by_type:
            docs_by_type[dt] = d
    important_doc_types = _important_claim_doc_types(limit=3)
    important_documents = [{'doc_type': dt, 'doc': docs_by_type.get(dt)} for dt in important_doc_types]

    # Get gallery images for this sale's order
    gallery_images = []
    if sale.order:
        from .models import GalleryImage
        gallery_images = GalleryImage.objects.filter(order=sale.order).order_by('-uploaded_at')
    related_sales = []
    if sale.customer:
        related_sales = sale.customer.anthill_sales.exclude(pk=sale.pk).order_by('-activity_date')

    # Get invoices matching this sale's contract number
    sale_invoices = []
    if sale.contract_number:
        sale_invoices = list(
            Invoice.objects.filter(contract_number=sale.contract_number)
            .prefetch_related('line_items', 'payments', 'purchase_orders')
            .order_by('-date')
        )

    # Get payment history for this sale, split by source
    all_payments = list(sale.payments.all().order_by('date'))
    xero_payments = [p for p in all_payments if p.source != 'manual']
    manual_payments = [p for p in all_payments if p.source == 'manual']

    # Table footer totals (include all amounts as shown in table)
    xero_payments_total = sum(p.amount for p in xero_payments if p.amount) or None
    manual_payments_total = sum(p.amount for p in manual_payments if p.amount) or None

    # Financial summary using credit-payment matching (exclude ignored)
    from decimal import Decimal
    active_payments = [p for p in all_payments if not p.ignored]
    total_paid, discount = _match_credits_to_payments(active_payments)
    sale_value = sale.sale_value or Decimal('0')
    effective_value = sale_value - discount

    # Cancelled / dead sales owe nothing
    is_cancelled = sale.status in ('dead', 'cancelled')
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

    context = {
        'sale': sale,
        'coversheet': coversheet,
        'designers': Designer.objects.order_by('name'),
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
        'adjusted_profit': adjusted_profit,
        'gallery_images': gallery_images,
        'is_cancelled': is_cancelled,
        'sale_invoices': sale_invoices,
    }

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

        _recalculate_sale_financials(sale)

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
    payment.delete()
    _recalculate_sale_financials(sale)
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
        .prefetch_related('payments')
        .exclude(activity_type__icontains='lead')
        .exclude(activity_type__icontains='enquir')
        .order_by('-activity_date')
    )

    sales_data = []
    grand_total_value = Decimal('0')
    grand_total_paid = Decimal('0')
    grand_total_outstanding = Decimal('0')

    for sale in sales:
        all_payments = list(sale.payments.all().order_by('date'))
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

        _recalculate_sale_financials(sale)

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

