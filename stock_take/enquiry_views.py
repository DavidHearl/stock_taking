import json
import logging
import re
import ast

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt

from .models import WebsiteEnquiry, log_activity
from .permissions import page_permission_required

logger = logging.getLogger(__name__)


def _normalize_key(key):
    """Normalize payload keys so First Name / first_name / first-name all match."""
    return re.sub(r'[^a-z0-9]+', '', str(key).strip().lower())


def _coerce_payload_value(raw_value):
    """Parse object-like string payload fragments into Python values when possible."""
    if isinstance(raw_value, str):
        text = raw_value.strip()
        if text and text[0] in '[{':
            try:
                return json.loads(text)
            except (json.JSONDecodeError, TypeError):
                try:
                    return ast.literal_eval(text)
                except (ValueError, SyntaxError):
                    return raw_value
    return raw_value


def _extract_scalar_payload_value(raw_value, *keys):
    """Extract a human-usable scalar from payload fragments that may be nested dicts/lists."""
    value = _coerce_payload_value(raw_value)

    if isinstance(value, dict):
        candidate_keys = set()
        for key in keys:
            raw_key = str(key).strip().lower()
            candidate_keys.add(_normalize_key(raw_key))
            candidate_keys.update(part for part in re.split(r'[^a-z0-9]+', raw_key) if part)

        normalized_items = { _normalize_key(k): v for k, v in value.items() }
        for candidate in candidate_keys:
            if candidate in normalized_items:
                nested = _extract_scalar_payload_value(normalized_items[candidate], *keys)
                if nested not in (None, ''):
                    return nested

        for nested_value in value.values():
            nested = _extract_scalar_payload_value(nested_value, *keys)
            if nested not in (None, ''):
                return nested
        return ''

    if isinstance(value, list):
        for item in value:
            nested = _extract_scalar_payload_value(item, *keys)
            if nested not in (None, ''):
                return nested
        return ''

    return value


def _collect_payload_values(data):
    """Flatten top-level and nested payload key/value pairs into a normalized dict."""
    values = {}

    def add_pair(raw_key, raw_value):
        if raw_key in (None, '') or raw_value in (None, ''):
            return
        values.setdefault(_normalize_key(raw_key), raw_value)

    if isinstance(data, dict):
        for k, v in data.items():
            add_pair(k, v)

    nested = data.get('data') if isinstance(data, dict) else None
    if isinstance(nested, str):
        try:
            nested = json.loads(nested)
        except (json.JSONDecodeError, TypeError):
            nested = None

    if isinstance(nested, dict):
        for k, v in nested.items():
            add_pair(k, v)
    elif isinstance(nested, list):
        # Common JSON dump shape: [{'name':'First Name','value':'Jane'}, ...]
        for item in nested:
            if not isinstance(item, dict):
                continue
            key = item.get('name') or item.get('label') or item.get('key')
            val = item.get('value') or item.get('val') or item.get('answer')
            add_pair(key, val)

    return values


def _extract_payload_value(data, *keys):
    """Return first non-empty value from normalized key matches in payload dumps."""
    values = _collect_payload_values(data)
    for key in keys:
        value = values.get(_normalize_key(key))
        if value not in (None, ''):
            return _extract_scalar_payload_value(value, *keys)
    return ''


def _extract_bool_value(data, *keys):
    """Return boolean value parsed from top-level or nested payload fields."""
    raw = _extract_payload_value(data, *keys)
    if raw in (None, ''):
        return None
    value = str(raw).strip().lower()
    if value in {'1', 'true', 'yes', 'y', 'on', 'checked'}:
        return True
    if value in {'0', 'false', 'no', 'n', 'off', 'unchecked'}:
        return False
    return None


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
    if not expected_key:
        logger.error('WEBSITE_ENQUIRY_API_KEY is not configured in Django settings/environment')
        return JsonResponse({'error': 'Server API key is not configured'}, status=500)
    if api_key != expected_key:
        return JsonResponse({'error': 'Invalid API key'}, status=403)

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    first_name = str(_extract_payload_value(data, 'first_name', 'first name', 'firstname', 'first-name', 'first')).strip()
    last_name = str(_extract_payload_value(data, 'last_name', 'last name', 'lastname', 'last-name', 'last')).strip()
    full_name = f"{first_name} {last_name}".strip()

    name = str(_extract_payload_value(data, 'name', 'full name', 'your name', 'contact name')).strip()[:255]
    if not name:
        name = full_name[:255]

    # If a form only sends full name, split it into first/last for structured fields.
    if name and (not first_name or not last_name):
        name_parts = [p for p in name.split() if p]
        if name_parts and not first_name:
            first_name = name_parts[0]
        if len(name_parts) > 1 and not last_name:
            last_name = ' '.join(name_parts[1:])

    email = str(_extract_payload_value(data, 'email', 'email address')).strip()[:254] or None
    phone = str(_extract_payload_value(data, 'phone', 'telephone', 'mobile')).strip()[:100] or None
    region = str(_extract_payload_value(data, 'uk_or_roi', 'uk or roi', 'region', 'country')).strip()[:30]
    address = str(_extract_payload_value(data, 'address', 'address line 1', 'full address')).strip()
    newsletter_signup = _extract_bool_value(
        data,
        'newsletter_signup',
        'newsletter signup',
        'sign up to our newsletter',
        'newsletter',
    )
    contact_for_design_appointment = _extract_bool_value(
        data,
        'contact_for_design_appointment',
        'contact me for a design appointment',
        'contact me for design appointment',
    )
    subject = str(_extract_payload_value(data, 'subject')).strip()[:500]
    message = str(_extract_payload_value(data, 'message', 'comments', 'enquiry')).strip()
    source = str(_extract_payload_value(data, 'source', 'form_title', 'form name')).strip()[:255]

    # Require at least a name or email
    if not name and not email:
        return JsonResponse({'error': 'At least name or email is required'}, status=400)

    # Get client IP
    x_forwarded = request.META.get('HTTP_X_FORWARDED_FOR')
    ip = x_forwarded.split(',')[0].strip() if x_forwarded else request.META.get('REMOTE_ADDR')

    enquiry = WebsiteEnquiry.objects.create(
        name=name,
        first_name=first_name[:120],
        last_name=last_name[:120],
        email=email,
        phone=phone,
        region=region,
        address=address,
        newsletter_signup=newsletter_signup,
        contact_for_design_appointment=contact_for_design_appointment,
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
            Q(first_name__icontains=search_query) |
            Q(last_name__icontains=search_query) |
            Q(email__icontains=search_query) |
            Q(phone__icontains=search_query) |
            Q(region__icontains=search_query) |
            Q(address__icontains=search_query) |
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


@page_permission_required('website_enquiries')
def website_enquiry_detail(request, enquiry_id):
    """Return full enquiry details including raw JSON payload."""
    if request.method != 'GET':
        return JsonResponse({'error': 'GET required'}, status=405)

    try:
        enquiry = WebsiteEnquiry.objects.get(pk=enquiry_id)
    except WebsiteEnquiry.DoesNotExist:
        return JsonResponse({'error': 'Not found'}, status=404)

    return JsonResponse({
        'success': True,
        'id': enquiry.pk,
        'raw_data': enquiry.raw_data or {},
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
