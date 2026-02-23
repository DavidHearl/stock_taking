from django.contrib.auth.decorators import login_required
from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse, StreamingHttpResponse
from django.db.models import Count, Max, Sum, Q
from .models import Customer, Order, PurchaseOrder, AnthillSale, Invoice
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
    age_filter = request.GET.get('age', '9m')  # default to <9 months

    # Location comes from the user's profile (site-wide setting)
    profile = getattr(request.user, 'profile', None)
    location_filter = profile.selected_location if profile else ''

    now = timezone.now()

    # Define date bracket cutoffs
    cutoff_9m = now - timedelta(days=274)    # ~9 months
    cutoff_2y = now - timedelta(days=730)    # ~2 years
    cutoff_10y = now - timedelta(days=3650)  # ~10 years

    # Use the most recent sale date (if available) to determine age bracket.
    # If a customer had a sale 3 months ago, they are in the <9m bracket
    # even if their account was created 10 years ago.
    from django.db.models.functions import Coalesce, Greatest
    customers_base = Customer.objects.prefetch_related('orders').annotate(
        latest_sale=Max('anthill_sales__activity_date'),
        base_date=Coalesce('creation_time', 'anthill_created_date'),
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
                Q(postcode__icontains=term)
            )
        # All terms must match (AND), but each term can match any field
        search_q = per_term_qs[0]
        for extra in per_term_qs[1:]:
            search_q &= extra

    # Date bracket filters
    def bracket_filter(qs, bracket):
        if bracket == '9m':
            return qs.filter(effective_date__gte=cutoff_9m)
        elif bracket == '2y':
            return qs.filter(effective_date__gte=cutoff_2y, effective_date__lt=cutoff_9m)
        elif bracket == '10y':
            return qs.filter(effective_date__gte=cutoff_10y, effective_date__lt=cutoff_2y)
        elif bracket == 'over10':
            return qs.filter(Q(effective_date__lt=cutoff_10y) | Q(effective_date__isnull=True))
        return qs

    # Compute counts for each bracket (before search, after location filter)
    count_9m = bracket_filter(customers_base, '9m').count()
    count_2y = bracket_filter(customers_base, '2y').count()
    count_10y = bracket_filter(customers_base, '10y').count()
    count_over10 = bracket_filter(customers_base, 'over10').count()

    # Apply date filter to get the current bracket's queryset
    customers = bracket_filter(customers_base, age_filter)

    # When searching, show results from ALL brackets grouped by bracket
    search_expanded_from = None
    search_by_bracket = None  # List of (label, bracket_key, queryset) when searching across all
    if search_q:
        # Search across ALL brackets and group results
        bracket_defs = [
            ('< 9 Months', '9m'),
            ('< 2 Years', '2y'),
            ('< 10 Years', '10y'),
            ('Over 10 Years', 'over10'),
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

    # Pagination (only when not showing grouped search results)
    if customers is not None:
        page_number = request.GET.get('page', 1)
        paginator = Paginator(customers, 100)
        page_obj = paginator.get_page(page_number)
    else:
        page_obj = None
        paginator = None

    context = {
        'customers': page_obj,
        'page_obj': page_obj,
        'filtered_count': paginator.count if paginator else 0,
        'search_query': search_query,
        'age_filter': age_filter,
        'location_filter': location_filter,
        'count_9m': count_9m,
        'count_2y': count_2y,
        'count_10y': count_10y,
        'count_over10': count_over10,
        'search_expanded_from': search_expanded_from,
        'search_by_bracket': search_by_bracket,
    }

    return render(request, 'stock_take/customers_list.html', context)


@login_required
def customer_detail(request, pk):
    """Display detailed view of a single customer"""
    customer = get_object_or_404(Customer, pk=pk)

    # Get linked orders
    orders = Order.objects.filter(customer=customer).order_by('-order_date')

    # Get Anthill sales for this customer
    anthill_sales = customer.anthill_sales.all().order_by('-activity_date')

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

    return JsonResponse({'success': True})


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
    """Merge two customers: transfer orders/data from remove_id into keep_id, then delete remove_id"""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    try:
        data = json.loads(request.body)
        keep_id = data.get('keep_id')
        remove_id = data.get('remove_id')
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    if not keep_id or not remove_id:
        return JsonResponse({'error': 'Both keep_id and remove_id are required'}, status=400)

    try:
        keep_customer = Customer.objects.get(pk=keep_id)
        remove_customer = Customer.objects.get(pk=remove_id)
    except Customer.DoesNotExist:
        return JsonResponse({'error': 'Customer not found'}, status=404)

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
# Sales views
# ════════════════════════════════════════════════════════════════════════

@login_required
def sales_list(request):
    """Display list of all Anthill events with search, date-bracket filters, and location."""
    from django.utils import timezone
    from django.core.paginator import Paginator
    from datetime import timedelta

    search_query = request.GET.get('q', '').strip()
    age_filter = request.GET.get('age', '9m')

    # Location from user profile
    profile = getattr(request.user, 'profile', None)
    location_filter = profile.selected_location if profile else ''

    now = timezone.now()
    cutoff_9m = now - timedelta(days=274)
    cutoff_2y = now - timedelta(days=730)
    cutoff_10y = now - timedelta(days=3650)

    sales_base = AnthillSale.objects.select_related('customer', 'order').order_by('-activity_date')

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
                Q(customer__name__icontains=term) |
                Q(customer__first_name__icontains=term) |
                Q(customer__last_name__icontains=term)
            )
        search_q = per_term_qs[0]
        for extra in per_term_qs[1:]:
            search_q &= extra

    # Date bracket filters
    def bracket_filter(qs, bracket):
        if bracket == '9m':
            return qs.filter(activity_date__gte=cutoff_9m)
        elif bracket == '2y':
            return qs.filter(activity_date__gte=cutoff_2y, activity_date__lt=cutoff_9m)
        elif bracket == '10y':
            return qs.filter(activity_date__gte=cutoff_10y, activity_date__lt=cutoff_2y)
        elif bracket == 'over10':
            return qs.filter(Q(activity_date__lt=cutoff_10y) | Q(activity_date__isnull=True))
        return qs

    count_9m = bracket_filter(sales_base, '9m').count()
    count_2y = bracket_filter(sales_base, '2y').count()
    count_10y = bracket_filter(sales_base, '10y').count()
    count_over10 = bracket_filter(sales_base, 'over10').count()

    sales = bracket_filter(sales_base, age_filter)

    search_expanded_from = None
    if search_q:
        filtered = sales.filter(search_q)
        if filtered.exists():
            sales = filtered
        else:
            bracket_order = ['9m', '2y', '10y', 'over10']
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

    context = {
        'sales': page_obj,
        'page_obj': page_obj,
        'filtered_count': paginator.count,
        'search_query': search_query,
        'age_filter': age_filter,
        'location_filter': location_filter,
        'count_9m': count_9m,
        'count_2y': count_2y,
        'count_10y': count_10y,
        'count_over10': count_over10,
        'search_expanded_from': search_expanded_from,
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

    context = {
        'sale': sale,
        'related_sales': related_sales,
    }

    return render(request, 'stock_take/sale_detail.html', context)
