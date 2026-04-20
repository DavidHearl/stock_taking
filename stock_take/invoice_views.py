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

from django.db.models import Q
from django.shortcuts import render, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.http import StreamingHttpResponse, JsonResponse
from django.utils import timezone


from .models import Invoice, PurchaseOrder, PurchaseOrderProduct, AnthillSale, Customer

logger = logging.getLogger(__name__)


# ── Invoice list ──────────────────────────────────────────────────
@login_required
def invoices_list(request):
    """Display invoices from the local database."""

    status_filter = request.GET.get('status', 'all')
    search_query = request.GET.get('q', '').strip()

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

    invoices = list(qs.select_related('customer', 'order').order_by('-date'))

    # Summary stats (over filtered set)
    total_invoices = len(invoices)
    total_value = sum(inv.total for inv in invoices)
    total_outstanding = sum(inv.amount_outstanding for inv in invoices)
    total_paid = sum(inv.amount_paid for inv in invoices)
    paid_count = sum(1 for inv in invoices if inv.payment_status == 'paid')
    unpaid_count = sum(1 for inv in invoices if inv.payment_status == 'unpaid')
    partial_count = sum(1 for inv in invoices if inv.payment_status == 'partial')
    overdue_count = sum(1 for inv in invoices if inv.is_overdue)

    # Last sync timestamp
    last_sync = Invoice.objects.order_by('-synced_at').values_list('synced_at', flat=True).first()

    # Map contract numbers to AnthillSale PKs and customer PKs for linking
    contract_numbers = set(inv.contract_number for inv in invoices if inv.contract_number)
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

    # Build a name→PK map for invoices still missing a customer link
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
        # If invoice has no customer FK, try sale's customer, then name match
        if not inv.customer_id:
            inv.sale_customer_pk = (
                sale_customer_map.get(inv.contract_number)
                or name_customer_map.get(inv.client_name)
            )

    context = {
        'invoices': invoices,
        'total_invoices': total_invoices,
        'total_value': total_value,
        'total_outstanding': total_outstanding,
        'total_paid': total_paid,
        'paid_count': paid_count,
        'unpaid_count': unpaid_count,
        'partial_count': partial_count,
        'overdue_count': overdue_count,
        'status_filter': status_filter,
        'search_query': search_query,
        'last_sync': last_sync,
    }

    return render(request, 'stock_take/invoices.html', context)


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

    context = {
        'invoice': invoice,
        'line_items': line_items,
        'payments': payments,
        'linked_pos': linked_pos,
        'linked_pos_info': linked_pos_info,
    }
    return render(request, 'stock_take/invoice_detail.html', context)


# ── Push single invoice to Xero as Draft ──────────────────────────
@login_required
def push_invoice_to_xero(request, invoice_id):
    """Create a DRAFT sales invoice in Xero from a local Invoice record."""
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'POST required'}, status=405)

    invoice = get_object_or_404(Invoice, id=invoice_id)

    from .services import xero_api

    # Look up or create the contact in Xero
    contact_id = ''
    if invoice.client_name:
        contact_id = xero_api.find_contact_by_name(invoice.client_name)

    if not contact_id:
        return JsonResponse({
            'ok': False,
            'error': f'Contact "{invoice.client_name}" not found in Xero. Create the contact first.',
        })

    # Build line items — single line with the invoice total
    line_items = [{
        "Description": f"{invoice.payment_type or 'Payment'} — {invoice.contract_number}",
        "Quantity": "1",
        "UnitAmount": str(invoice.total),
        "AccountCode": "G1001",
    }]

    invoice_data = {
        "Type": "ACCREC",
        "Contact": {"ContactID": contact_id},
        "Status": "DRAFT",
        "CurrencyCode": invoice.currency or "GBP",
        "LineAmountTypes": "Inclusive",
        "LineItems": line_items,
        "Reference": invoice.contract_number,
    }

    if invoice.date:
        invoice_data["Date"] = invoice.date.isoformat()
        invoice_data["DueDate"] = (invoice.due_date or invoice.date).isoformat()

    payload = {"Invoices": [invoice_data]}
    result = xero_api._api_put("Invoices", payload)

    if result is None:
        err = xero_api.get_last_api_error() or 'Unknown Xero API error'
        return JsonResponse({'ok': False, 'error': err})

    # Extract the Xero InvoiceID from the response
    invoices_resp = result.get('Invoices', [])
    if invoices_resp:
        invoice.xero_id = invoices_resp[0].get('InvoiceID', '')
        invoice.save(update_fields=['xero_id'])

    return JsonResponse({'ok': True, 'xero_id': invoice.xero_id})


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

    Invoice.objects.create(
        invoice_number=contract,
        contract_number=contract,
        client_name=pay['customer'],
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
            username = os.getenv('ANTHILL_USER_USERNAME')
            password = os.getenv('ANTHILL_USER_PASSWORD')
            subdomain = os.getenv('ANTHILL_SUBDOMAIN', 'sliderobes')

            if not username or not password:
                _emit({'type': 'error', 'message': 'ANTHILL_USER_USERNAME / ANTHILL_USER_PASSWORD not configured.'})
                return

            base_url = f'https://{subdomain}.anthillcrm.com'
            period_qs = '' if period == 'all_time' else period
            target_url = f'{base_url}/n/screens/12/CAIaEgmLsAFMrjHYQhGCvnwKumuXYyiDAw?d={period_qs}'

            try:
                from playwright.sync_api import sync_playwright
            except ImportError:
                _emit({'type': 'error', 'message': 'Playwright is not installed on the server.'})
                return

            filtered_out = 0
            loc_label = location_filter or 'All Locations'
            scraped_rows = []   # collect raw payment dicts here

            # ── Phase 1: Scrape with Playwright (no ORM calls) ────
            with sync_playwright() as p:
                _emit({'type': 'status', 'message': 'Launching browser...'})

                browser = p.chromium.launch(headless=True)
                context = browser.new_context(
                    user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
                )
                page = context.new_page()

                _emit({'type': 'status', 'message': 'Connecting to Anthill CRM...'})
                page.goto(target_url, timeout=30000)
                page.wait_for_load_state('domcontentloaded', timeout=15000)

                # Handle login
                current_url = page.url.lower()
                if 'sign-in' in current_url or 'login' in current_url or 'signin' in current_url:
                    _emit({'type': 'status', 'message': 'Logging in...'})
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
                    _emit({'type': 'status', 'message': f'Filtering for {loc_label}...'})
                else:
                    _emit({'type': 'status', 'message': 'Waiting for payments table...'})
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
                    _emit({'type': 'page', 'current': pg_num, 'total': total_pages})

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
