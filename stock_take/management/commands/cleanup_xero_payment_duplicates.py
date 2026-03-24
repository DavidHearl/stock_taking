import re
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db.models import Sum

from stock_take.models import AnthillPayment, AnthillSale


UUID_RE = re.compile(
    r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$',
    re.IGNORECASE,
)


class Command(BaseCommand):
    help = (
        'Clean up duplicate Xero payment records caused by overpayments/prepayments/credit notes '
        'being recorded with the full amount on every sale for the same customer. '
        'Removes old-format plain-UUID records when new-format UUID_InvoiceID records exist, '
        'and detects cross-sale duplication where the same base payment appears on multiple sales.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be deleted without actually deleting',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']

        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN MODE — nothing will be deleted'))

        total_old_format_removed = 0
        total_cross_sale_removed = 0
        sales_affected = set()

        # ── Pass 1: Remove old-format records (plain UUID) where new-format
        #    (UUID_InvoiceID) records exist on the same sale ──
        self.stdout.write('\n── Pass 1: Old-format payment ID cleanup ──')

        xero_payments = AnthillPayment.objects.filter(source='xero').select_related('sale')

        # Build set of new-format base IDs per sale
        new_format_by_sale = {}  # {sale_pk: set of base_ids}
        for ap in xero_payments:
            pid = ap.anthill_payment_id or ''
            if '_' in pid:
                base = pid.split('_', 1)[0].lower()
                new_format_by_sale.setdefault(ap.sale_id, set()).add(base)

        # Find and remove old-format records that clash
        for ap in xero_payments:
            pid = ap.anthill_payment_id or ''
            if not UUID_RE.match(pid):
                continue
            bases = new_format_by_sale.get(ap.sale_id, set())
            if pid.lower() in bases:
                sale_name = ap.sale.customer_name if ap.sale else '?'
                contract = ap.sale.contract_number if ap.sale else '?'
                self.stdout.write(
                    f'  OLD-FORMAT: {sale_name} ({contract}) — '
                    f'payment {pid} £{ap.amount} [{ap.payment_type}]'
                )
                if not dry_run:
                    ap.delete()
                total_old_format_removed += 1
                sales_affected.add(ap.sale_id)

        self.stdout.write(f'  → {total_old_format_removed} old-format records {"would be " if dry_run else ""}removed')

        # ── Pass 2: Detect cross-sale duplication — same base payment ID
        #    on multiple different sales ──
        self.stdout.write('\n── Pass 2: Cross-sale duplicate detection ──')

        # Re-query after pass 1 deletions
        xero_payments = AnthillPayment.objects.filter(source='xero').select_related('sale')

        # Group by base_payment_id across all sales
        base_to_records = {}  # {base_id: [(ap, sale_pk, contract_number)]}
        for ap in xero_payments:
            pid = ap.anthill_payment_id or ''
            if not pid:
                continue
            # Extract base: if new format (UUID_InvoiceID), take the UUID part
            if '_' in pid:
                base = pid.split('_', 1)[0].lower()
            else:
                base = pid.lower()
            base_to_records.setdefault(base, []).append(ap)

        for base_id, records in base_to_records.items():
            # Group by sale
            by_sale = {}
            for ap in records:
                by_sale.setdefault(ap.sale_id, []).append(ap)

            if len(by_sale) <= 1:
                continue  # Only on one sale — no cross-sale issue

            # Check if these are legitimately different allocations
            # (different amounts from Allocations array) vs duplicated full amounts
            all_amounts = [ap.amount for ap in records]
            unique_amounts = set(all_amounts)

            # If every record has the SAME amount and it appears on multiple sales,
            # that's the classic duplication pattern
            if len(unique_amounts) == 1 and len(by_sale) > 1:
                amount = list(unique_amounts)[0]
                self.stdout.write(
                    self.style.WARNING(
                        f'  CROSS-SALE DUP: base {base_id[:12]}… — '
                        f'£{amount} appears on {len(by_sale)} sales:'
                    )
                )
                # Keep the record on the first sale (by contract number), remove from others
                sorted_sales = sorted(by_sale.keys(),
                    key=lambda pk: (AnthillSale.objects.filter(pk=pk).values_list('contract_number', flat=True).first() or ''))
                keep_sale = sorted_sales[0]
                for sale_pk in sorted_sales[1:]:
                    for ap in by_sale[sale_pk]:
                        sale_name = ap.sale.customer_name if ap.sale else '?'
                        contract = ap.sale.contract_number if ap.sale else '?'
                        self.stdout.write(
                            f'    REMOVE from {sale_name} ({contract}) — '
                            f'£{ap.amount} [{ap.payment_type}]'
                        )
                        if not dry_run:
                            ap.delete()
                        total_cross_sale_removed += 1
                        sales_affected.add(sale_pk)

                keep_contract = AnthillSale.objects.filter(pk=keep_sale).values_list('contract_number', flat=True).first() or '?'
                self.stdout.write(f'    KEEP on ({keep_contract})')
            else:
                # Different amounts per sale — likely legitimate per-invoice allocations
                # Just report for visibility
                self.stdout.write(
                    f'  OK: base {base_id[:12]}… has {len(by_sale)} sales with '
                    f'different amounts — likely valid allocations'
                )

        self.stdout.write(f'  → {total_cross_sale_removed} cross-sale duplicates {"would be " if dry_run else ""}removed')

        # ── Pass 3: Remove payments that exceed the sale value ──
        self.stdout.write('\n── Pass 3: Over-cap payment cleanup ──')

        total_overcap_removed = 0
        overcap_sales = AnthillSale.objects.filter(sale_value__gt=0).prefetch_related('payments')

        for sale in overcap_sales:
            sale_value = sale.sale_value or Decimal('0')
            if sale_value <= 0:
                continue
            payments = list(
                AnthillPayment.objects.filter(sale=sale)
                .order_by('date', 'pk')
            )
            running = Decimal('0')
            for ap in payments:
                amt = ap.amount or Decimal('0')
                if running + amt > sale_value + Decimal('0.50'):
                    self.stdout.write(
                        f'  OVER-CAP: {sale.customer_name} ({sale.contract_number}) — '
                        f'£{amt} [{ap.payment_type}] {ap.xero_invoice_number or ""} '
                        f'(running £{running} + £{amt} > sale £{sale_value})'
                    )
                    if not dry_run:
                        ap.delete()
                    total_overcap_removed += 1
                    sales_affected.add(sale.pk)
                else:
                    running += amt

        self.stdout.write(f'  → {total_overcap_removed} over-cap payments {"would be " if dry_run else ""}removed')

        # ── Summary ──
        self.stdout.write(f'\n── Summary ──')
        total = total_old_format_removed + total_cross_sale_removed + total_overcap_removed
        self.stdout.write(
            self.style.SUCCESS(
                f'Total: {total} records {"would be " if dry_run else ""}removed '
                f'across {len(sales_affected)} sales'
            )
        )

        if dry_run and total > 0:
            self.stdout.write(self.style.WARNING(
                '\nRun without --dry-run to actually delete these records.'
            ))
