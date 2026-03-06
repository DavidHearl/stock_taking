from django.contrib.auth.decorators import login_required
from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse, StreamingHttpResponse
from django.db.models import Count, Max, Sum, Q, Exists, OuterRef
from .models import Customer, Order, PurchaseOrder, AnthillSale, AnthillPayment, Invoice, SyncLog, Lead
import logging
import json
import time

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

    # Get Anthill sales for this customer — annotated with payment totals
    anthill_sales = (
        customer.anthill_sales
        .annotate(
            payments_total=Sum('payments__amount'),
            payments_count=Count('payments'),
        )
        .order_by('-activity_date')
    )

    # Get invoices linked to this customer
    invoices = Invoice.objects.filter(customer=customer).order_by('-date')

    # Get contacts from raw_data
    contacts = []
    if customer.raw_data and isinstance(customer.raw_data, dict):
        contacts = customer.raw_data.get('contacts', [])

    context = {
        'customer': customer,
        'orders': orders,
        'order_count': orders.count(),
        'contacts': contacts,
        'anthill_sales': anthill_sales,
        'anthill_sales_count': anthill_sales.count(),
        'invoices': invoices,
        'invoice_count': invoices.count(),
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
    """Display list of Anthill sales (Category 3 only) with search, date-bracket filters, and location."""
    from django.utils import timezone
    from django.core.paginator import Paginator
    from datetime import timedelta

    search_query = request.GET.get('q', '').strip()
    age_filter = request.GET.get('age', '1y')

    # Location from user profile
    profile = getattr(request.user, 'profile', None)
    location_filter = profile.selected_location if profile else ''

    now = timezone.now()
    cutoff_1y = now - timedelta(days=365)
    cutoff_2y = now - timedelta(days=730)

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

    # Date bracket filters
    # Historic = sales with activity_type starting with 'Historic'
    # Date-based brackets exclude historic sales
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

    count_1y = bracket_filter(sales_base, '1y').count()
    count_1_2y = bracket_filter(sales_base, '1_2y').count()
    count_2_10y = bracket_filter(sales_base, '2_10y').count()
    count_historic = bracket_filter(sales_base, 'historic').count()

    sales = bracket_filter(sales_base, age_filter)

    search_expanded_from = None
    if search_q:
        filtered = sales.filter(search_q)
        if filtered.exists():
            sales = filtered
        else:
            bracket_order = ['1y', '1_2y', '2_10y', 'historic']
            try:
                start_idx = bracket_order.index(age_filter) + 1
            except ValueError:
                start_idx = 0
            remaining = bracket_order[start_idx:] + bracket_order[:bracket_order.index(age_filter)]
            for bracket in remaining:
                expanded_qs = bracket_filter(sales_base, bracket).filter(search_q)
                if expanded_qs.exists():
                    sales = expanded_qs
                    search_expanded_from = bracket
                    break
            else:
                sales = filtered

    page_number = request.GET.get('page', 1)
    paginator = Paginator(sales, 100)
    page_obj = paginator.get_page(page_number)

    # Build a fallback map: anthill_activity_id -> Order (via Order.sale_number match)
    # Used when AnthillSale.order FK is not populated
    page_activity_ids = [s.anthill_activity_id for s in page_obj]
    matched_orders = Order.objects.filter(sale_number__in=page_activity_ids).only('id', 'sale_number')
    order_map = {o.sale_number: o for o in matched_orders}

    context = {
        'sales': page_obj,
        'page_obj': page_obj,
        'filtered_count': paginator.count,
        'search_query': search_query,
        'age_filter': age_filter,
        'location_filter': location_filter,
        'count_1y': count_1y,
        'count_1_2y': count_1_2y,
        'count_2_10y': count_2_10y,
        'count_historic': count_historic,
        'search_expanded_from': search_expanded_from,
        'order_map': order_map,
    }

    return render(request, 'stock_take/sales_list.html', context)


@login_required
def sale_detail(request, pk):
    """Display detailed view of a single Anthill event."""
    sale = get_object_or_404(AnthillSale.objects.select_related('customer', 'order'), pk=pk)

    # Get other sales for the same customer
    related_sales = []
    if sale.customer:
        related_sales = sale.customer.anthill_sales.exclude(pk=sale.pk).order_by('-activity_date')

    # Get payment history for this sale, split by source
    all_payments = list(sale.payments.all().order_by('date'))
    xero_payments = [p for p in all_payments if p.source != 'manual']
    manual_payments = [p for p in all_payments if p.source == 'manual']
    payments_total = sum(p.amount for p in all_payments if p.amount) or None
    xero_payments_total = sum(p.amount for p in xero_payments if p.amount) or None
    manual_payments_total = sum(p.amount for p in manual_payments if p.amount) or None

    context = {
        'sale': sale,
        'related_sales': related_sales,
        'payments': all_payments,
        'xero_payments': xero_payments,
        'manual_payments': manual_payments,
        'payments_total': payments_total,
        'xero_payments_total': xero_payments_total,
        'manual_payments_total': manual_payments_total,
    }

    return render(request, 'stock_take/sale_detail.html', context)


def _recalculate_sale_balance(sale):
    """Recompute balance_payable and paid_in_full from all payment records."""
    from decimal import Decimal
    total_paid = sale.payments.aggregate(total=Sum('amount'))['total'] or Decimal('0')
    sale_value = sale.sale_value or Decimal('0')
    new_balance = max(sale_value - total_paid, Decimal('0'))
    sale.balance_payable = new_balance
    sale.paid_in_full = new_balance <= Decimal('0')
    sale.save(update_fields=['balance_payable', 'paid_in_full'])


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

    created_ids = []
    errors = []
    for i, p in enumerate(payments_data):
        # Parse date — accept "dd/mm/yy HH:MM", "dd/mm/yyyy HH:MM", "dd/mm/yy", "dd/mm/yyyy"
        date_val = None
        raw_date = str(p.get('date', '')).strip()
        for fmt in ('%d/%m/%y %H:%M', '%d/%m/%Y %H:%M', '%d/%m/%y', '%d/%m/%Y'):
            try:
                date_val = datetime.strptime(raw_date, fmt)
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

    _recalculate_sale_balance(sale)
    return JsonResponse({'success': True, 'created': len(created_ids), 'errors': errors})


@login_required
def delete_manual_payment(request, pk, payment_pk):
    """Delete a manual payment (POST)."""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required'}, status=405)

    payment = get_object_or_404(AnthillPayment, pk=payment_pk, sale__pk=pk, source='manual')
    sale = payment.sale
    payment.delete()
    _recalculate_sale_balance(sale)
    return JsonResponse({'success': True})


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

