"""
Invoice views – displays invoices from the local DB.
"""

import json
import logging
import os
import queue
import re
import threading
from datetime import datetime
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser

from django.db import transaction
from django.core.paginator import Paginator
from django.db.models import Case, Count, ExpressionWrapper, DecimalField as OrmDecimalField, F, IntegerField, Q, Sum, When
from django.shortcuts import render, get_object_or_404
from django.template.loader import render_to_string
from django.contrib.auth.decorators import login_required
from django.http import StreamingHttpResponse, JsonResponse
from django.utils import timezone


from .models import Invoice, OverheadPurchaseOrder, PurchaseInvoice, PurchaseOrder, PurchaseOrderProduct, AnthillSale, Customer, Order, EnabledGLCode, Supplier

logger = logging.getLogger(__name__)


def _sanitize_activity_suffix(value: str) -> str:
    cleaned = re.sub(r'[^A-Za-z0-9-]+', '-', (value or '').strip().upper())
    cleaned = cleaned.strip('-')
    return cleaned or 'UNKNOWN'


def _contract_base_id(contract_number: str) -> str:
    """Return the base activity ID for a contract number.

    Preference order:
      1) trailing 6-digit sale number from contract (e.g. BFS-SD-425035 -> 425035)
      2) sanitized contract fallback
    """
    contract = (contract_number or '').strip().upper()
    m = re.search(r'(\d{6})$', contract)
    if m:
        return m.group(1)
    return _sanitize_activity_suffix(contract)


def _build_placeholder_activity_id(contract_number: str) -> str:
    """
    Create a stable placeholder activity ID for scraped invoice payments.
    """
    base = _contract_base_id(contract_number)

    if not AnthillSale.objects.filter(anthill_activity_id=base).exists():
        return base

    for idx in range(2, 1000):
        candidate = f'{str(base)[:25]}-{idx}'
        if not AnthillSale.objects.filter(anthill_activity_id=candidate).exists():
            return candidate

    # Extremely defensive fallback; keeps field within max_length=30.
    return f'{str(base)[:20]}-{int(timezone.now().timestamp()) % 1000000000}'


@transaction.atomic
def _ensure_sale_and_customer(contract_number: str, customer_name: str, showroom: str):
    """
    Ensure Atlas has both Customer and AnthillSale records for a payment contract.
    Also links the AnthillSale to a matching Order if one exists (by sale_number).

    Returns:
        (sale, customer)
    """
    contract = (contract_number or '').strip()
    if not contract:
        return None, None

    name = (customer_name or '').strip()
    location = (showroom or '').strip()

    sale = (
        AnthillSale.objects
        .select_related('customer', 'order')
        .filter(contract_number=contract)
        .order_by('-activity_date', '-pk')
        .first()
    )

    # Fall back to matching the real Anthill sale by its base activity ID (the
    # trailing 6-digit sale number) when the contract number hasn't been linked
    # yet. Without this we'd create a phantom "<base>-N" duplicate sale and
    # attach the scraped payment to it instead of the genuine sale.
    if not sale:
        base = _contract_base_id(contract)
        if base:
            sale = (
                AnthillSale.objects
                .select_related('customer', 'order')
                .filter(anthill_activity_id=base)
                .order_by('-activity_date', '-pk')
                .first()
            )

    customer = None
    if sale and sale.customer_id:
        customer = sale.customer
    elif name:
        customer = Customer.objects.filter(name=name).first()
    elif sale and sale.customer_name:
        customer = Customer.objects.filter(name=sale.customer_name).first()

    if not customer and (name or sale):
        customer = Customer.objects.create(
            name=name or (sale.customer_name if sale else ''),
            location=location or (sale.location if sale else ''),
        )

    if sale:
        update_fields = []
        if customer and sale.customer_id != customer.pk:
            sale.customer = customer
            update_fields.append('customer')
        if not sale.customer_name and name:
            sale.customer_name = name
            update_fields.append('customer_name')
        if not sale.contract_number and contract:
            sale.contract_number = contract
            update_fields.append('contract_number')
        if not sale.location and location:
            sale.location = location
            update_fields.append('location')
        
        # Link to Order if not already linked
        if not sale.order_id:
            matching_order = Order.objects.filter(sale_number=sale.anthill_activity_id).first()
            if matching_order:
                sale.order = matching_order
                update_fields.append('order')
        
        if update_fields:
            sale.save(update_fields=update_fields)
        return sale, customer

    # When creating new sale, calculate activity_id and try to link to matching Order
    activity_id = _build_placeholder_activity_id(contract)
    matching_order = Order.objects.filter(sale_number=activity_id).first()
    
    sale = AnthillSale.objects.create(
        anthill_activity_id=activity_id,
        contract_number=contract,
        customer=customer,
        customer_name=name,
        location=location,
        category='3',
        activity_type='Room Sale',
        status='open',
        anthill_customer_id=(customer.anthill_customer_id if customer else ''),
        activity_date=timezone.now(),
        order=matching_order,  # Link to matching Order if it exists
    )
    return sale, customer


_INV_PER_PAGE = 50


# ── Invoice list (combined sales + purchase) ──────────────────────
@login_required
def invoices_list(request):
    """Display sales or purchase invoices — combined page with a type selector."""
    invoice_type = request.GET.get('type', 'sales')
    if invoice_type not in ('sales', 'purchase'):
        invoice_type = 'sales'
    status_filter = request.GET.get('status', 'all')
    search_query = request.GET.get('q', '').strip()
    try:
        page_num = max(1, int(request.GET.get('page', 1)))
    except (ValueError, TypeError):
        page_num = 1

    if invoice_type == 'purchase':
        context = _purchase_invoices_context(status_filter, search_query, page_num)
    else:
        context = _sales_invoices_context(request, status_filter, search_query, page_num)

    context['invoice_type'] = invoice_type
    context['status_filter'] = status_filter
    context['search_query'] = search_query

    # Client-side tab switch (Sales ⇄ Purchase / status / pagination) fetches
    # just the toolbar + body fragments, so there's no full page reload.
    if request.GET.get('partial') == '1':
        return JsonResponse({
            'toolbar': render_to_string('stock_take/partials/invoices_toolbar.html', context, request=request),
            'body': render_to_string('stock_take/partials/invoices_body.html', context, request=request),
            'title': 'Invoices ({:,})'.format(context.get('total_invoices', 0)),
        })

    return render(request, 'stock_take/invoices.html', context)


def _sales_invoices_context(request, status_filter, search_query, page_num):
    """Build context for the Sales Invoices tab."""
    qs = Invoice.objects.all()

    # Location filter — match Anthill sync behaviour
    location_filter = ''
    if hasattr(request.user, 'profile'):
        location_filter = (request.user.profile.selected_location or '').strip()
    if location_filter:
        qs = qs.filter(showroom__icontains=location_filter)

    # Status / payment filters
    if status_filter == 'unpaid':
        qs = qs.exclude(payment_status='paid')
    elif status_filter == 'draft':
        qs = qs.filter(status='Draft')
    elif status_filter == 'approved':
        qs = qs.filter(status='Approved')
    elif status_filter == 'sent':
        qs = qs.filter(status='Sent')
    elif status_filter == 'overdue':
        qs = qs.filter(is_overdue=True)

    # Text search
    if search_query:
        qs = qs.filter(
            Q(invoice_number__icontains=search_query)
            | Q(client_name__icontains=search_query)
            | Q(project_number__icontains=search_query)
            | Q(description__icontains=search_query)
        )

    qs = qs.order_by('-date', '-id')

    # Aggregate stats over the full filtered set (before pagination)
    stats = qs.aggregate(
        total_invoices=Count('id'),
        total_value=Sum('total'),
        total_outstanding=Sum('amount_outstanding'),
        total_paid_sum=Sum('amount_paid'),
        paid_count=Count(Case(When(payment_status='paid', then=1), output_field=IntegerField())),
        unpaid_count=Count(Case(When(payment_status='unpaid', then=1), output_field=IntegerField())),
        partial_count=Count(Case(When(payment_status='partial', then=1), output_field=IntegerField())),
        overdue_count=Count(Case(When(is_overdue=True, then=1), output_field=IntegerField())),
    )

    # Paginate — only load _INV_PER_PAGE records per page
    paginator = Paginator(qs.select_related('customer', 'order'), _INV_PER_PAGE)
    page_obj = paginator.get_page(page_num)
    invoices = list(page_obj.object_list)

    # Map contract numbers to AnthillSale PKs and customer PKs for current page.
    # A single bulk query replaces the previous per-row _ensure_sale_and_customer
    # backfill (which issued several queries per invoice). Link-integrity backfill
    # is handled during the Anthill sync, not on every list page load.
    contract_numbers = {inv.contract_number for inv in invoices if inv.contract_number}
    sale_map = {}
    sale_customer_map = {}
    if contract_numbers:
        for cn, pk, cust_pk in (
            AnthillSale.objects.filter(contract_number__in=contract_numbers)
            .values_list('contract_number', 'pk', 'customer_id')
        ):
            sale_map[cn] = pk
            if cust_pk:
                sale_customer_map[cn] = cust_pk

    unlinked_names = set()
    for inv in invoices:
        if not inv.customer_id and inv.contract_number not in sale_customer_map and inv.client_name:
            unlinked_names.add(inv.client_name)
    name_customer_map = {}
    if unlinked_names:
        name_customer_map = dict(
            Customer.objects.filter(name__in=unlinked_names)
            .values_list('name', 'pk')
        )

    for inv in invoices:
        inv.sale_pk = sale_map.get(inv.contract_number)
        if not inv.customer_id:
            inv.sale_customer_pk = (
                sale_customer_map.get(inv.contract_number)
                or name_customer_map.get(inv.client_name)
            )

    last_sync = Invoice.objects.order_by('-synced_at').values_list('synced_at', flat=True).first()

    return {
        'invoices': invoices,
        'page_obj': page_obj,
        'total_invoices': stats['total_invoices'] or 0,
        'total_value': stats['total_value'] or Decimal('0'),
        'total_outstanding': stats['total_outstanding'] or Decimal('0'),
        'total_paid': stats['total_paid_sum'] or Decimal('0'),
        'paid_count': stats['paid_count'] or 0,
        'unpaid_count': stats['unpaid_count'] or 0,
        'partial_count': stats['partial_count'] or 0,
        'overdue_count': stats['overdue_count'] or 0,
        'last_sync': last_sync,
    }


def _purchase_invoices_context(status_filter, search_query, page_num):
    """Build context for the Purchase Invoices tab."""
    qs = PurchaseInvoice.objects.all()

    if status_filter == 'unpaid':
        qs = qs.exclude(payment_status='paid')
    elif status_filter not in ('', 'all'):
        qs = qs.filter(status=status_filter.capitalize())

    if search_query:
        qs = qs.filter(
            Q(invoice_number__icontains=search_query)
            | Q(supplier_name__icontains=search_query)
            | Q(notes__icontains=search_query)
        )

    qs = qs.order_by('-invoice_number')

    # Aggregate stats over the full filtered set (before pagination).
    # amount_outstanding is a @property (total - amount_paid), so we must derive it.
    _outstanding_expr = ExpressionWrapper(F('total') - F('amount_paid'), output_field=OrmDecimalField(max_digits=12, decimal_places=2))
    stats = qs.aggregate(
        total_invoices=Count('id'),
        total_value=Sum('total'),
        total_outstanding=Sum(_outstanding_expr),
        total_paid_sum=Sum('amount_paid'),
        paid_count=Count(Case(When(payment_status='paid', then=1), output_field=IntegerField())),
        unpaid_count=Count(Case(When(payment_status='unpaid', then=1), output_field=IntegerField())),
        partial_count=Count(Case(When(payment_status='partial', then=1), output_field=IntegerField())),
    )

    # Paginate — only load _INV_PER_PAGE records per page
    paginator = Paginator(qs, _INV_PER_PAGE)
    page_obj = paginator.get_page(page_num)
    invoices = list(page_obj.object_list)

    suppliers = list(Supplier.objects.values_list('name', flat=True).order_by('name'))

    return {
        'invoices': invoices,
        'page_obj': page_obj,
        'total_invoices': stats['total_invoices'] or 0,
        'total_value': stats['total_value'] or Decimal('0'),
        'total_outstanding': stats['total_outstanding'] or Decimal('0'),
        'total_paid': stats['total_paid_sum'] or Decimal('0'),
        'paid_count': stats['paid_count'] or 0,
        'unpaid_count': stats['unpaid_count'] or 0,
        'partial_count': stats['partial_count'] or 0,
        'suppliers': suppliers,
        'opo_category_choices': OverheadPurchaseOrder.CATEGORY_CHOICES,
        'opo_gl_codes': EnabledGLCode.objects.filter(enabled=True).order_by('code'),
    }


# ── Invoice detail ────────────────────────────────────────────────
@login_required
def invoice_detail(request, invoice_id):
    """Display full detail for a single invoice."""
    invoice = get_object_or_404(
        Invoice.objects.select_related('customer', 'order'),
        id=invoice_id,
    )
    line_items = invoice.line_items.all()
    payments = invoice.payments.all()
    linked_pos = invoice.purchase_orders.all().order_by('display_number')

    # For each linked PO, annotate with partial-linking info
    linked_pos_info = []
    for po in linked_pos:
        total_products = po.products.count()
        linked_products = invoice.linked_products.filter(purchase_order=po).count()
        linked_pos_info.append({
            'po': po,
            'total_products': total_products,
            'linked_products': linked_products,
            'is_partial': 0 < linked_products < total_products,
        })

    # Auto-detect currency: Irish showrooms / customers default to EUR
    _irish_keywords = ('dublin', 'ireland', 'cork', 'limerick', 'galway', 'waterford',
                       'kilkenny', 'wexford', 'wicklow', 'kildare', 'roi')
    auto_currency = 'GBP'
    showroom_lc = (invoice.showroom or '').lower()
    if any(kw in showroom_lc for kw in _irish_keywords):
        auto_currency = 'EUR'
    elif invoice.customer:
        country_lc = (invoice.customer.country or '').lower()
        if country_lc in ('ireland', 'ie', 'roi', 'republic of ireland'):
            auto_currency = 'EUR'
    # Use saved currency if already set, otherwise fall back to auto-detected
    display_currency = invoice.currency if invoice.currency in ('GBP', 'EUR') else auto_currency

    gl_codes = EnabledGLCode.objects.filter(enabled=True).order_by('code')

    # Amendments: invoices that have this invoice as their parent
    amendments = invoice.amendments.order_by('created_at')

    context = {
        'invoice': invoice,
        'line_items': line_items,
        'payments': payments,
        'linked_pos': linked_pos,
        'linked_pos_info': linked_pos_info,
        'gl_codes': gl_codes,
        'auto_currency': auto_currency,
        'display_currency': display_currency,
        'amendments': amendments,
    }
    return render(request, 'stock_take/invoice_detail.html', context)


# ── Push single invoice to Xero as Draft ──────────────────────────
@login_required
def push_invoice_to_xero(request, invoice_id):
    """Create a DRAFT sales invoice in Xero from a local Invoice record."""
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'POST required'}, status=405)
    try:
        return _push_invoice_to_xero_impl(request, invoice_id)
    except Exception as exc:
        logger.exception('Unexpected error in push_invoice_to_xero for invoice %s', invoice_id)
        return JsonResponse({'ok': False, 'error': str(exc)})


def _push_invoice_to_xero_impl(request, invoice_id):
    """Inner implementation — called by push_invoice_to_xero.

    Accepts optional JSON body:
      currency    – 'GBP' or 'EUR' (defaults to invoice.currency or auto-detected)
      gl_code     – Xero account code string (defaults to first enabled GL code)
      attach_pdf  – true/false, whether to upload the local attachment to Xero
    """
    invoice = get_object_or_404(Invoice, id=invoice_id)

    # Parse optional request body
    try:
        body = json.loads(request.body) if request.body else {}
    except (json.JSONDecodeError, ValueError):
        body = {}

    # Currency: request body > invoice field > auto-detect
    _irish_keywords = ('dublin', 'ireland', 'cork', 'limerick', 'galway', 'waterford',
                       'kilkenny', 'wexford', 'wicklow', 'kildare', 'roi')
    showroom_lc = (invoice.showroom or '').lower()
    auto_currency = 'GBP'
    if any(kw in showroom_lc for kw in _irish_keywords):
        auto_currency = 'EUR'
    elif invoice.customer:
        country_lc = (invoice.customer.country or '').lower()
        if country_lc in ('ireland', 'ie', 'roi', 'republic of ireland'):
            auto_currency = 'EUR'

    currency = body.get('currency', '').strip().upper()
    if currency not in ('GBP', 'EUR'):
        currency = invoice.currency if invoice.currency in ('GBP', 'EUR') else auto_currency

    # GL code: request body > first enabled code
    gl_code = (body.get('gl_code') or '').strip()
    if not gl_code:
        first_gl = EnabledGLCode.objects.filter(enabled=True).order_by('code').first()
        gl_code = first_gl.code if first_gl else ''

    attach_pdf = bool(body.get('attach_pdf', False))

    from .services import xero_api

    # Look up contact in Xero; if missing, auto-create from local customer/invoice data.
    contact_id = ''
    if invoice.client_name:
        contact_id = xero_api.find_contact_by_name(invoice.client_name)

    if not contact_id:
        local_customer = invoice.customer
        if not local_customer and invoice.client_name:
            local_customer = Customer.objects.filter(name=invoice.client_name).first()

        first_name = ''
        last_name = ''
        email = ''
        phone = ''
        address_1 = ''
        address_2 = ''
        city = ''
        region = ''
        postcode = ''
        country = ''

        if local_customer:
            first_name = (local_customer.first_name or '').strip()
            last_name = (local_customer.last_name or '').strip()
            email = (local_customer.email or '').strip()
            phone = (local_customer.phone or '').strip()
            address_1 = (local_customer.address_1 or local_customer.address or '').strip()
            address_2 = (local_customer.address_2 or '').strip()
            city = (local_customer.city or local_customer.suburb or '').strip()
            region = (local_customer.state or '').strip()
            postcode = (local_customer.postcode or '').strip()
            country_val = (local_customer.country or '').strip()
        else:
            country_val = ''

        create_res = xero_api.create_contact(
            name=invoice.client_name,
            first_name=first_name,
            last_name=last_name,
            email=email,
            phone=phone,
            address_line1=address_1,
            address_line2=address_2,
            city=city,
            region=region,
            postal_code=postcode,
            country=country_val,
        )

        contacts = (create_res or {}).get('Contacts', []) if isinstance(create_res, dict) else []
        if contacts:
            contact_id = contacts[0].get('ContactID', '')

        # Fallback lookup in case Xero accepted but response shape differs.
        if not contact_id and invoice.client_name:
            contact_id = xero_api.find_contact_by_name(invoice.client_name)

        if not contact_id:
            err = xero_api.get_last_api_error() or f'Contact "{invoice.client_name}" not found in Xero and auto-create failed.'
            return JsonResponse({'ok': False, 'error': err})

        if local_customer and not local_customer.xero_id:
            local_customer.xero_id = contact_id
            local_customer.save(update_fields=['xero_id'])

    # Build line items — single line with the invoice total
    line_item = {
        "Description": f"{invoice.payment_type or 'Payment'} — {invoice.contract_number}",
        "Quantity": "1",
        "UnitAmount": str(invoice.total),
    }
    if gl_code:
        line_item["AccountCode"] = gl_code

    invoice_data = {
        "Type": "ACCREC",
        "Contact": {"ContactID": contact_id},
        "Status": "DRAFT",
        "CurrencyCode": currency,
        "LineAmountTypes": "Inclusive",
        "LineItems": [line_item],
        "Reference": invoice.contract_number,
    }

    if invoice.date:
        invoice_data["Date"] = invoice.date.isoformat()
        invoice_data["DueDate"] = (invoice.due_date or invoice.date).isoformat()

    # Save currency choice back to the invoice record
    if invoice.currency != currency:
        invoice.currency = currency
        invoice.save(update_fields=['currency'])

    payload = {"Invoices": [invoice_data]}
    result = xero_api._api_put("Invoices", payload)

    if result is None:
        err = xero_api.get_last_api_error() or 'Unknown Xero API error'
        return JsonResponse({'ok': False, 'error': err})

    # Extract the Xero InvoiceID from the response
    invoices_resp = result.get('Invoices', [])
    xero_invoice_id = ''
    if invoices_resp:
        xero_invoice_id = invoices_resp[0].get('InvoiceID', '')
        invoice.xero_id = xero_invoice_id
        invoice.save(update_fields=['xero_id'])

    # Optionally attach the local PDF to the Xero invoice
    attachment_warning = None
    if attach_pdf and xero_invoice_id and invoice.attachment:
        try:
            import os
            import mimetypes
            file_path = invoice.attachment.path
            filename = os.path.basename(file_path)
            mime_type, _ = mimetypes.guess_type(filename)
            mime_type = mime_type or 'application/octet-stream'
            with open(file_path, 'rb') as fh:
                file_bytes = fh.read()
            attach_result = xero_api.attach_file_to_invoice(
                xero_invoice_id, filename, file_bytes, mime_type
            )
            if attach_result is None:
                attachment_warning = xero_api.get_last_api_error() or 'Attachment upload failed'
        except Exception as exc:
            logger.error(f'Failed to attach PDF to Xero invoice {xero_invoice_id}: {exc}')
            attachment_warning = str(exc)

    response_data = {'ok': True, 'xero_id': invoice.xero_id, 'currency': currency, 'gl_code': gl_code}
    if attachment_warning:
        response_data['attachment_warning'] = attachment_warning
    return JsonResponse(response_data)


# ── Recalculate invoice totals from line items ────────────────────
@login_required
def recalculate_invoice(request, invoice_id):
    """Recompute invoice subtotal/tax/total from line items and update amount_outstanding."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    invoice = get_object_or_404(Invoice, id=invoice_id)

    if not invoice.line_items.exists():
        return JsonResponse({'success': False, 'error': 'No line items — recalculation skipped'})

    agg = invoice.line_items.aggregate(s=Sum('line_total'), t=Sum('tax_amount'))
    subtotal = agg['s'] or Decimal('0')
    total_tax = agg['t'] or Decimal('0')
    total = subtotal + total_tax
    amount_outstanding = max(total - invoice.amount_paid, Decimal('0'))

    if total > 0 and invoice.amount_paid >= total:
        payment_status = 'paid'
    elif invoice.amount_paid > 0:
        payment_status = 'partial'
    else:
        payment_status = 'unpaid'

    invoice.subtotal = subtotal
    invoice.total_tax = total_tax
    invoice.total = total
    invoice.amount_outstanding = amount_outstanding
    invoice.payment_status = payment_status
    invoice.save(update_fields=['subtotal', 'total_tax', 'total', 'amount_outstanding', 'payment_status'])

    return JsonResponse({
        'success': True,
        'subtotal': str(subtotal),
        'total_tax': str(total_tax),
        'total': str(total),
        'amount_paid': str(invoice.amount_paid),
        'amount_outstanding': str(amount_outstanding),
    })


# ── Check invoices against Xero ──────────────────────────────────
@login_required
def check_invoices_in_xero(request):
    """Check which local invoices (without xero_id) already exist in Xero.

    Looks up each unique contract_number via the Xero search API and saves
    the xero_id if a match is found.  Returns the list of invoice IDs that
    were matched so the front-end can flip their buttons to ticks.
    """
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'POST required'}, status=405)

    from .services import xero_api

    # Get all local invoices that don't yet have a xero_id and have a contract number
    unlinked = Invoice.objects.filter(
        xero_id__isnull=True,
        contract_number__gt='',
    ).exclude(xero_id='').values_list('id', 'contract_number')

    # Also include ones where xero_id is empty string
    unlinked = Invoice.objects.filter(
        contract_number__gt='',
    ).filter(
        Q(xero_id__isnull=True) | Q(xero_id='')
    ).values_list('id', 'contract_number')

    # Deduplicate by contract number to avoid repeated API calls
    contract_map = {}  # contract_number -> [invoice_ids]
    for inv_id, contract in unlinked:
        contract_map.setdefault(contract, []).append(inv_id)

    matched_ids = []
    matched_xero_ids = {}  # invoice_id -> xero_id

    for contract, inv_ids in contract_map.items():
        xero_invoices = xero_api.get_invoices_by_reference(contract)
        if xero_invoices:
            xero_id = xero_invoices[0].get('InvoiceID', '')
            if xero_id:
                Invoice.objects.filter(id__in=inv_ids).update(xero_id=xero_id)
                matched_ids.extend(inv_ids)
                for inv_id in inv_ids:
                    matched_xero_ids[inv_id] = xero_id

    return JsonResponse({'ok': True, 'matched': matched_ids, 'matched_xero_ids': matched_xero_ids, 'checked': len(contract_map)})


# ── Sync via SSE (WorkGuru removed — stub) ────────────────────────
@login_required
def sync_invoices_stream(request):
    """
    SSE endpoint – previously synced invoices from WorkGuru.
    WorkGuru integration has been removed. Invoices are now synced from Xero.
    """

    def _sse(payload):
        return f"data: {json.dumps(payload)}\n\n"

    def event_stream():
        yield _sse({
            'status': 'complete',
            'message': 'WorkGuru sync has been removed. Use Xero invoice sync instead.',
            'created': 0,
            'updated': 0,
            'skipped': 0,
            'total': 0,
            'errors': [],
        })

    response = StreamingHttpResponse(event_stream(), content_type='text/event-stream')
    response['Cache-Control'] = 'no-cache'
    response['X-Accel-Buffering'] = 'no'
    return response


# ── Sync from Anthill CRM ─────────────────────────────────────────
class _AnthillPaymentsTableParser(HTMLParser):
    """Extract rows from the first <table class="sortable"> tbody."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._in_target = False
        self._in_tbody = False
        self._depth = 0
        self._in_row = False
        self._in_cell = False
        self._current_row = []
        self._current_cell_parts = []
        self.rows = []
        self.found = False

    def handle_starttag(self, tag, attrs):
        attr_dict = dict(attrs)
        if tag == 'table' and not self._in_target:
            if 'sortable' in attr_dict.get('class', '').split():
                self._in_target = True
                self._depth = 1
                return
        if not self._in_target:
            return
        if tag == 'table':
            self._depth += 1
        elif tag == 'tbody':
            self._in_tbody = True
        elif tag == 'tr' and self._in_tbody:
            self._in_row = True
            self._current_row = []
        elif tag in ('td', 'th') and self._in_row:
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
        elif tag == 'tbody':
            self._in_tbody = False
        elif tag == 'tr' and self._in_row:
            self._in_row = False
            self.rows.append(list(self._current_row))
        elif tag in ('td', 'th') and self._in_cell:
            self._in_cell = False
            text = re.sub(r'\s+', ' ', ' '.join(self._current_cell_parts)).strip()
            self._current_row.append(text)

    def handle_data(self, data):
        if self._in_cell:
            stripped = data.strip()
            if stripped:
                self._current_cell_parts.append(stripped)


def _parse_amount(raw: str) -> Decimal:
    """Parse '£1,234.56' → Decimal('1234.56')."""
    try:
        return Decimal(raw.replace('£', '').replace('€', '').replace(',', '').strip())
    except (InvalidOperation, ValueError):
        return Decimal('0')


def _parse_date(raw: str):
    """Parse 'dd Month yyyy' or 'dd/mm/yyyy' → date object."""
    if not raw:
        return None
    for fmt in ('%d %B %Y', '%d/%m/%Y', '%d-%m-%Y', '%d %b %Y'):
        try:
            return datetime.strptime(raw.strip(), fmt).date()
        except ValueError:
            continue
    return None


def _row_to_payment(row):
    """Map a raw HTML table row (list of strings) to a payment dict."""
    if len(row) < 12:
        return None
    return {
        'showroom': row[0],
        'payment_date': row[2],
        'customer': row[3],
        'payment_type': row[4],
        'amount': row[5],
        'created_by': row[6],
        'status': row[8],
        'contract_number': row[9],
        'method': row[10],
        'payment_received': row[11],
    }


def _check_payment(pay):
    """Check if a payment already exists in the DB. Returns 'matched' or 'new'."""
    amount = _parse_amount(pay['amount'])
    payment_date = _parse_date(pay['payment_date'])
    contract = pay['contract_number'].strip()

    if not contract:
        return 'new'

    existing = Invoice.objects.filter(
        contract_number=contract,
        payment_type=pay['payment_type'],
        total=amount,
        date=payment_date,
    ).exists()

    return 'matched' if existing else 'new'


def _create_invoice_from_payment(pay, now):
    """Create a new Invoice from a payment dict."""
    amount = _parse_amount(pay['amount'])
    payment_date = _parse_date(pay['payment_date'])
    received_date = _parse_date(pay['payment_received'])
    contract = pay['contract_number'].strip()

    sale, customer = _ensure_sale_and_customer(contract, pay.get('customer', ''), pay.get('showroom', ''))

    Invoice.objects.create(
        invoice_number=contract,
        contract_number=contract,
        client_name=pay['customer'],
        customer=customer,
        showroom=pay['showroom'],
        payment_type=pay['payment_type'],
        total=amount,
        amount_outstanding=amount,
        date=payment_date,
        payment_received_date=received_date,
        payment_method=pay['method'],
        anthill_payment_status=pay['status'],
        created_by=pay['created_by'],
        status='Approved' if pay['status'] == 'Confirmed' else 'Draft',
        payment_status='unpaid',
        synced_at=now,
    )


def scrape_anthill_payments(period='last_28_days', location_filter='', emit=None):
    """Scrape the Anthill CRM "Payments" screen and return the raw payment rows.

    Shared by the SSE invoice-sync endpoint and the calendar payment check.
    ``emit(payload)`` is an optional progress callback (same event shape the SSE
    endpoint streams); it is called best-effort and never allowed to raise.
    Returns ``(scraped_rows, filtered_out)`` where each row is the dict produced
    by :func:`_row_to_payment`.

    Raises ``RuntimeError`` on hard failures (missing credentials, Playwright not
    installed) so callers can decide how to surface them.
    """
    def _e(payload):
        if emit:
            try:
                emit(payload)
            except Exception:
                pass

    username = os.getenv('ANTHILL_USER_USERNAME')
    password = os.getenv('ANTHILL_USER_PASSWORD')
    subdomain = os.getenv('ANTHILL_SUBDOMAIN', 'sliderobes')

    if not username or not password:
        raise RuntimeError('ANTHILL_USER_USERNAME / ANTHILL_USER_PASSWORD not configured.')

    base_url = f'https://{subdomain}.anthillcrm.com'
    period_qs = '' if period == 'all_time' else period
    target_url = f'{base_url}/n/screens/12/CAIaEgmLsAFMrjHYQhGCvnwKumuXYyiDAw?d={period_qs}'

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise RuntimeError('Playwright is not installed on the server.')

    filtered_out = 0
    loc_label = location_filter or 'All Locations'
    scraped_rows = []   # collect raw payment dicts here

    # ── Scrape with Playwright (no ORM calls) ────
    with sync_playwright() as p:
        _e({'type': 'status', 'message': 'Launching browser...'})

        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        )
        page = context.new_page()

        _e({'type': 'status', 'message': 'Connecting to Anthill CRM...'})
        page.goto(target_url, timeout=30000)
        page.wait_for_load_state('domcontentloaded', timeout=15000)

        # Handle login
        current_url = page.url.lower()
        if 'sign-in' in current_url or 'login' in current_url or 'signin' in current_url:
            _e({'type': 'status', 'message': 'Logging in...'})
            all_inputs = page.locator('input:visible').all()
            text_inputs = [inp for inp in all_inputs
                           if (inp.get_attribute('type') or 'text').lower() in ('text', 'email', '')]
            pass_inputs = [inp for inp in all_inputs
                           if (inp.get_attribute('type') or '').lower() == 'password']
            if text_inputs:
                text_inputs[0].fill(username)
            if pass_inputs:
                pass_inputs[0].fill(password)
            submit = page.locator('button[type="submit"], input[type="submit"]').first
            submit.click()
            page.wait_for_load_state('networkidle', timeout=30000)
            if '/n/screens/12/' not in page.url:
                page.goto(target_url, timeout=30000)
                page.wait_for_load_state('networkidle', timeout=30000)

        # Wait for the table
        if location_filter:
            _e({'type': 'status', 'message': f'Filtering for {loc_label}...'})
        else:
            _e({'type': 'status', 'message': 'Waiting for payments table...'})
        try:
            page.wait_for_selector('table.sortable tbody tr', timeout=30000)
        except Exception:
            pass

        # Determine page count
        pager = page.locator('#component-1 .pager select')
        total_pages = 1
        if pager.count() > 0:
            options = pager.locator('option').all()
            total_pages = len(options)

        # ── Scrape page by page ───────────────────────────
        for pg_num in range(1, total_pages + 1):
            _e({'type': 'page', 'current': pg_num, 'total': total_pages})

            if pg_num > 1:
                pager.select_option(str(pg_num))
                page.wait_for_timeout(1500)
                try:
                    page.wait_for_selector('table.sortable tbody tr', timeout=15000)
                except Exception:
                    pass

            parser = _AnthillPaymentsTableParser()
            parser.feed(page.content())
            if not parser.found:
                continue

            for row in parser.rows:
                pay = _row_to_payment(row)
                if not pay:
                    continue

                # Filter by selected location
                if location_filter:
                    row_showroom = (pay['showroom'] or '').strip().lower()
                    if location_filter.lower() not in row_showroom:
                        filtered_out += 1
                        continue

                scraped_rows.append(pay)

        browser.close()

    return scraped_rows, filtered_out


def confirmed_anthill_payments_by_contract(period='last_12_months', location_filter='', use_cache=True):
    """Return confirmed Anthill payments grouped by contract number.

    ``{contract_number: [{'amount': Decimal, 'date': date, 'payment_type': str}, ...]}``

    Scrapes the Anthill payments screen (result cached briefly so repeated
    calendar checks stay fast). Returns ``{}`` on any failure so callers can
    treat Anthill enrichment as best-effort.
    """
    from django.core.cache import cache

    cache_key = f'anthill_confirmed_payments:{period}:{(location_filter or "").lower()}'
    if use_cache:
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

    try:
        rows, _ = scrape_anthill_payments(period=period, location_filter=location_filter)
    except Exception:
        logger.exception('Anthill confirmed-payment scrape failed')
        return {}

    by_contract = {}
    for pay in rows:
        if (pay.get('status') or '').strip().lower() != 'confirmed':
            continue
        contract = (pay.get('contract_number') or '').strip()
        if not contract:
            continue
        by_contract.setdefault(contract, []).append({
            'amount': _parse_amount(pay.get('amount', '')),
            'date': _parse_date(pay.get('payment_date', '')),
            'payment_type': pay.get('payment_type', '') or 'Payment',
        })

    cache.set(cache_key, by_contract, timeout=300)
    return by_contract


_SENTINEL = object()  # marks end of SSE stream


@login_required
def sync_invoices_from_anthill(request):
    """
    SSE streaming endpoint – scrapes the Anthill CRM payments screen page by
    page and pushes each row to the browser as it arrives.

    The heavy work (Playwright + ORM) runs in a background thread so it stays
    fully synchronous.  Events are passed to the response generator via a
    thread-safe queue.

    Events:
      {type: 'status',  message: '...'}
      {type: 'page',    current: N, total: N}
      {type: 'row',     payment: {...}, action: 'created'|'updated'|'skipped'}
      {type: 'done',    created: N, updated: N, skipped: N, total: N}
      {type: 'error',   message: '...'}
    """
    # Read user's selected location before spawning the thread
    location_filter = ''
    if hasattr(request.user, 'profile'):
        location_filter = (request.user.profile.selected_location or '').strip()

    # Read requested time period
    VALID_PERIODS = {
        'today', 'this_week', 'this_month', 'this_year',
        'last_7_days', 'last_28_days', 'last_12_months', 'all_time',
    }
    period = request.GET.get('period', 'last_28_days')
    if period not in VALID_PERIODS:
        period = 'last_28_days'

    q = queue.Queue()

    def _emit(payload):
        q.put(f"data: {json.dumps(payload)}\n\n")

    def _worker():
        """Runs in a daemon thread — all sync I/O is safe here."""
        try:
            # ── Phase 1: Scrape with Playwright (no ORM calls) ────
            try:
                scraped_rows, filtered_out = scrape_anthill_payments(
                    period=period, location_filter=location_filter, emit=_emit)
            except RuntimeError as exc:
                _emit({'type': 'error', 'message': str(exc)})
                return
            loc_label = location_filter or 'All Locations'

            # ── Phase 2: Check scraped rows against DB ────────────
            # Playwright context is closed so there is no event loop
            # on this thread — ORM calls are safe.
            _emit({'type': 'status', 'message': f'Checking {len(scraped_rows)} payments against database...'})
            matched = new = 0
            now = timezone.now()

            for pay in scraped_rows:
                action = _check_payment(pay)
                if action == 'matched':
                    matched += 1
                else:
                    new += 1
                _emit({'type': 'row', 'payment': pay, 'action': action})

            _emit({
                'type': 'done',
                'matched': matched,
                'new': new,
                'filtered_out': filtered_out,
                'total': len(scraped_rows),
                'location': loc_label,
            })

        except Exception as exc:
            logger.exception('Anthill payments sync failed')
            _emit({'type': 'error', 'message': str(exc)})
        finally:
            q.put(_SENTINEL)

    def event_stream():
        thread = threading.Thread(target=_worker, daemon=True)
        thread.start()
        while True:
            item = q.get()
            if item is _SENTINEL:
                break
            yield item

    response = StreamingHttpResponse(event_stream(), content_type='text/event-stream')
    response['Cache-Control'] = 'no-cache'
    response['X-Accel-Buffering'] = 'no'
    return response


@login_required
def create_invoices_from_anthill(request):
    """
    POST endpoint – receives a JSON list of payment dicts from the browser
    and creates Invoice records for the ones that don't already exist.
    """
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required'}, status=405)

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'success': False, 'error': 'Invalid JSON'}, status=400)

    payments = data.get('payments', [])
    now = timezone.now()
    created = 0
    skipped = 0

    try:
        for pay in payments:
            contract = (pay.get('contract_number') or '').strip()
            if not contract:
                skipped += 1
                continue

            amount = _parse_amount(pay.get('amount', ''))
            payment_date = _parse_date(pay.get('payment_date', ''))

            exists = Invoice.objects.filter(
                contract_number=contract,
                payment_type=pay.get('payment_type', ''),
                total=amount,
                date=payment_date,
            ).exists()

            if exists:
                skipped += 1
                continue

            _create_invoice_from_payment(pay, now)
            created += 1
    except Exception as exc:
        logger.exception('create_invoices_from_anthill failed after %d created', created)
        return JsonResponse({
            'success': False,
            'error': f'Import failed after {created} record(s) created: {exc}',
        }, status=500)

    return JsonResponse({
        'success': True,
        'created': created,
        'skipped': skipped,
    })


# ── Invoice search (for linking to POs) ──────────────────────────
@login_required
def invoice_search(request):
    """Return invoices matching a search query (AJAX, JSON).

    Used by the PO detail page to search for invoices to link.
    """
    q = request.GET.get('q', '').strip()
    exclude_po = request.GET.get('exclude_po', '').strip()

    if len(q) < 2:
        return JsonResponse({'results': []})

    qs = Invoice.objects.filter(
        Q(invoice_number__icontains=q) |
        Q(client_name__icontains=q) |
        Q(project_number__icontains=q) |
        Q(description__icontains=q)
    ).order_by('-date')

    # Optionally exclude invoices already linked to a specific PO
    if exclude_po:
        try:
            po = PurchaseOrder.objects.get(workguru_id=int(exclude_po))
            qs = qs.exclude(purchase_orders=po)
        except (PurchaseOrder.DoesNotExist, ValueError):
            pass

    qs = qs[:20]

    results = []
    for inv in qs:
        results.append({
            'id': inv.id,
            'invoice_number': inv.invoice_number,
            'client_name': inv.client_name or '',
            'date': inv.date.strftime('%d/%m/%Y') if inv.date else '',
            'total': str(inv.total),
            'status': inv.status,
            'payment_status': inv.payment_status,
        })

    return JsonResponse({'results': results})


# ── Create invoice (standalone) ───────────────────────────────────
@login_required
def create_invoice(request):
    """Create a new Invoice (AJAX). Redirects to invoice detail on success."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    invoice_number = data.get('invoice_number', '').strip()
    if not invoice_number:
        return JsonResponse({'error': 'Invoice number is required'}, status=400)

    from decimal import Decimal, InvalidOperation
    from datetime import datetime as _dt

    def _parse_date(val):
        if not val:
            return None
        for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y'):
            try:
                return _dt.strptime(val.strip(), fmt).date()
            except ValueError:
                continue
        return None

    total_str = data.get('total', '0')
    try:
        total_val = Decimal(str(total_str)) if total_str else Decimal('0')
    except (InvalidOperation, ValueError):
        total_val = Decimal('0')

    invoice = Invoice.objects.create(
        invoice_number=invoice_number,
        client_name=data.get('client_name', '').strip(),
        date=_parse_date(data.get('date', '')),
        due_date=_parse_date(data.get('due_date', '')),
        description=data.get('description', '').strip(),
        status=data.get('status', 'Draft'),
        subtotal=total_val,
        total=total_val,
        amount_outstanding=total_val,
        payment_status='unpaid',
    )

    return JsonResponse({
        'success': True,
        'invoice_id': invoice.id,
        'invoice_url': f'/invoices/{invoice.id}/',
    })


# ── Create amendment (sub) invoice ────────────────────────────────
@login_required
def create_amendment_invoice(request, invoice_id):
    """Create an amendment invoice linked to an existing parent invoice.

    Auto-generates the sub-reference number:
      INV-A00-006        → first amendment  → INV-A00-006-A
      INV-A00-006-A      already exists     → INV-A00-006-A2
      INV-A00-006-A2     already exists     → INV-A00-006-A3
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    parent = get_object_or_404(Invoice, id=invoice_id)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    reason = data.get('reason', '').strip()

    # Build sub-reference number
    existing_amendments = parent.amendments.values_list('invoice_number', flat=True)
    count = len(existing_amendments)
    if count == 0:
        sub_number = f'{parent.invoice_number}-A'
    else:
        sub_number = f'{parent.invoice_number}-A{count + 1}'

    # Ensure uniqueness (edge case: someone manually created a clashing number)
    suffix = count + 1
    while Invoice.objects.filter(invoice_number=sub_number).exists():
        suffix += 1
        sub_number = f'{parent.invoice_number}-A{suffix}'

    amendment = Invoice.objects.create(
        invoice_number=sub_number,
        parent_invoice=parent,
        amendment_reason=reason,
        client_name=parent.client_name,
        client_id=parent.client_id,
        customer=parent.customer,
        project_name=parent.project_name,
        project_number=parent.project_number,
        project_id=parent.project_id,
        order=parent.order,
        showroom=parent.showroom,
        contract_number=parent.contract_number,
        date=parent.date,
        status='Draft',
        is_vat_inclusive=parent.is_vat_inclusive,
        vat_rate=parent.vat_rate,
        currency=parent.currency,
        subtotal=Decimal('0'),
        total_tax=Decimal('0'),
        total=Decimal('0'),
        amount_outstanding=Decimal('0'),
        payment_status='unpaid',
    )

    return JsonResponse({
        'success': True,
        'invoice_id': amendment.id,
        'invoice_number': amendment.invoice_number,
        'invoice_url': f'/invoices/{amendment.id}/',
    })


# ── Create invoice and link to PO ─────────────────────────────────
@login_required
def po_create_invoice(request, po_id):
    """Create a new Invoice and automatically link it to a PurchaseOrder.
    
    Accepts both JSON (application/json) and multipart form data (for file upload).
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    po = get_object_or_404(PurchaseOrder, workguru_id=po_id)

    from decimal import Decimal, InvalidOperation
    from datetime import datetime as _dt

    # Support both JSON and multipart/form-data
    content_type = request.content_type or ''
    if 'multipart' in content_type:
        data = request.POST
        uploaded_file = request.FILES.get('attachment')
    else:
        try:
            data = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({'error': 'Invalid JSON'}, status=400)
        uploaded_file = None

    invoice_number = data.get('invoice_number', '').strip()
    if not invoice_number:
        return JsonResponse({'error': 'Invoice number is required'}, status=400)

    def _parse_date(val):
        if not val:
            return None
        for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y'):
            try:
                return _dt.strptime(val.strip(), fmt).date()
            except ValueError:
                continue
        return None

    def _parse_decimal(val, default='0'):
        try:
            return Decimal(str(val)) if val else Decimal(default)
        except (InvalidOperation, ValueError):
            return Decimal(default)

    total_val = _parse_decimal(data.get('total', '0'))
    freight_val = _parse_decimal(data.get('freight_cost', '0'))
    currency = data.get('currency', 'GBP').strip().upper() or 'GBP'
    is_vat_inclusive = data.get('is_vat_inclusive', 'true')
    if isinstance(is_vat_inclusive, str):
        is_vat_inclusive = is_vat_inclusive.lower() in ('true', '1', 'yes', 'on')
    vat_rate = _parse_decimal(data.get('vat_rate', '20'), '20')

    # Parse line items
    line_items_raw = []
    if 'multipart' in content_type:
        # Line items from form fields: line_name_0, line_qty_0, line_rate_0, etc.
        idx = 0
        while data.get(f'line_name_{idx}') is not None:
            name = data.get(f'line_name_{idx}', '').strip()
            qty = _parse_decimal(data.get(f'line_qty_{idx}', '1'), '1')
            rate = _parse_decimal(data.get(f'line_rate_{idx}', '0'))
            if name:
                line_items_raw.append({'name': name, 'quantity': qty, 'rate': rate})
            idx += 1
    else:
        line_items_raw = data.get('line_items', [])

    invoice = Invoice.objects.create(
        invoice_number=invoice_number,
        client_name=data.get('client_name', '').strip(),
        date=_parse_date(data.get('date', '')),
        due_date=_parse_date(data.get('due_date', '')),
        description=data.get('description', '').strip(),
        status=data.get('status', 'Draft'),
        subtotal=total_val,
        total=total_val + freight_val,
        freight_cost=freight_val,
        currency=currency,
        is_vat_inclusive=is_vat_inclusive,
        vat_rate=vat_rate,
        amount_outstanding=total_val + freight_val,
        payment_status='unpaid',
    )

    # Create line items
    from .models import InvoiceLineItem
    for i, item in enumerate(line_items_raw):
        name = item.get('name', '').strip() if isinstance(item, dict) else str(item)
        qty = _parse_decimal(item.get('quantity', '1') if isinstance(item, dict) else '1', '1')
        rate = _parse_decimal(item.get('rate', '0') if isinstance(item, dict) else '0')
        line_total = qty * rate
        if name:
            InvoiceLineItem.objects.create(
                invoice=invoice,
                name=name,
                quantity=qty,
                rate=rate,
                line_total=line_total,
                sort_order=i,
            )

    # Attach file if provided
    if uploaded_file:
        invoice.attachment = uploaded_file
        invoice.save(update_fields=['attachment'])

    invoice.purchase_orders.add(po)

    return JsonResponse({
        'success': True,
        'invoice': {
            'id': invoice.id,
            'invoice_number': invoice.invoice_number,
            'client_name': invoice.client_name,
            'date': invoice.date.strftime('%d/%m/%Y') if invoice.date else '',
            'total': str(invoice.total),
            'freight_cost': str(invoice.freight_cost),
            'currency': invoice.currency,
            'status': invoice.status,
            'payment_status': invoice.payment_status,
            'has_attachment': bool(invoice.attachment),
        }
    })


# ── Link / unlink invoice to PO ──────────────────────────────────
@login_required
def po_link_invoice(request, po_id):
    """Link an existing Invoice to a PurchaseOrder (AJAX)."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    po = get_object_or_404(PurchaseOrder, workguru_id=po_id)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    invoice_id = data.get('invoice_id')
    if not invoice_id:
        return JsonResponse({'error': 'invoice_id required'}, status=400)

    invoice = get_object_or_404(Invoice, id=invoice_id)
    invoice.purchase_orders.add(po)

    return JsonResponse({
        'success': True,
        'invoice': {
            'id': invoice.id,
            'invoice_number': invoice.invoice_number,
            'client_name': invoice.client_name or '',
            'date': invoice.date.strftime('%d/%m/%Y') if invoice.date else '',
            'total': str(invoice.total),
            'status': invoice.status,
            'payment_status': invoice.payment_status,
        }
    })


@login_required
def po_unlink_invoice(request, po_id):
    """Remove an Invoice ↔ PurchaseOrder link (AJAX)."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    po = get_object_or_404(PurchaseOrder, workguru_id=po_id)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    invoice_id = data.get('invoice_id')
    if not invoice_id:
        return JsonResponse({'error': 'invoice_id required'}, status=400)

    invoice = get_object_or_404(Invoice, id=invoice_id)
    invoice.purchase_orders.remove(po)

    return JsonResponse({'success': True})


# ── Link / unlink PO from Invoice detail page ────────────────────
@login_required
def invoice_link_po(request, invoice_id):
    """Link a PurchaseOrder to an Invoice (AJAX, called from invoice detail)."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    invoice = get_object_or_404(Invoice, id=invoice_id)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    po_id = data.get('po_id')
    if not po_id:
        return JsonResponse({'error': 'po_id required'}, status=400)

    po = get_object_or_404(PurchaseOrder, workguru_id=po_id)
    invoice.purchase_orders.add(po)

    return JsonResponse({
        'success': True,
        'po': {
            'id': po.workguru_id,
            'display_number': po.display_number or po.number or f'#{po.workguru_id}',
            'supplier_name': po.supplier_name or '',
            'project_name': po.project_name or '',
            'status': po.status or '',
            'total': str(po.total),
        }
    })


@login_required
def invoice_unlink_po(request, invoice_id):
    """Remove a PurchaseOrder link from an Invoice (AJAX)."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    invoice = get_object_or_404(Invoice, id=invoice_id)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    po_id = data.get('po_id')
    if not po_id:
        return JsonResponse({'error': 'po_id required'}, status=400)

    po = get_object_or_404(PurchaseOrder, workguru_id=po_id)
    invoice.purchase_orders.remove(po)

    return JsonResponse({'success': True})


# ── Upload / download / delete invoice attachment ─────────────────
@login_required
def invoice_upload_attachment(request, invoice_id):
    """Upload a PDF/file attachment to an invoice (multipart form POST)."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    invoice = get_object_or_404(Invoice, id=invoice_id)
    uploaded = request.FILES.get('file')
    if not uploaded:
        return JsonResponse({'error': 'No file provided'}, status=400)

    # Delete old attachment if present
    if invoice.attachment:
        invoice.attachment.delete(save=False)

    invoice.attachment = uploaded
    invoice.save(update_fields=['attachment'])

    return JsonResponse({
        'success': True,
        'filename': uploaded.name,
        'url': invoice.attachment.url,
    })


@login_required
def invoice_delete_attachment(request, invoice_id):
    """Delete the attachment from an invoice (POST)."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    invoice = get_object_or_404(Invoice, id=invoice_id)
    if invoice.attachment:
        invoice.attachment.delete(save=False)
        invoice.attachment = None
        invoice.save(update_fields=['attachment'])

    return JsonResponse({'success': True})


# ── PO products for partial linking ──────────────────────────────
@login_required
def po_products_for_linking(request, po_id):
    """Return the products on a PO so the user can pick which to link.

    Also returns which products are already linked to a given invoice.
    """
    po = get_object_or_404(PurchaseOrder, workguru_id=po_id)
    invoice_id = request.GET.get('invoice_id', '')

    already_linked_ids = set()
    if invoice_id:
        try:
            inv = Invoice.objects.get(id=int(invoice_id))
            already_linked_ids = set(
                inv.linked_products.filter(purchase_order=po).values_list('id', flat=True)
            )
        except (Invoice.DoesNotExist, ValueError):
            pass

    products = po.products.all().order_by('sort_order', 'id')
    results = []
    for p in products:
        results.append({
            'id': p.id,
            'sku': p.sku or '',
            'name': p.name or '',
            'description': p.description or '',
            'quantity': str(p.order_quantity or p.quantity or 0),
            'line_total': str(p.line_total),
            'linked': p.id in already_linked_ids,
        })

    return JsonResponse({'products': results, 'po_display_number': po.display_number or po.number or str(po.workguru_id)})


@login_required
def invoice_set_linked_products(request, invoice_id):
    """Set the linked products for a specific PO on this invoice (POST).

    Body: { po_id: int, product_ids: [int, ...] }
    If product_ids is empty, all products are unlinked for that PO.
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    invoice = get_object_or_404(Invoice, id=invoice_id)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    po_id = data.get('po_id')
    product_ids = data.get('product_ids', [])

    if not po_id:
        return JsonResponse({'error': 'po_id required'}, status=400)

    po = get_object_or_404(PurchaseOrder, workguru_id=po_id)

    # Remove all product links for this PO first
    existing = invoice.linked_products.filter(purchase_order=po)
    invoice.linked_products.remove(*existing)

    # Add selected ones
    if product_ids:
        products_to_link = PurchaseOrderProduct.objects.filter(
            id__in=product_ids, purchase_order=po
        )
        invoice.linked_products.add(*products_to_link)

    linked_count = invoice.linked_products.filter(purchase_order=po).count()
    total_count = po.products.count()

    return JsonResponse({
        'success': True,
        'linked_count': linked_count,
        'total_count': total_count,
    })
