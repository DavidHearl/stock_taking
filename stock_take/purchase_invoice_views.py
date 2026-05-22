"""
Purchase Invoice views – inbound invoices from suppliers/fitters.
Each line item can be allocated to a specific Order (job) so the cost
flows through into job-level costing.
"""

import json
import logging
import re
from datetime import date as date_type
from decimal import Decimal, InvalidOperation

from django.contrib.auth.decorators import login_required
from django.db.models import Q, Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render

from .models import Order, PurchaseInvoice, PurchaseInvoiceLineItem, PurchaseOrder, PurchaseOrderProduct, Supplier, Timesheet, log_activity, EnabledGLCode, OverheadPurchaseOrder

logger = logging.getLogger(__name__)


def _parse_description_date(description, fallback_date=None):
    """Extract a date from a line description such as 'Wednesday 6th May'.

    Falls back to *fallback_date* (usually the invoice date) when nothing
    useful can be parsed.
    """
    import re as _re
    from datetime import datetime as _dt

    if not description:
        return fallback_date

    # Strip leading day-of-week names
    desc = _re.sub(
        r'\b(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday|'
        r'Mon|Tue|Wed|Thu|Fri|Sat|Sun)\b',
        '', description, flags=_re.IGNORECASE,
    ).strip()

    # Strip ordinal suffixes: 6th → 6
    desc = _re.sub(r'(\d+)(?:st|nd|rd|th)\b', r'\1', desc).strip()

    year = fallback_date.year if fallback_date else _dt.now().year

    # Try "DD Month YYYY" first
    for fmt in ('%d %B %Y', '%d %b %Y'):
        m = _re.search(r'\b(\d{1,2})\s+([A-Za-z]{3,9})\s+(\d{4})\b', desc)
        if m:
            try:
                return _dt.strptime(f"{m.group(1)} {m.group(2)} {m.group(3)}", fmt.replace('%Y', '%Y')).date()
            except ValueError:
                pass

    # Try "DD Month" (no year) – use fallback year
    m = _re.search(r'\b(\d{1,2})\s+([A-Za-z]{3,9})\b', desc)
    if m:
        for fmt in ('%d %B %Y', '%d %b %Y'):
            try:
                return _dt.strptime(f"{m.group(1)} {m.group(2)} {year}", fmt).date()
            except ValueError:
                pass

    return fallback_date


def _match_fitter_for_invoice(invoice):
    """Return the best matching Fitter for the given PurchaseInvoice, or None."""
    from .models import Fitter
    supplier = (invoice.supplier_name or '').strip()
    if not supplier:
        return None
    # Exact match first
    fitter = Fitter.objects.filter(name__iexact=supplier).first()
    if fitter:
        return fitter
    # Substring match either way
    fitter = Fitter.objects.filter(name__icontains=supplier).first()
    if fitter:
        return fitter
    return Fitter.objects.filter(
        **{'name__icontains': supplier.split()[0]}
    ).first() if supplier.split() else None


def _sync_timesheet_for_pi_line(line):
    """Create or update the installation timesheet linked to a purchase invoice line.

    A timesheet is created when either:
    * The line is allocated to an order, OR
    * The line is marked as is_fit_day

    Date resolution order:
      1. Parse date from the line description (e.g. "Fitting 5th May")
      2. Fall back to line.line_date if set
      3. Fall back to the invoice date
    """
    from django.utils import timezone

    should_create = line.order_id or line.is_fit_day

    if should_create:
        fitter = _match_fitter_for_invoice(line.invoice)

        # Always try description-based date first; line_date is the fallback
        fallback = line.line_date or line.invoice.date or timezone.now().date()
        date = _parse_description_date(line.description, fallback) or fallback

        # Build the display description: prefer customer name over raw line text
        if line.order_id and line.order:
            customer = f"{line.order.first_name or ''} {line.order.last_name or ''}".strip()
            display_desc = customer or line.description
        else:
            display_desc = line.description

        ts, created = Timesheet.objects.get_or_create(
            purchase_invoice_line=line,
            defaults={
                'order': line.order,
                'timesheet_type': 'installation',
                'fitter': fitter,
                'date': date,
                'hours': 8,
                'description': display_desc,
            },
        )
        if not created:
            changed = False
            if ts.order_id != line.order_id:
                ts.order = line.order
                changed = True
            if ts.description != display_desc:
                ts.description = display_desc
                changed = True
            if fitter and ts.fitter_id != fitter.id:
                ts.fitter = fitter
                changed = True
            if ts.date != date:
                ts.date = date
                changed = True
            if ts.hours != 8:
                ts.hours = 8
                changed = True
            if changed:
                ts.save()
    else:
        # Not a fit day and no order – remove any linked timesheet
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
def _extract_pdf_fields(pdf_bytes: bytes) -> dict:
    """Extract invoice fields from raw PDF bytes. Returns the ``extracted`` dict.

    Called by both ``parse_purchase_invoice_pdf`` (file upload) and
    ``parse_email_attachment`` (Graph API attachment).
    """
    import io
    import re

    extracted: dict = {}
    try:
        import pdfplumber
        import warnings

        if not pdf_bytes:
            return extracted

        text_lines: list[str] = []
        all_tables: list = []

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
                    try:
                        page_tables = page.extract_tables()
                        if page_tables:
                            all_tables.extend(page_tables)
                    except Exception:
                        pass

        full_text = '\n'.join(text_lines)

        # Always return the raw text so the front-end can log it for debugging
        # (truncated to keep response small)
        extracted['_raw_text'] = full_text[:2000]

        if not full_text.strip():
            return extracted

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

        # ── Line items ────────────────────────────────────────────
        # Strategy: find lines that end with a money amount (optionally qty + unit price + total)
        # Pattern: <description text>  [qty]  [£unit]  £total
        # We skip header/footer noise lines.
        _SKIP_LINE_WORDS = re.compile(
            r'^(?:Description|Qty|Quantity|Unit\s*price|Unit\s*amount|Amount|Total|'
            r'Grand\s*total|Invoice\s*(?:sub)?total|Invoice\s*total|'
            r'Payment\s*details|Bank\s*name|Account|Sort\s*code|Payment\s*ref|'
            r'Invoice\s*(?:number|date|due)|Due\s*date|Billed?\s*(?:to|from)|'
            r'Page\b|Tax|VAT|Sub\s*total|Subtotal|Balance|Discount|Shipping|Delivery|'
            r'Credits?|Payments?)'
            r'|^\+\d',           # +20.0%VAT style adjustment lines
            re.IGNORECASE,
        )
        # Match a line item: non-empty description + optional qty + optional unit + amount
        # Captures: (description, qty_or_empty, unit_or_empty, amount)
        _LINE_ITEM_RE = re.compile(
            r'^(.{3,}?)\s+'                          # description (min 3 chars)
            r'(?:(\d+(?:\.\d+)?)\s+)?'               # optional qty
            r'(?:[£\$€]?\s*[\d,]+\.\d{2}\s+)?'       # optional unit price
            r'[£\$€]?\s*([\d,]+\.\d{2})\s*$',        # line total (required)
            re.MULTILINE,
        )
        _MONEY_CELL_RE = re.compile(r'^[£\$€\s]*[\d,]+\.\d{2}$')

        line_items_found = []
        seen_descriptions = set()
        known_total = extracted.get('total')

        # Collect all summary/footer amounts to exclude from line items
        # (subtotal, VAT, grand total, balance due, payments, credits)
        _SUMMARY_AMOUNT_RE = re.compile(
            r'(?:Subtotal|Sub\s*total|VAT|Tax|Grand\s*total|Invoice\s*(?:sub)?total|'
            r'Invoice\s*total|Balance\s*[Dd]ue|Payments?|Credits?|Total\s*(?:Products?|Charges?)?)[:\s]+'
            r'[£\$€]?\s*([\d,]+\.?\d*)',
            re.IGNORECASE,
        )
        summary_amounts = {m.group(1).replace(',', '') for m in _SUMMARY_AMOUNT_RE.finditer(full_text)}
        if known_total:
            summary_amounts.add(known_total)

        # ── Strategy 1: table extraction (reliable for tabular PDFs) ────────
        for table in all_tables:
            if not table or len(table) < 2:
                continue
            # Skip rows that are entirely empty
            data_rows = [r for r in table[1:] if r and any((c or '').strip() for c in r)]
            if not data_rows:
                continue
            max_cols = max((len(r) for r in data_rows), default=0)
            if max_cols < 2:
                continue
            # Find the rightmost column where the majority of rows look like money amounts
            amount_col = None
            for ci in range(max_cols - 1, 0, -1):
                money_count = sum(
                    1 for r in data_rows
                    if ci < len(r) and _MONEY_CELL_RE.match((r[ci] or '').strip())
                )
                if money_count >= max(1, len(data_rows) // 2):
                    amount_col = ci
                    break
            if amount_col is None:
                continue
            for row in data_rows:
                if not row:
                    continue
                desc = (row[0] or '').strip()
                if not desc or len(desc) < 2:
                    continue
                if _SKIP_LINE_WORDS.match(desc):
                    continue
                if amount_col >= len(row):
                    continue
                amount_raw = re.sub(r'[£\$€,\s]', '', (row[amount_col] or '').strip())
                if not amount_raw:
                    continue
                try:
                    amount = round(float(amount_raw), 2)
                except ValueError:
                    continue
                if amount_raw in summary_amounts:
                    continue
                if desc.lower() in seen_descriptions:
                    continue
                seen_descriptions.add(desc.lower())
                line_items_found.append({'description': desc, 'quantity': 1, 'amount': amount})

        # ── Strategy 2: regex on raw text lines (fallback) ──────────────────
        if not line_items_found:
            for line in text_lines:
                line = line.strip()
                if not line:
                    continue
                if _SKIP_LINE_WORDS.match(line):
                    continue
                m = _LINE_ITEM_RE.match(line)
                if not m:
                    continue
                desc = m.group(1).strip().rstrip('.')
                qty_str = m.group(2) or '1'
                amount_str = m.group(3).replace(',', '')

                # Skip if the amount matches any summary/footer amount
                if amount_str in summary_amounts:
                    continue
                # Skip duplicate descriptions
                if desc.lower() in seen_descriptions:
                    continue
                # Skip very short descriptions (likely header noise)
                if len(desc) < 3:
                    continue

                try:
                    amount = float(amount_str)
                    qty = float(qty_str)
                except ValueError:
                    continue

                seen_descriptions.add(desc.lower())
                line_items_found.append({
                    'description': desc,
                    'quantity': qty if qty != 1.0 else 1,
                    'amount': round(amount, 2),
                })

        if line_items_found:
            extracted['line_items'] = line_items_found

        return extracted

    except Exception as exc:
        logger.warning('PDF parse failed (non-fatal): %s', exc)
        return extracted


@login_required
def parse_purchase_invoice_pdf(request):
    """Parse a PDF file upload and return extracted invoice fields as JSON."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    uploaded = request.FILES.get('file')
    if not uploaded:
        return JsonResponse({'error': 'No file provided'}, status=400)

    if not uploaded.name.lower().endswith('.pdf'):
        return JsonResponse({'success': True, 'extracted': {}})

    pdf_bytes = uploaded.read()
    if not pdf_bytes:
        return JsonResponse({'success': True, 'extracted': {}})

    extracted = _extract_pdf_fields(pdf_bytes)
    return JsonResponse({'success': True, 'extracted': extracted})


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
        'opo_category_choices': OverheadPurchaseOrder.CATEGORY_CHOICES,
        'opo_gl_codes': EnabledGLCode.objects.filter(enabled=True).order_by('code'),
    }
    return render(request, 'stock_take/purchase_invoices.html', context)


# ── Detail ────────────────────────────────────────────────────────
@login_required
def purchase_invoice_detail(request, invoice_id):
    invoice = get_object_or_404(PurchaseInvoice, id=invoice_id)
    from django.db.models import F
    line_items = invoice.line_items.select_related('order').order_by(
        F('order__sale_number').asc(nulls_last=True), 'sort_order', 'id'
    )

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
        'linked_opos': invoice.overhead_pos.all().order_by('-created_at'),
        'opo_category_choices': OverheadPurchaseOrder.CATEGORY_CHOICES,
        'opo_gl_codes': EnabledGLCode.objects.filter(enabled=True).order_by('code'),
        'amendments': invoice.amendments.order_by('created_at'),
    }
    # Pre-compute VAT breakdown: net = sum of line items, VAT added on top.
    # Only apply vat_rate when there is no explicit VAT line item already in the list
    # (to avoid double-counting when the user has flattened/added VAT as a line).
    if invoice.vat_rate and invoice.vat_rate > 0:
        line_net = invoice.line_items.aggregate(t=Sum('line_total'))['t'] or Decimal('0')
        has_vat_line = invoice.line_items.filter(description__iregex=r'^VAT\s').exists()
        if not has_vat_line:
            vat_amount  = (line_net * invoice.vat_rate / Decimal('100')).quantize(Decimal('0.01'))
            gross_total = (line_net + vat_amount).quantize(Decimal('0.01'))
            # Auto-correct stored total if it doesn't match the gross (e.g. VAT was set after creation)
            if invoice.total != gross_total:
                invoice.total = gross_total
                invoice.save(update_fields=['total'])
            context['net_total']  = line_net
            context['vat_amount'] = vat_amount
        # else: VAT is already a line item – omit the Net/VAT breakdown rows
    return render(request, 'stock_take/purchase_invoice_detail.html', context)


# ── Create amendment (sub) invoice ────────────────────────────────
@login_required
def create_purchase_amendment_invoice(request, invoice_id):
    """Create an amendment invoice linked to an existing parent purchase invoice.

    Auto-generates the sub-reference number:
      INV-A00-006        → first amendment  → INV-A00-006-A
      INV-A00-006-A      already exists     → INV-A00-006-A2
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    parent = get_object_or_404(PurchaseInvoice, id=invoice_id)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    reason = data.get('reason', '').strip()

    # Build sub-reference number
    count = parent.amendments.count()
    if count == 0:
        sub_number = f'{parent.invoice_number}-A'
    else:
        sub_number = f'{parent.invoice_number}-A{count + 1}'

    suffix = count + 1
    while PurchaseInvoice.objects.filter(invoice_number=sub_number).exists():
        suffix += 1
        sub_number = f'{parent.invoice_number}-A{suffix}'

    amendment = PurchaseInvoice.objects.create(
        invoice_number=sub_number,
        parent_invoice=parent,
        amendment_reason=reason,
        supplier_name=parent.supplier_name,
        date=parent.date,
        status='Draft',
        currency=parent.currency,
        vat_rate=parent.vat_rate,
        total=Decimal('0'),
        amount_paid=Decimal('0'),
        payment_status='unpaid',
    )

    return JsonResponse({
        'success': True,
        'invoice_id': amendment.id,
        'invoice_number': amendment.invoice_number,
        'invoice_url': f'/purchase-invoices/{amendment.id}/',
    })


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
    _vr = (data.get('vat_rate') or '').strip()
    vat_rate_val = _parse_decimal(_vr) if _vr else None

    invoice = PurchaseInvoice.objects.create(
        invoice_number     = invoice_number,
        reference          = (data.get('reference') or '').strip(),
        supplier_reference = (data.get('supplier_reference') or '').strip(),
        supplier_name      = (data.get('supplier_name') or '').strip(),
        date               = _parse_date(data.get('date', '')),
        due_date           = _parse_date(data.get('due_date', '')),
        status             = data.get('status', 'Draft'),
        total              = total_val,
        notes              = (data.get('notes') or '').strip(),
        currency           = (data.get('currency') or 'GBP').strip().upper() or 'GBP',
        vat_rate           = vat_rate_val,
        created_by         = request.user.get_full_name() or request.user.username,
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
            po_product_id = (data.get(f'line_po_product_{idx}') or '').strip()
            if desc:
                line_items_raw.append({
                    'description': desc, 'quantity': qty,
                    'rate': rate, 'order_id': order_id,
                    'po_product_id': po_product_id,
                })
            idx += 1
    else:
        line_items_raw = data.get('line_items', [])

    for i, item in enumerate(line_items_raw):
        if isinstance(item, dict):
            desc          = (item.get('description') or '').strip()
            qty           = _parse_decimal(item.get('quantity', '1'), '1')
            rate          = _parse_decimal(item.get('rate', '0'))
            order_id      = item.get('order_id')
            po_product_id = (item.get('po_product_id') or '').strip()
        else:
            desc, qty, rate, order_id, po_product_id = str(item), Decimal('1'), Decimal('0'), None, ''
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
            if po_product_id:
                try:
                    PurchaseOrderProduct.objects.filter(id=int(po_product_id)).update(invoice_price=rate)
                except (ValueError, TypeError):
                    pass
            # Auto-create installation timesheet if allocated to an order
            _sync_timesheet_for_pi_line(line)

    # Recalculate total from lines if no total provided, applying VAT if set
    if total_val == 0 and line_items_raw:
        net = invoice.line_items.aggregate(t=Sum('line_total'))['t'] or Decimal('0')
        if vat_rate_val and vat_rate_val > 0:
            invoice.total = net * (1 + vat_rate_val / Decimal('100'))
        else:
            invoice.total = net
        invoice.save(update_fields=['total'])

    if uploaded_file:
        invoice.attachment = uploaded_file
        invoice.save(update_fields=['attachment'])

    # Link purchase order if provided
    po_id = (data.get('purchase_order_id') or '').strip()
    if po_id:
        try:
            po_obj = PurchaseOrder.objects.get(id=int(po_id))
            invoice.purchase_orders.add(po_obj)
            # If the invoice has a carriage/freight line, save it to the PO
            freight_val = _parse_decimal(data.get('freight_cost', '0'))
            if freight_val > 0:
                po_obj.freight_cost = freight_val
                po_obj.save(update_fields=['freight_cost'])
        except (PurchaseOrder.DoesNotExist, ValueError):
            pass

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
    if 'currency' in data:
        invoice.currency = (data['currency'] or 'GBP').strip().upper()[:3]
    if 'vat_rate' in data:
        _vr = str(data.get('vat_rate') or '').strip()
        invoice.vat_rate = _parse_decimal(_vr) if _vr else None
        # Recompute gross total from line items whenever VAT rate changes.
        # Don't apply the rate when VAT is already a line item.
        line_net = invoice.line_items.aggregate(t=Sum('line_total'))['t'] or Decimal('0')
        _has_vat_line = invoice.line_items.filter(description__iregex=r'^VAT\s').exists()
        if invoice.vat_rate and invoice.vat_rate > 0 and not _has_vat_line:
            invoice.total = (line_net * (1 + invoice.vat_rate / Decimal('100'))).quantize(Decimal('0.01'))
        else:
            invoice.total = line_net

    invoice.save()
    # If no specific field was updated this is a pure recalculate call – recompute from lines.
    _update_keys = ('invoice_number', 'supplier_name', 'date', 'due_date', 'status',
                    'payment_status', 'total', 'amount_paid', 'notes', 'currency', 'vat_rate')
    if not any(k in data for k in _update_keys):
        _recalc_invoice_total(invoice)
        invoice.refresh_from_db()
    # Compute VAT breakdown for response
    line_net = invoice.line_items.aggregate(t=Sum('line_total'))['t'] or Decimal('0')
    vat_amount = Decimal('0')
    has_vat_line = invoice.line_items.filter(description__iregex=r'^VAT\s').exists()
    if invoice.vat_rate and invoice.vat_rate > 0 and not has_vat_line:
        vat_amount = (line_net * invoice.vat_rate / Decimal('100')).quantize(Decimal('0.01'))
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
        'vat_rate':          str(invoice.vat_rate) if invoice.vat_rate is not None else '',
        'line_net':          str(line_net),
        'vat_amount':        str(vat_amount),
        'currency':          invoice.currency or 'GBP',
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
    raw_date   = data.get('line_date') or ''
    sort_order = invoice.line_items.count()

    line_date = None
    if raw_date:
        try:
            line_date = date_type.fromisoformat(raw_date)
        except (ValueError, TypeError):
            pass

    line = PurchaseInvoiceLineItem.objects.create(
        invoice     = invoice,
        description = desc,
        line_date   = line_date,
        is_fit_day  = bool(data.get('is_fit_day')),
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
    if 'line_date' in data:
        raw_date = data['line_date']
        if raw_date:
            try:
                line.line_date = date_type.fromisoformat(raw_date)
            except (ValueError, TypeError):
                pass
        else:
            line.line_date = None
    if 'is_fit_day' in data:
        line.is_fit_day = bool(data['is_fit_day'])
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


@login_required
def copy_purchase_invoice_line(request, invoice_id, line_id):
    """Duplicate a line item, inserting it immediately after the original."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    invoice = get_object_or_404(PurchaseInvoice, id=invoice_id)
    line    = get_object_or_404(PurchaseInvoiceLineItem, id=line_id, invoice=invoice)

    new_line = PurchaseInvoiceLineItem.objects.create(
        invoice     = invoice,
        description = line.description,
        line_date   = line.line_date,
        quantity    = line.quantity,
        rate        = line.rate,
        line_total  = line.line_total,
        order       = line.order,
        sort_order  = line.sort_order + 1,
    )
    _recalc_invoice_total(invoice)

    return JsonResponse({
        'success': True,
        'line': _line_to_dict(new_line),
        'after_line_id': line.id,
        'invoice_total': str(invoice.total),
        'amount_paid': str(invoice.amount_paid),
        'amount_outstanding': str(invoice.amount_outstanding),
    })


@login_required
def split_purchase_invoice_line(request, invoice_id, line_id):
    """Split a line into N equal parts: qty stays 1 on each, rate is divided evenly."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    invoice = get_object_or_404(PurchaseInvoice, id=invoice_id)
    line    = get_object_or_404(PurchaseInvoiceLineItem, id=line_id, invoice=invoice)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    try:
        parts = int(data.get('parts', 2))
    except (TypeError, ValueError):
        return JsonResponse({'error': 'Invalid number of parts'}, status=400)

    if parts < 2 or parts > 100:
        return JsonResponse({'error': 'Parts must be between 2 and 100'}, status=400)

    # Divide rate evenly; any penny rounding goes on the first line
    split_rate    = (line.rate / parts).quantize(Decimal('0.01'))
    first_rate    = line.rate - split_rate * (parts - 1)  # absorbs rounding

    # Update original line
    line.quantity   = Decimal('1')
    line.rate       = first_rate
    line.line_total = first_rate
    line.save(update_fields=['quantity', 'rate', 'line_total'])

    # Create the remaining N-1 lines
    new_lines = []
    for _ in range(parts - 1):
        new_line = PurchaseInvoiceLineItem.objects.create(
            invoice     = invoice,
            description = line.description,
            line_date   = line.line_date,
            quantity    = Decimal('1'),
            rate        = split_rate,
            line_total  = split_rate,
            order       = line.order,
            sort_order  = line.sort_order + 1,
        )
        new_lines.append(_line_to_dict(new_line))

    _recalc_invoice_total(invoice)

    return JsonResponse({
        'success': True,
        'original': _line_to_dict(line),
        'new_lines': new_lines,
        'after_line_id': line.id,
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
@login_required
def flatten_vat_to_line_item(request, invoice_id):
    """Convert the invoice's VAT rate into an explicit line item and clear the rate."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    invoice = get_object_or_404(PurchaseInvoice, id=invoice_id)

    if not invoice.vat_rate or invoice.vat_rate <= 0:
        return JsonResponse({'error': 'This invoice has no VAT rate set.'}, status=400)

    line_net = invoice.line_items.aggregate(t=Sum('line_total'))['t'] or Decimal('0')
    vat_amount = (line_net * invoice.vat_rate / Decimal('100')).quantize(Decimal('0.01'))

    sort_order = invoice.line_items.count()
    vat_line = PurchaseInvoiceLineItem.objects.create(
        invoice=invoice,
        description=f'VAT {invoice.vat_rate:g}% on {invoice.currency or "GBP"} {line_net:.2f}',
        quantity=Decimal('1'),
        rate=vat_amount,
        line_total=vat_amount,
        sort_order=sort_order,
    )

    # Clear the VAT rate so the invoice total is now the flat sum of all line items
    invoice.vat_rate = None
    invoice.save(update_fields=['vat_rate'])
    _recalc_invoice_total(invoice)
    invoice.refresh_from_db()

    return JsonResponse({
        'success': True,
        'line': _line_to_dict(vat_line),
        'invoice_total': str(invoice.total),
        'amount_paid': str(invoice.amount_paid),
        'amount_outstanding': str(invoice.amount_outstanding),
    })


def _recalc_invoice_total(invoice):
    line_sum = invoice.line_items.aggregate(t=Sum('line_total'))['t'] or Decimal('0')
    # Apply vat_rate only when there is no explicit VAT line item (avoids double-counting).
    has_vat_line = invoice.line_items.filter(description__iregex=r'^VAT\s').exists()
    if invoice.vat_rate and invoice.vat_rate > 0 and not has_vat_line:
        vat = (line_sum * invoice.vat_rate / Decimal('100')).quantize(Decimal('0.01'))
        total = line_sum + vat
    else:
        total = line_sum
    invoice.total = total
    # Clamp amount_paid — never let it go negative
    if invoice.amount_paid > total:
        invoice.amount_paid = max(Decimal('0'), total)
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
    customer_name = ''
    if line.order:
        customer_name = f"{line.order.first_name} {line.order.last_name}".strip()
    return {
        'id': line.id,
        'description': line.description,
        'line_date': line.line_date.isoformat() if line.line_date else '',
        'is_fit_day': line.is_fit_day,
        'quantity': str(line.quantity),
        'rate': str(line.rate),
        'line_total': str(line.line_total),
        'order_id': line.order_id,
        'sale_number': line.order.sale_number if line.order else '',
        'customer_name': customer_name,
        'order_label': (
            f"{line.order.sale_number} – {customer_name}".strip(' –')
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
    """Search PurchaseInvoice records by invoice number or supplier name."""
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


@login_required
def next_invoice_number(request):
    """Return the next auto-generated invoice number."""
    return JsonResponse({'invoice_number': PurchaseInvoice.generate_invoice_number()})


@login_required
def supplier_search(request):
    """Search Supplier records by name for autocomplete."""
    q = request.GET.get('q', '').strip()
    if not q:
        return JsonResponse({'results': []})
    suppliers = Supplier.objects.filter(
        name__icontains=q, is_active=True
    ).order_by('name')[:20]
    return JsonResponse({'results': [
        {'id': s.id, 'name': s.name, 'city': s.city or '', 'vat_rate': float(s.vat_rate) if s.vat_rate is not None else None}
        for s in suppliers
    ]})


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


# ── Search Xero for an existing invoice ──────────────────────────
@login_required
def search_purchase_invoice_in_xero(request, invoice_id):
    """Search Xero for an ACCPAY invoice matching this invoice's number/reference.
    If found, saves the xero_id locally and returns the Xero URL.
    Does NOT create anything in Xero — use the detail page to push.
    """
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required'}, status=405)

    invoice = get_object_or_404(PurchaseInvoice, id=invoice_id)

    if invoice.xero_id:
        xero_url = f'https://go.xero.com/AccountsPayable/View.aspx?InvoiceID={invoice.xero_id}'
        return JsonResponse({'found': True, 'xero_id': invoice.xero_id, 'xero_url': xero_url})

    from stock_take.services import xero_api

    inv_number = (invoice.invoice_number or '').strip()
    if not inv_number:
        return JsonResponse({'found': False, 'message': 'Invoice has no invoice number to search by'})

    # Xero excludes DELETED invoices from results by default.
    # Keep the filter simple — complex WHERE clauses with != can cause 400 errors.
    result = xero_api._api_get('Invoices', params={
        'where': f'Type=="ACCPAY" AND InvoiceNumber=="{inv_number}"',
    })

    invoices = (result or {}).get('Invoices', [])
    # Exclude any that Xero happens to return as DELETED or VOIDED
    invoices = [i for i in invoices if i.get('Status') not in ('DELETED', 'VOIDED')]
    if not invoices:
        return JsonResponse({'found': False, 'message': f'No Xero bill found with number "{inv_number}"'})

    xero_invoice = invoices[0]
    xero_id = xero_invoice.get('InvoiceID', '')
    if not xero_id:
        return JsonResponse({'found': False, 'message': 'Xero returned an invoice without an ID'})

    invoice.xero_id = xero_id
    invoice.save(update_fields=['xero_id'])

    xero_url = f'https://go.xero.com/AccountsPayable/View.aspx?InvoiceID={xero_id}'
    return JsonResponse({'found': True, 'xero_id': xero_id, 'xero_url': xero_url})


# ── Sync payment statuses from Xero ──────────────────────────────────
@login_required
def sync_xero_payment_statuses(request):
    """Fetch payment status from Xero for all locally-linked purchase invoices.
    Batches up to 100 IDs per API call. Updates payment_status and amount_paid.
    """
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required'}, status=405)

    from stock_take.services import xero_api

    linked = list(PurchaseInvoice.objects.exclude(xero_id='').values('id', 'xero_id', 'total'))
    if not linked:
        return JsonResponse({'success': True, 'updated': 0, 'message': 'No linked invoices to sync'})

    # Build a map xero_id -> local invoice info
    xero_map = {item['xero_id']: item for item in linked}
    ids = list(xero_map.keys())

    updated = 0
    errors = []
    BATCH = 100

    for i in range(0, len(ids), BATCH):
        batch_ids = ids[i:i + BATCH]
        result = xero_api._api_get('Invoices', params={'IDs': ','.join(batch_ids)})
        if not result:
            errors.append('Xero API call failed for a batch')
            continue

        for xero_inv in result.get('Invoices', []):
            xero_id = xero_inv.get('InvoiceID', '')
            if not xero_id or xero_id not in xero_map:
                continue

            xero_status = xero_inv.get('Status', '')
            if xero_status in ('DELETED', 'VOIDED'):
                continue

            try:
                amount_paid = Decimal(str(xero_inv.get('AmountPaid', 0)))
                amount_due = Decimal(str(xero_inv.get('AmountDue', 0)))
            except (InvalidOperation, TypeError):
                continue

            if xero_status == 'PAID' or amount_due == 0:
                new_status = 'paid'
                new_paid = xero_map[xero_id]['total']  # local total
            elif amount_paid > 0:
                new_status = 'partial'
                new_paid = amount_paid
            else:
                new_status = 'unpaid'
                new_paid = Decimal('0')

            rows = PurchaseInvoice.objects.filter(
                id=xero_map[xero_id]['id']
            ).exclude(
                payment_status=new_status,
                amount_paid=new_paid,
            ).update(payment_status=new_status, amount_paid=new_paid)
            updated += rows

    return JsonResponse({'success': True, 'updated': updated, 'errors': errors})


# ── Manually link a Xero ID ──────────────────────────────────────
UUID_RE = re.compile(
    r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}',
    re.IGNORECASE,
)


@login_required
def manual_link_xero(request, invoice_id):
    """Save a manually entered Xero InvoiceID (or full URL) against the local invoice."""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required'}, status=405)

    invoice = get_object_or_404(PurchaseInvoice, id=invoice_id)
    raw = (request.POST.get('xero_id') or '').strip()
    match = UUID_RE.search(raw)
    if not match:
        return JsonResponse({'success': False, 'error': 'No valid Xero UUID found in the value you entered'})

    xero_id = match.group(0).lower()
    invoice.xero_id = xero_id
    invoice.save(update_fields=['xero_id'])

    xero_url = f'https://go.xero.com/AccountsPayable/View.aspx?InvoiceID={xero_id}'
    return JsonResponse({'success': True, 'xero_id': xero_id, 'xero_url': xero_url})


# ── Remove Xero link ─────────────────────────────────────────────
@login_required
def remove_xero_link(request, invoice_id):
    """Clear the locally stored xero_id, without touching Xero itself."""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required'}, status=405)
    invoice = get_object_or_404(PurchaseInvoice, id=invoice_id)
    invoice.xero_id = ''
    invoice.save(update_fields=['xero_id'])
    return JsonResponse({'success': True})


# ── Void/delete a Xero draft bill ─────────────────────────────────
@login_required
def void_xero_purchase_invoice(request, invoice_id):
    """Delete the Xero DRAFT bill and clear the local xero_id link."""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required'}, status=405)
    invoice = get_object_or_404(PurchaseInvoice, id=invoice_id)
    if not invoice.xero_id:
        return JsonResponse({'success': False, 'error': 'No Xero ID on this invoice'}, status=400)

    from stock_take.services import xero_api
    xero_invoice_id = invoice.xero_id
    payload = {'Invoices': [{'InvoiceID': xero_invoice_id, 'Status': 'DELETED'}]}
    result = xero_api._api_put('Invoices', payload)
    if result is None:
        err = xero_api.get_last_api_error() or 'Could not delete Xero invoice'
        return JsonResponse({'success': False, 'error': err})

    invoice.xero_id = ''
    invoice.save(update_fields=['xero_id'])
    return JsonResponse({'success': True})


# ── Push to Xero ──────────────────────────────────────────────────
@login_required
def push_purchase_invoice_to_xero(request, invoice_id):
    """Push a purchase invoice to Xero as a DRAFT accounts-payable (ACCPAY) invoice."""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required'}, status=405)

    invoice = get_object_or_404(PurchaseInvoice, id=invoice_id)

    if invoice.xero_id:
        return JsonResponse({
            'success': False,
            'error': f'Already pushed to Xero (ID: {invoice.xero_id})',
        }, status=400)

    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        body = {}
    gl_code = (body.get('gl_code') or '').strip()
    attach_pdf = body.get('attach_pdf', True)

    from stock_take.services import xero_api

    supplier_name = (invoice.supplier_name or '').strip()
    if not supplier_name:
        return JsonResponse({'success': False, 'error': 'Invoice has no supplier name'}, status=400)

    # ── Find or create the supplier contact in Xero ───────────
    contact_id = xero_api.find_contact_by_name(supplier_name)

    if not contact_id:
        # Try to look up from the Supplier model for richer contact data
        supplier_obj = Supplier.objects.filter(name__iexact=supplier_name).first()
        create_kwargs = dict(
            name=supplier_name,
            first_name='',
            last_name='',
            email='',
            phone='',
            address_line1='',
            address_line2='',
            city='',
            region='',
            postal_code='',
            country='',
        )
        if supplier_obj:
            create_kwargs.update({
                'email': supplier_obj.email or '',
                'phone': supplier_obj.phone or '',
                'address_line1': supplier_obj.address or '',
            })

        create_res = xero_api.create_contact(**create_kwargs)
        contacts = (create_res or {}).get('Contacts', []) if isinstance(create_res, dict) else []
        if contacts:
            contact_id = contacts[0].get('ContactID', '')

        if not contact_id:
            err = xero_api.get_last_api_error() or f'Contact "{supplier_name}" not found in Xero and auto-create failed.'
            return JsonResponse({'success': False, 'error': err})

    # ── Build line items ──────────────────────────────────────
    line_items_qs = invoice.line_items.all().order_by('sort_order', 'id')
    has_vat = invoice.vat_rate and invoice.vat_rate > 0

    # Known UK Xero tax codes by rate.  Any other rate is looked up (or created)
    # in the account via the TaxRates API.
    _UK_VAT_CODE_MAP = {
        0:  'ZERORATEDINPUT',
        5:  'RRINPUT',
        20: 'INPUT2',
    }
    if has_vat:
        vat_rate_int = int(invoice.vat_rate)
        tax_type = _UK_VAT_CODE_MAP.get(vat_rate_int)
        if tax_type is None:
            # Look up or create the rate in Xero
            tax_type = xero_api.find_or_create_tax_rate(vat_rate_int)
        if tax_type is None:
            # Auto-create failed — check if the caller wants to push anyway without a tax code
            force_no_vat = data.get('force_no_vat') is True
            if not force_no_vat:
                xero_err = xero_api.get_last_api_error()
                detail = f' Xero said: {xero_err}' if xero_err else ''
                return JsonResponse({
                    'success': False,
                    'error': (
                        f'Could not find or create a {vat_rate_int}% VAT rate in Xero.{detail} '
                        f'You can either add a tax rate named "VAT {vat_rate_int}%" in Xero, '
                        f'or push the invoice without a VAT code and assign the tax in Xero manually.'
                    ),
                    'can_force_no_vat': True,
                    'vat_rate_int': vat_rate_int,
                })
            # Force push: use NOINPUT so Xero accepts the invoice without a tax code
            tax_type = 'NOINPUT'
            line_amount_type = 'NoTax'
        else:
            line_amount_type = 'Exclusive'
    else:
        tax_type = None
        line_amount_type = 'NoTax'

    if line_items_qs.exists():
        xero_lines = []
        for li in line_items_qs:
            line = {
                'Description': li.description or 'Item',
                'Quantity': str(li.quantity),
                'UnitAmount': str(li.rate),
            }
            if gl_code:
                line['AccountCode'] = gl_code
            if tax_type:
                line['TaxType'] = tax_type
            xero_lines.append(line)
    else:
        # Fallback: single line using invoice total (already gross)
        line = {
            'Description': invoice.notes or invoice.invoice_number,
            'Quantity': '1',
            'UnitAmount': str(invoice.total),
        }
        if gl_code:
            line['AccountCode'] = gl_code
        if tax_type:
            line['TaxType'] = tax_type
        xero_lines = [line]

    # ── Build the Xero invoice payload ────────────────────────
    invoice_data = {
        'Type': 'ACCPAY',
        'Contact': {'ContactID': contact_id},
        'Status': 'DRAFT',
        'InvoiceNumber': invoice.invoice_number,
        'CurrencyCode': invoice.currency or 'GBP',
        'LineAmountTypes': line_amount_type,
        'LineItems': xero_lines,
        'Reference': invoice.notes or '',
    }

    if invoice.date:
        invoice_data['Date'] = invoice.date.isoformat()
    if invoice.due_date:
        invoice_data['DueDate'] = invoice.due_date.isoformat()
    elif invoice.date:
        invoice_data['DueDate'] = invoice.date.isoformat()

    payload = {'Invoices': [invoice_data]}
    result = xero_api._api_put('Invoices', payload)

    if result is None:
        err = xero_api.get_last_api_error() or 'Unknown Xero API error'
        return JsonResponse({'success': False, 'error': err})

    invoices_resp = result.get('Invoices', [])
    if not invoices_resp:
        return JsonResponse({'success': False, 'error': 'Xero returned no invoice data'})

    xero_invoice_id = invoices_resp[0].get('InvoiceID', '')
    if xero_invoice_id:
        invoice.xero_id = xero_invoice_id
        invoice.save(update_fields=['xero_id'])

    # Attach the PDF if requested and available
    pdf_attached = False
    attach_warning = None
    if xero_invoice_id and attach_pdf and invoice.attachment:
        try:
            filename = invoice.attachment.name.split('/')[-1]
            with invoice.attachment.open('rb') as f:
                file_bytes = f.read()
            content_type = 'application/pdf' if filename.lower().endswith('.pdf') else 'application/octet-stream'
            attach_result = xero_api.attach_file_to_invoice(xero_invoice_id, filename, file_bytes, content_type)
            if attach_result is not None:
                pdf_attached = True
            else:
                attach_warning = xero_api.get_last_api_error() or 'Attachment upload returned no data'
                logger.warning(f'PDF attach returned None for Xero invoice {xero_invoice_id}: {attach_warning}')
        except Exception as attach_err:
            attach_warning = str(attach_err)
            logger.warning(f'Could not attach PDF to Xero invoice {xero_invoice_id}: {attach_err}')
    elif xero_invoice_id and attach_pdf and not invoice.attachment:
        attach_warning = 'No attachment on this invoice'

    log_activity(
        user=request.user,
        event_type='xero_push',
        description=(
            f'{request.user.get_full_name() or request.user.username} pushed purchase invoice '
            f'{invoice.invoice_number} to Xero as draft ACCPAY (Xero ID: {xero_invoice_id}).'
        ),
    )

    xero_url = f'https://go.xero.com/AccountsPayable/View.aspx?InvoiceID={xero_invoice_id}'
    response_data = {
        'success': True,
        'xero_id': xero_invoice_id,
        'xero_url': xero_url,
        'pdf_attached': pdf_attached,
        'message': f'Invoice pushed to Xero as a draft bill (ID: {xero_invoice_id})',
    }
    if attach_warning:
        response_data['attach_warning'] = attach_warning
    return JsonResponse(response_data)


# ── Generate / re-sync timesheets from invoice lines ─────────────
@login_required
def resync_invoice_timesheets(request, invoice_id):
    """Re-run _sync_timesheet_for_pi_line for every line on this invoice.

    Creates missing timesheets, updates fitter/date on existing ones.
    Returns a summary of what was created/updated/skipped.
    """
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required'}, status=405)

    invoice = get_object_or_404(PurchaseInvoice, id=invoice_id)
    lines = invoice.line_items.select_related('order', 'invoice').all()

    created_count = 0
    updated_count = 0
    skipped_count = 0
    fitter_name = ''

    fitter = _match_fitter_for_invoice(invoice)
    if fitter:
        fitter_name = fitter.name

    for line in lines:
        if not line.order_id and not line.is_fit_day:
            skipped_count += 1
            continue
        existing = Timesheet.objects.filter(purchase_invoice_line=line).first()
        _sync_timesheet_for_pi_line(line)
        if existing:
            updated_count += 1
        else:
            created_count += 1

    return JsonResponse({
        'success': True,
        'created': created_count,
        'updated': updated_count,
        'skipped': skipped_count,
        'fitter': fitter_name,
    })


# ── OPO / PO creation & linking from invoice side ────────────────

@login_required
def create_opo_from_invoice(request, invoice_id):
    """Create a new OverheadPurchaseOrder pre-filled from this invoice and link it."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    invoice = get_object_or_404(PurchaseInvoice, id=invoice_id)
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    def _dec(key, default='0'):
        try:
            return Decimal(str(data.get(key) or default))
        except (InvalidOperation, ValueError):
            return Decimal(default)

    supplier_name = (data.get('supplier_name') or invoice.supplier_name or '').strip()
    if not supplier_name:
        return JsonResponse({'error': 'supplier_name is required'}, status=400)

    po = OverheadPurchaseOrder(
        supplier_name=supplier_name,
        category=data.get('category', 'other'),
        description=data.get('description', ''),
        status='draft',
        date=invoice.date,
        amount_net=_dec('amount_net'),
        amount_vat=_dec('amount_vat'),
        amount_gross=_dec('amount_gross', str(invoice.total)),
        gl_code=data.get('gl_code', ''),
        notes=data.get('notes', ''),
        created_by=request.user.get_full_name() or request.user.username,
    )
    po.save()
    po.purchase_invoices.add(invoice)

    return JsonResponse({
        'success': True,
        'opo': {'id': po.id, 'reference': po.reference, 'supplier_name': po.supplier_name, 'amount_gross': str(po.amount_gross), 'status': po.status},
    }, status=201)


@login_required
def link_opo_to_invoice(request, invoice_id):
    """Link an existing OverheadPurchaseOrder to this invoice."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    invoice = get_object_or_404(PurchaseInvoice, id=invoice_id)
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    opo_id = data.get('opo_id')
    if not opo_id:
        return JsonResponse({'error': 'opo_id required'}, status=400)

    opo = get_object_or_404(OverheadPurchaseOrder, pk=opo_id)
    opo.purchase_invoices.add(invoice)

    return JsonResponse({
        'success': True,
        'opo': {'id': opo.id, 'reference': opo.reference, 'supplier_name': opo.supplier_name, 'amount_gross': str(opo.amount_gross), 'status': opo.status},
    })


@login_required
def unlink_opo_from_invoice(request, invoice_id, opo_id):
    """Remove the link between an OverheadPurchaseOrder and this invoice."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    invoice = get_object_or_404(PurchaseInvoice, id=invoice_id)
    opo = get_object_or_404(OverheadPurchaseOrder, pk=opo_id)
    opo.purchase_invoices.remove(invoice)
    return JsonResponse({'success': True})


@login_required
def search_opos_for_invoice(request, invoice_id):
    """Return OPOs not already linked to this invoice, matching the search query."""
    invoice = get_object_or_404(PurchaseInvoice, id=invoice_id)
    q = request.GET.get('q', '').strip()

    already_linked = invoice.overhead_pos.values_list('id', flat=True)
    qs = OverheadPurchaseOrder.objects.exclude(id__in=already_linked)

    if q:
        qs = qs.filter(
            Q(reference__icontains=q) | Q(supplier_name__icontains=q) | Q(description__icontains=q)
        )
    else:
        qs = qs.order_by('-created_at')[:20]

    results = [
        {'id': po.id, 'reference': po.reference, 'supplier_name': po.supplier_name, 'amount_gross': str(po.amount_gross), 'status': po.status}
        for po in qs
    ]
    return JsonResponse({'results': results})


@login_required
def create_po_from_invoice(request, invoice_id):
    """Create a new local PurchaseOrder pre-filled from this invoice and link it."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    invoice = get_object_or_404(PurchaseInvoice, id=invoice_id)
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    supplier_name = (data.get('supplier_name') or invoice.supplier_name or '').strip()
    description = (data.get('description') or invoice.invoice_number or '').strip()

    # Generate a unique workguru_id in the 800000+ range for manual/local POs
    max_id = PurchaseOrder.objects.order_by('-workguru_id').values_list('workguru_id', flat=True).first() or 0
    manual_id = max(max_id + 1, 800000)

    # Generate display number: PO<n+1>
    last_num = 0
    for po_obj in PurchaseOrder.objects.filter(display_number__startswith='PO').order_by('-display_number'):
        m = re.match(r'^PO(\d+)$', po_obj.display_number or '')
        if m:
            last_num = max(last_num, int(m.group(1)))
    display_number = f'PO{last_num + 1}'

    po = PurchaseOrder.objects.create(
        workguru_id=manual_id,
        number=display_number,
        display_number=display_number,
        description=description or None,
        supplier_name=supplier_name or None,
        total=invoice.total,
        status='Draft',
    )
    invoice.purchase_orders.add(po)

    return JsonResponse({'success': True, 'po': _po_to_dict(po)}, status=201)


@login_required
def create_po_standalone(request):
    """Create a new local PurchaseOrder without a pre-existing invoice (used in the Create Invoice modal)."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    supplier_name = (data.get('supplier_name') or '').strip()
    description = (data.get('description') or '').strip()

    max_id = PurchaseOrder.objects.order_by('-workguru_id').values_list('workguru_id', flat=True).first() or 0
    manual_id = max(max_id + 1, 800000)

    last_num = 0
    for po_obj in PurchaseOrder.objects.filter(display_number__startswith='PO').order_by('-display_number'):
        m = re.match(r'^PO(\d+)$', po_obj.display_number or '')
        if m:
            last_num = max(last_num, int(m.group(1)))
    display_number = f'PO{last_num + 1}'

    po = PurchaseOrder.objects.create(
        workguru_id=manual_id,
        number=display_number,
        display_number=display_number,
        description=description or None,
        supplier_name=supplier_name or None,
        status='Draft',
    )
    return JsonResponse({'success': True, 'po': _po_to_dict(po)}, status=201)


