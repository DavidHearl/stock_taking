"""
link_sales_orders
─────────────────
Link AnthillSale records to their matching Order records (and vice-versa)
and sync fit_date between them.

Matching strategy:
    Order.sale_number == AnthillSale.anthill_activity_id

Actions:
    1. Set AnthillSale.order FK where it is currently NULL but a matching Order exists.
    2. Copy fit_date: Order → Sale when the sale has no fit_date but the order does.
    3. Copy fit_date: Sale → Order when the order has no fit_date but the sale does.

Usage:
    python manage.py link_sales_orders --dry-run     # Preview changes
    python manage.py link_sales_orders               # Apply changes
"""

from django.core.management.base import BaseCommand

from stock_take.models import AnthillSale, Order


class Command(BaseCommand):
    help = (
        'Link AnthillSale ↔ Order by matching sale_number/anthill_activity_id, '
        'and sync fit_date between them.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Show what would change without writing to the database',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']

        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN — no changes will be saved\n'))

        # Build lookup: sale_number -> Order
        orders_by_sale_num = {}
        for order in Order.objects.filter(sale_number__gt='').select_related():
            orders_by_sale_num[order.sale_number] = order

        self.stdout.write(f'Orders with sale_number: {len(orders_by_sale_num)}')

        sales = AnthillSale.objects.filter(
            anthill_activity_id__in=orders_by_sale_num.keys()
        ).select_related('order')

        self.stdout.write(f'Matching AnthillSale records: {sales.count()}\n')

        stats = {
            'linked': 0,
            'fit_to_sale': 0,
            'fit_to_order': 0,
            'already_linked': 0,
        }

        sale_updates = []
        order_updates = []

        for sale in sales:
            order = orders_by_sale_num.get(sale.anthill_activity_id)
            if not order:
                continue

            sale_changed = False
            order_changed = False

            # ── 1. Link sale → order ──
            if sale.order_id != order.pk:
                self.stdout.write(
                    f'  LINK: {sale.customer_name} — '
                    f'Sale {sale.anthill_activity_id} → Order {order.sale_number} (pk={order.pk})'
                )
                sale.order = order
                sale_changed = True
                stats['linked'] += 1
            else:
                stats['already_linked'] += 1

            # ── 2. Sync fit_date: Order → Sale ──
            if not sale.fit_date and order.fit_date:
                self.stdout.write(
                    f'  FIT → SALE: {sale.customer_name} ({sale.anthill_activity_id}) — '
                    f'{order.fit_date}'
                )
                sale.fit_date = order.fit_date
                sale_changed = True
                stats['fit_to_sale'] += 1

            # ── 3. Sync fit_date: Sale → Order ──
            if not order.fit_date and sale.fit_date:
                self.stdout.write(
                    f'  FIT → ORDER: {sale.customer_name} ({order.sale_number}) — '
                    f'{sale.fit_date}'
                )
                order.fit_date = sale.fit_date
                order_changed = True
                stats['fit_to_order'] += 1

            if sale_changed:
                sale_updates.append(sale)
            if order_changed:
                order_updates.append(order)

        # ── Write changes ──
        if not dry_run:
            if sale_updates:
                AnthillSale.objects.bulk_update(
                    sale_updates, ['order', 'fit_date'], batch_size=500
                )
            if order_updates:
                Order.objects.bulk_update(
                    order_updates, ['fit_date'], batch_size=500
                )

        # ── Summary ──
        self.stdout.write(f'\n── Summary ──')
        self.stdout.write(f'Sale → Order links created : {stats["linked"]}')
        self.stdout.write(f'Already linked             : {stats["already_linked"]}')
        self.stdout.write(f'Fit date → Sale            : {stats["fit_to_sale"]}')
        self.stdout.write(f'Fit date → Order           : {stats["fit_to_order"]}')

        total_changes = stats['linked'] + stats['fit_to_sale'] + stats['fit_to_order']
        if total_changes:
            self.stdout.write(self.style.SUCCESS(
                f'\nTotal changes: {total_changes} {"(preview only)" if dry_run else "(applied)"}'
            ))
        else:
            self.stdout.write(self.style.SUCCESS('\nEverything is already in sync.'))

        if dry_run and total_changes:
            self.stdout.write(self.style.WARNING(
                '\nRun without --dry-run to apply these changes.'
            ))
