from django import template
from datetime import datetime

register = template.Library()

@register.filter
def format_date_str(value):
    """Format a date string stored in various formats to DD/MM/YYYY for display."""
    if not value:
        return ''
    if hasattr(value, 'strftime'):
        return value.strftime('%d/%m/%Y')
    for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%dT%H:%M:%S.%f'):
        try:
            return datetime.strptime(str(value)[:19], fmt).strftime('%d/%m/%Y')
        except (ValueError, TypeError):
            continue
    return str(value)

@register.filter
def date_for_input(value):
    """Normalise a date string (any stored format) to YYYY-MM-DD for <input type="date">."""
    if not value:
        return ''
    if hasattr(value, 'strftime'):
        return value.strftime('%Y-%m-%d')
    for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%dT%H:%M:%S.%f'):
        try:
            return datetime.strptime(str(value)[:19], fmt).strftime('%Y-%m-%d')
        except (ValueError, TypeError):
            continue
    return str(value)

@register.filter
def get_item(dictionary, key):
    """Get an item from a dictionary using a variable key"""
    if dictionary:
        return dictionary.get(key)
    return None

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
