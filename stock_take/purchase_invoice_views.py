"""
Purchase Invoice views – inbound invoices from suppliers/fitters.
Each line item can be allocated to a specific Order (job) so the cost
flows through into job-level costing.
"""

import json
import logging
from decimal import Decimal, InvalidOperation

from django.contrib.auth.decorators import login_required
from django.db.models import Q, Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render

from .models import Order, PurchaseInvoice, PurchaseInvoiceLineItem, PurchaseOrder, Supplier, Timesheet, log_activity

logger = logging.getLogger(__name__)


def _sync_timesheet_for_pi_line(line):
    """Create or update the installation timesheet linked to a purchase invoice line.

    * If the line is allocated to an order, ensure exactly one timesheet exists.
    * If the line has no order, delete any linked timesheet.
    """
    from django.utils import timezone

    if line.order_id:
        ts, created = Timesheet.objects.get_or_create(
            purchase_invoice_line=line,
            defaults={
                'order': line.order,
                'timesheet_type': 'installation',
                'date': line.invoice.date or timezone.now().date(),
                'description': line.description,
            },
        )
        if not created:
            # Keep in sync if order or cost changed
            changed = False
            if ts.order_id != line.order_id:
                ts.order = line.order
                changed = True
            if ts.description != line.description:
                ts.description = line.description
                changed = True
            if changed:
                ts.save()
    else:
        # No order – remove any linked timesheet
        Timesheet.objects.filter(purchase_invoice_line=line).delete()

def _parse_date(val):
    from datetime import datetime as _dt
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
        return Decimal(str(val)) if val not in (None, '') else Decimal(default)
    except (InvalidOperation, ValueError):
        return Decimal(default)


def _parse_pdf_text_date(val):
    """Convert a raw date string extracted from a PDF into YYYY-MM-DD, or None."""
    import re as _re
    from datetime import datetime as _dt

    if not val:
        return None
    val = val.strip()

    # Strip ordinal suffixes: 1st, 2nd, 3rd, 15th
    val_clean = _re.sub(r'(\d+)(?:st|nd|rd|th)\b', r'\1', val).strip()

    # Numeric formats: DD/MM/YYYY  DD-MM-YYYY  DD.MM.YYYY  YYYY-MM-DD  DD/MM/YY
    for fmt in ('%d/%m/%Y', '%d-%m-%Y', '%d.%m.%Y', '%Y-%m-%d',
                '%d/%m/%y', '%d-%m-%y', '%d.%m.%y'):
        try:
            return _dt.strptime(val_clean, fmt).date().isoformat()
        except ValueError:
            pass

    # Text months: "15 January 2024", "15 Jan 2024", "January 15, 2024"
    for fmt in ('%d %B %Y', '%d %b %Y', '%B %d, %Y', '%b %d, %Y',
                '%d %B, %Y', '%d %b, %Y'):
        try:
            return _dt.strptime(val_clean, fmt).date().isoformat()
        except ValueError:
            pass

    return None


# Date regex fragments — reused across patterns
_DATE_RE = (
    r'(\d{1,2}[\/\-\.]\d{1,2}[\/\-\.]\d{2,4}'          # DD/MM/YYYY etc.
    r'|\d{1,2}(?:st|nd|rd|th)?\s+\w+\s+\d{4}'           # 15 January 2024
    r'|\w+\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4}'         # January 15, 2024
    r'|\d{4}[\/\-\.]\d{1,2}[\/\-\.]\d{1,2})'            # YYYY-MM-DD
)


# ── PDF parsing ───────────────────────────────────────────────────
@login_required
def parse_purchase_invoice_pdf(request):
    """Parse a PDF file upload and return extracted invoice fields as JSON.

    Accepts a multipart POST with a ``file`` field.  Returns::

        {"success": true, "extracted": {
            "invoice_number": "INV-001",
            "supplier_name":  "ACME Ltd",
            "date":           "2024-01-15",
            "due_date":       "2024-02-15",
            "total":          "1234.56"
        }}

    Fields that cannot be found are omitted from ``extracted``.
    Non-PDF files return ``{"success": true, "extracted": {}}``.
    """
    import io
    import re

    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    uploaded = request.FILES.get('file')
    if not uploaded:
        return JsonResponse({'error': 'No file provided'}, status=400)

    if not uploaded.name.lower().endswith('.pdf'):
        return JsonResponse({'success': True, 'extracted': {}})

    try:
        import pdfplumber
        import warnings

        pdf_bytes = uploaded.read()
        if not pdf_bytes:
            return JsonResponse({'success': True, 'extracted': {}})

        text_lines: list[str] = []

        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
                for page in pdf.pages[:4]:
                    try:
                        page_text = page.extract_text()
                    except Exception:
                        page_text = None
                    if page_text:
                        text_lines.extend(page_text.split('\n'))

        full_text = '\n'.join(text_lines)
        extracted: dict = {}

        # Always return the raw text so the front-end can log it for debugging
        # (truncated to keep response small)
        extracted['_raw_text'] = full_text[:2000]

        if not full_text.strip():
            return JsonResponse({'success': True, 'extracted': extracted})

        # ── Invoice number ────────────────────────────────────────
        inv_patterns = [
            # "Invoice Number: INV-001", "Invoice No: 123", "Invoice #123"
            r'(?:Invoice\s*(?:Number|No\.?|Num\.?|#|Ref(?:erence)?\.?)|Inv\s*(?:No\.?|#))[:\s#]*([A-Za-z0-9][\w\-\/\.]+)',
            # "Tax Invoice No: 123"
            r'Tax\s*Invoice\s*(?:No\.?|#)?[:\s#]*([A-Za-z0-9][\w\-\/\.]+)',
            # "Reference: INV-001" or "Ref: INV-001"
            r'(?:Ref(?:erence)?)\s*[:\s]\s*([A-Za-z0-9][\w\-\/\.]+)',
            # "Invoice" followed by what looks like an invoice number on the same line
            r'Invoice[:\s]+([A-Z]{0,5}\d[\w\-\/\.]+)',
        ]
        for pat in inv_patterns:
            m = re.search(pat, full_text, re.IGNORECASE)
            if m:
                candidate = m.group(1).strip().rstrip('.')
                # Sanity: must contain at least one digit
                if re.search(r'\d', candidate):
                    extracted['invoice_number'] = candidate
                    break

        # ── Supplier name (DB cross-reference) ───────────────────
        # Load all known supplier names and try to find one in the PDF text
        try:
            db_suppliers = list(
                Supplier.objects.values_list('name', flat=True)
                .exclude(name__isnull=True)
                .exclude(name__exact='')
            )
        except Exception:
            db_suppliers = []

        full_text_lower = full_text.lower()
        best_supplier = None
        best_supplier_len = 0
        for s in db_suppliers:
            s_lower = s.strip().lower()
            if s_lower and len(s_lower) >= 2 and s_lower in full_text_lower:
                # Prefer the longest match (more specific)
                if len(s_lower) > best_supplier_len:
                    best_supplier = s.strip()
                    best_supplier_len = len(s_lower)

        if best_supplier:
            extracted['supplier_name'] = best_supplier
        else:
            # Fallback: look for labelled supplier
            supplier_m = re.search(
                r'(?:From|Supplier|Vendor|Billed?\s*(?:From|By)|Issued\s*By|Seller|Company)[:\s]+'
                r'([^\n]{2,80})',
                full_text, re.IGNORECASE,
            )
            if supplier_m:
                extracted['supplier_name'] = supplier_m.group(1).strip()
            else:
                # Last fallback: first non-trivial line (many invoices put company name first)
                for line in text_lines[:6]:
                    line = line.strip()
                    if (
                        line and len(line) >= 3
                        and not re.match(
                            r'^(Invoice|Tax\s*Invoice|Statement|Receipt|Page\b|Date|No\.|To:)',
                            line, re.IGNORECASE
                        )
                        and not re.match(r'^\d+[\/\-\.]\d', line)  # not a date
                        and not re.match(r'^\d+$', line)           # not just a number
                    ):
                        extracted['supplier_name'] = line
                        break

        # ── Due date (search before invoice date to avoid cross-match) ──
        due_patterns = [
            r'(?:Due\s*Date|Payment\s*Due(?:\s*Date)?|Pay(?:ment)?\s*(?:Due\s*)?By|Terms?\s*(?:Due|Date))[:\s]+' + _DATE_RE,
            r'Due[:\s]+' + _DATE_RE,
        ]
        for pat in due_patterns:
            due_m = re.search(pat, full_text, re.IGNORECASE)
            if due_m:
                parsed = _parse_pdf_text_date(due_m.group(1))
                if parsed:
                    extracted['due_date'] = parsed
                    break

        # ── Invoice date ─────────────────────────────────────────
        inv_date_patterns = [
            # "Invoice Date: ..." or "Date of Invoice: ..."
            r'(?:Invoice\s*Date|Date\s*of\s*Invoice|Tax\s*(?:Point\s*)?Date|Invoice\s*Dated?)[:\s]+' + _DATE_RE,
            # "Date: ..." (but not "Due Date")
            r'(?<![Dd]ue\s)Date[:\s]+' + _DATE_RE,
            # "Dated: ..."
            r'Dated[:\s]+' + _DATE_RE,
        ]
        for pat in inv_date_patterns:
            date_m = re.search(pat, full_text, re.IGNORECASE)
            if date_m:
                parsed = _parse_pdf_text_date(date_m.group(1))
                if parsed:
                    extracted['date'] = parsed
                    break

        # Last resort: find ANY date anywhere in the text for invoice date
        if 'date' not in extracted:
            all_dates = re.findall(_DATE_RE, full_text)
            for raw_date in all_dates:
                parsed = _parse_pdf_text_date(raw_date)
                if parsed:
                    # Skip if this is the due date we already found
                    if parsed != extracted.get('due_date'):
                        extracted['date'] = parsed
                        break
            # If we still have nothing but found a due date, use any date
            if 'date' not in extracted and all_dates:
                for raw_date in all_dates:
                    parsed = _parse_pdf_text_date(raw_date)
                    if parsed:
                        extracted['date'] = parsed
                        break

        # ── Total amount ──────────────────────────────────────────
        total_patterns = [
            r'(?:Total\s*(?:Amount\s*)?(?:Due|Payable)?|Amount\s*(?:Due|Payable)|Grand\s*Total'
            r'|Invoice\s*Total|Balance\s*Due|Net\s*(?:Total|Amount)|Total\s*(?:to\s*Pay|Inc(?:l?\.?\s*VAT)?))[:\s]+'
            r'[£\$€]?\s*([\d,]+\.?\d*)',
            r'\bTotal\b[:\s]+[£\$€]?\s*([\d,]+\.?\d*)',
        ]
        for pat in total_patterns:
            total_m = re.search(pat, full_text, re.IGNORECASE)
            if total_m:
                try:
                    extracted['total'] = str(Decimal(total_m.group(1).replace(',', '')))
                    break
                except (InvalidOperation, ValueError):
                    pass

        return JsonResponse({'success': True, 'extracted': extracted})

    except Exception as exc:
        # Log the error server-side but return gracefully so the form still works;
        # the user just won't get auto-populated fields.
        logger.warning('PDF parse failed (non-fatal): %s', exc)
        return JsonResponse({'success': True, 'extracted': {}})


# ── List ──────────────────────────────────────────────────────────
@login_required
def purchase_invoices_list(request):
    status_filter = request.GET.get('status', 'all')
    search_query  = request.GET.get('q', '').strip()

    qs = PurchaseInvoice.objects.all()

    if status_filter == 'unpaid':
        qs = qs.exclude(payment_status='paid')
    elif status_filter not in ('', 'all'):
        qs = qs.filter(status=status_filter.capitalize())

    if search_query:
        qs = qs.filter(
            Q(invoice_number__icontains=search_query) |
            Q(supplier_name__icontains=search_query) |
            Q(notes__icontains=search_query)
        )

    invoices = list(qs)

    total_value       = sum(inv.total for inv in invoices)
    total_outstanding = sum(inv.amount_outstanding for inv in invoices)
    total_paid        = sum(inv.amount_paid for inv in invoices)
    paid_count        = sum(1 for inv in invoices if inv.payment_status == 'paid')
    unpaid_count      = sum(1 for inv in invoices if inv.payment_status == 'unpaid')
    partial_count     = sum(1 for inv in invoices if inv.payment_status == 'partial')

    suppliers = list(Supplier.objects.values_list('name', flat=True).order_by('name'))

    context = {
        'invoices': invoices,
        'total_invoices': len(invoices),
        'total_value': total_value,
        'total_outstanding': total_outstanding,
        'total_paid': total_paid,
        'paid_count': paid_count,
        'unpaid_count': unpaid_count,
        'partial_count': partial_count,
        'status_filter': status_filter,
        'search_query': search_query,
        'suppliers': suppliers,
    }
    return render(request, 'stock_take/purchase_invoices.html', context)


# ── Detail ────────────────────────────────────────────────────────
@login_required
def purchase_invoice_detail(request, invoice_id):
    invoice = get_object_or_404(PurchaseInvoice, id=invoice_id)
    line_items = invoice.line_items.select_related('order').all()

    # Build per-order allocation summary
    order_allocations = {}
    for line in line_items:
        if line.order_id:
            oid = line.order_id
            if oid not in order_allocations:
                order_allocations[oid] = {
                    'order': line.order,
                    'total': Decimal('0'),
                    'lines': [],
                }
            order_allocations[oid]['total'] += line.line_total
            order_allocations[oid]['lines'].append(line)

    context = {
        'invoice': invoice,
        'lines': line_items,
        'order_allocations': list(order_allocations.values()),
        'linked_pos': invoice.purchase_orders.all().order_by('-workguru_id'),
    }
    return render(request, 'stock_take/purchase_invoice_detail.html', context)


# ── Create invoice ────────────────────────────────────────────────
@login_required
def create_purchase_invoice(request):
    """Create a new PurchaseInvoice. Supports JSON and multipart (for file upload)."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    content_type = request.content_type or ''
    if 'multipart' in content_type:
        data          = request.POST
        uploaded_file = request.FILES.get('attachment')
    else:
        try:
            data = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({'error': 'Invalid JSON'}, status=400)
        uploaded_file = None

    invoice_number = (data.get('invoice_number') or '').strip()
    if not invoice_number:
        return JsonResponse({'error': 'Invoice number is required'}, status=400)

    total_val = _parse_decimal(data.get('total', '0'))

    invoice = PurchaseInvoice.objects.create(
        invoice_number = invoice_number,
        supplier_name  = (data.get('supplier_name') or '').strip(),
        date           = _parse_date(data.get('date', '')),
        due_date       = _parse_date(data.get('due_date', '')),
        status         = data.get('status', 'Draft'),
        total          = total_val,
        notes          = (data.get('notes') or '').strip(),
        created_by     = request.user.get_full_name() or request.user.username,
    )

    # Parse and save line items
    line_items_raw = []
    if 'multipart' in content_type:
        idx = 0
        while data.get(f'line_desc_{idx}') is not None:
            desc     = (data.get(f'line_desc_{idx}') or '').strip()
            qty      = _parse_decimal(data.get(f'line_qty_{idx}', '1'), '1')
            rate     = _parse_decimal(data.get(f'line_rate_{idx}', '0'))
            order_id = data.get(f'line_order_{idx}', '') or None
            if desc:
                line_items_raw.append({
                    'description': desc, 'quantity': qty,
                    'rate': rate, 'order_id': order_id,
                })
            idx += 1
    else:
        line_items_raw = data.get('line_items', [])

    for i, item in enumerate(line_items_raw):
        if isinstance(item, dict):
            desc     = (item.get('description') or '').strip()
            qty      = _parse_decimal(item.get('quantity', '1'), '1')
            rate     = _parse_decimal(item.get('rate', '0'))
            order_id = item.get('order_id')
        else:
            desc, qty, rate, order_id = str(item), Decimal('1'), Decimal('0'), None
        if desc:
            line = PurchaseInvoiceLineItem.objects.create(
                invoice     = invoice,
                description = desc,
                quantity    = qty,
                rate        = rate,
                line_total  = qty * rate,
                sort_order  = i,
            )
            if order_id:
                try:
                    line.order = Order.objects.get(id=int(order_id))
                    line.save(update_fields=['order'])
                except (Order.DoesNotExist, ValueError):
                    pass
            # Auto-create installation timesheet if allocated to an order
            _sync_timesheet_for_pi_line(line)

    # Recalculate total from lines if no total provided
    if total_val == 0 and line_items_raw:
        invoice.total = invoice.line_items.aggregate(t=Sum('line_total'))['t'] or 0
        invoice.save(update_fields=['total'])

    if uploaded_file:
        invoice.attachment = uploaded_file
        invoice.save(update_fields=['attachment'])

    log_activity(
        user=request.user,
        event_type='invoice_created',
        description=f'{request.user.get_full_name() or request.user.username} created purchase invoice {invoice.invoice_number} (supplier: {invoice.supplier_name or "—"}, total: £{invoice.total}).',
    )

    return JsonResponse({
        'success': True,
        'invoice_id':  invoice.id,
        'invoice_url': f'/purchase-invoices/{invoice.id}/',
    })


# ── Update invoice header ─────────────────────────────────────────
@login_required
def update_purchase_invoice(request, invoice_id):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    invoice = get_object_or_404(PurchaseInvoice, id=invoice_id)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    if 'invoice_number' in data:
        invoice.invoice_number = data['invoice_number'].strip()
    if 'supplier_name' in data:
        invoice.supplier_name = data['supplier_name'].strip()
    if 'date' in data:
        invoice.date = _parse_date(data['date'])
    if 'due_date' in data:
        invoice.due_date = _parse_date(data['due_date'])
    if 'status' in data:
        invoice.status = data['status']
    if 'payment_status' in data:
        invoice.payment_status = data['payment_status']
    if 'total' in data:
        invoice.total = _parse_decimal(data['total'])
        # Re-derive payment status based on updated total (if amount_paid not being changed)
        if 'amount_paid' not in data:
            if invoice.total <= 0:
                invoice.payment_status = 'unpaid'
            elif invoice.amount_paid >= invoice.total:
                invoice.payment_status = 'paid'
            elif invoice.amount_paid > 0:
                invoice.payment_status = 'partial'
            else:
                invoice.payment_status = 'unpaid'
    if 'amount_paid' in data:
        invoice.amount_paid = _parse_decimal(data['amount_paid'])
        # Auto-update payment status
        if invoice.amount_paid >= invoice.total > 0:
            invoice.payment_status = 'paid'
        elif invoice.amount_paid > 0:
            invoice.payment_status = 'partial'
        else:
            invoice.payment_status = 'unpaid'
    if 'notes' in data:
        invoice.notes = data['notes'].strip()

    invoice.save()
    return JsonResponse({
        'success':           True,
        'invoice_number':    invoice.invoice_number,
        'supplier_name':     invoice.supplier_name or '',
        'date':              invoice.date.strftime('%d/%m/%Y') if invoice.date else '',
        'due_date':          invoice.due_date.strftime('%d/%m/%Y') if invoice.due_date else '',
        'status':            invoice.status,
        'amount_paid':       str(invoice.amount_paid),
        'total':             str(invoice.total),
        'amount_outstanding': str(invoice.amount_outstanding),
        'payment_status':    invoice.payment_status,
        'notes':             invoice.notes or '',
    })


# ── Line items CRUD ───────────────────────────────────────────────
@login_required
def add_purchase_invoice_line(request, invoice_id):
    """Add a new line item to an invoice."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    invoice = get_object_or_404(PurchaseInvoice, id=invoice_id)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    desc = (data.get('description') or '').strip()
    if not desc:
        return JsonResponse({'error': 'Description is required'}, status=400)

    qty        = _parse_decimal(data.get('quantity', '1'), '1')
    rate       = _parse_decimal(data.get('rate', '0'))
    order_id   = data.get('order_id')
    sort_order = invoice.line_items.count()

    line = PurchaseInvoiceLineItem.objects.create(
        invoice     = invoice,
        description = desc,
        quantity    = qty,
        rate        = rate,
        line_total  = qty * rate,
        sort_order  = sort_order,
    )

    if order_id:
        try:
            line.order = Order.objects.get(id=int(order_id))
            line.save(update_fields=['order'])
        except (Order.DoesNotExist, ValueError):
            pass

    # Auto-create installation timesheet if allocated to an order
    _sync_timesheet_for_pi_line(line)

    _recalc_invoice_total(invoice)

    return JsonResponse({
        'success': True,
        'line': _line_to_dict(line),
        'invoice_total': str(invoice.total),
        'amount_paid': str(invoice.amount_paid),
        'amount_outstanding': str(invoice.amount_outstanding),
    })


@login_required
def update_purchase_invoice_line(request, invoice_id, line_id):
    """Update a line item's description, qty, rate, or order allocation."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    invoice = get_object_or_404(PurchaseInvoice, id=invoice_id)
    line    = get_object_or_404(PurchaseInvoiceLineItem, id=line_id, invoice=invoice)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    if 'description' in data:
        line.description = data['description'].strip()
    if 'quantity' in data:
        line.quantity = _parse_decimal(data['quantity'], '1')
    if 'rate' in data:
        line.rate = _parse_decimal(data['rate'])
    line.line_total = line.quantity * line.rate

    # Order allocation — pass null/empty to clear
    if 'order_id' in data:
        order_id = data['order_id']
        if order_id in (None, '', 0, '0'):
            line.order = None
        else:
            try:
                line.order = Order.objects.get(id=int(order_id))
            except (Order.DoesNotExist, ValueError):
                return JsonResponse({'error': 'Order not found'}, status=400)

    line.save()
    _recalc_invoice_total(invoice)

    # Sync the linked installation timesheet (create/update/delete)
    _sync_timesheet_for_pi_line(line)

    return JsonResponse({
        'success': True,
        'line': _line_to_dict(line),
        'invoice_total': str(invoice.total),
        'amount_paid': str(invoice.amount_paid),
        'amount_outstanding': str(invoice.amount_outstanding),
    })


@login_required
def delete_purchase_invoice_line(request, invoice_id, line_id):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    invoice = get_object_or_404(PurchaseInvoice, id=invoice_id)
    line    = get_object_or_404(PurchaseInvoiceLineItem, id=line_id, invoice=invoice)
    # CASCADE on the FK will auto-delete any linked timesheet
    line.delete()
    _recalc_invoice_total(invoice)

    return JsonResponse({
        'success': True,
        'invoice_total': str(invoice.total),
        'amount_paid': str(invoice.amount_paid),
        'amount_outstanding': str(invoice.amount_outstanding),
    })


# ── Attachment upload / delete ────────────────────────────────────

# ── Delete entire invoice ─────────────────────────────────────────
@login_required
def delete_purchase_invoice(request, invoice_id):
    """Delete a purchase invoice and all its line items (CASCADE)."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    invoice = get_object_or_404(PurchaseInvoice, id=invoice_id)

    inv_number = invoice.invoice_number
    inv_supplier = invoice.supplier_name or '—'

    # Delete attachment file from storage if present
    if invoice.attachment:
        invoice.attachment.delete(save=False)

    invoice.delete()  # CASCADE deletes line items → CASCADE deletes linked timesheets

    log_activity(
        user=request.user,
        event_type='invoice_deleted',
        description=f'{request.user.get_full_name() or request.user.username} deleted purchase invoice {inv_number} (supplier: {inv_supplier}).',
    )

    return JsonResponse({'success': True})


@login_required
def upload_purchase_invoice_attachment(request, invoice_id):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    invoice  = get_object_or_404(PurchaseInvoice, id=invoice_id)
    uploaded = request.FILES.get('file')
    if not uploaded:
        return JsonResponse({'error': 'No file provided'}, status=400)

    if invoice.attachment:
        invoice.attachment.delete(save=False)
    invoice.attachment = uploaded
    invoice.save(update_fields=['attachment'])

    return JsonResponse({'success': True, 'url': invoice.attachment.url, 'filename': uploaded.name})


@login_required
def delete_purchase_invoice_attachment(request, invoice_id):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    invoice = get_object_or_404(PurchaseInvoice, id=invoice_id)
    if invoice.attachment:
        invoice.attachment.delete(save=False)
        invoice.attachment = None
        invoice.save(update_fields=['attachment'])

    return JsonResponse({'success': True})


# ── Order purchase invoice lookup (for order details page) ────────
@login_required
def order_purchase_invoice_lines(request, order_id):
    """Return all purchase invoice lines allocated to an order (AJAX)."""
    order = get_object_or_404(Order, id=order_id)
    lines = PurchaseInvoiceLineItem.objects.filter(order=order).select_related('invoice')

    results = []
    for line in lines:
        results.append({
            'id': line.id,
            'invoice_id': line.invoice_id,
            'invoice_number': line.invoice.invoice_number,
            'supplier_name': line.invoice.supplier_name,
            'description': line.description,
            'quantity': str(line.quantity),
            'rate': str(line.rate),
            'line_total': str(line.line_total),
        })

    total = sum(Decimal(r['line_total']) for r in results)
    return JsonResponse({'lines': results, 'total': str(total)})


# ── Helpers ───────────────────────────────────────────────────────
def _recalc_invoice_total(invoice):
    total = invoice.line_items.aggregate(t=Sum('line_total'))['t'] or Decimal('0')
    invoice.total = total
    # Clamp amount_paid
    if invoice.amount_paid > total:
        invoice.amount_paid = total
    # Re-derive payment status
    if invoice.total <= 0:
        invoice.payment_status = 'unpaid'
    elif invoice.amount_paid >= invoice.total:
        invoice.payment_status = 'paid'
    elif invoice.amount_paid > 0:
        invoice.payment_status = 'partial'
    else:
        invoice.payment_status = 'unpaid'
    invoice.save(update_fields=['total', 'amount_paid', 'payment_status'])


def _line_to_dict(line):
    return {
        'id': line.id,
        'description': line.description,
        'quantity': str(line.quantity),
        'rate': str(line.rate),
        'line_total': str(line.line_total),
        'order_id': line.order_id,
        'order_label': (
            f"{line.order.sale_number} – {line.order.first_name} {line.order.last_name}".strip(' –')
            if line.order else ''
        ),
    }


# ── PO linking (from Purchase Invoice detail page) ────────────────────
@login_required
def link_purchase_invoice_po(request, invoice_id):
    """Link a PurchaseOrder to this PurchaseInvoice (M2M)."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    invoice = get_object_or_404(PurchaseInvoice, id=invoice_id)
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    po_id = data.get('po_id')
    if not po_id:
        return JsonResponse({'error': 'po_id is required'}, status=400)

    po = get_object_or_404(PurchaseOrder, workguru_id=po_id)
    invoice.purchase_orders.add(po)

    return JsonResponse({
        'success': True,
        'po': _po_to_dict(po),
    })


@login_required
def unlink_purchase_invoice_po(request, invoice_id, po_id):
    """Remove the link between a PurchaseInvoice and a PurchaseOrder."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    invoice = get_object_or_404(PurchaseInvoice, id=invoice_id)
    po = get_object_or_404(PurchaseOrder, workguru_id=po_id)
    invoice.purchase_orders.remove(po)
    return JsonResponse({'success': True})


@login_required
def search_purchase_invoices(request):
    """Search PurchaseInvoice records by invoice number or supplier name.
    Used from the PO detail page to find invoices to link."""
    q = request.GET.get('q', '').strip()
    if len(q) < 2:
        return JsonResponse({'results': []})

    invoices = PurchaseInvoice.objects.filter(
        Q(invoice_number__icontains=q) | Q(supplier_name__icontains=q)
    ).order_by('-date', '-created_at')[:20]

    results = []
    for inv in invoices:
        results.append({
            'id': inv.id,
            'invoice_number': inv.invoice_number,
            'supplier_name': inv.supplier_name or '',
            'date': inv.date.strftime('%d/%m/%Y') if inv.date else '',
            'total': str(inv.total),
            'amount_outstanding': str(inv.amount_outstanding),
            'status': inv.status,
            'payment_status': inv.payment_status,
        })

    return JsonResponse({'results': results})


def _po_to_dict(po):
    return {
        'workguru_id': po.workguru_id,
        'display_number': po.display_number or po.number or f'#{po.workguru_id}',
        'supplier_name': po.supplier_name or '—',
        'total': str(po.total),
        'status': po.status or '—',
        'url': f'/purchase-order/{po.workguru_id}/',
    }


def _purchase_invoice_to_dict(inv):
    return {
        'id': inv.id,
        'invoice_number': inv.invoice_number,
        'supplier_name': inv.supplier_name or '—',
        'date': inv.date.strftime('%d/%m/%Y') if inv.date else '—',
        'total': str(inv.total),
        'amount_outstanding': str(inv.amount_outstanding),
        'status': inv.status,
        'payment_status': inv.payment_status,
        'url': f'/purchase-invoices/{inv.id}/',
    }
