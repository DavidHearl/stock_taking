"""
sync_anthill_fit_dates
----------------------
Parses the 'Fit From Date' custom field (stored as text in AnthillSale.fit_from_date)
into a proper date and saves it to AnthillSale.fit_date.

The Anthill CRM SOAP API does not expose an appointments endpoint. The confirmed
fit/installation date is stored in the 'Fit From Date' custom field on each sale,
which is already synced as text by sync_anthill_workflow.py into the fit_from_date
field. This command converts those text values to proper date objects.

Date formats handled: DD/MM/YYYY, DD/MM/YY, YYYY-MM-DD, D/M/YYYY

Usage
-----
  # Dry run - show what would change without writing to DB
  python manage.py sync_anthill_fit_dates --dry-run

  # Full sync - parse all sales with a fit_from_date value
  python manage.py sync_anthill_fit_dates

  # Only populate sales that currently have no fit_date (fastest)
  python manage.py sync_anthill_fit_dates --missing-only

  # Single sale (for testing / debugging)
  python manage.py sync_anthill_fit_dates --sale-id 419324

  # Only sales active within the last N days
  python manage.py sync_anthill_fit_dates --days 365
"""

import logging
import sys
from datetime import date, datetime, timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone as tz

from stock_take.models import AnthillSale, SyncLog


logger = logging.getLogger(__name__)

DATE_FORMATS = (
    '%d/%m/%Y',  # 16/02/2026  (most common from Anthill)
    '%d/%m/%y',  # 16/02/26
    '%Y-%m-%d',  # 2026-02-16
    '%d-%m-%Y',  # 16-02-2026
)


def parse_fit_date(text: str) -> date | None:
    """Parse an Anthill 'Fit From Date' text string into a date, or return None."""
    if not text:
        return None
    text = text.strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


class Command(BaseCommand):
    help = (
        'Parse AnthillSale.fit_from_date (text) into AnthillSale.fit_date (date). '
        'The fit_from_date field is synced from the Anthill "Fit From Date" custom field '
        'by sync_anthill_workflow.py. This command converts those text values to proper dates.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Report changes without writing to the database',
        )
        parser.add_argument(
            '--missing-only', action='store_true',
            help='Only process sales that currently have no fit_date',
        )
        parser.add_argument(
            '--days', type=int, default=None,
            help='Only process sales whose activity_date is within the last N days',
        )
        parser.add_argument(
            '--sale-id', type=str, default=None,
            help='Process a single sale by Anthill activity ID (e.g. 419324)',
        )

    def handle(self, *args, **options):
        _ensure_file_logger()

        # Force UTF-8 output on Windows to avoid encoding errors in the terminal
        if hasattr(sys.stdout, 'reconfigure'):
            try:
                sys.stdout.reconfigure(encoding='utf-8', errors='replace')
            except Exception:
                pass

    def handle(self, *args, **options):
        # Force UTF-8 output on Windows
        if hasattr(sys.stdout, 'reconfigure'):
            try:
                sys.stdout.reconfigure(encoding='utf-8', errors='replace')
            except Exception:
                pass

        dry_run = options['dry_run']
        missing_only = options['missing_only']
        days = options['days']
        sale_id_filter = options['sale_id']

        if dry_run:
            self.stdout.write(self.style.WARNING('[DRY-RUN] No changes will be written.\n'))

        # Build queryset — only sales that have a fit_from_date value
        qs = AnthillSale.objects.exclude(fit_from_date='').exclude(fit_from_date__isnull=True)

        if sale_id_filter:
            qs = qs.filter(anthill_activity_id=sale_id_filter)
        elif days:
            cutoff = tz.now() - timedelta(days=days)
            qs = qs.filter(activity_date__gte=cutoff)

        if missing_only:
            qs = qs.filter(fit_date__isnull=True)

        sales_list = list(qs.values_list('pk', 'anthill_activity_id', 'customer_name', 'fit_from_date', 'fit_date'))
        total = len(sales_list)
        self.stdout.write(f'Sales with fit_from_date to process: {total}\n')

        if total == 0:
            self.stdout.write('Nothing to do.')
            return

        stats = {'set': 0, 'unchanged': 0, 'unparseable': 0}
        bulk_updates = []
        unparseable = []

        for pk, activity_id, customer_name, fit_from_date_text, existing_date in sales_list:
            parsed = parse_fit_date(fit_from_date_text)

            if parsed is None:
                unparseable.append(f'  {activity_id} ({customer_name}): "{fit_from_date_text}"')
                stats['unparseable'] += 1
                continue

            if parsed == existing_date:
                stats['unchanged'] += 1
                continue

            self.stdout.write(
                f'  {activity_id} ({customer_name}): "{fit_from_date_text}" => {parsed}'
                + (f'  [was: {existing_date}]' if existing_date else '')
            )
            stats['set'] += 1

            if not dry_run:
                sale = AnthillSale(pk=pk)
                sale.fit_date = parsed
                bulk_updates.append(sale)

        if not dry_run and bulk_updates:
            AnthillSale.objects.bulk_update(bulk_updates, ['fit_date'], batch_size=500)

        self.stdout.write('')
        self.stdout.write('-' * 50)
        self.stdout.write(f'Total processed : {total}')
        self.stdout.write(f'Set / updated   : {stats["set"]}')
        self.stdout.write(f'Unchanged       : {stats["unchanged"]}')
        self.stdout.write(f'Unparseable     : {stats["unparseable"]}')

        if unparseable:
            self.stdout.write(self.style.WARNING('\nCould not parse these fit_from_date values:'))
            for line in unparseable[:20]:
                self.stdout.write(line)
            if len(unparseable) > 20:
                self.stdout.write(f'  ... and {len(unparseable) - 20} more')

        if dry_run:
            self.stdout.write(self.style.WARNING('\n[DRY-RUN] No changes were written.'))
            return

        log_status = 'success' if stats['unparseable'] == 0 else 'warning'
        SyncLog.objects.create(
            script_name='sync_anthill_fit_dates',
            status=log_status,
            notes=(
                f'Processed {total} sales with fit_from_date. '
                f'Set: {stats["set"]}, Unchanged: {stats["unchanged"]}, '
                f'Unparseable: {stats["unparseable"]}.'
            ),
        )
        self.stdout.write(self.style.SUCCESS('\nDone.'))
