"""Shared helpers for computing ordering status across views.

Currently provides a single global stock-shortage calculation that returns the
set of sale numbers whose required accessories cannot be covered by available
stock (current + incoming). This mirrors the per-batch shortage logic used by
the ordering page's indicator endpoint, but computes the full global set so it
can be reused by the sales list.
"""

import datetime
from collections import defaultdict

from django.db.models import Sum, F, Value, DecimalField, ExpressionWrapper
from django.db.models.functions import Coalesce

from .models import Accessory, StockItem, PurchaseOrderProduct

_EPS = 0.001


def get_short_sale_numbers():
    """Return a set of sale_numbers that are short of stock for at least one SKU.

    A SKU is short when current stock + incoming (Approved PO) quantity is less
    than total demand across all active, non-allocated, stock-linked accessories.
    For each short SKU, stock is allocated to orders in fit_date order (earliest
    first); orders that cannot be fully covered are flagged as short.
    """
    active_accs = list(
        Accessory.objects.filter(
            order__job_finished=False,
            is_allocated=False,
            missing=False,
            stock_item__isnull=False,
        ).values('order__sale_number', 'order__fit_date', 'sku', 'quantity')
    )
    if not active_accs:
        return set()

    unique_skus = {a['sku'] for a in active_accs}

    stock_qtys = dict(
        StockItem.objects.filter(sku__in=unique_skus).values_list('sku', 'quantity')
    )

    incoming_rows = (
        PurchaseOrderProduct.objects
        .filter(
            sku__in=unique_skus,
            purchase_order__status__in=['Approved', 'Ordered', 'Sent', 'Partially Received'],
        )
        .filter(order_quantity__gt=Coalesce(F('received_quantity'), Value(0, output_field=DecimalField())))
        .values('sku')
        .annotate(total=Sum(
            ExpressionWrapper(
                (F('order_quantity') - Coalesce(F('received_quantity'), Value(0, output_field=DecimalField())))
                * Coalesce(F('stock_item__pack_size'), Value(1, output_field=DecimalField())),
                output_field=DecimalField()
            )
        ))
    )
    incoming_qtys = {r['sku']: float(r['total'] or 0) for r in incoming_rows}

    total_demand = defaultdict(float)
    for a in active_accs:
        total_demand[a['sku']] += float(a['quantity'])

    short_skus = {
        sku for sku in unique_skus
        if (stock_qtys.get(sku, 0) or 0) + incoming_qtys.get(sku, 0) - total_demand[sku] < -_EPS
    }
    if not short_skus:
        return set()

    # Aggregate demand per (sku, sale_number) and track fit dates.
    order_sku_demand = defaultdict(float)
    order_fit_dates = {}
    for a in active_accs:
        if a['sku'] not in short_skus:
            continue
        key = (a['sku'], a['order__sale_number'])
        order_sku_demand[key] += float(a['quantity'])
        order_fit_dates[key] = a['order__fit_date']

    sku_orders = defaultdict(list)
    for (sku, sn), qty in order_sku_demand.items():
        sku_orders[sku].append((sn, qty, order_fit_dates[(sku, sn)]))

    short_sale_numbers = set()
    for sku, orders in sku_orders.items():
        available = (stock_qtys.get(sku, 0) or 0) + incoming_qtys.get(sku, 0)
        orders_sorted = sorted(
            orders,
            key=lambda x: (x[2] is None, x[2] if x[2] is not None else datetime.date.max, x[0])
        )
        remaining = available
        for sn, qty, _fit_date in orders_sorted:
            if remaining < qty - _EPS:
                shortage = qty - max(0.0, remaining)
                if shortage > _EPS:
                    short_sale_numbers.add(sn)
            remaining = max(0.0, remaining - qty)

    return short_sale_numbers
