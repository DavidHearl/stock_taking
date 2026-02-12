"""
Invoice views – displays invoices from the local DB and provides a
streaming sync endpoint that pulls data from WorkGuru.
"""

import json
import logging

from django.db.models import Q
from django.shortcuts import render, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.http import StreamingHttpResponse

from .models import Invoice
from .services.workguru_api import WorkGuruAPI, WorkGuruAPIError
from .services.workguru_invoices import (
    fetch_all_invoice_ids,
    fetch_invoice_detail,
    upsert_invoice,
)

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


# ── Sync via SSE (incremental by default) ─────────────────────────
@login_required
def sync_invoices_stream(request):
    """
    SSE endpoint – streams sync progress to the browser.

    Query params:
        ?full=1  – force full resync of every invoice (slower but
                   refreshes payment data for all records).
    """
    force_full = request.GET.get('full') == '1'

    def _sse(payload):
        return f"data: {json.dumps(payload)}\n\n"

    def event_stream():
        try:
            api = WorkGuruAPI.authenticate()

            # -- Step 1: fetch all invoice summaries -----------------
            yield _sse({'status': 'progress', 'message': 'Connecting to WorkGuru...'})

            try:
                summaries = fetch_all_invoice_ids(api)
            except WorkGuruAPIError as e:
                yield _sse({'error': str(e)})
                return

            api_total = len(summaries)
            api_ids = {s['id'] for s in summaries if s.get('id')}

            # -- Step 2: determine which invoices are new ------------
            existing_ids = set(
                Invoice.objects.values_list('workguru_id', flat=True)
            )
            new_ids = api_ids - existing_ids
            skipped = len(existing_ids & api_ids)

            if force_full:
                to_sync = [s for s in summaries if s.get('id')]
                yield _sse({
                    'status': 'progress',
                    'message': (
                        f'Full sync: {api_total} invoices from WorkGuru. '
                        f'Fetching detail for all...'
                    ),
                })
            else:
                to_sync = [s for s in summaries if s.get('id') in new_ids]
                yield _sse({
                    'status': 'progress',
                    'message': (
                        f'Found {api_total} invoices in WorkGuru. '
                        f'{len(new_ids)} new, {skipped} already synced.'
                    ),
                })

            if not to_sync:
                yield _sse({
                    'status': 'complete',
                    'created': 0,
                    'updated': 0,
                    'skipped': skipped,
                    'total': api_total,
                    'errors': [],
                })
                return

            # -- Step 3: sync each invoice ---------------------------
            created = 0
            updated = 0
            errors = []
            total_to_sync = len(to_sync)

            for idx, summary in enumerate(to_sync, 1):
                wg_id = summary['id']
                inv_num = summary.get('invoiceNumber', f'ID {wg_id}')

                try:
                    # Fetch accurate detail from GetInvoiceById
                    detail = fetch_invoice_detail(api, wg_id)
                    inv_data = detail if detail else summary

                    invoice_obj, was_created = upsert_invoice(inv_data)

                    if was_created:
                        created += 1
                    else:
                        updated += 1

                except Exception as exc:
                    errors.append(f"{inv_num}: {exc}")
                    logger.error(f"Error syncing invoice {inv_num}: {exc}", exc_info=True)

                # Progress every 5 invoices (or on the last one)
                if idx % 5 == 0 or idx == total_to_sync:
                    yield _sse({
                        'status': 'progress',
                        'message': (
                            f'Syncing {idx}/{total_to_sync}... '
                            f'({created} created, {updated} updated'
                            f'{", " + str(len(errors)) + " errors" if errors else ""})'
                        ),
                    })

            # -- Step 4: done ----------------------------------------
            yield _sse({
                'status': 'complete',
                'created': created,
                'updated': updated,
                'skipped': skipped,
                'total': api_total,
                'errors': errors,
            })

        except WorkGuruAPIError as e:
            yield _sse({'error': str(e)})
        except Exception as e:
            logger.error(f"Invoice sync error: {e}", exc_info=True)
            yield _sse({'error': str(e)})

    response = StreamingHttpResponse(event_stream(), content_type='text/event-stream')
    response['Cache-Control'] = 'no-cache'
    response['X-Accel-Buffering'] = 'no'
    return response
