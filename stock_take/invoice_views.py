"""
Invoice views – displays invoices from the local DB.
"""

import json
import logging

from django.db.models import Q
from django.shortcuts import render, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.http import StreamingHttpResponse, JsonResponse

from .models import Invoice, PurchaseOrder

logger = logging.getLogger(__name__)


# ── Invoice list ──────────────────────────────────────────────────
@login_required
def invoices_list(request):
    """Display invoices from the local database."""

    status_filter = request.GET.get('status', 'all')
    search_query = request.GET.get('q', '').strip()

    qs = Invoice.objects.all()

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

    invoices = qs.select_related('customer', 'order')

    # Summary stats (over filtered set)
    total_invoices = invoices.count()
    total_value = sum(inv.total for inv in invoices)
    total_outstanding = sum(inv.amount_outstanding for inv in invoices)
    total_paid = sum(inv.amount_paid for inv in invoices)
    paid_count = sum(1 for inv in invoices if inv.payment_status == 'paid')
    unpaid_count = sum(1 for inv in invoices if inv.payment_status == 'unpaid')
    partial_count = sum(1 for inv in invoices if inv.payment_status == 'partial')
    overdue_count = sum(1 for inv in invoices if inv.is_overdue)

    # Last sync timestamp
    last_sync = Invoice.objects.order_by('-synced_at').values_list('synced_at', flat=True).first()

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

    context = {
        'invoice': invoice,
        'line_items': line_items,
        'payments': payments,
        'linked_pos': linked_pos,
    }
    return render(request, 'stock_take/invoice_detail.html', context)


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
    """Create a new Invoice and automatically link it to a PurchaseOrder (AJAX)."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    po = get_object_or_404(PurchaseOrder, workguru_id=po_id)

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
    invoice.purchase_orders.add(po)

    return JsonResponse({
        'success': True,
        'invoice': {
            'id': invoice.id,
            'invoice_number': invoice.invoice_number,
            'client_name': invoice.client_name,
            'date': invoice.date.strftime('%d/%m/%Y') if invoice.date else '',
            'total': str(invoice.total),
            'status': invoice.status,
            'payment_status': invoice.payment_status,
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
