"""Accounts Payable inbox views.

Reads emails from the accounts.payable@sliderobes.com shared mailbox via the
Microsoft Graph API, caches them locally, and lets users create Purchase Invoices
directly from email attachments.
"""

import json
import logging
import re as _re

from django.contrib.auth.decorators import login_required
from django.core.files.base import ContentFile
from django.core.paginator import Paginator
from django.db import models as db_models
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils.dateparse import parse_datetime
from django.utils import timezone

from .models import MailboxEmail, MailboxExemption, PurchaseInvoice, PurchaseInvoiceLineItem, Order, Supplier, PurchaseOrder
from .permissions import page_permission_required
from .purchase_invoice_views import _extract_pdf_fields, _parse_date, _parse_decimal
from .services import graph_api

logger = logging.getLogger(__name__)


_MATCHABLE_STATUSES = ('Approved', 'Received', 'Partially Received')


def _find_po_matches(emails):
    """Return {email.id: [po_dict, ...]} for unprocessed emails that have
    a potential PO match (Approved / Received / Partially Received).

    Matching uses two signals:
    1. A PO number (3-6 digits, optionally prefixed PO/PO#) found in the
       email subject.
    2. The PO supplier name containing a word from the sender name / email.
    """
    candidates = [e for e in emails if not e.is_processed and not e.is_ignored]
    if not candidates:
        return {}

    pos = list(
        PurchaseOrder.objects
        .filter(status__in=_MATCHABLE_STATUSES)
        .values('id', 'workguru_id', 'display_number', 'number', 'supplier_name', 'status', 'total')
    )
    if not pos:
        return {}

    # Build number → po lookup (upper-cased, also pure-digit key)
    po_by_number = {}
    for po in pos:
        for field in (po['display_number'], po['number']):
            if field:
                key = field.strip().upper()
                po_by_number[key] = po
                # also index by the numeric portion only
                digits = _re.sub(r'[^0-9]', '', field)
                if digits:
                    po_by_number.setdefault(digits, po)

    result = {}
    for email in candidates:
        found = {}  # po['id'] -> po  (deduplicates)

        # Signal 1: PO-like numbers in subject
        subject = email.subject or ''
        for m in _re.finditer(r'(?:PO\s*#?\s*)?(\d{3,6})\b', subject, _re.IGNORECASE):
            key = m.group(1)
            if key in po_by_number:
                po = po_by_number[key]
                found[po['id']] = po

        # Signal 2: supplier name words in sender text
        sender_text = f"{email.sender_name} {email.sender_email}".lower()
        for po in pos:
            if po['id'] in found:
                continue
            sname = (po['supplier_name'] or '').strip().lower()
            if not sname:
                continue
            # Try full name
            if sname in sender_text:
                found[po['id']] = po
            else:
                # Try first two significant words (≥4 chars)
                words = [w for w in sname.split() if len(w) >= 4]
                if words and any(w in sender_text for w in words[:2]):
                    found[po['id']] = po

        if found:
            result[email.id] = list(found.values())

    return result



@login_required
@page_permission_required('accounts_payable')
def accounts_payable_inbox(request):
    """Display the Accounts Payable shared mailbox inbox."""
    mailbox = graph_api._get_settings()['mailbox']

    emails = MailboxEmail.objects.select_related('purchase_invoice', 'processed_by').all()

    # Tab / status filter
    status_filter = request.GET.get('status', '')
    if status_filter == 'ignored':
        emails = emails.filter(is_ignored=True)
    elif status_filter == 'unprocessed':
        emails = emails.filter(is_ignored=False, is_processed=False)
    elif status_filter == 'processed':
        emails = emails.filter(is_ignored=False, is_processed=True)
    else:
        # Default: all non-ignored
        emails = emails.filter(is_ignored=False)

    search = request.GET.get('q', '').strip()
    if search:
        emails = emails.filter(
            db_models.Q(subject__icontains=search)
            | db_models.Q(sender_name__icontains=search)
            | db_models.Q(sender_email__icontains=search)
        )

    total = MailboxEmail.objects.filter(is_ignored=False).count()
    unprocessed = MailboxEmail.objects.filter(is_ignored=False, is_processed=False).count()
    processed = MailboxEmail.objects.filter(is_ignored=False, is_processed=True).count()
    ignored = MailboxEmail.objects.filter(is_ignored=True).count()

    last_synced = MailboxEmail.objects.aggregate(
        last=db_models.Max('synced_at')
    )['last']

    paginator = Paginator(emails, 100)
    page_number = request.GET.get('page', 1)
    page_obj = paginator.get_page(page_number)

    email_po_matches = _find_po_matches(page_obj.object_list)

    # Annotate each email with a CSS class based on the best matched PO status
    for email in page_obj.object_list:
        matched = email_po_matches.get(email.id, [])
        if matched:
            statuses = {po['status'] for po in matched}
            if statuses & {'Received', 'Partially Received'}:
                email.po_match_class = 'po-received'
            else:
                email.po_match_class = 'po-approved'
        else:
            email.po_match_class = ''

    return render(request, 'stock_take/accounts_payable.html', {
        'emails': page_obj,
        'page_obj': page_obj,
        'total': total,
        'unprocessed': unprocessed,
        'processed': processed,
        'ignored': ignored,
        'is_configured': graph_api.is_configured(),
        'status_filter': status_filter,
        'search_query': search,
        'mailbox': mailbox,
        'last_synced': last_synced,
        'suppliers': list(Supplier.objects.values_list('name', flat=True).order_by('name')),
    })


# ── Sync from mailbox ─────────────────────────────────────────────────────────

@login_required
def sync_mailbox(request):
    """Pull emails from the shared mailbox inbox since the last sync."""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required'}, status=405)

    if not graph_api.is_configured():
        return JsonResponse({
            'success': False,
            'error': 'Microsoft Graph API credentials are not configured. See setup instructions on the page.',
        })

    mailbox = graph_api._get_settings()['mailbox']

    # Only fetch messages received after the most recently stored email.
    # Falls back to fetching everything if the DB is empty.
    last_received = MailboxEmail.objects.aggregate(
        latest=db_models.Max('received_at')
    )['latest']

    try:
        messages, error = graph_api.fetch_inbox_messages(mailbox, since=last_received)
        if error:
            return JsonResponse({'success': False, 'error': error})

        # Build exemption set once for the whole sync
        exempted = set(
            MailboxExemption.objects.values_list('email_address', flat=True)
        )

        new_count = 0
        for msg in messages:
            graph_id = msg.get('id', '')
            if not graph_id:
                continue

            sender = msg.get('from', {}).get('emailAddress', {})

            # Skip emails from exempted senders
            sender_addr = (sender.get('address') or '').strip().lower()
            if sender_addr and sender_addr in exempted:
                continue

            # Outlook item / embedded message content types to exclude
            SKIP_CONTENT_TYPES = {
                'application/vnd.ms-outlook',
                'message/rfc822',
                'application/ms-tnef',
            }

            # Build attachment metadata list — skip inline images and Outlook items
            raw_atts = msg.get('attachments', [])
            attachment_data = [
                {
                    'id': a.get('id', ''),
                    'name': a.get('name', ''),
                    'content_type': a.get('contentType', ''),
                    'size': a.get('size', 0),
                }
                for a in raw_atts
                if not a.get('isInline', False)
                and a.get('name', '')
                and (a.get('contentType') or '').lower().split(';')[0].strip() not in SKIP_CONTENT_TYPES
            ]

            received_at = None
            received_str = msg.get('receivedDateTime', '')
            if received_str:
                received_at = parse_datetime(received_str)

            _, created = MailboxEmail.objects.update_or_create(
                graph_message_id=graph_id,
                defaults={
                    'subject': (msg.get('subject') or '')[:500],
                    'sender_name': (sender.get('name') or '')[:255],
                    'sender_email': (sender.get('address') or '')[:254],
                    'received_at': received_at,
                    'body_preview': (msg.get('bodyPreview') or '')[:1000],
                    'is_read': msg.get('isRead', False),
                    'attachment_names': json.dumps(attachment_data),
                },
            )
            if created:
                new_count += 1

        return JsonResponse({
            'success': True,
            'new': new_count,
            'total': len(messages),
        })
    except Exception as exc:
        logger.exception('Unexpected error during mailbox sync')
        return JsonResponse({'success': False, 'error': str(exc)})


# ── Create invoice from email ─────────────────────────────────────────────────

@login_required
def create_invoice_from_email(request, email_id):
    """Create a PurchaseInvoice from an email with full form data + downloaded attachment."""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required'}, status=405)

    email = get_object_or_404(MailboxEmail, id=email_id)
    mailbox = graph_api._get_settings()['mailbox']

    # Accept multipart (full modal form) or plain JSON (legacy simple create)
    ct = request.content_type or ''
    if 'multipart' in ct:
        data = request.POST
    else:
        try:
            data = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            data = {}

    invoice_number = (data.get('invoice_number') or '').strip()
    if not invoice_number:
        return JsonResponse({'success': False, 'error': 'Invoice number is required'}, status=400)

    attachment_id = (data.get('attachment_id') or '').strip()

    # Resolve which attachment to download
    attachment_list = email.attachment_list
    chosen = None
    if attachment_id:
        chosen = next((a for a in attachment_list if a['id'] == attachment_id), None)
    if not chosen and attachment_list:
        chosen = next(
            (a for a in attachment_list if 'pdf' in a.get('content_type', '').lower()),
            attachment_list[0],
        )

    # Create the invoice with all form fields
    total_val = _parse_decimal(data.get('total', '0'))
    invoice = PurchaseInvoice.objects.create(
        invoice_number=invoice_number,
        reference=(data.get('reference') or '').strip(),
        supplier_reference=(data.get('supplier_reference') or '').strip(),
        supplier_name=(data.get('supplier_name') or '').strip(),
        date=_parse_date(data.get('date', '')),
        due_date=_parse_date(data.get('due_date', '')),
        status=data.get('status', 'Draft'),
        total=total_val,
        notes=(data.get('notes') or '').strip(),
        created_by=request.user.get_full_name() or request.user.username,
    )

    # Parse and save line items (multipart format)
    if 'multipart' in ct:
        idx = 0
        while data.get(f'line_desc_{idx}') is not None:
            desc = (data.get(f'line_desc_{idx}') or '').strip()
            qty = _parse_decimal(data.get(f'line_qty_{idx}', '1'), '1')
            rate = _parse_decimal(data.get(f'line_rate_{idx}', '0'))
            order_id = data.get(f'line_order_{idx}', '') or None
            if desc:
                line = PurchaseInvoiceLineItem.objects.create(
                    invoice=invoice,
                    description=desc,
                    quantity=qty,
                    rate=rate,
                    line_total=qty * rate,
                    sort_order=idx,
                )
                if order_id:
                    try:
                        line.order = Order.objects.get(id=int(order_id))
                        line.save(update_fields=['order'])
                    except (Order.DoesNotExist, ValueError):
                        pass
            idx += 1

    # Recalculate total from lines if no flat total was provided
    if total_val == 0:
        line_total = invoice.line_items.aggregate(t=db_models.Sum('line_total'))['t']
        if line_total:
            invoice.total = line_total
            invoice.save(update_fields=['total'])

    # Download and attach the file from Graph API
    if chosen:
        content, filename, _att_ct, err = graph_api.download_attachment(
            mailbox, email.graph_message_id, chosen['id']
        )
        if not err:
            invoice.attachment.save(filename, ContentFile(content), save=True)
            # Use filename as reference if none was provided
            if not invoice.reference:
                invoice.reference = filename
                invoice.save(update_fields=['reference'])

    # Link purchase order if provided
    po_id = (data.get('purchase_order_id') or '').strip()
    if po_id:
        from .models import PurchaseOrder
        try:
            invoice.purchase_orders.add(PurchaseOrder.objects.get(id=int(po_id)))
        except (PurchaseOrder.DoesNotExist, ValueError):
            pass

    # Link email → invoice
    email.purchase_invoice = invoice
    email.is_processed = True
    email.processed_by = request.user
    email.save(update_fields=['purchase_invoice', 'is_processed', 'processed_by'])

    return JsonResponse({
        'success': True,
        'redirect': reverse('purchase_invoice_detail', args=[invoice.id]),
    })


# ── Parse email attachment PDF ────────────────────────────────────────────────

@login_required
def parse_email_attachment(request, email_id, attachment_id):
    """Download an email attachment from Graph API and extract invoice fields from it."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    email = get_object_or_404(MailboxEmail, id=email_id)

    # Validate attachment belongs to this email
    known_ids = [a['id'] for a in email.attachment_list]
    if attachment_id not in known_ids:
        return JsonResponse({'error': 'Attachment not found'}, status=404)

    chosen = next((a for a in email.attachment_list if a['id'] == attachment_id), None)
    if not chosen or 'pdf' not in (chosen.get('content_type') or '').lower():
        return JsonResponse({'success': True, 'extracted': {}})

    mailbox = graph_api._get_settings()['mailbox']
    content, _filename, _ct, err = graph_api.download_attachment(
        mailbox, email.graph_message_id, attachment_id
    )
    if err:
        return JsonResponse({'success': False, 'error': f'Could not download attachment: {err}'})

    extracted = _extract_pdf_fields(content)
    return JsonResponse({'success': True, 'extracted': extracted})


# ── Ignore email ──────────────────────────────────────────────────────────────

@login_required
def ignore_email(request, email_id):
    """Mark an email as ignored (or un-ignore it)."""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required'}, status=405)
    email = get_object_or_404(MailboxEmail, id=email_id)
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        data = {}
    email.is_ignored = data.get('ignore', True)
    email.save(update_fields=['is_ignored'])
    return JsonResponse({'success': True, 'is_ignored': email.is_ignored})


# ── Unprocess email ───────────────────────────────────────────────────────────

@login_required
def unprocess_email(request, email_id):
    """Mark a processed email as unprocessed (unlinks invoice, does not delete it)."""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required'}, status=405)
    email = get_object_or_404(MailboxEmail, id=email_id)
    email.is_processed = False
    email.purchase_invoice = None
    email.processed_by = None
    email.save(update_fields=['is_processed', 'purchase_invoice', 'processed_by'])
    return JsonResponse({'success': True})


# ── Link existing invoice to email ───────────────────────────────────────────

@login_required
def link_existing_invoice_to_email(request, email_id):
    """Link an already-existing PurchaseInvoice to this email and mark it as processed."""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required'}, status=405)

    email = get_object_or_404(MailboxEmail, id=email_id)

    try:
        body = json.loads(request.body)
        invoice_id = int(body.get('invoice_id', 0))
    except (json.JSONDecodeError, ValueError, TypeError):
        return JsonResponse({'success': False, 'error': 'Invalid request body'}, status=400)

    invoice = get_object_or_404(PurchaseInvoice, id=invoice_id)

    email.purchase_invoice = invoice
    email.is_processed = True
    email.processed_by = request.user
    email.save(update_fields=['purchase_invoice', 'is_processed', 'processed_by'])

    from django.urls import reverse
    return JsonResponse({
        'success': True,
        'redirect': reverse('purchase_invoice_detail', args=[invoice.id]),
    })


# ── Download attachment ───────────────────────────────────────────────────────

@login_required
def download_mailbox_attachment(request, email_id, attachment_id):
    """Proxy a single attachment from the Graph API to the browser."""
    email = get_object_or_404(MailboxEmail, id=email_id)
    mailbox = graph_api._get_settings()['mailbox']

    # Validate attachment_id belongs to this email
    known_ids = [a['id'] for a in email.attachment_list]
    if attachment_id not in known_ids:
        return HttpResponse('Attachment not found', status=404)

    content, filename, content_type, err = graph_api.download_attachment(
        mailbox, email.graph_message_id, attachment_id
    )
    if err:
        return HttpResponse(f'Error downloading attachment: {err}', status=502)

    content_type = content_type or 'application/octet-stream'
    response = HttpResponse(content, content_type=content_type)
    # Open PDFs inline in the browser; force-download everything else
    if 'pdf' in content_type.lower():
        response['Content-Disposition'] = f'inline; filename="{filename}"'
    else:
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


# ── Exemption list ────────────────────────────────────────────────────────────

@login_required
def manage_exemptions(request):
    """GET: return JSON list of exempted addresses.
    POST {action:'add', email, note}: add an exemption.
    POST {action:'remove', email}: remove an exemption.
    """
    if request.method == 'GET':
        exemptions = list(
            MailboxExemption.objects.values('id', 'email_address', 'note', 'created_at')
        )
        return JsonResponse({'exemptions': exemptions})

    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required'}, status=405)

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'success': False, 'error': 'Invalid JSON'}, status=400)

    action = data.get('action', '')
    email_addr = (data.get('email') or '').strip().lower()
    if not email_addr:
        return JsonResponse({'success': False, 'error': 'Email address required'})

    if action == 'add':
        note = (data.get('note') or '')[:255]
        obj, created = MailboxExemption.objects.get_or_create(
            email_address=email_addr,
            defaults={'note': note},
        )
        if not created and note:
            obj.note = note
            obj.save(update_fields=['note'])
        return JsonResponse({'success': True, 'created': created, 'id': obj.id})

    if action == 'remove':
        deleted, _ = MailboxExemption.objects.filter(email_address=email_addr).delete()
        return JsonResponse({'success': True, 'deleted': deleted})

    return JsonResponse({'success': False, 'error': 'Unknown action'}, status=400)


# ── Bulk operations ───────────────────────────────────────────────────────────

@login_required
def bulk_email_action(request):
    """POST {action: 'delete'|'ignore', ids: [1,2,3]}: operate on multiple emails."""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required'}, status=405)

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'success': False, 'error': 'Invalid JSON'}, status=400)

    ids = data.get('ids', [])
    action = data.get('action', '')

    if not ids or not isinstance(ids, list):
        return JsonResponse({'success': False, 'error': 'No ids provided'})

    qs = MailboxEmail.objects.filter(id__in=ids)

    if action == 'delete':
        count, _ = qs.delete()
        return JsonResponse({'success': True, 'count': count})

    if action == 'ignore':
        count = qs.update(is_ignored=True)
        return JsonResponse({'success': True, 'count': count})

    if action == 'unignore':
        count = qs.update(is_ignored=False)
        return JsonResponse({'success': True, 'count': count})

    return JsonResponse({'success': False, 'error': 'Unknown action'}, status=400)
