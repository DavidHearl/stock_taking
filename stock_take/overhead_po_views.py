"""Views for Overhead Purchase Orders (non-Cost-of-Sales spend)."""

import json
import logging
from decimal import Decimal, InvalidOperation

from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import urlencode
from django.views.decorators.http import require_POST

from .models import OverheadPurchaseOrder, PurchaseInvoice, Supplier, EnabledGLCode

logger = logging.getLogger(__name__)


def _po_to_dict(po):
    return {
        'id': po.id,
        'reference': po.reference,
        'supplier_name': po.supplier_name,
        'category': po.category,
        'category_display': po.get_category_display(),
        'status': po.status,
        'status_display': po.get_status_display(),
        'description': po.description,
        'date': po.date.isoformat() if po.date else None,
        'expected_date': po.expected_date.isoformat() if po.expected_date else None,
        'amount_net': float(po.amount_net),
        'amount_vat': float(po.amount_vat),
        'amount_gross': float(po.amount_gross),
        'notes': po.notes,
        'gl_code': po.gl_code,
        'created_by': po.created_by,
        'created_at': po.created_at.isoformat(),
        'invoice_count': po.purchase_invoices.count(),
    }


def _invoice_to_dict(inv):
    return {
        'id': inv.id,
        'invoice_number': inv.invoice_number,
        'supplier_name': inv.supplier_name,
        'date': inv.date.isoformat() if inv.date else None,
        'due_date': inv.due_date.isoformat() if inv.due_date else None,
        'total': float(inv.total or 0),
        'amount_paid': float(inv.amount_paid or 0),
        'outstanding': float((inv.total or 0) - (inv.amount_paid or 0)),
        'status': inv.status,
        'payment_status': inv.payment_status,
    }


# ── List ──────────────────────────────────────────────────────────

def build_overhead_list_context(request):
    """Build the context for the Overhead PO list so it can be embedded as a
    tab on the combined Purchase Orders page.

    Filter params are read with an ``o`` prefix (``oq``, ``ostatus``,
    ``ocategory``) so they don't collide with the main PO tab's own filters,
    and the returned context keys are namespaced with ``opo_`` for the same
    reason. The queryset itself is exposed as ``overhead_pos``.

    The layout mirrors the Purchase Orders tab: search and category filter
    server-side (page reload), but status filtering is done client-side via
    status pills — so every status is rendered here and the pills show/hide
    rows in JS. ``ostatus`` is passed through only to set the active pill on
    initial load.
    """
    qs = OverheadPurchaseOrder.objects.all()

    search = request.GET.get('oq', '').strip()
    if search:
        qs = qs.filter(
            Q(reference__icontains=search) |
            Q(supplier_name__icontains=search) |
            Q(description__icontains=search)
        )

    # Supplier exclusion filter (mirrors the PO tab's Suppliers dropdown)
    excluded_suppliers = request.GET.getlist('oexclude_supplier')
    if excluded_suppliers:
        qs = qs.exclude(supplier_name__in=excluded_suppliers)

    category_filter = request.GET.get('ocategory', '')
    if category_filter:
        qs = qs.filter(category=category_filter)

    # Status filtering is client-side (pills) — pass ostatus through for the
    # initially-active pill only; do NOT filter the queryset by it here.
    status_filter = request.GET.get('ostatus', 'all')

    # Per-status counts on the search/supplier/category-filtered set (pill badges)
    status_counts = dict(
        qs.values_list('status').annotate(c=Count('id')).values_list('status', 'c')
    )

    # Distinct overhead supplier names for the Suppliers filter dropdown
    all_suppliers = list(
        OverheadPurchaseOrder.objects
        .exclude(supplier_name__isnull=True).exclude(supplier_name='')
        .values_list('supplier_name', flat=True)
        .distinct().order_by('supplier_name')
    )

    suppliers = (
        Supplier.objects.filter(is_active=True).order_by('name')
    )

    return {
        'overhead_pos': qs.prefetch_related('purchase_invoices'),
        'opo_total_count': OverheadPurchaseOrder.objects.count(),
        'opo_total_filtered': qs.count(),
        'opo_status_counts': status_counts,
        'opo_status_filter': status_filter,
        'opo_category_filter': category_filter,
        'opo_excluded_suppliers': excluded_suppliers,
        'opo_all_suppliers': all_suppliers,
        'opo_search_query': search,
        'opo_category_choices': OverheadPurchaseOrder.CATEGORY_CHOICES,
        'opo_status_choices': OverheadPurchaseOrder.STATUS_CHOICES,
        'opo_suppliers': suppliers,
    }


@login_required
def overhead_po_list(request):
    """Overheads are now a tab on the combined Purchase Orders page.

    This URL is kept so existing links/bookmarks (and the overhead detail
    back-link / post-delete redirect) still resolve — it simply forwards to
    the Overheads tab, preserving any inbound query params.
    """
    params = {'tab': 'overheads'}
    if request.GET.get('new') == '1':
        params['new'] = '1'
    return redirect(f"{reverse('purchase_orders_list')}?{urlencode(params)}")


# ── Create ────────────────────────────────────────────────────────

@login_required
@require_POST
def overhead_po_create(request):
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    def _dec(key, default='0'):
        try:
            return Decimal(str(data.get(key) or default))
        except (InvalidOperation, ValueError):
            return Decimal(default)

    supplier_name = (data.get('supplier_name') or '').strip()
    if not supplier_name:
        return JsonResponse({'error': 'supplier_name is required'}, status=400)

    po = OverheadPurchaseOrder(
        supplier_name=supplier_name,
        category=data.get('category', 'other'),
        description=data.get('description', ''),
        status=data.get('status', 'draft'),
        date=data.get('date') or None,
        expected_date=data.get('expected_date') or None,
        amount_net=_dec('amount_net'),
        amount_vat=_dec('amount_vat'),
        amount_gross=_dec('amount_gross'),
        notes=data.get('notes', ''),
        gl_code=data.get('gl_code', ''),
        created_by=request.user.get_full_name() or request.user.username,
    )

    # Optionally link to a Supplier record
    supplier_id = data.get('supplier_id')
    if supplier_id:
        try:
            po.supplier = Supplier.objects.get(pk=supplier_id)
        except Supplier.DoesNotExist:
            pass

    po.save()
    return JsonResponse({'success': True, 'po': _po_to_dict(po)}, status=201)


# ── Detail ────────────────────────────────────────────────────────

@login_required
def overhead_po_detail(request, po_id):
    po = get_object_or_404(OverheadPurchaseOrder, pk=po_id)
    linked_invoices = po.purchase_invoices.all().order_by('-date')

    suppliers = Supplier.objects.filter(is_active=True).order_by('name')

    context = {
        'po': po,
        'linked_invoices': linked_invoices,
        'category_choices': OverheadPurchaseOrder.CATEGORY_CHOICES,
        'status_choices': OverheadPurchaseOrder.STATUS_CHOICES,
        'suppliers': suppliers,
    }
    return render(request, 'stock_take/overhead_po_detail.html', context)


# ── Update ────────────────────────────────────────────────────────

@login_required
@require_POST
def overhead_po_update(request, po_id):
    po = get_object_or_404(OverheadPurchaseOrder, pk=po_id)
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    def _dec(key):
        try:
            return Decimal(str(data[key])) if data.get(key) not in (None, '') else None
        except (InvalidOperation, ValueError):
            return None

    if 'supplier_name' in data:
        po.supplier_name = (data['supplier_name'] or '').strip()
    if 'category' in data:
        po.category = data['category']
    if 'description' in data:
        po.description = data['description']
    if 'status' in data:
        po.status = data['status']
    if 'date' in data:
        po.date = data['date'] or None
    if 'expected_date' in data:
        po.expected_date = data['expected_date'] or None
    if 'notes' in data:
        po.notes = data['notes']
    if 'gl_code' in data:
        po.gl_code = data.get('gl_code', '')

    for field in ('amount_net', 'amount_vat', 'amount_gross'):
        v = _dec(field)
        if v is not None:
            setattr(po, field, v)

    supplier_id = data.get('supplier_id')
    if supplier_id:
        try:
            po.supplier = Supplier.objects.get(pk=supplier_id)
        except Supplier.DoesNotExist:
            pass
    elif 'supplier_id' in data and not supplier_id:
        po.supplier = None

    po.save()
    return JsonResponse({'success': True, 'po': _po_to_dict(po)})


# ── Delete ────────────────────────────────────────────────────────

@login_required
@require_POST
def overhead_po_delete(request, po_id):
    po = get_object_or_404(OverheadPurchaseOrder, pk=po_id)
    po.delete()
    return JsonResponse({'success': True})


# ── Invoice linking ───────────────────────────────────────────────

@login_required
@require_POST
def overhead_po_link_invoice(request, po_id):
    po = get_object_or_404(OverheadPurchaseOrder, pk=po_id)
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    invoice_id = data.get('invoice_id')
    if not invoice_id:
        return JsonResponse({'error': 'invoice_id required'}, status=400)

    invoice = get_object_or_404(PurchaseInvoice, pk=invoice_id)
    po.purchase_invoices.add(invoice)
    return JsonResponse({'success': True, 'invoice': _invoice_to_dict(invoice)})


@login_required
@require_POST
def overhead_po_unlink_invoice(request, po_id, invoice_id):
    po = get_object_or_404(OverheadPurchaseOrder, pk=po_id)
    invoice = get_object_or_404(PurchaseInvoice, pk=invoice_id)
    po.purchase_invoices.remove(invoice)
    return JsonResponse({'success': True})


# ── Invoice search (for linking) ──────────────────────────────────

@login_required
def overhead_po_search_invoices(request, po_id):
    """Return unlinked PurchaseInvoice records matching the search query."""
    po = get_object_or_404(OverheadPurchaseOrder, pk=po_id)
    q = request.GET.get('q', '').strip()

    already_linked = po.purchase_invoices.values_list('id', flat=True)

    qs = PurchaseInvoice.objects.exclude(id__in=already_linked)
    if q:
        qs = qs.filter(
            Q(invoice_number__icontains=q) |
            Q(supplier_name__icontains=q)
        )
    else:
        # Default: show recent unlinked invoices
        qs = qs.order_by('-date')

    qs = qs[:30]
    return JsonResponse({'results': [_invoice_to_dict(inv) for inv in qs]})
