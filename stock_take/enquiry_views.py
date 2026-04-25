import json
import logging

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt

from .models import WebsiteEnquiry, log_activity
from .permissions import page_permission_required

logger = logging.getLogger(__name__)


# ─── Public API: receive enquiry from WordPress ───────────────────────────────

@csrf_exempt
def website_enquiry_receive(request):
    """API endpoint that accepts JSON from the WordPress contact form.

    Authentication: ``X-API-Key`` header must match ``settings.WEBSITE_ENQUIRY_API_KEY``.

    Expected JSON body (all fields optional except at least one contact detail):
        {
            "name":    "Jane Smith",
            "email":   "jane@example.com",
            "phone":   "07700 900000",
            "subject": "New wardrobe enquiry",
            "message": "I'd like a quote for ...",
            "source":  "Homepage contact form"
        }
    Any extra fields are stored verbatim in ``raw_data``.
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    # Authenticate
    api_key = request.headers.get('X-API-Key', '')
    expected_key = getattr(settings, 'WEBSITE_ENQUIRY_API_KEY', '')
    if not expected_key or api_key != expected_key:
        return JsonResponse({'error': 'Invalid API key'}, status=403)

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    name = str(data.get('name', '')).strip()[:255]
    email = str(data.get('email', '')).strip()[:254] or None
    phone = str(data.get('phone', '')).strip()[:100] or None
    subject = str(data.get('subject', '')).strip()[:500]
    message = str(data.get('message', '')).strip()
    source = str(data.get('source', '')).strip()[:255]

    # Require at least a name or email
    if not name and not email:
        return JsonResponse({'error': 'At least name or email is required'}, status=400)

    # Get client IP
    x_forwarded = request.META.get('HTTP_X_FORWARDED_FOR')
    ip = x_forwarded.split(',')[0].strip() if x_forwarded else request.META.get('REMOTE_ADDR')

    enquiry = WebsiteEnquiry.objects.create(
        name=name,
        email=email,
        phone=phone,
        subject=subject,
        message=message,
        source=source,
        ip_address=ip,
        raw_data=data,
    )

    logger.info(f"Website enquiry received: #{enquiry.pk} from {name} <{email}>")

    return JsonResponse({
        'success': True,
        'id': enquiry.pk,
        'received_at': enquiry.received_at.isoformat(),
    }, status=201)


# ─── Internal views ───────────────────────────────────────────────────────────

@page_permission_required('website_enquiries')
def website_enquiries_list(request):
    """List all website enquiries."""
    status_filter = request.GET.get('status', 'all')
    search_query = request.GET.get('q', '').strip()

    enquiries = WebsiteEnquiry.objects.all()

    if status_filter != 'all':
        enquiries = enquiries.filter(status=status_filter)

    if search_query:
        from django.db.models import Q
        enquiries = enquiries.filter(
            Q(name__icontains=search_query) |
            Q(email__icontains=search_query) |
            Q(phone__icontains=search_query) |
            Q(subject__icontains=search_query) |
            Q(message__icontains=search_query)
        )

    counts = {
        'all': WebsiteEnquiry.objects.count(),
        'new': WebsiteEnquiry.objects.filter(status='new').count(),
        'contacted': WebsiteEnquiry.objects.filter(status='contacted').count(),
        'converted': WebsiteEnquiry.objects.filter(status='converted').count(),
        'closed': WebsiteEnquiry.objects.filter(status='closed').count(),
    }

    return render(request, 'stock_take/website_enquiries.html', {
        'enquiries': enquiries,
        'status_filter': status_filter,
        'search_query': search_query,
        'counts': counts,
    })


@login_required
def website_enquiry_update(request, enquiry_id):
    """Update status and/or notes on an enquiry (AJAX POST)."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    try:
        enquiry = WebsiteEnquiry.objects.get(pk=enquiry_id)
    except WebsiteEnquiry.DoesNotExist:
        return JsonResponse({'error': 'Not found'}, status=404)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    update_fields = []
    if 'status' in data:
        valid_statuses = {s[0] for s in WebsiteEnquiry.STATUS_CHOICES}
        if data['status'] in valid_statuses:
            enquiry.status = data['status']
            update_fields.append('status')
    if 'notes' in data:
        enquiry.notes = str(data['notes'])
        update_fields.append('notes')

    if update_fields:
        update_fields.append('updated_at')
        enquiry.save(update_fields=update_fields)

    log_activity(
        user=request.user,
        event_type='enquiry_updated',
        description=f'{request.user.get_full_name() or request.user.username} updated enquiry #{enquiry.pk} ({enquiry.name}).',
    )

    return JsonResponse({'success': True, 'status': enquiry.status, 'notes': enquiry.notes})


@login_required
def website_enquiry_delete(request, enquiry_id):
    """Delete an enquiry (AJAX POST)."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    try:
        enquiry = WebsiteEnquiry.objects.get(pk=enquiry_id)
    except WebsiteEnquiry.DoesNotExist:
        return JsonResponse({'error': 'Not found'}, status=404)

    name = enquiry.name
    enquiry.delete()

    log_activity(
        user=request.user,
        event_type='enquiry_deleted',
        description=f'{request.user.get_full_name() or request.user.username} deleted enquiry from {name}.',
    )

    return JsonResponse({'success': True})
