"""Accounts Payable inbox views.

Reads emails from the accounts.payable@sliderobes.com shared mailbox via the
Microsoft Graph API, caches them locally, and lets users create Purchase Invoices
directly from email attachments.
"""

import json
import html as _html
import logging
import re as _re
from datetime import timedelta, datetime
from decimal import Decimal, InvalidOperation

from django.contrib.auth.decorators import login_required
from django.core.files.base import ContentFile
from django.core.paginator import Paginator
from django.db import models as db_models
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.dateparse import parse_datetime
from django.utils import timezone
from django.utils.html import strip_tags
from django.views.decorators.clickjacking import xframe_options_sameorigin

from .models import MailboxEmail, MailboxEmailFilter, MailboxExemption, PurchaseInvoice, PurchaseInvoiceLineItem, Order, Supplier, PurchaseOrder, PurchaseOrderProduct, EnabledGLCode, OverheadPurchaseOrder, SupplierEmailRule, SupplierContact
from .permissions import page_permission_required
from .pricing_utils import apply_invoice_price
from .purchase_invoice_views import _extract_pdf_fields, _parse_date, _parse_decimal
from .services import graph_api

logger = logging.getLogger(__name__)


_MATCHABLE_STATUSES = ('Draft', 'Approved', 'Received', 'Partially Received')


def _with_attachments(qs):
	"""Restrict a MailboxEmail queryset to emails that actually carry an attachment.

	Only emails with a document attached can become a purchase invoice, so the
	inbox ignores the rest. `attachment_names` is a JSON blob (blank, not null)
	holding the non-inline attachments, and `attachment_count` is a Python-only
	property — so the empty cases have to be excluded by value, not `__isnull`.
	"""
	return qs.exclude(attachment_names='').exclude(attachment_names='[]')

# Free/personal email domains — the local-part (username) of these addresses
# is a personal identifier, not a company name, so word-split supplier matching
# against them produces false positives.
_PERSONAL_DOMAINS = frozenset({
    'gmail.com', 'googlemail.com',
    'hotmail.com', 'hotmail.co.uk', 'hotmail.fr', 'hotmail.de', 'hotmail.es',
    'outlook.com', 'live.com', 'live.co.uk', 'live.fr', 'msn.com',
    'yahoo.com', 'yahoo.co.uk', 'yahoo.fr', 'yahoo.ie',
    'icloud.com', 'me.com', 'mac.com',
    'aol.com', 'protonmail.com', 'proton.me',
})


def _amounts_match(a, b, tol=Decimal('0.05')):
    """True when two monetary values are equal within a small tolerance.

    A few pence of slack absorbs rounding differences between the invoice total
    extracted from a PDF and the stored PO total (e.g. 771.06 vs 771.05).
    """
    if a is None or b is None:
        return False
    try:
        return abs(Decimal(str(a)) - Decimal(str(b))) <= tol
    except (InvalidOperation, TypeError, ValueError):
        return False


def _fuzzy_amount_po(etotal, pos, exclude_ids=()):
    """Return the single most plausible PO whose total is *close to* the invoice
    total, or None when the guess would be ambiguous.

    Carriage (and the odd small surcharge) is added on the supplier's invoice
    but is often absent from the PO, so the invoice total is typically a little
    ABOVE the PO total — e.g. invoice £331.13 vs PO £323.46. We allow the PO to
    sit a touch above too, to absorb rounding or minor discounts.

    To avoid coincidental collisions we only return a guess when there is a
    single clear front-runner: the closest PO must beat the runner-up by a
    meaningful margin.
    """
    try:
        inv = Decimal(str(etotal))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if inv <= 0:
        return None
    # Plausible carriage uplift: the greater of £30 or 12% of the invoice.
    cap = max(Decimal('30'), (inv * Decimal('0.12')))
    lower = Decimal('-2.00')  # PO may be marginally above (rounding / small discount)

    scored = []
    for po in pos:
        if po['id'] in exclude_ids:
            continue
        total = po.get('total')
        if total is None:
            continue
        try:
            diff = inv - Decimal(str(total))
        except (InvalidOperation, TypeError, ValueError):
            continue
        if lower <= diff <= cap:
            scored.append((abs(diff), po))

    if not scored:
        return None
    scored.sort(key=lambda t: t[0])
    best_diff, best_po = scored[0]
    # Require a clear winner — if the runner-up is almost as close, don't guess.
    if len(scored) > 1 and (scored[1][0] - best_diff) < Decimal('5.00'):
        return None
    return best_po


def _find_po_matches(emails, supplier_rules=None):
    """Return {email.id: [po_dict, ...]} for unprocessed emails that have
    a potential PO match (Approved / Received / Partially Received).

    Matching uses three signals:
    1. A PO number (3-6 digits, optionally prefixed PO/PO#) found in the
       email subject.
    2. The PO supplier name found in the sender display name or email domain.
       The email local-part (username) is intentionally excluded — matching
       against it causes false positives (e.g. "stuartstevenson202@hotmail.co.uk"
       incorrectly matching a supplier called "Stuart Stevenson").
    3. A SupplierEmailRule maps the sender's address/domain to a known supplier
       name, which is then matched against PO supplier names.
    """
    candidates = [e for e in emails if not e.is_processed and not e.is_ignored]
    if not candidates:
        return {}

    pos = list(
        PurchaseOrder.objects
        .filter(status__in=_MATCHABLE_STATUSES)
        .values('id', 'workguru_id', 'display_number', 'number', 'supplier_name', 'status', 'total', 'po_type')
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
        # Skip emails with no attachments — nothing to process
        if not (email.attachment_names or '').strip():
            continue

        found = {}  # po['id'] -> po  (deduplicates)

        # Signal 1: explicit PO reference in subject (PO prefix required to avoid
        # coincidental number matches like invoice numbers, dates, etc.)
        subject = email.subject or ''
        for m in _re.finditer(r'PO\s*#?\s*(\d{3,6})\b', subject, _re.IGNORECASE):
            key = m.group(1)
            if key in po_by_number:
                po = po_by_number[key]
                found[po['id']] = po

        # Signal 2: supplier name in sender display name / email domain.
        # We deliberately use only the domain portion of the email address
        # (not the local-part/username) to avoid false positives from personal
        # email addresses whose username happens to contain supplier name words.
        sender_domain = ''
        if '@' in (email.sender_email or ''):
            sender_domain = email.sender_email.split('@', 1)[1].lower()
        is_personal = sender_domain in _PERSONAL_DOMAINS

        # For personal domains, match only against the display name.
        # For business domains, also include the domain itself (e.g.
        # "firstglass.ie" will match supplier "First Glass").
        if is_personal:
            sender_text = (email.sender_name or '').lower()
        else:
            sender_text = f"{email.sender_name} {sender_domain}".lower()

        for po in pos:
            if po['id'] in found:
                continue
            sname = (po['supplier_name'] or '').strip().lower()
            if not sname:
                continue
            # Full name must be present in sender text (works for both signal types)
            if sname in sender_text:
                found[po['id']] = po
            elif not is_personal:
                # Word-split matching only for business domains to avoid matching
                # personal names against supplier names on free email services.
                # Require at least 2 significant words (≥4 chars) to match.
                words = [w for w in sname.split() if len(w) >= 4]
                if len(words) >= 2 and sum(1 for w in words if w in sender_text) >= 2:
                    found[po['id']] = po

        # Signal 3: SupplierEmailRule maps this sender to a known supplier.
        # Used when the domain alone doesn't text-match the supplier name
        # (e.g. financedept@osdoors.com → "O & S Doors").
        if not found and supplier_rules:
            addr = (email.sender_email or '').lower()
            resolved_name = supplier_rules.get(addr, '')
            if not resolved_name and '@' in addr:
                resolved_name = supplier_rules.get('@' + addr.split('@', 1)[1], '')
            if resolved_name:
                rname = resolved_name.strip().lower()
                for po in pos:
                    if po['id'] in found:
                        continue
                    if (po['supplier_name'] or '').strip().lower() == rname:
                        found[po['id']] = po

        # Signal 4: the extracted invoice amount points at a PO total. This is
        # the "educated guess" layer. It works in two passes:
        #   (a) Exact — invoice total == PO total (±5p). Strongest amount signal.
        #   (b) Fuzzy — invoice total is a little ABOVE a PO total (carriage that
        #       is on the invoice but not the PO). Only used when there is a
        #       single clear candidate, so it never guesses ambiguously.
        etotal = getattr(email, 'extracted_total', None)
        amount_ids = set()   # PO ids matched on amount (exact or fuzzy)
        fuzzy_ids = set()    # subset of amount_ids that were fuzzy/approximate
        if etotal is not None:
            if found:
                # Supplier already identified — tag whichever found PO(s) match
                # the invoice amount so the UI can surface the most likely one.
                exact_hit = False
                for pid, po in found.items():
                    if _amounts_match(etotal, po['total']):
                        amount_ids.add(pid)
                        exact_hit = True
                # No exact hit among the supplier's POs — fall back to a
                # carriage-tolerant guess within that same set.
                if not exact_hit:
                    fpo = _fuzzy_amount_po(etotal, list(found.values()))
                    if fpo:
                        amount_ids.add(fpo['id'])
                        fuzzy_ids.add(fpo['id'])
            else:
                exact_pos = [po for po in pos if _amounts_match(etotal, po['total'])]
                if len(exact_pos) == 1:
                    po = exact_pos[0]
                    found[po['id']] = po
                    amount_ids.add(po['id'])
                elif not exact_pos:
                    # Discretionary carriage-tolerant guess across all open POs,
                    # accepted only when one candidate clearly stands out.
                    fpo = _fuzzy_amount_po(etotal, pos)
                    if fpo:
                        found[fpo['id']] = fpo
                        amount_ids.add(fpo['id'])
                        fuzzy_ids.add(fpo['id'])

        if found:
            # Tag amount matches on a per-email copy so the flag never leaks
            # across emails that share the same underlying po dict.
            email_matches = []
            for pid, po in found.items():
                po_copy = dict(po)
                po_copy['_amount_match'] = pid in amount_ids
                po_copy['_amount_fuzzy'] = pid in fuzzy_ids
                email_matches.append(po_copy)
            result[email.id] = email_matches

    return result



_PO_DATE_FORMATS = (
    '%Y-%m-%dT%H:%M:%S.%f', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d',
    '%d/%m/%Y', '%d-%m-%Y',
)


def _parse_po_date(value):
    """Parse a PurchaseOrder string date (mixed formats) into a date, or None."""
    if not value:
        return None
    raw = value.strip()
    for fmt in _PO_DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    # Fall back to parsing the ISO date prefix (e.g. "2026-01-05T...")
    try:
        return datetime.strptime(raw[:10], '%Y-%m-%d').date()
    except ValueError:
        return None


def _get_approved_unreceived_pos():
    """Received POs from 2026 onward that have no linked invoice (neither an
    uploaded PO invoice nor a linked PurchaseInvoice). An item must be received
    before an invoice can be issued."""
    pos = (
        PurchaseOrder.objects
        .filter(status__in=('Received', 'Partially Received'))
        .exclude(supplier_name__icontains='Carnehill')
        .exclude(invoice_not_required=True)
        .annotate(
            _po_invoice_count=db_models.Count('invoices', distinct=True),
            _linked_invoice_count=db_models.Count('linked_purchase_invoices', distinct=True),
        )
        .filter(_po_invoice_count=0, _linked_invoice_count=0)
    )

    # Map supplier name → best search term from the Rules section, so the
    # sidebar search can target the supplier's known sender address/domain
    # instead of the raw supplier name. A supplier may have several rules
    # (e.g. two staff addresses on the same domain) — in that case search by
    # the shared domain so all of them match. Strip a leading '@' so a domain
    # rule (e.g. '@hafele.ie') still matches sender addresses.
    supplier_rules = {}
    for rule in SupplierEmailRule.objects.all():
        key = (rule.supplier_name or '').strip().lower()
        pattern = rule.email_pattern.lstrip('@').strip()
        if key and pattern:
            supplier_rules.setdefault(key, []).append(pattern)

    supplier_email_map = {}
    for key, patterns in supplier_rules.items():
        domains = {p.split('@', 1)[1] if '@' in p else p for p in patterns}
        # Single shared domain → search by domain (matches every address on it).
        # Otherwise fall back to the first specific pattern.
        supplier_email_map[key] = next(iter(domains)) if len(domains) == 1 else patterns[0]

    result = []
    for po in pos:
        po_date = _parse_po_date(po.received_date) or _parse_po_date(po.approved_date) or _parse_po_date(po.issue_date)
        if not po_date or po_date.year < 2026:
            continue
        po.sidebar_date = po_date
        po.search_email = supplier_email_map.get((po.supplier_name or '').strip().lower(), '')
        result.append(po)

    # Order by PO number descending (e.g. PO1760, PO1758, ...), keeping split
    # orders (e.g. PO1760_1) directly next to their original PO. We sort on the
    # base PO number first, then by the split suffix ascending so the original
    # (no suffix) comes before _1, _2, etc.
    def _po_sort_key(p):
        raw = p.display_number or p.number or ''
        m = _re.match(r'^\D*(\d+)(?:_(\d+))?', raw)
        base = int(m.group(1)) if m and m.group(1) else 0
        suffix = int(m.group(2)) if m and m.group(2) else 0
        # reverse=True applied below: negate suffix so the original sorts first
        return (base, -suffix)

    result.sort(key=_po_sort_key, reverse=True)
    return result


def _build_supplier_email_index():
	"""Build two lookups from the email addresses stored on supplier records:

	    exact  – {address: supplier_name}   every address on file
	    domain – {domain: supplier_name}     business domains that belong to a
	                                          single supplier (unambiguous)

	Addresses come from both ``Supplier.email`` and each ``SupplierContact.email``.
	The domain map deliberately drops personal domains and any domain shared by
	more than one supplier, so a fallback match never guesses ambiguously.
	"""
	exact = {}
	domain_names = {}  # domain -> set of supplier names
	pairs = list(
		Supplier.objects.filter(is_active=True)
		.exclude(email__isnull=True).exclude(email='')
		.values_list('email', 'name')
	)
	pairs += list(
		SupplierContact.objects.exclude(email='')
		.values_list('email', 'supplier__name')
	)
	for raw_email, name in pairs:
		if not raw_email or not name:
			continue
		addr = raw_email.lower().strip()
		if '@' not in addr:
			continue
		exact.setdefault(addr, name)
		domain = addr.split('@', 1)[1]
		if domain and domain not in _PERSONAL_DOMAINS:
			domain_names.setdefault(domain, set()).add(name)
	domain = {d: next(iter(names)) for d, names in domain_names.items() if len(names) == 1}
	return exact, domain


@login_required
@page_permission_required('accounts_payable')
def accounts_payable(request):
	"""Accounts Payable landing page — purchase orders awaiting a supplier invoice.

	The work here is driven by the PO list, not the mailbox: pick the supplier an
	invoice came from, and the awaiting-invoice POs narrow to that supplier so the
	invoice can be raised against the right one. The shared mailbox is a secondary
	source and lives behind the inbox modal.
	"""
	pos = _get_approved_unreceived_pos()

	# Supplier picker options are built from the awaiting-invoice POs themselves,
	# so the dropdown can never offer a supplier with nothing to invoice.
	# PurchaseOrder has no FK to Supplier — the join is supplier_id == workguru_id
	# — so carry both keys and let the client filter on whichever the PO has.
	supplier_options = {}
	for po in pos:
		name = (po.supplier_name or '').strip()
		if not name:
			continue
		key = name.lower()
		entry = supplier_options.setdefault(key, {
			'name': name,
			'supplier_id': po.supplier_id,
			'po_count': 0,
			'total': Decimal('0'),
		})
		entry['po_count'] += 1
		entry['total'] += po.total or Decimal('0')
		if entry['supplier_id'] is None and po.supplier_id is not None:
			entry['supplier_id'] = po.supplier_id

	po_suppliers = sorted(supplier_options.values(), key=lambda s: s['name'].lower())

	awaiting_total = sum((po.total or Decimal('0')) for po in pos)

	# This page owns the inbox controls (entity tabs, status pills, search) and
	# drives the embedded inbox through its query string, so it needs the same
	# counts the inbox itself renders. Defaults match the inbox's own defaults:
	# unprocessed, excluding statements.
	_inbox = _with_attachments(MailboxEmail.objects.filter(is_split=False))
	_unprocessed = _inbox.filter(is_processed=False, is_ignored=False)
	inbox_counts = {
		'total': _unprocessed.exclude(tab='statements').count(),
		'rjl': _unprocessed.filter(tab='rjl').count(),
		'group': _unprocessed.filter(tab='group').count(),
		'statements': _unprocessed.filter(tab='statements').count(),
	}

	return render(request, 'stock_take/accounts_payable.html', {
		'pos': pos,
		'po_suppliers': po_suppliers,
		'awaiting_count': len(pos),
		'awaiting_total': awaiting_total,
		'inbox_counts': inbox_counts,
		'inbox_unprocessed': inbox_counts['total'],
		'is_configured': graph_api.is_configured(),
		'mailbox': graph_api._get_settings()['mailbox'],
		'last_synced': MailboxEmail.objects.aggregate(last=db_models.Max('synced_at'))['last'],
		# Plain supplier names — consumed by the shared apay_rules_modal.html partial.
		'suppliers': list(Supplier.objects.values_list('name', flat=True).order_by('name')),
		# Consumed by the shared invoice_modal.html partial.
		'opo_category_choices': OverheadPurchaseOrder.CATEGORY_CHOICES,
		'opo_gl_codes': EnabledGLCode.objects.filter(enabled=True).order_by('code'),
	})


@login_required
@page_permission_required('accounts_payable')
def accounts_payable_inbox_fragment(request):
	"""Just the email table + pagination, for the Accounts Payable inbox modal.

	The modal renders the emails inline rather than loading the whole inbox page,
	so it fetches this fragment and swaps it in. Same filtering as the full page —
	the context is built once by accounts_payable_inbox and reused here.
	"""
	context = _build_inbox_context(request)
	return HttpResponse(
		render_to_string('stock_take/partials/apay_email_table.html', context, request=request)
	)


@login_required
@page_permission_required('accounts_payable')
def accounts_payable_inbox(request):
    """Display the Accounts Payable shared mailbox inbox."""
    return render(request, 'stock_take/accounts_payable_inbox.html', _build_inbox_context(request))


def _build_inbox_context(request):
    """Shared context for the full inbox page and its table-only fragment."""
    mailbox = graph_api._get_settings()['mailbox']

    emails = MailboxEmail.objects.select_related('purchase_invoice', 'processed_by', 'matched_po').all()

    # Hide emails that have been split into per-attachment child emails — the
    # children are shown instead.
    emails = emails.exclude(is_split=True)

    # Only emails carrying an attachment can become an invoice.
    emails = _with_attachments(emails)

    # Entity tab filter (All / RJL / Group / Statements)
    entity_tab = request.GET.get('tab', '')
    if entity_tab in ('rjl', 'group', 'statements'):
        emails = emails.filter(tab=entity_tab)
    else:
        # Default 'All' tab: all emails excluding statements
        emails = emails.exclude(tab='statements')

    # Status filter — default to 'unprocessed'
    status_filter = request.GET.get('status', 'unprocessed')
    if status_filter == 'ignored':
        emails = emails.filter(is_ignored=True)
    elif status_filter == 'processed':
        emails = emails.filter(is_ignored=False, is_processed=True)
    elif status_filter == 'all':
        emails = emails.filter(is_ignored=False)
    else:  # 'unprocessed' (default)
        emails = emails.filter(is_ignored=False, is_processed=False)

    search = request.GET.get('q', '').strip()
    if search:
        emails = emails.filter(
            db_models.Q(subject__icontains=search)
            | db_models.Q(sender_name__icontains=search)
            | db_models.Q(sender_email__icontains=search)
        )

    matched_only = request.GET.get('matched_only', '') == '1'

    # Build supplier rules once — used by both _find_po_matches and supplier annotation
    _supplier_rules = {r.email_pattern.lower(): r.supplier_name for r in SupplierEmailRule.objects.all()}

    if matched_only:
        # Use a fresh queryset (no select_related) so .only() doesn't conflict
        # with the deferred traversal restriction on processed_by.
        lightweight = list(
            MailboxEmail.objects.filter(is_processed=False, is_ignored=False, is_split=False)
            .only('id', 'subject', 'sender_name', 'sender_email', 'attachment_names', 'extracted_total')
        )
        email_po_matches_all = _find_po_matches(lightweight, supplier_rules=_supplier_rules)
        matched_ids = list(email_po_matches_all.keys())
        emails = emails.filter(id__in=matched_ids)

    # All counts run through _with_attachments too, so the badges match the rows
    # actually on screen rather than counting attachment-less email.
    _all_emails = _with_attachments(MailboxEmail.objects.filter(is_split=False))
    total = _all_emails.filter(is_ignored=False).count()
    unprocessed = _all_emails.filter(is_ignored=False, is_processed=False).count()
    processed = _all_emails.filter(is_ignored=False, is_processed=True).count()
    ignored = _all_emails.filter(is_ignored=True).count()
    # Tab badge counts — respect the current status filter so badges match the visible rows
    if status_filter == 'ignored':
        _count_base = _all_emails.filter(is_ignored=True)
    elif status_filter == 'processed':
        _count_base = _all_emails.filter(is_processed=True, is_ignored=False)
    elif status_filter == 'all':
        _count_base = _all_emails.filter(is_ignored=False)
    else:  # unprocessed (default)
        _count_base = _all_emails.filter(is_processed=False, is_ignored=False)
    total_count = _count_base.exclude(tab='statements').count()
    statements_count = _count_base.filter(tab='statements').count()

    last_synced = MailboxEmail.objects.aggregate(
        last=db_models.Max('synced_at')
    )['last']

    emails = emails.order_by('-is_priority', '-received_at')
    paginator = Paginator(emails, 50)
    page_number = request.GET.get('page', 1)
    page_obj = paginator.get_page(page_number)

    email_po_matches = _find_po_matches(page_obj.object_list, supplier_rules=_supplier_rules)

    # Annotate each email with the single best PO match.
    # Amount matches are the strongest signal — an exact amount match wins
    # outright, then a fuzzy (carriage-tolerant) amount match, then PO status.
    # Priority: Draft < Approved < Partially Received < Received
    _STATUS_PRIORITY = {'Draft': 0, 'Approved': 1, 'Partially Received': 2, 'Received': 3}
    for email in page_obj.object_list:
        matched = email_po_matches.get(email.id, [])
        if matched:
            def _rank(p):
                if p.get('_amount_match') and not p.get('_amount_fuzzy'):
                    amount_rank = 0  # exact amount
                elif p.get('_amount_match'):
                    amount_rank = 1  # fuzzy amount
                else:
                    amount_rank = 2
                return (amount_rank, _STATUS_PRIORITY.get(p['status'], 9))
            best = min(matched, key=_rank)
            email.po_match = best
            email.po_match_amount = bool(best.get('_amount_match'))
            email.po_match_fuzzy = bool(best.get('_amount_fuzzy'))
            email.po_match_class = (
                'po-received' if best['status'] in ('Received', 'Partially Received')
                else 'po-approved'
            )
        else:
            email.po_match = None
            email.po_match_amount = False
            email.po_match_fuzzy = False
            email.po_match_class = ''

    # Annotate each email with a matched supplier name. A SupplierEmailRule
    # (explicit mapping) wins first; failing that we fall back to the email
    # address stored on the Supplier record / its contacts, so a sender whose
    # address is already on file connects automatically without needing a rule.
    # (_supplier_rules already built above — reuse it here.)
    _supplier_email_exact, _supplier_email_domain = _build_supplier_email_index()

    def _match_supplier(sender_email):
        if not sender_email:
            return ''
        addr = sender_email.lower().strip()
        # 1. Explicit SupplierEmailRule — exact address, then domain pattern.
        if addr in _supplier_rules:
            return _supplier_rules[addr]
        domain = addr.split('@', 1)[1] if '@' in addr else ''
        if domain and ('@' + domain) in _supplier_rules:
            return _supplier_rules['@' + domain]
        # 2. Address on the supplier record / contacts — exact match first, then
        #    a business-domain match (personal domains are too ambiguous).
        if addr in _supplier_email_exact:
            return _supplier_email_exact[addr]
        if domain and domain not in _PERSONAL_DOMAINS and domain in _supplier_email_domain:
            return _supplier_email_domain[domain]
        return ''

    for email in page_obj.object_list:
        email.matched_supplier = _match_supplier(email.sender_email)

    return {
        'embed': request.GET.get('embed') == '1',
        'emails': page_obj,
        'page_obj': page_obj,
        'total': total,
        'unprocessed': unprocessed,
        'processed': processed,
        'ignored': ignored,
        'is_configured': graph_api.is_configured(),
        'status_filter': status_filter,
        'search_query': search,
        'matched_only': matched_only,
        'mailbox': mailbox,
        'last_synced': last_synced,
        'entity_tab': entity_tab,
        'total_count': total_count,
        'statements_count': statements_count,
        'suppliers': list(Supplier.objects.values_list('name', flat=True).order_by('name')),
        'opo_category_choices': OverheadPurchaseOrder.CATEGORY_CHOICES,
        'opo_gl_codes': EnabledGLCode.objects.filter(enabled=True).order_by('code'),
    }


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

    # Determine the lookback window:
    # - force=true  → full resync (no date filter, fetches entire inbox)
    # - normal sync → always look back at least 30 days so emails that arrived
    #   while the service was paused, or were delayed, are never silently missed.
    #   update_or_create on graph_message_id makes this safely idempotent.
    try:
        req_data = json.loads(request.body) if request.body else {}
    except (json.JSONDecodeError, ValueError):
        req_data = {}

    force_full = bool(req_data.get('force'))

    if force_full:
        since = None  # fetch entire inbox
    else:
        since = timezone.now() - timedelta(days=30)

    try:
        messages, error = graph_api.fetch_inbox_messages(mailbox, since=since)
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

            raw_body = (msg.get('body') or {}).get('content') or ''
            body_text = strip_tags(raw_body)            # remove HTML tags
            body_text = _html.unescape(body_text)       # &nbsp; → \xa0, &amp; → & etc.
            body_text = body_text.replace('\xa0', ' ')  # non-breaking space → regular space
            body_text = _re.sub(r'[ \t]+', ' ', body_text)   # collapse runs of spaces/tabs
            body_text = _re.sub(r'\n{3,}', '\n\n', body_text)  # collapse 3+ blank lines
            body_text = body_text.strip()

            _, created = MailboxEmail.objects.update_or_create(
                graph_message_id=graph_id,
                defaults={
                    'subject': (msg.get('subject') or '')[:500],
                    'sender_name': (sender.get('name') or '')[:255],
                    'sender_email': (sender.get('address') or '')[:254],
                    'received_at': received_at,
                    'body_preview': body_text,
                    'is_read': msg.get('isRead', False),
                    'attachment_names': json.dumps(attachment_data),
                },
            )
            if created:
                new_count += 1
                # Apply tab routing filters to newly synced emails
                email_obj = MailboxEmail.objects.get(graph_message_id=graph_id)
                _apply_email_tab_filter(email_obj)
                # Auto-classify as a statement if no routing rule matched
                if not email_obj.tab:
                    subj_lower = (msg.get('subject') or '').lower()
                    att_names_lower = ' '.join(
                        a.get('name', '') for a in attachment_data
                    ).lower()
                    if 'statement' in subj_lower or 'statement' in att_names_lower:
                        email_obj.tab = 'statements'
                        email_obj.save(update_fields=['tab'])

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
    vat_rate  = _parse_decimal(data.get('vat_rate', '0'))
    discount_val = _parse_decimal(data.get('discount', '0'))
    if discount_val < 0:
        discount_val = Decimal('0')
    discount_pre_vat = str(data.get('discount_pre_vat', 'true')).strip().lower() in ('true', '1', 'yes', 'on')
    invoice = PurchaseInvoice.objects.create(
        invoice_number=invoice_number,
        reference=(data.get('reference') or '').strip(),
        supplier_reference=(data.get('supplier_reference') or '').strip(),
        supplier_name=(data.get('supplier_name') or '').strip(),
        date=_parse_date(data.get('date', '')),
        due_date=_parse_date(data.get('due_date', '')),
        status=data.get('status', 'Draft'),
        total=total_val,
        vat_rate=vat_rate if vat_rate > 0 else None,
        discount=discount_val,
        discount_pre_vat=discount_pre_vat,
        notes=(data.get('notes') or '').strip(),
        currency=(data.get('currency') or 'GBP').strip().upper() or 'GBP',
        created_by=request.user.get_full_name() or request.user.username,
    )

    # Parse and save line items (multipart format)
    if 'multipart' in ct:
        idx = 0
        while data.get(f'line_desc_{idx}') is not None:
            desc = (data.get(f'line_desc_{idx}') or '').strip()
            qty = _parse_decimal(data.get(f'line_qty_{idx}', '1'), '1')
            rate = _parse_decimal(data.get(f'line_rate_{idx}', '0'))
            # Prefer the exact line total from the modal so rounding of the unit
            # rate (e.g. 66.80 / 12 = 5.5666… → 5.57) does not introduce a penny
            # discrepancy (5.57 × 12 = 66.84). Fall back to qty × rate when absent.
            raw_total = data.get(f'line_total_{idx}')
            line_total = _parse_decimal(raw_total) if raw_total not in (None, '') else qty * rate
            order_id = data.get(f'line_order_{idx}', '') or None
            po_product_id = (data.get(f'line_po_product_{idx}') or '').strip()
            if desc:
                line = PurchaseInvoiceLineItem.objects.create(
                    invoice=invoice,
                    description=desc,
                    quantity=qty,
                    rate=rate,
                    line_total=line_total,
                    sort_order=idx,
                )
                if order_id:
                    try:
                        line.order = Order.objects.get(id=int(order_id))
                        line.save(update_fields=['order'])
                    except (Order.DoesNotExist, ValueError):
                        pass
                if po_product_id:
                    try:
                        pop = PurchaseOrderProduct.objects.get(id=int(po_product_id))
                        # Apply the invoiced unit price: overwrite the order price,
                        # refresh the product cost and log price history.
                        apply_invoice_price(pop, rate, pop.purchase_order.display_number, user=getattr(request, 'user', None))
                    except (PurchaseOrderProduct.DoesNotExist, ValueError, TypeError):
                        pass
            idx += 1

    # Recalculate total from lines if no flat total was provided
    if total_val == 0:
        line_total = invoice.line_items.aggregate(t=db_models.Sum('line_total'))['t'] or 0
        if line_total:
            line_total = Decimal(str(line_total))
            if discount_pre_vat:
                taxable = line_total - discount_val
                gross = taxable * (1 + vat_rate / 100) if vat_rate and vat_rate > 0 else taxable
            else:
                gross = line_total * (1 + vat_rate / 100) if vat_rate and vat_rate > 0 else line_total
                gross = gross - discount_val
            if gross < 0:
                gross = Decimal('0')
            invoice.total = gross
            invoice.save(update_fields=['total'])

    # Download and attach the file from Graph API
    if chosen:
        mailbox = graph_api._get_settings().get('mailbox', '')
        content, filename, _att_ct, err = graph_api.download_attachment(
            mailbox, email.source_graph_message_id, chosen['id']
        )
        if not err:
            invoice.attachment.save(filename, ContentFile(content), save=True)
            # Use filename as reference if none was provided
            if not invoice.reference:
                invoice.reference = filename
                invoice.save(update_fields=['reference'])

    # Link purchase order if provided
    po_id = (data.get('purchase_order_id') or '').strip()
    linked_po = None
    if po_id:
        from .models import PurchaseOrder
        try:
            linked_po = PurchaseOrder.objects.get(workguru_id=po_id)
            invoice.purchase_orders.add(linked_po)
        except PurchaseOrder.DoesNotExist:
            pass

    # Add surcharge as a product line on the linked PO
    surcharge_cost = _parse_decimal(data.get('surcharge_cost', '0'))
    if surcharge_cost > 0 and linked_po is not None:
        PurchaseOrderProduct.objects.create(
            purchase_order=linked_po,
            name='Surcharge',
            description=f'Surcharge from invoice {invoice_number}',
            order_price=surcharge_cost,
            order_quantity=1,
            quantity=1,
            line_total=surcharge_cost,
        )

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
        mailbox, email.source_graph_message_id, attachment_id
    )
    if err:
        return JsonResponse({'success': False, 'error': f'Could not download attachment: {err}'})

    extracted = _extract_pdf_fields(content)

    # Persist PO match back to the email so it shows in the inbox column
    spo = extracted.get('suggested_po')
    if spo and not email.matched_po_id:
        try:
            po_obj = PurchaseOrder.objects.get(workguru_id=spo['workguru_id'])
            email.matched_po = po_obj
            email.save(update_fields=['matched_po'])
        except PurchaseOrder.DoesNotExist:
            pass

    return JsonResponse({'success': True, 'extracted': extracted})


# ── Scan email attachment for PO reference ─────────────────────────────────

@login_required
def scan_email_attachment_po(request, email_id):
    """Download the first PDF attachment of an email, extract the PO reference,
    save it to MailboxEmail.matched_po, and return the PO data as JSON.
    Called automatically from the AP inbox page for unscanned emails.
    Accepts optional JSON body ``{"force": true}`` to re-scan even if already matched.
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    email = get_object_or_404(MailboxEmail, id=email_id)

    # Check if caller wants to force a rescan
    force = False
    try:
        body = json.loads(request.body)
        force = bool(body.get('force', False))
    except Exception:
        pass

    # If already scanned and not forcing, return early ONLY when we also have
    # the extracted total — otherwise fall through to download the PDF so the
    # total can be extracted even when the PO match is already stored.
    if email.matched_po_id and not force and email.extracted_total is not None:
        po = email.matched_po
        return JsonResponse({'success': True, 'po': {
            'workguru_id': po.workguru_id,
            'display_number': po.display_number or po.number or str(po.workguru_id),
            'supplier_name': po.supplier_name or '',
            'status': po.status or '',
            'po_type': po.po_type,
        }, 'total': str(email.extracted_total)})

    # Clear any previously stored match when forcing a rescan
    if force and email.matched_po_id:
        email.matched_po = None
        email.save(update_fields=['matched_po'])

    pdf_att = next(
        (
            a for a in email.attachment_list
            if 'pdf' in (a.get('content_type') or '').lower()
            or (a.get('name') or '').lower().endswith('.pdf')
        ),
        None,
    )
    if not pdf_att:
        return JsonResponse({'success': True, 'po': None, 'total': None})

    mailbox = graph_api._get_settings()['mailbox']
    content, _filename, _ct, err = graph_api.download_attachment(
        mailbox, email.source_graph_message_id, pdf_att['id']
    )
    if err:
        return JsonResponse({'success': False, 'error': str(err)})

    extracted = _extract_pdf_fields(content)

    # Persist extracted total regardless of whether a PO was found
    raw_total = extracted.get('total')
    if raw_total is not None:
        from decimal import Decimal, InvalidOperation
        try:
            email.extracted_total = Decimal(str(raw_total))
            email.save(update_fields=['extracted_total'])
        except (InvalidOperation, TypeError):
            pass

    spo = extracted.get('suggested_po')
    po_data = None
    amount_match = False
    amount_fuzzy = False
    if spo:
        try:
            po = PurchaseOrder.objects.get(workguru_id=spo['workguru_id'])
            if not email.matched_po_id:
                email.matched_po = po
                email.save(update_fields=['matched_po'])
            po_data = {
                'workguru_id': po.workguru_id,
                'display_number': po.display_number or po.number or str(po.workguru_id),
                'supplier_name': po.supplier_name or '',
                'status': po.status or '',
                'po_type': po.po_type,
            }
        except PurchaseOrder.DoesNotExist:
            pass
    elif email.matched_po_id:
        # PO was already matched; keep it
        po = email.matched_po
        po_data = {
            'workguru_id': po.workguru_id,
            'display_number': po.display_number or po.number or str(po.workguru_id),
            'supplier_name': po.supplier_name or '',
            'status': po.status or '',
            'po_type': po.po_type,
        }

    # No explicit PO reference in the PDF and nothing stored — fall back to the
    # same educated-guess logic the inbox page uses (subject PO / supplier name /
    # supplier rule / amount / carriage-tolerant amount). This keeps a rescan
    # consistent with the page-load view instead of wiping a plausible match
    # back to a blank dash. Guesses are returned for display but NOT persisted
    # to matched_po — only an explicit reference is treated as confirmed.
    if po_data is None:
        _supplier_rules = {
            r.email_pattern.lower(): r.supplier_name
            for r in SupplierEmailRule.objects.all()
        }
        guesses = _find_po_matches([email], supplier_rules=_supplier_rules)
        matched = guesses.get(email.id, [])
        if matched:
            _STATUS_PRIORITY = {'Draft': 0, 'Approved': 1, 'Partially Received': 2, 'Received': 3}

            def _rank(p):
                if p.get('_amount_match') and not p.get('_amount_fuzzy'):
                    amount_rank = 0
                elif p.get('_amount_match'):
                    amount_rank = 1
                else:
                    amount_rank = 2
                return (amount_rank, _STATUS_PRIORITY.get(p['status'], 9))

            best = min(matched, key=_rank)
            amount_match = bool(best.get('_amount_match'))
            amount_fuzzy = bool(best.get('_amount_fuzzy'))
            po_data = {
                'workguru_id': best['workguru_id'],
                'display_number': best['display_number'] or best['number'] or str(best['workguru_id']),
                'supplier_name': best['supplier_name'] or '',
                'status': best['status'] or '',
                'po_type': best['po_type'],
            }

    total_str = str(email.extracted_total) if email.extracted_total is not None else None
    resp = {
        'success': True,
        'po': po_data,
        'total': total_str,
        'amount_match': amount_match,
        'amount_fuzzy': amount_fuzzy,
    }
    if total_str is None:
        # Include raw text so the caller can debug why extraction failed
        resp['_debug_text'] = extracted.get('_raw_text', '')[:800]
    return JsonResponse(resp)


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


# ── Delete email ──────────────────────────────────────────────────────────────

@login_required
def delete_email(request, email_id):
    """Permanently delete an inbox email record."""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required'}, status=405)
    email = get_object_or_404(MailboxEmail, id=email_id)
    email.delete()
    return JsonResponse({'success': True})


# ── Split email into per-attachment emails ────────────────────────────────────

@login_required
def split_email(request, email_id):
    """Split an email that has multiple attachments into one email per attachment.

    Each child email is a copy of the original carrying a single attachment, so
    each invoice can be processed independently. The original is kept (hidden
    from the inbox via ``is_split``) so a future mailbox sync does not re-create
    it, and child emails download their attachment bytes from the parent's
    Graph message.
    """
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required'}, status=405)

    email = get_object_or_404(MailboxEmail, id=email_id)

    if email.parent_email_id:
        return JsonResponse({'success': False, 'error': 'This email is already a split part.'}, status=400)

    attachments = email.attachment_list
    if len(attachments) < 2:
        return JsonResponse({'success': False, 'error': 'Need at least 2 attachments to split.'}, status=400)

    created = 0
    for idx, att in enumerate(attachments, start=1):
        synthetic_id = f'{email.graph_message_id}::part::{idx}'
        # Skip if this part already exists (idempotent re-split)
        if MailboxEmail.objects.filter(graph_message_id=synthetic_id).exists():
            continue
        att_name = att.get('name') or f'Attachment {idx}'
        MailboxEmail.objects.create(
            graph_message_id=synthetic_id,
            parent_email=email,
            subject=f'{email.subject} ({att_name})' if email.subject else att_name,
            sender_name=email.sender_name,
            sender_email=email.sender_email,
            received_at=email.received_at,
            body_preview=email.body_preview,
            is_read=email.is_read,
            attachment_names=json.dumps([att]),
            tab=email.tab,
            is_priority=email.is_priority,
        )
        created += 1

    email.is_split = True
    email.save(update_fields=['is_split'])

    return JsonResponse({'success': True, 'created': created})


# ── Mark email as filed (statements) ─────────────────────────────────────

@login_required
def mark_email_filed(request, email_id):
    """Mark a statement email as filed (processed without a purchase invoice)."""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required'}, status=405)
    email = get_object_or_404(MailboxEmail, id=email_id)
    email.is_processed = True
    email.processed_by = request.user
    email.save(update_fields=['is_processed', 'processed_by'])
    return JsonResponse({'success': True})

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


# ── Email tab filter helpers ──────────────────────────────────────────────────

def _apply_email_tab_filter(email_obj):
    """Assign a tab to email_obj based on MailboxEmailFilter rules."""
    sender = (email_obj.sender_email or '').lower().strip()
    if not sender:
        return
    for f in MailboxEmailFilter.objects.all():
        pattern = (f.email_pattern or '').lower().strip()
        if not pattern:
            continue
        matched = False
        if pattern.startswith('@'):
            matched = sender.endswith(pattern)
        else:
            matched = (pattern == sender)
        if matched:
            if f.tab == 'priority':
                email_obj.is_priority = True
                email_obj.save(update_fields=['is_priority'])
            else:
                email_obj.tab = f.tab
                email_obj.save(update_fields=['tab'])
            return


# ── Supplier matching rules ───────────────────────────────────────────────────

@login_required
@page_permission_required('accounts_payable')
def manage_supplier_rules(request):
    """GET: return all SupplierEmailRule records.
    POST {action:'add', email_pattern, supplier_name, note}: create/update a rule.
    POST {action:'remove', id}: delete a rule.
    """
    if request.method == 'GET':
        rules = list(
            SupplierEmailRule.objects
            .values('id', 'email_pattern', 'supplier_name', 'note')
            .order_by('email_pattern')
        )
        return JsonResponse({'rules': rules})

    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required'}, status=405)

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'success': False, 'error': 'Invalid JSON'}, status=400)

    action = data.get('action', '')

    if action == 'add':
        pattern = (data.get('email_pattern') or '').strip().lower()
        supplier = (data.get('supplier_name') or '').strip()
        note = (data.get('note') or '').strip()
        if not pattern:
            return JsonResponse({'success': False, 'error': 'email_pattern required'}, status=400)
        if not supplier:
            return JsonResponse({'success': False, 'error': 'supplier_name required'}, status=400)
        rule, _ = SupplierEmailRule.objects.update_or_create(
            email_pattern=pattern,
            defaults={'supplier_name': supplier, 'note': note},
        )
        return JsonResponse({'success': True, 'id': rule.id})

    if action == 'remove':
        rule_id = data.get('id')
        SupplierEmailRule.objects.filter(id=rule_id).delete()
        return JsonResponse({'success': True})

    return JsonResponse({'success': False, 'error': 'Unknown action'}, status=400)


# ── Email filter management ───────────────────────────────────────────────────

@login_required
@page_permission_required('accounts_payable')
def manage_email_filters(request):
    """GET: return filter list. POST {action, email_pattern, tab, note}: add / remove / apply."""
    if request.method == 'GET':
        filters = list(
            MailboxEmailFilter.objects
            .values('id', 'email_pattern', 'tab', 'note')
            .order_by('tab', 'email_pattern')
        )
        return JsonResponse({'filters': filters})

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'success': False, 'error': 'Invalid JSON'}, status=400)

    action = data.get('action', '')

    if action == 'add':
        pattern = (data.get('email_pattern') or '').strip().lower()
        tab = (data.get('tab') or '').strip()
        note = (data.get('note') or '').strip()
        if not pattern:
            return JsonResponse({'success': False, 'error': 'email_pattern required'}, status=400)
        if tab not in ('rjl', 'group', 'priority'):
            return JsonResponse({'success': False, 'error': 'tab must be rjl, group, or priority'}, status=400)
        f, _ = MailboxEmailFilter.objects.update_or_create(
            email_pattern=pattern,
            defaults={'tab': tab, 'note': note},
        )
        # Apply to all existing emails
        updated = 0
        for email_obj in MailboxEmail.objects.all():
            sender = (email_obj.sender_email or '').lower()
            if pattern.startswith('@'):
                matched = sender.endswith(pattern)
            else:
                matched = (pattern == sender)
            if matched:
                if tab == 'priority':
                    email_obj.is_priority = True
                    email_obj.save(update_fields=['is_priority'])
                else:
                    email_obj.tab = tab
                    email_obj.save(update_fields=['tab'])
                updated += 1
        return JsonResponse({'success': True, 'id': f.id, 'applied_to': updated})

    if action == 'run':
        filter_id = data.get('id')
        try:
            f = MailboxEmailFilter.objects.get(id=filter_id)
        except MailboxEmailFilter.DoesNotExist:
            return JsonResponse({'success': False, 'error': 'Filter not found'}, status=404)
        updated = 0
        for email_obj in MailboxEmail.objects.all():
            sender = (email_obj.sender_email or '').lower()
            if f.email_pattern.startswith('@'):
                matched = sender.endswith(f.email_pattern)
            else:
                matched = (f.email_pattern == sender)
            if matched:
                if f.tab == 'priority':
                    email_obj.is_priority = True
                    email_obj.save(update_fields=['is_priority'])
                else:
                    email_obj.tab = f.tab
                    email_obj.save(update_fields=['tab'])
                updated += 1
        return JsonResponse({'success': True, 'applied_to': updated})

    if action == 'remove':
        filter_id = data.get('id')
        MailboxEmailFilter.objects.filter(id=filter_id).delete()
        return JsonResponse({'success': True})

    return JsonResponse({'success': False, 'error': 'Unknown action'}, status=400)


# ── Download attachment ───────────────────────────────────────────────────────

@login_required
@login_required
@xframe_options_sameorigin
def download_mailbox_attachment(request, email_id, attachment_id):
    """Proxy a single attachment from the Graph API to the browser."""
    email = get_object_or_404(MailboxEmail, id=email_id)
    mailbox = graph_api._get_settings()['mailbox']

    # Validate attachment_id belongs to this email
    known_ids = [a['id'] for a in email.attachment_list]
    if attachment_id not in known_ids:
        return HttpResponse('Attachment not found', status=404)

    content, filename, content_type, err = graph_api.download_attachment(
        mailbox, email.source_graph_message_id, attachment_id
    )
    if err:
        return HttpResponse(f'Error downloading attachment: {err}', status=502)

    content_type = content_type or 'application/octet-stream'
    response = HttpResponse(content, content_type=content_type)
    # Open PDFs inline in the browser; force-download everything else.
    # A ?download=1 query param forces an attachment disposition for any type.
    force_download = request.GET.get('download') in ('1', 'true', 'yes')
    if 'pdf' in content_type.lower() and not force_download:
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
