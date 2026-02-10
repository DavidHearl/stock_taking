from django.contrib.auth.decorators import login_required
from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse, StreamingHttpResponse
from django.db.models import Count, Sum, Q
from .services.workguru_api import WorkGuruAPI, WorkGuruAPIError
from .models import Customer, Order, PurchaseOrder
import logging
import requests
import json
import time

logger = logging.getLogger(__name__)


def sync_customers_from_workguru():
    """Sync all clients from WorkGuru API to local Customer model"""
    try:
        api = WorkGuruAPI.authenticate()
        url = f"{api.base_url}/api/services/app/Client/GetClients"
        api.log_section("SYNCING CUSTOMERS")

        all_clients = []
        skip = 0
        batch_size = 100

        # Paginate through all clients
        while True:
            params = {'MaxResultCount': batch_size, 'SkipCount': skip}
            response = requests.get(url, headers=api.headers, params=params, timeout=30)

            if response.status_code != 200:
                api.log(f"Error fetching clients: {response.status_code}\n")
                return False, f"Error fetching clients: {response.status_code}"

            data = response.json()
            items = data.get('result', {}).get('items', [])
            total_count = data.get('result', {}).get('totalCount', 0)

            all_clients.extend(items)
            skip += batch_size

            if skip >= total_count or not items:
                break

        api.log(f"Fetched {len(all_clients)} clients from WorkGuru\n")

        synced = 0
        for client in all_clients:
            wg_id = client.get('id')
            if not wg_id:
                continue

            from django.utils.dateparse import parse_datetime

            defaults = {
                'name': (client.get('name') or '')[:255],
                'code': client.get('code'),
                'email': client.get('email') or None,
                'phone': (client.get('phone') or '')[:50] or None,
                'fax': (client.get('fax') or '')[:50] or None,
                'website': client.get('website') or None,
                'abn': client.get('abn'),
                'address_1': client.get('address1'),
                'address_2': client.get('address2'),
                'city': client.get('city'),
                'state': client.get('state'),
                'suburb': client.get('suburb'),
                'postcode': (client.get('postcode') or '')[:20],
                'country': client.get('country'),
                'currency': client.get('currency'),
                'credit_days': (client.get('creditDays') or client.get('numberOfCreditDays') or '')[:20],
                'credit_limit': client.get('creditLimit') or 0,
                'credit_terms_type': client.get('creditTermsType'),
                'price_tier': client.get('priceTier'),
                'price_tier_id': client.get('priceTierId'),
                'billing_client': client.get('billingClient'),
                'billing_client_id': client.get('billingClientId'),
                'default_invoice_template_id': client.get('defaultInvoiceTemplateId'),
                'default_quote_template_id': client.get('defaultQuoteTemplateId'),
                'is_active': client.get('isActive', True),
                'xero_id': client.get('xeroId'),
                'creation_time': parse_datetime(client['creationTime']) if client.get('creationTime') else None,
                'last_modification_time': parse_datetime(client['lastModificationTime']) if client.get('lastModificationTime') else None,
                'raw_data': client,
            }

            # Handle email validation — blank emails should be None
            if defaults['email'] and '@' not in defaults['email']:
                defaults['email'] = None

            # Handle website validation
            if defaults['website'] and not defaults['website'].startswith(('http://', 'https://')):
                defaults['website'] = f"https://{defaults['website']}" if '.' in defaults['website'] else None

            Customer.objects.update_or_create(
                workguru_id=wg_id,
                defaults=defaults,
            )
            synced += 1

        api.log(f"Synced {synced} customers\n")
        return True, synced

    except WorkGuruAPIError as e:
        return False, str(e)
    except Exception as e:
        logger.exception("Error syncing customers")
        return False, str(e)


@login_required
def sync_customers_stream(request):
    """SSE streaming endpoint for customer sync progress"""
    def event_stream():
        yield 'data: {"progress": 5, "message": "Authenticating with WorkGuru..."}\n\n'
        time.sleep(0.3)

        try:
            api = WorkGuruAPI.authenticate()
        except WorkGuruAPIError as e:
            yield f'data: {{"progress": 100, "message": "Authentication failed: {e}", "error": true}}\n\n'
            return

        yield 'data: {"progress": 10, "message": "Fetching clients from WorkGuru..."}\n\n'

        url = f"{api.base_url}/api/services/app/Client/GetClients"
        all_clients = []
        skip = 0
        batch_size = 100

        # Get total count first
        try:
            resp = requests.get(url, headers=api.headers, params={'MaxResultCount': 1, 'SkipCount': 0}, timeout=30)
            total_count = resp.json().get('result', {}).get('totalCount', 0)
        except Exception:
            total_count = 5000  # estimate

        yield f'data: {{"progress": 15, "message": "Found {total_count} clients. Fetching..."}}\n\n'

        # Fetch all pages
        while True:
            params = {'MaxResultCount': batch_size, 'SkipCount': skip}
            try:
                response = requests.get(url, headers=api.headers, params=params, timeout=30)
                if response.status_code != 200:
                    yield f'data: {{"progress": 100, "message": "API error: {response.status_code}", "error": true}}\n\n'
                    return
                items = response.json().get('result', {}).get('items', [])
            except Exception as e:
                yield f'data: {{"progress": 100, "message": "Fetch error: {e}", "error": true}}\n\n'
                return

            all_clients.extend(items)
            skip += batch_size
            fetch_pct = min(15 + int(50 * len(all_clients) / max(total_count, 1)), 65)
            yield f'data: {{"progress": {fetch_pct}, "message": "Fetched {len(all_clients)} of {total_count} clients..."}}\n\n'

            if skip >= total_count or not items:
                break

        yield f'data: {{"progress": 70, "message": "Saving {len(all_clients)} clients to database..."}}\n\n'

        from django.utils.dateparse import parse_datetime
        synced = 0
        for i, client in enumerate(all_clients):
            wg_id = client.get('id')
            if not wg_id:
                continue

            defaults = {
                'name': (client.get('name') or '')[:255],
                'code': client.get('code'),
                'email': client.get('email') or None,
                'phone': (client.get('phone') or '')[:50] or None,
                'fax': (client.get('fax') or '')[:50] or None,
                'website': client.get('website') or None,
                'abn': client.get('abn'),
                'address_1': client.get('address1'),
                'address_2': client.get('address2'),
                'city': client.get('city'),
                'state': client.get('state'),
                'suburb': client.get('suburb'),
                'postcode': (client.get('postcode') or '')[:20],
                'country': client.get('country'),
                'currency': client.get('currency'),
                'credit_days': (client.get('creditDays') or client.get('numberOfCreditDays') or '')[:20],
                'credit_limit': client.get('creditLimit') or 0,
                'credit_terms_type': client.get('creditTermsType'),
                'price_tier': client.get('priceTier'),
                'price_tier_id': client.get('priceTierId'),
                'billing_client': client.get('billingClient'),
                'billing_client_id': client.get('billingClientId'),
                'default_invoice_template_id': client.get('defaultInvoiceTemplateId'),
                'default_quote_template_id': client.get('defaultQuoteTemplateId'),
                'is_active': client.get('isActive', True),
                'xero_id': client.get('xeroId'),
                'creation_time': parse_datetime(client['creationTime']) if client.get('creationTime') else None,
                'last_modification_time': parse_datetime(client['lastModificationTime']) if client.get('lastModificationTime') else None,
                'raw_data': client,
            }

            if defaults['email'] and '@' not in defaults['email']:
                defaults['email'] = None
            if defaults['website'] and not defaults['website'].startswith(('http://', 'https://')):
                defaults['website'] = f"https://{defaults['website']}" if '.' in defaults['website'] else None

            try:
                Customer.objects.update_or_create(workguru_id=wg_id, defaults=defaults)
                synced += 1
            except Exception as e:
                logger.warning(f"Error saving customer {wg_id}: {e}")

            if i % 200 == 0 and i > 0:
                save_pct = min(70 + int(25 * i / len(all_clients)), 95)
                yield f'data: {{"progress": {save_pct}, "message": "Saved {synced} customers..."}}\n\n'

        yield f'data: {{"progress": 100, "message": "Sync complete! {synced} customers synced.", "done": true}}\n\n'

    response = StreamingHttpResponse(event_stream(), content_type='text/event-stream')
    response['Cache-Control'] = 'no-cache'
    response['X-Accel-Buffering'] = 'no'
    return response


@login_required
def customers_list(request):
    """Display list of all customers"""
    search_query = request.GET.get('q', '').strip()
    status_filter = request.GET.get('status', 'all')

    customers = Customer.objects.prefetch_related('orders').all().order_by('name', 'last_name', 'first_name')

    # Apply status filter
    if status_filter == 'active':
        customers = customers.filter(is_active=True)
    elif status_filter == 'inactive':
        customers = customers.filter(is_active=False)

    # Apply search
    if search_query:
        customers = customers.filter(
            Q(name__icontains=search_query) |
            Q(email__icontains=search_query) |
            Q(phone__icontains=search_query) |
            Q(code__icontains=search_query) |
            Q(city__icontains=search_query) |
            Q(postcode__icontains=search_query)
        )

    total_count = Customer.objects.count()
    active_count = Customer.objects.filter(is_active=True).count()
    inactive_count = Customer.objects.filter(is_active=False).count()

    context = {
        'customers': customers,
        'total_count': total_count,
        'active_count': active_count,
        'inactive_count': inactive_count,
        'filtered_count': customers.count(),
        'search_query': search_query,
        'status_filter': status_filter,
    }

    return render(request, 'stock_take/customers_list.html', context)


@login_required
def customer_detail(request, customer_id):
    """Display detailed view of a single customer"""
    customer = get_object_or_404(Customer, workguru_id=customer_id)

    # Get linked orders
    orders = Order.objects.filter(customer=customer).order_by('-order_date')

    # Get contacts from raw_data
    contacts = []
    if customer.raw_data and isinstance(customer.raw_data, dict):
        contacts = customer.raw_data.get('contacts', [])

    context = {
        'customer': customer,
        'orders': orders,
        'order_count': orders.count(),
        'contacts': contacts,
    }

    return render(request, 'stock_take/customer_detail.html', context)


@login_required
def customer_save(request, customer_id):
    """Save edited customer details via AJAX POST"""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    customer = get_object_or_404(Customer, workguru_id=customer_id)

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
def customer_delete(request, customer_id):
    """Delete a customer"""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    customer = get_object_or_404(Customer, pk=customer_id)
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
