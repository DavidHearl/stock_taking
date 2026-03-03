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

from .models import Order, PurchaseInvoice, PurchaseInvoiceLineItem, Supplier

logger = logging.getLogger(__name__)


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

    # Recalculate total from lines if no total provided
    if total_val == 0 and line_items_raw:
        invoice.total = invoice.line_items.aggregate(t=Sum('line_total'))['t'] or 0
        invoice.save(update_fields=['total'])

    if uploaded_file:
        invoice.attachment = uploaded_file
        invoice.save(update_fields=['attachment'])

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
        'success':        True,
        'invoice_number': invoice.invoice_number,
        'supplier_name':  invoice.supplier_name or '',
        'date':           invoice.date.strftime('%d/%m/%Y') if invoice.date else '',
        'due_date':       invoice.due_date.strftime('%d/%m/%Y') if invoice.due_date else '',
        'status':         invoice.status,
        'amount_paid':    str(invoice.amount_paid),
        'payment_status': invoice.payment_status,
        'notes':          invoice.notes or '',
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
    line.delete()
    _recalc_invoice_total(invoice)

    return JsonResponse({
        'success': True,
        'invoice_total': str(invoice.total),
        'amount_paid': str(invoice.amount_paid),
        'amount_outstanding': str(invoice.amount_outstanding),
    })


# ── Attachment upload / delete ────────────────────────────────────
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
