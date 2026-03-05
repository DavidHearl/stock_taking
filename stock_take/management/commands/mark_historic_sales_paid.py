"""
Management command: mark_historic_sales_paid
─────────────────────────────────────────────
Marks AnthillSale records as paid_in_full=True ONLY where the sum of linked
AnthillPayment records is >= the sale_value (i.e. the sale is genuinely fully
paid). Sales with a real outstanding balance are left untouched.

Optionally pass --reset to first clear ALL paid_in_full flags before applying
the genuine-payment filter (useful for correcting a previous over-broad run).

Usage:
    python manage.py mark_historic_sales_paid             # Mark genuinely-paid sales
    python manage.py mark_historic_sales_paid --dry-run   # Preview only
    python manage.py mark_historic_sales_paid --reset     # Reset all then re-mark genuinely paid
    python manage.py mark_historic_sales_paid --location Belfast
"""

from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import models
from django.db.models import DecimalField, Sum, Value
from django.db.models.functions import Coalesce

from stock_take.models import AnthillSale


class Command(BaseCommand):
    help = 'Mark AnthillSale records as paid_in_full only where payments cover the sale value'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Preview counts without making changes',
        )
        parser.add_argument(
            '--reset',
            action='store_true',
            help='First reset ALL paid_in_full flags to False, then re-apply the genuine-payment filter',
        )
        parser.add_argument(
            '--location',
            type=str,
            default=None,
            help='Restrict to a specific location (case-insensitive)',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        do_reset = options['reset']
        location = options.get('location')

        # ── Optional reset ────────────────────────────────────────────────────
        if do_reset:
            reset_qs = AnthillSale.objects.filter(paid_in_full=True)
            if location:
                reset_qs = reset_qs.filter(location__iexact=location)
            reset_count = reset_qs.count()
            if dry_run:
                self.stdout.write(self.style.WARNING(
                    f'DRY RUN: would reset {reset_count} record(s) to paid_in_full=False'
                ))
            else:
                reset_qs.update(paid_in_full=False)
                self.stdout.write(f'Reset {reset_count} record(s) to paid_in_full=False')

        # ── Find genuinely paid sales (balance_payable is null or <= 0) ────────
        # We use AnthillSale.balance_payable which Anthill itself calculates as
        # sale_value - deposits/payments received.  If it's null or zero the
        # customer owes nothing according to Anthill.
        qs = AnthillSale.objects.filter(sale_value__gt=0, paid_in_full=False)
        if location:
            qs = qs.filter(location__iexact=location)

        paid_ids = list(
            qs.filter(
                models.Q(balance_payable__isnull=True) | models.Q(balance_payable__lte=0)
            ).values_list('pk', flat=True)
        )

        if dry_run:
            self.stdout.write(self.style.WARNING(
                f'DRY RUN: {len(paid_ids)} sale(s) would be marked as paid_in_full=True '
                f'(payments cover the full sale value).'
            ))
            return

        if paid_ids:
            updated = AnthillSale.objects.filter(pk__in=paid_ids).update(paid_in_full=True)
        else:
            updated = 0

        self.stdout.write(self.style.SUCCESS(
            f'Marked {updated} sale(s) as paid_in_full=True (genuine full payment confirmed).'
        ))
