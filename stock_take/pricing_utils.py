"""Utilities for propagating invoiced unit prices to product costs.

When a supplier invoice price is recorded against a purchase-order line the
invoiced unit price becomes the source of truth: it overwrites the line's order
price, refreshes the linked stock item's cost / average landed price, and is
recorded in the product's price history.
"""
from decimal import Decimal


def recalc_average_landed_price(stock_item):
    """Return the average unit cost across all non-cancelled PO lines for an item."""
    from .models import PurchaseOrderProduct

    po_lines = PurchaseOrderProduct.objects.filter(
        stock_item=stock_item,
        order_quantity__gt=0,
    ).exclude(purchase_order__status='Cancelled')

    total_cost = Decimal('0')
    total_qty = Decimal('0')
    for line in po_lines:
        qty_value = float(line.received_quantity or 0) or float(line.order_quantity or 0)
        if qty_value <= 0:
            continue
        qty = Decimal(str(qty_value))
        # invoice_price is a unit price; fall back to the order price when absent
        if line.invoice_price and float(line.invoice_price) > 0:
            unit_price = Decimal(str(float(line.invoice_price)))
        else:
            unit_price = Decimal(str(float(line.order_price or 0)))
        total_cost += unit_price * qty
        total_qty += qty

    if total_qty > 0:
        return (total_cost / total_qty).quantize(Decimal('0.01'))
    return None


def apply_invoice_price(product, new_invoice_price, reference, user=None,
                        always_log=False, log_history=True, history_date=None):
    """Apply an invoiced unit price to a PurchaseOrderProduct.

    Overwrites the line's ``invoice_price`` and ``order_price``, recalculates the
    line total, refreshes the linked stock item's cost / average landed price and
    records a price-history entry.

    Args:
        product: the PurchaseOrderProduct being invoiced.
        new_invoice_price: the invoiced unit price.
        reference: reference stored on the price-history entry (usually the PO number).
        user: the user to attribute the change to.
        always_log: create a price-history entry even when the cost is unchanged.
        log_history: when False, update prices without creating a history entry.
        history_date: optional datetime to back-date the price-history entry.

    Returns the created ``PriceHistory`` instance, or ``None`` when nothing was logged.
    """
    from .models import PriceHistory

    try:
        unit_price = float(new_invoice_price) if new_invoice_price else 0
    except (ValueError, TypeError):
        return None
    if unit_price <= 0:
        return None

    # The invoiced unit price overwrites the order price so the PO reflects what
    # was actually charged by the supplier.
    product.invoice_price = unit_price
    product.order_price = unit_price
    product.line_total = round(unit_price * float(product.order_quantity or 0), 2)
    product.save(update_fields=['invoice_price', 'order_price', 'line_total'])

    stock_item = product.stock_item
    if not stock_item:
        return None

    old_cost = Decimal(str(stock_item.cost or 0)).quantize(Decimal('0.001'))
    new_cost = Decimal(str(unit_price)).quantize(Decimal('0.001'))

    # Persist the unit cost via pack_cost_price so StockItem.save()'s
    # sync_pack_pricing() derives the same cost instead of overwriting it from
    # the previous pack price.
    pack_size = int(stock_item.pack_size or 1) or 1
    stock_item.pack_cost_price = (new_cost * Decimal(pack_size)).quantize(Decimal('0.001'))
    avg = recalc_average_landed_price(stock_item)
    if avg is not None:
        stock_item.average_landed_price = avg
    stock_item.save(update_fields=['pack_cost_price', 'cost', 'average_landed_price'])

    if not log_history:
        return None
    if new_cost == old_cost and not always_log:
        return None

    entry = PriceHistory.objects.create(
        stock_item=stock_item,
        old_price=old_cost,
        new_price=new_cost,
        change_source='invoice',
        reference=reference,
        notes=f'Invoice price updated from £{old_cost} to £{new_cost} via {reference}',
        created_by=user,
    )
    if history_date is not None:
        PriceHistory.objects.filter(pk=entry.pk).update(created_at=history_date)
    return entry
