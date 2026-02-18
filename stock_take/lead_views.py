import json
import logging

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render, redirect
from django.contrib import messages

from .models import Lead

logger = logging.getLogger(__name__)


@login_required
def leads_list(request):
    """Display list of all leads"""
    search_query = request.GET.get('q', '').strip()
    status_filter = request.GET.get('status', 'all')

    leads = Lead.objects.all()

    # Apply status filter
    if status_filter == 'active':
        leads = leads.exclude(status__in=['converted', 'lost'])
    elif status_filter == 'converted':
        leads = leads.filter(status='converted')
    elif status_filter == 'lost':
        leads = leads.filter(status='lost')
    elif status_filter != 'all':
        leads = leads.filter(status=status_filter)

    # Apply search
    if search_query:
        leads = leads.filter(
            Q(name__icontains=search_query) |
            Q(email__icontains=search_query) |
            Q(phone__icontains=search_query) |
            Q(city__icontains=search_query) |
            Q(postcode__icontains=search_query) |
            Q(source__icontains=search_query)
        )

    total_count = Lead.objects.count()
    active_count = Lead.objects.exclude(status__in=['converted', 'lost']).count()
    converted_count = Lead.objects.filter(status='converted').count()
    lost_count = Lead.objects.filter(status='lost').count()
    filtered_count = leads.count()

    # Pagination
    paginator = Paginator(leads, 100)
    page_number = request.GET.get('page', 1)
    page_obj = paginator.get_page(page_number)

    context = {
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

    return render(request, 'stock_take/leads_list.html', context)


@login_required
def lead_detail(request, pk):
    """Display detailed view of a single lead"""
    lead = get_object_or_404(Lead, pk=pk)

    context = {
        'lead': lead,
        'status_choices': Lead.STATUS_CHOICES,
        'source_choices': Lead.SOURCE_CHOICES,
    }

    return render(request, 'stock_take/lead_detail.html', context)


@login_required
def lead_save(request, pk):
    """Save edited lead details via AJAX POST"""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    lead = get_object_or_404(Lead, pk=pk)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    editable_fields = [
        'name', 'email', 'phone', 'website',
        'address_1', 'address_2', 'city', 'state', 'postcode', 'country',
        'status', 'source', 'notes',
    ]

    update_fields = []
    for field in editable_fields:
        if field in data:
            val = data[field]
            if field == 'value':
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
            setattr(lead, field, val)
            update_fields.append(field)

    # Handle value separately (decimal)
    if 'value' in data:
        try:
            lead.value = float(data['value']) if data['value'] else 0
        except (ValueError, TypeError):
            lead.value = 0
        update_fields.append('value')

    if update_fields:
        lead.save(update_fields=update_fields)

    return JsonResponse({'success': True})


@login_required
def lead_delete(request, pk):
    """Delete a lead"""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    lead = get_object_or_404(Lead, pk=pk)
    lead.delete()
    return JsonResponse({'success': True})


@login_required
def leads_bulk_delete(request):
    """Bulk delete leads by list of IDs"""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    try:
        data = json.loads(request.body)
        ids = data.get('ids', [])
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    if not ids:
        return JsonResponse({'error': 'No IDs provided'}, status=400)

    deleted_count, _ = Lead.objects.filter(pk__in=ids).delete()
    return JsonResponse({'success': True, 'deleted': deleted_count})


@login_required
def lead_create(request):
    """Create a new lead manually"""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    name = request.POST.get('name', '').strip()
    if not name:
        messages.error(request, 'Lead name is required.')
        return redirect('leads_list')

    lead = Lead.objects.create(
        name=name,
        email=request.POST.get('email', '').strip() or None,
        phone=request.POST.get('phone', '').strip() or None,
        website=request.POST.get('website', '').strip() or None,
        address_1=request.POST.get('address_1', '').strip() or None,
        city=request.POST.get('city', '').strip() or None,
        postcode=request.POST.get('postcode', '').strip() or None,
        country=request.POST.get('country', '').strip() or None,
        source=request.POST.get('source', '').strip() or None,
        status='new',
    )

    messages.success(request, f'Lead "{lead.name}" created successfully.')
    return redirect('lead_detail', pk=lead.pk)


@login_required
def lead_merge(request):
    """Merge two leads: transfer data from remove_id into keep_id, then delete remove_id"""
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
        keep_lead = Lead.objects.get(pk=keep_id)
        remove_lead = Lead.objects.get(pk=remove_id)
    except Lead.DoesNotExist:
        return JsonResponse({'error': 'Lead not found'}, status=404)

    # Fill in any blank fields on keep_lead from remove_lead
    fill_fields = [
        'email', 'phone', 'website',
        'address_1', 'address_2', 'city', 'state', 'postcode', 'country',
        'source', 'notes',
    ]
    updated_fields = []
    for field in fill_fields:
        keep_val = getattr(keep_lead, field)
        remove_val = getattr(remove_lead, field)
        if not keep_val and remove_val:
            setattr(keep_lead, field, remove_val)
            updated_fields.append(field)

    if updated_fields:
        keep_lead.save(update_fields=updated_fields)

    remove_name = str(remove_lead)
    remove_lead.delete()

    return JsonResponse({
        'success': True,
        'fields_filled': len(updated_fields),
        'removed': remove_name,
    })


@login_required
def lead_convert(request, pk):
    """Convert a lead to a customer"""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    lead = get_object_or_404(Lead, pk=pk)

    if lead.status == 'converted' and lead.converted_to_customer:
        return JsonResponse({'error': 'Lead already converted', 'customer_id': lead.converted_to_customer.pk})

    from .models import Customer

    # Generate a unique workguru_id for manually created customers
    max_id = Customer.objects.order_by('-workguru_id').values_list('workguru_id', flat=True).first() or 0
    manual_id = max(max_id + 1, 700000)

    customer = Customer.objects.create(
        workguru_id=manual_id,
        name=lead.name,
        email=lead.email,
        phone=lead.phone,
        website=lead.website,
        address_1=lead.address_1,
        address_2=lead.address_2,
        city=lead.city,
        state=lead.state,
        postcode=lead.postcode,
        country=lead.country,
        is_active=True,
    )

    lead.status = 'converted'
    lead.converted_to_customer = customer
    lead.save(update_fields=['status', 'converted_to_customer'])

    return JsonResponse({'success': True, 'customer_id': customer.pk, 'customer_name': customer.name})
