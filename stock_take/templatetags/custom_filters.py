import re
from django import template
from datetime import datetime
from ..date_utils import parse_date_str

register = template.Library()

@register.filter
def format_date_str(value):
	"""Format a date string stored in various formats to DD/MM/YYYY for display."""
	if not value:
		return ''
	parsed = parse_date_str(value)
	# Unrecognised formats are shown as-is rather than swallowed.
	return parsed.strftime('%d/%m/%Y') if parsed else str(value)

@register.filter
def date_for_input(value):
	"""Normalise a date string (any stored format) to YYYY-MM-DD for <input type="date">."""
	if not value:
		return ''
	parsed = parse_date_str(value)
	return parsed.isoformat() if parsed else str(value)

@register.filter
def get_item(dictionary, key):
    """Get an item from a dictionary using a variable key"""
    if dictionary:
        return dictionary.get(key)
    return None

@register.filter
def price_2_4(value):
    """Format a number with a minimum of 2 and a maximum of 4 decimal places.
    Trailing zeros beyond 2 places are stripped (e.g. 5 -> '5.00',
    5.5 -> '5.50', 5.1234 -> '5.1234', 5.12340 -> '5.1234')."""
    if value is None or value == '':
        return ''
    try:
        num = float(value)
    except (ValueError, TypeError):
        return value
    s = f'{num:.4f}'.rstrip('0')
    integer, _, decimals = s.partition('.')
    decimals = (decimals + '00')[:max(2, len(decimals))]
    return f'{integer}.{decimals}'

# Status/category string → global .badge-* colour variant. One map per domain
# context so the same word can differ by domain if ever needed. Mirrored in JS
# (window.badgeClass in static/js/script.js) for pills rendered client-side —
# keep the two in sync. Unknown keys fall back to badge-neutral.
_BADGE_STATUS_MAP = {
	'po': {
		'draft': 'badge-neutral', 'sent': 'badge-primary', 'received': 'badge-success',
		'partially-received': 'badge-warning', 'approved': 'badge-primary', 'cancelled': 'badge-danger',
	},
	'opo': {
		'draft': 'badge-neutral', 'approved': 'badge-primary', 'invoiced': 'badge-warning',
		'paid': 'badge-success', 'cancelled': 'badge-danger',
	},
	'po_boards': {
		'ordered': 'badge-success', 'not-ordered': 'badge-warning',
		'received': 'badge-success', 'pending': 'badge-warning',
	},
	'pnx': {'received': 'badge-success', 'pending': 'badge-warning'},
	'pinv': {'draft': 'badge-neutral', 'approved': 'badge-primary', 'paid': 'badge-success', 'void': 'badge-danger'},
	'pinv_payment': {'paid': 'badge-success', 'partial': 'badge-warning', 'unpaid': 'badge-danger'},
	'invoice': {'draft': 'badge-neutral', 'approved': 'badge-primary', 'sent': 'badge-success'},
	'lead': {
		'new': 'badge-primary', 'contacted': 'badge-warning', 'qualified': 'badge-purple',
		'proposal': 'badge-info', 'converted': 'badge-success', 'lost': 'badge-neutral',
	},
	'enq': {'new': 'badge-info', 'contacted': 'badge-warning', 'converted': 'badge-success', 'closed': 'badge-neutral'},
	'sale': {'required': 'badge-warning', 'short': 'badge-danger', 'ordered': 'badge-primary', 'validated': 'badge-success'},
	'supplier_payment': {
		'account': 'badge-success', 'card': 'badge-info', 'direct_debit': 'badge-purple',
		'bank_transfer': 'badge-primary', 'proforma': 'badge-warning',
	},
	'payment_source': {'xero': 'badge-info', 'manual': 'badge-purple'},
}


@register.filter
def badge_class(value, context):
	"""Map a status/category string to a global .badge-* colour variant.

	Usage: <span class="badge {{ obj.status|badge_class:'po' }}">{{ obj.status }}</span>
	Case/whitespace-insensitive; unknown values fall back to badge-neutral so a
	pill always has a defined colour."""
	mapping = _BADGE_STATUS_MAP.get(context, {})
	key = (str(value) if value is not None else '').strip().lower()
	# Try the key as-is, then with spaces normalised to hyphens (e.g. the PO
	# status "Partially Received" → "partially-received").
	return mapping.get(key) or mapping.get(key.replace(' ', '-'), 'badge-neutral')


@register.filter
def currency_symbol(code):
    """Return the symbol for a currency code (GBP -> £, EUR -> €, USD -> $)."""
    symbols = {'GBP': '£', 'EUR': '€', 'USD': '$'}
    return symbols.get((code or 'GBP').upper(), (code or 'GBP'))

@register.filter
def calculate_remaining(accessory):
    """Waterfall stock allocation: available stock (on-hand + incoming) is
    allocated to orders with the earliest fit_date first.  Only orders that
    cannot be fully covered after higher-priority orders are served will
    show a shortage.

    If this accessory is already allocated (stock deducted), fall back to
    the simple formula so we don't double-count.
    """
    if not accessory.stock_item:
        return 0

    stock = float(accessory.stock_item.quantity)
    incoming = float(accessory.incoming_quantity)

    if accessory.is_allocated:
        allocated = float(accessory.allocated_quantity)
        return stock - allocated + incoming

    from django.db.models import Sum, Q
    from stock_take.models import Accessory

    total_available = stock + incoming

    this_fit_date = accessory.order.fit_date
    this_sale_number = accessory.order.sale_number

    # Base filter: same SKU, active orders, not yet allocated, not missing,
    # excluding the current order.
    base = Q(
        sku=accessory.sku,
        order__job_finished=False,
        is_allocated=False,
        missing=False,
    ) & ~Q(order=accessory.order)

    if this_fit_date is not None:
        # Higher priority = earlier fit_date; same date → lower sale_number
        prior = base & (
            Q(order__fit_date__lt=this_fit_date)
            | Q(order__fit_date=this_fit_date,
                order__sale_number__lt=this_sale_number)
        )
    else:
        # No fit_date = lowest priority; every dated order ranks above us
        prior = base & Q(order__fit_date__isnull=False)

    prior_demand = float(
        Accessory.objects.filter(prior).aggregate(
            total=Sum('quantity'))['total'] or 0
    )

    available_for_this = max(0.0, total_available - prior_demand)
    qty = float(accessory.quantity)
    return available_for_this - qty

@register.filter
def split_options(value, delimiter=','):
    """Split a string by delimiter and return a list"""
    if not value:
        return []
    return [option.strip() for option in value.split(delimiter)]

@register.filter
def sum_accessory_costs(accessories):
    """Calculate total cost of all accessories (cost_price * quantity)"""
    total = 0
    for accessory in accessories:
        total += accessory.cost_price * accessory.quantity
    return f"{total:.2f}"

@register.filter
def sum_expenses(expenses):
    """Calculate total amount of all expenses"""
    total = 0
    for expense in expenses:
        total += float(expense.amount or 0)
    return total


@register.filter
def multiply(value, arg):
    """Multiply two numbers. Usage: {{ cost_price|multiply:quantity }}"""
    try:
        return f"{float(value) * float(arg):.2f}"
    except (ValueError, TypeError):
        return '0.00'


@register.filter
def make_range(value):
    """Return range(value) for iteration in templates. Usage: {% for i in total|make_range %}"""
    try:
        return range(int(value))
    except (ValueError, TypeError):
        return range(0)


@register.filter
def short_timesince(value):
    """Show hours ago if under 48h, otherwise days ago."""
    from django.utils import timezone
    if not value:
        return ''
    now = timezone.now()
    diff = now - value
    total_hours = int(diff.total_seconds() / 3600)
    if total_hours < 1:
        return 'just now'
    if total_hours < 48:
        return f'{total_hours}h ago'
    days = diff.days
    return f'{days} days ago'


@register.filter
def get_item(dictionary, key):
    """Allow dict lookups in templates: {{ my_dict|get_item:key }}"""
    if dictionary is None:
        return None
    # Try the key as-is first (int keys), then as a string
    result = dictionary.get(key)
    if result is None:
        result = dictionary.get(str(key))
    return result


# Internal material SKU prefix. Kept in full on the generated PNX/CSV files,
# but not shown to the user on screen — see material_code / material_thickness.
MATERIAL_DISPLAY_PREFIX = 'SHT_MFC_EGG_'

# Egger board SKU shape: SHT_MFC_EGG_<code>_<thickness>_  (e.g. SHT_MFC_EGG_H1234ST9_18_)
_MATERIAL_EGG_RE = re.compile(r'^' + re.escape(MATERIAL_DISPLAY_PREFIX) + r'(.+)_(\d+)_$')


@register.filter
def strip_material_prefix(value):
    """Strip the internal material SKU prefix (SHT_MFC_EGG_) for display only.

    The full value is still written to the PNX and CSV exports — this filter
    only affects what the user sees on screen."""
    if not value:
        return value
    s = str(value)
    if s.startswith(MATERIAL_DISPLAY_PREFIX):
        return s[len(MATERIAL_DISPLAY_PREFIX):]
    return s


@register.filter
def material_code(value):
    """The material colour code shown to the user, with both the internal SKU
    prefix (SHT_MFC_EGG_) and the trailing thickness (_18_) removed. The full
    SKU is still written to the PNX/CSV exports — this is display only."""
    if not value:
        return value
    s = str(value)
    m = _MATERIAL_EGG_RE.match(s)
    if m:
        return m.group(1)
    if s.startswith(MATERIAL_DISPLAY_PREFIX):
        return s[len(MATERIAL_DISPLAY_PREFIX):]
    return s


@register.filter
def material_thickness(value):
    """The board thickness (e.g. '18') parsed from an Egger material SKU, or ''
    when the value doesn't carry one."""
    if not value:
        return ''
    m = _MATERIAL_EGG_RE.match(str(value))
    return m.group(2) if m else ''


@register.filter
def material_prefix(value):
    """Return the SKU prefix (SHT_MFC_EGG_) if the value carries it, otherwise
    ''. Used to reconstruct the full matname when the user edits the displayed
    (code-only) value."""
    if value and str(value).startswith(MATERIAL_DISPLAY_PREFIX):
        return MATERIAL_DISPLAY_PREFIX
    return ''
