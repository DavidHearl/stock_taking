"""
backfill_sale_fit_dates
───────────────────────
One-off management command to copy confirmed fit dates from linked Order records
into the new AnthillSale.fit_date field.

Matching strategy:
  Order.sale_number == AnthillSale.anthill_activity_id

For each matching pair where the Order has a fit_date and the AnthillSale
does not yet have one (unless --force is set), the fit_date is copied.

Usage
─────
  # Dry run (no DB writes)
  python manage.py backfill_sale_fit_dates --dry-run

  # Apply
  python manage.py backfill_sale_fit_dates

  # Overwrite even non-null values
  python manage.py backfill_sale_fit_dates --force
"""

from django.core.management.base import BaseCommand
from stock_take.models import AnthillSale, Order


class Command(BaseCommand):
    help = 'Copy fit_date from Orders into AnthillSale.fit_date (matched by sale_number / anthill_activity_id)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Report what would change without writing to DB',
        )
        parser.add_argument(
            '--force', action='store_true',
            help='Overwrite existing fit_date values on AnthillSale',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        force = options['force']

        if dry_run:
            self.stdout.write('[DRY-RUN] No changes will be written.\n')

        # Build a dict of sale_number -> fit_date for orders that have a fit_date
        order_fit_dates = dict(
            Order.objects
            .filter(fit_date__isnull=False)
            .values_list('sale_number', 'fit_date')
        )

        # Find AnthillSale records whose anthill_activity_id matches an Order sale_number
        sale_qs = AnthillSale.objects.filter(
            anthill_activity_id__in=order_fit_dates.keys()
        )
        if not force:
            sale_qs = sale_qs.filter(fit_date__isnull=True)

        total = sale_qs.count()
        self.stdout.write(f'AnthillSale records to update: {total}')

        updated = 0
        bulk_updates = []
        for sale in sale_qs.only('pk', 'anthill_activity_id', 'fit_date'):
            new_date = order_fit_dates[sale.anthill_activity_id]
            self.stdout.write(
                f'  Sale {sale.anthill_activity_id}: '
                f'{sale.fit_date!r} -> {new_date!r}'
            )
            sale.fit_date = new_date
            bulk_updates.append(sale)
            updated += 1

        if not dry_run and bulk_updates:
            AnthillSale.objects.bulk_update(bulk_updates, ['fit_date'], batch_size=500)

        self.stdout.write(
            self.style.SUCCESS(
                f'\n{"[DRY-RUN] Would update" if dry_run else "Updated"} {updated} record(s).'
            )
        )
