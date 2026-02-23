"""
Invoice views – displays invoices from the local DB.
"""

import json
import logging

from django.db.models import Q
from django.shortcuts import render, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.http import StreamingHttpResponse

from .models import Invoice

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

    context = {
        'invoice': invoice,
        'line_items': line_items,
        'payments': payments,
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
