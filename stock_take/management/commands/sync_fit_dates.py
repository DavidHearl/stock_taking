"""
Management command: sync_fit_dates

Two-pass sync to keep fit dates consistent across Order and AnthillSale:

  Pass 1 — Order.fit_date from FitAppointment
    Backfills Order.fit_date where it is null but a linked FitAppointment exists.

  Pass 2 — AnthillSale.fit_date from Order.fit_date
    Backfills AnthillSale.fit_date where it is null but the linked Order has a
    fit_date set.  This is the most common gap: the appointment views used to sync
    Order.fit_date but never touched AnthillSale, so the sales list (which groups
    on AnthillSale.fit_date) would wrongly show such sales as PFP.

Safe to re-run; only touches rows that actually need updating.

Usage:
    python manage.py sync_fit_dates
    python manage.py sync_fit_dates --dry-run
"""
from django.core.management.base import BaseCommand
from stock_take.models import AnthillSale, FitAppointment


class Command(BaseCommand):
    help = 'Sync fit dates: Order.fit_date ← FitAppointment, AnthillSale.fit_date ← Order.fit_date.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be updated without saving.',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']

        # ── Pass 1: Order.fit_date ← earliest FitAppointment ───────────────
        appts = (
            FitAppointment.objects
            .filter(order__isnull=False, order__fit_date__isnull=True)
            .select_related('order')
            .order_by('order_id', 'fit_date')
        )

        pass1 = {}  # order_id -> (order, date) – keep earliest date per order
        for appt in appts:
            oid = appt.order_id
            if oid not in pass1:
                pass1[oid] = (appt.order, appt.fit_date)
            elif appt.fit_date < pass1[oid][1]:
                pass1[oid] = (appt.order, appt.fit_date)

        if pass1:
            self.stdout.write(f'Pass 1 — backfilling Order.fit_date ({len(pass1)} orders):')
            for order, fit_date in pass1.values():
                self.stdout.write(
                    f'  {"[DRY RUN] " if dry_run else ""}Order {order.id} '
                    f'({order.first_name} {order.last_name} / {order.sale_number}): '
                    f'fit_date → {fit_date}'
                )
                if not dry_run:
                    order.fit_date = fit_date
                    order.save(update_fields=['fit_date'])
        else:
            self.stdout.write('Pass 1 — nothing to update (all Orders already have fit_date).')

        # ── Pass 2: AnthillSale.fit_date ← Order.fit_date ──────────────────
        # Find AnthillSales whose fit_date is null but the linked Order has one.
        stale_sales = (
            AnthillSale.objects
            .filter(fit_date__isnull=True, order__isnull=False, order__fit_date__isnull=False)
            .select_related('order')
        )

        pass2_count = 0
        for sale in stale_sales:
            fit_date = sale.order.fit_date
            self.stdout.write(
                f'  {"[DRY RUN] " if dry_run else ""}AnthillSale {sale.id} '
                f'({sale.customer_name} / {sale.contract_number}): '
                f'fit_date → {fit_date}'
            )
            if not dry_run:
                sale.fit_date = fit_date
                sale.save(update_fields=['fit_date'])
            pass2_count += 1

        if pass2_count:
            self.stdout.write(
                f'Pass 2 — {"would update" if dry_run else "updated"} '
                f'{pass2_count} AnthillSale(s).'
            )
        else:
            self.stdout.write('Pass 2 — nothing to update (all AnthillSales already have fit_date).')

        self.stdout.write(self.style.SUCCESS('Done.'))
