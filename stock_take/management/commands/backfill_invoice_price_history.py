"""
backfill_invoice_price_history
──────────────────────────────
One-off management command to backfill product price history from purchase-order
lines that already have an invoice price recorded but never propagated it.

Historically, attaching an invoice price to a PO line (via invoice linking) only
stored ``PurchaseOrderProduct.invoice_price`` — it never overwrote the order
price, updated the stock item cost, or created a ``PriceHistory`` entry. This
command repairs that data for every stock item.

For each stock item with invoice-priced PO lines, processed in chronological
order (by PO creation date):
  * the line's ``order_price`` / ``line_total`` are overwritten with the
    invoiced unit price,
  * a ``PriceHistory`` entry is created per PO (back-dated to the PO),
  * the stock item ``cost`` (via ``pack_cost_price``) and
    ``average_landed_price`` are refreshed to the latest invoiced price,
  * affected PO totals are recalculated.

Usage
─────
  # Dry run (no DB writes)
  python manage.py backfill_invoice_price_history --dry-run

  # Apply
  python manage.py backfill_invoice_price_history

  # Limit to a single SKU
  python manage.py backfill_invoice_price_history --sku LGT_TAP_TLW_K30-6730WW-5M

  # Rebuild: delete existing invoice-source history for affected items first
  python manage.py backfill_invoice_price_history --force
"""

from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Sum

from stock_take.models import PriceHistory, PurchaseOrder, PurchaseOrderProduct, StockItem
from stock_take.pricing_utils import recalc_average_landed_price


class Command(BaseCommand):
    help = 'Backfill product price history from PO lines that already have invoice prices'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Report what would change without writing to DB',
        )
        parser.add_argument(
            '--force', action='store_true',
            help='Delete existing invoice-source price history for affected items and rebuild',
        )
        parser.add_argument(
            '--sku', type=str, default=None,
            help='Limit the backfill to a single SKU',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        force = options['force']
        sku = options['sku']

        item_qs = StockItem.objects.filter(
            purchase_order_lines__invoice_price__gt=0,
        ).distinct()
        if sku:
            item_qs = item_qs.filter(sku=sku)

        items_processed = 0
        history_created = 0
        lines_updated = 0
        pos_recalced = set()

        for stock_item in item_qs.iterator():
            lines = list(
                PurchaseOrderProduct.objects.filter(
                    stock_item=stock_item,
                    invoice_price__gt=0,
                ).exclude(
                    purchase_order__status='Cancelled',
                ).select_related('purchase_order').order_by(
                    'purchase_order__created_at', 'id',
                )
            )
            if not lines:
                continue

            items_processed += 1
            affected_po_ids = set()

            with transaction.atomic():
                if force and not dry_run:
                    PriceHistory.objects.filter(
                        stock_item=stock_item, change_source='invoice',
                    ).delete()

                # Baseline cost: the original order price of the earliest PO line.
                running = Decimal(str(float(lines[0].order_price or 0))).quantize(Decimal('0.001'))

                for line in lines:
                    po = line.purchase_order
                    ref = po.display_number
                    inv = Decimal(str(float(line.invoice_price))).quantize(Decimal('0.001'))
                    new_line_total = round(float(inv) * float(line.order_quantity or 0), 2)

                    if not dry_run:
                        PurchaseOrderProduct.objects.filter(pk=line.pk).update(
                            order_price=inv,
                            line_total=new_line_total,
                        )
                    lines_updated += 1
                    affected_po_ids.add(po.id)

                    exists = PriceHistory.objects.filter(
                        stock_item=stock_item, change_source='invoice', reference=ref,
                    ).exists()
                    if force or not exists:
                        if not dry_run:
                            entry = PriceHistory.objects.create(
                                stock_item=stock_item,
                                old_price=running,
                                new_price=inv,
                                change_source='invoice',
                                reference=ref,
                                notes=f'Invoice price recorded (£{running} → £{inv}) via {ref}',
                                created_by=None,
                            )
                            PriceHistory.objects.filter(pk=entry.pk).update(created_at=po.created_at)
                        history_created += 1

                    running = inv

                if not dry_run:
                    # Persist the latest invoiced unit cost via pack_cost_price so
                    # sync_pack_pricing() derives the same cost on save.
                    pack_size = int(stock_item.pack_size or 1) or 1
                    stock_item.pack_cost_price = (running * Decimal(pack_size)).quantize(Decimal('0.001'))
                    avg = recalc_average_landed_price(stock_item)
                    if avg is not None:
                        stock_item.average_landed_price = avg
                    stock_item.save(update_fields=['pack_cost_price', 'cost', 'average_landed_price'])

                    # Recalculate totals for affected POs from their line totals.
                    for po in PurchaseOrder.objects.filter(id__in=affected_po_ids):
                        total = po.products.aggregate(t=Sum('line_total'))['t'] or 0
                        po.total = total
                        po.save(update_fields=['total'])
                        pos_recalced.add(po.id)

        prefix = '[DRY RUN] ' if dry_run else ''
        self.stdout.write(self.style.SUCCESS(
            f'{prefix}Items processed: {items_processed}, '
            f'PO lines updated: {lines_updated}, '
            f'price-history entries created: {history_created}, '
            f'PO totals recalculated: {len(pos_recalced)}'
        ))
