"""
Management command: merge_placeholder_sales
────────────────────────────────────────────
Clean up phantom "<base>-N" placeholder sales created by the scraped-invoice
payment flow (``invoice_views._build_placeholder_activity_id``).

When a scraped payment can't match an existing sale by ``contract_number`` but
the genuine Anthill sale already exists under its base activity id (e.g.
``425306``), the placeholder builder used to create a *second* sale
``425306-2`` with the same contract number, no linked order and no value, then
attach the payment to it. These duplicates clutter the Customer Payment Pool
(extra £0 "Paid" rows) and can surface under a different showroom.

This command finds those placeholders, re-homes their payments onto the genuine
sale, and deletes the placeholder.

A sale is treated as a mergeable placeholder when ALL of:
  - its activity id matches ``<base>-<number>`` (e.g. ``425306-2``)
  - it has no linked Order
  - it has no sale_value (None or 0)
  - it has no cover sheet
  - a sibling sale shares its (non-empty) contract_number and is NOT itself a
    placeholder (the merge target)

Usage:
    python manage.py merge_placeholder_sales                 # dry-run (default)
    python manage.py merge_placeholder_sales --customer "Smith"
    python manage.py merge_placeholder_sales --contract BFS-NR-425306
    python manage.py merge_placeholder_sales --fix          # apply changes
"""

import re
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from stock_take.models import AnthillSale

# Matches a placeholder activity id: a base followed by a numeric suffix.
_PLACEHOLDER_RE = re.compile(r'^(?P<base>.+)-(?P<idx>\d+)$')


class Command(BaseCommand):
    help = 'Merge phantom "<base>-N" placeholder sales back into the genuine sale.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--customer',
            type=str,
            default=None,
            help='Filter by customer name (case-insensitive partial match)',
        )
        parser.add_argument(
            '--contract',
            type=str,
            default=None,
            help='Filter by exact contract number',
        )
        parser.add_argument(
            '--fix',
            action='store_true',
            help='Actually apply the merges (default: dry-run)',
        )

    def handle(self, *args, **options):
        fix = options['fix']
        customer_filter = options.get('customer')
        contract_filter = options.get('contract')

        if not fix:
            self.stdout.write(self.style.WARNING(
                'DRY RUN — use --fix to apply changes.\n'
            ))

        candidates = (
            AnthillSale.objects
            .filter(order__isnull=True)
            .filter(anthill_activity_id__contains='-')
            .exclude(contract_number='')
            .exclude(contract_number__isnull=True)
            .select_related('customer', 'order')
            .prefetch_related('payments')
        )
        if customer_filter:
            candidates = candidates.filter(customer_name__icontains=customer_filter)
        if contract_filter:
            candidates = candidates.filter(contract_number=contract_filter)

        merged = 0
        moved_payments = 0
        deleted_payments = 0
        skipped = 0

        for placeholder in candidates:
            if not _PLACEHOLDER_RE.match(placeholder.anthill_activity_id):
                continue
            if placeholder.sale_value and placeholder.sale_value > 0:
                continue

            target = self._find_target(placeholder)
            if not target:
                continue

            if not self._coversheet_is_disposable(placeholder):
                self.stdout.write(self.style.WARNING(
                    f'\n{placeholder.anthill_activity_id} '
                    f'({placeholder.contract_number}) has a cover sheet with '
                    f'content — skipping. Review manually.'
                ))
                skipped += 1
                continue

            payments = list(placeholder.payments.all())
            self.stdout.write(self.style.NOTICE(
                f'\n{placeholder.anthill_activity_id} '
                f'({placeholder.customer_name or "?"}, '
                f'{placeholder.contract_number}) '
                f'-> {target.anthill_activity_id}'
            ))
            self.stdout.write(
                f'    {len(payments)} payment(s) to re-home, '
                f'then delete placeholder.'
            )

            if not fix:
                merged += 1
                moved_payments += len(payments)
                continue

            with transaction.atomic():
                self._backfill_target(placeholder, target)
                m, d = self._merge(placeholder, target, payments)
                moved_payments += m
                deleted_payments += d
                placeholder.delete()
                self._recalculate(target)
            merged += 1

        self.stdout.write('')
        verb = 'Merged' if fix else 'Would merge'
        self.stdout.write(self.style.SUCCESS(
            f'{verb} {merged} placeholder sale(s); '
            f'{moved_payments} payment(s) re-homed'
            + (f', {deleted_payments} duplicate(s) removed' if fix else '')
            + (f', {skipped} skipped' if skipped else '')
            + '.'
        ))
        if not fix and merged:
            self.stdout.write(self.style.WARNING(
                'Re-run with --fix to apply.'
            ))

    # ── Helpers ─────────────────────────────────────────────────────────────
    @staticmethod
    def _coversheet_is_disposable(sale):
        """True when the sale's cover sheet (if any) is empty auto-created
        scaffolding safe to delete with the placeholder. A finalized sheet, one
        with revision history, or one carrying real content is NOT disposable."""
        cs = getattr(sale, 'cover_sheet', None)
        if cs is None:
            return True
        if cs.is_final or cs.revision_number > 1:
            return False
        if cs.history_entries.exists():
            return False
        # Only genuinely-manual fields count as content. The on-site name/phone
        # and installation address are auto-populated from the customer record
        # on every cover sheet, so they don't signal real work.
        content_fields = (
            cs.products_scope, cs.measurements_notes, cs.access_notes,
            cs.health_safety_notes, cs.special_instructions,
            cs.door_details, cs.handle_details, cs.lighting_details,
        )
        return not any((f or '').strip() for f in content_fields)

    def _find_target(self, placeholder):
        """Pick the genuine sale a placeholder's payments belong to.

        The strongest link is the placeholder's base activity id: a sale whose
        ``anthill_activity_id`` equals the base (e.g. ``425306-2`` -> ``425306``)
        is the real Anthill sale even when its contract number hasn't been
        populated yet. Activity ids are unique, so this is at most one record.
        Otherwise fall back to a non-placeholder sibling sharing the contract.
        """
        m = _PLACEHOLDER_RE.match(placeholder.anthill_activity_id)
        base = m.group('base') if m else None

        if base:
            exact = (
                AnthillSale.objects
                .select_related('order')
                .filter(anthill_activity_id=base)
                .exclude(pk=placeholder.pk)
                .first()
            )
            if exact and (
                not placeholder.customer_id
                or not exact.customer_id
                or exact.customer_id == placeholder.customer_id
            ):
                return exact

        contract_qs = (
            AnthillSale.objects
            .select_related('order')
            .filter(contract_number=placeholder.contract_number)
            .exclude(pk=placeholder.pk)
        )

        def genuine(qs):
            return [
                s for s in qs
                if not _PLACEHOLDER_RE.match(s.anthill_activity_id)
                or (s.order_id and (s.sale_value or 0) > 0)
            ]

        def rank(s):
            return (
                1 if s.order_id else 0,
                1 if (s.sale_value or 0) > 0 else 0,
            )

        # Prefer a sibling for the same customer.
        if placeholder.customer_id:
            same_customer = genuine(contract_qs.filter(customer_id=placeholder.customer_id))
            if same_customer:
                return max(same_customer, key=rank)

        # Cross-customer fallback: the contract number uniquely identifies the
        # genuine sale, so adopt it when there is exactly one unambiguous match
        # (the placeholder's customer is a phantom created during the scrape).
        cross = genuine(contract_qs)
        if len(cross) == 1:
            return cross[0]
        if not placeholder.customer_id and cross:
            return max(cross, key=rank)
        return None

    @staticmethod
    def _backfill_target(placeholder, target):
        """Copy contract / customer details onto the genuine sale when it lacks
        them, so the linkage the placeholder held isn't lost on deletion."""
        fields = []
        if not target.contract_number and placeholder.contract_number:
            target.contract_number = placeholder.contract_number
            fields.append('contract_number')
        if not target.customer_id and placeholder.customer_id:
            target.customer_id = placeholder.customer_id
            fields.append('customer')
        if not target.customer_name and placeholder.customer_name:
            target.customer_name = placeholder.customer_name
            fields.append('customer_name')
        if not target.location and placeholder.location:
            target.location = placeholder.location
            fields.append('location')
        if fields:
            target.save(update_fields=fields)

    def _merge(self, placeholder, target, payments):
        """Reassign placeholder payments to target, dropping exact duplicates."""
        existing = list(target.payments.all())
        moved = 0
        deleted = 0
        for p in payments:
            if self._has_duplicate(p, existing):
                p.delete()
                deleted += 1
                continue
            p.sale = target
            p.save(update_fields=['sale'])
            existing.append(p)
            moved += 1
        return moved, deleted

    @staticmethod
    def _has_duplicate(payment, existing):
        """True when ``payment`` already exists on the target sale."""
        for e in existing:
            if payment.anthill_payment_id and e.anthill_payment_id == payment.anthill_payment_id:
                return True
            if (
                payment.xero_invoice_id
                and e.xero_invoice_id == payment.xero_invoice_id
                and (e.amount or Decimal('0')) == (payment.amount or Decimal('0'))
                and e.date == payment.date
            ):
                return True
        return False

    @staticmethod
    def _recalculate(sale):
        try:
            from stock_take.customer_views import _recalculate_sale_financials
            _recalculate_sale_financials(sale)
        except Exception:
            pass
