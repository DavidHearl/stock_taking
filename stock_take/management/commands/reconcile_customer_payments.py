"""
Management command: reconcile_customer_payments
────────────────────────────────────────────────
Post-sync reconciliation that detects misplaced payments across multiple
sales for the same customer and proposes / applies corrections.

This handles cases where:
  1. A payment is recorded against the wrong sale in Xero (wrong reference)
     causing one sale to appear overpaid and another underpaid.
  2. An invoice reference points to a sale that's already fully paid,
     meaning the payment actually belongs to another sale.

Algorithm:
  - Groups sales by customer (anthill_customer_id)
  - For each customer with multiple sales, calculates per-sale balance
  - Strategy 1 — excess match: if a sale is overpaid by £X and one of its
    payments is exactly £X, move that payment to an underpaid sibling.
  - Strategy 2 — shortfall match: if moving any single payment from an
    overpaid sale would exactly satisfy an underpaid sibling's shortfall.
  - After moves, recalculates paid_in_full for affected sales.

Prerequisites:
  - Run sync_xero_sale_payments first to ensure all payment data is fresh.
  - For sales marked paid_in_full that may have extra invoices in Xero,
    re-sync with --include-paid first.

Usage:
    python manage.py reconcile_customer_payments                        # Scan all
    python manage.py reconcile_customer_payments --customer "Claire Veitch"
    python manage.py reconcile_customer_payments --fix                  # Apply fixes
"""

import logging
from collections import defaultdict
from datetime import datetime
from decimal import Decimal

from django.core.management.base import BaseCommand

from stock_take.models import AnthillSale, AnthillPayment

logger = logging.getLogger(__name__)

# Allow £1 tolerance for rounding / VAT differences
TOLERANCE = Decimal('1.00')

# Statuses that mean a sale is finished and must balance to zero
COMPLETE_STATUSES = {'won', 'complete'}


class Command(BaseCommand):
    help = 'Detect and fix misplaced payments across multiple sales for the same customer'

    # ── CLI args ───────────────────────────────────────────────────────────
    def add_arguments(self, parser):
        parser.add_argument(
            '--customer',
            type=str,
            default=None,
            help='Filter by customer name (case-insensitive partial match)',
        )
        parser.add_argument(
            '--fix',
            action='store_true',
            help='Actually apply the proposed payment moves (default: dry-run)',
        )

    # ── Entry point ────────────────────────────────────────────────────────
    def handle(self, *args, **options):
        fix = options['fix']
        customer_filter = options.get('customer')

        if not fix:
            self.stdout.write(self.style.WARNING(
                'DRY RUN — use --fix to apply changes.\n'
            ))

        # ── Build sale queryset ────────────────────────────────────────────
        base_qs = (
            AnthillSale.objects
            .filter(category='3')
            .exclude(contract_number='')
            .exclude(contract_number__isnull=True)
            .exclude(sale_value__isnull=True)
            .filter(sale_value__gt=0)
        )
        if customer_filter:
            base_qs = base_qs.filter(customer_name__icontains=customer_filter)

        # Group by customer
        customers = defaultdict(list)
        for sale in base_qs:
            key = sale.anthill_customer_id or sale.customer_name
            if key:
                customers[key].append(sale)

        multi = {k: v for k, v in customers.items() if len(v) > 1}
        if not multi:
            self.stdout.write('No customers with multiple sales found.')
            return

        self.stdout.write(
            f'Scanning {len(multi)} customers with multiple sales …\n'
        )

        total_anomalies = 0
        total_moves = 0

        for _cust_key, sales in sorted(
            multi.items(), key=lambda x: x[1][0].customer_name
        ):
            moves, anomaly = self._analyse_customer(sales)
            if not anomaly:
                continue

            total_anomalies += 1
            cust_name = sales[0].customer_name
            self.stdout.write(self.style.NOTICE(f'\n{"=" * 60}'))
            self.stdout.write(self.style.NOTICE(f'Customer: {cust_name}'))
            self.stdout.write(self.style.NOTICE(f'{"=" * 60}'))

            # Print per-sale summary
            for s in self._sale_states(sales):
                self._print_sale_summary(s)

            if not moves:
                self.stdout.write(
                    self.style.WARNING(
                        '\n  No automatic fix found — manual review needed.'
                    )
                )
                # Flag zero-payment sales as a hint
                for s in self._sale_states(sales):
                    if s['total_paid'] == 0 and s['sale_value'] > 0:
                        self.stdout.write(
                            f'    Sale {s["sale"].anthill_activity_id} '
                            f'has 0 payments — check Xero for invoices '
                            f'under a different reference.'
                        )
                continue

            # Clean up previous split records before applying
            if fix and any(
                len(m) == 5 and m[4] is not None for m in moves
            ):
                all_sale_pks = set()
                for m in moves:
                    all_sale_pks.add(m[1].pk)
                    all_sale_pks.add(m[2].pk)
                deleted = AnthillPayment.objects.filter(
                    sale_id__in=all_sale_pks,
                    anthill_payment_id__contains='_split',
                ).delete()[0]
                if deleted:
                    self.stdout.write(
                        f'  Cleaned up {deleted} previous split records.'
                    )

            for move_tuple in moves:
                split_info = None
                if len(move_tuple) == 5:
                    payment, from_sale, to_sale, reason, split_info = move_tuple
                else:
                    payment, from_sale, to_sale, reason = move_tuple

                date_str = (
                    payment.date.strftime('%d/%m/%Y') if payment.date else '?'
                )

                if split_info:
                    keep_amount, move_amount = split_info
                    self.stdout.write(self.style.WARNING(
                        f'\n  PROPOSED SPLIT ({reason}):'
                    ))
                    self.stdout.write(
                        f'    Payment: £{payment.amount:.2f} '
                        f'({payment.xero_invoice_number or "?"} — '
                        f'{payment.payment_type}, {date_str})'
                    )
                    self.stdout.write(
                        f'    KEEP  £{keep_amount:.2f} on Sale '
                        f'{from_sale.anthill_activity_id} '
                        f'({from_sale.contract_number})'
                    )
                    self.stdout.write(
                        f'    MOVE  £{move_amount:.2f} to Sale '
                        f'{to_sale.anthill_activity_id} '
                        f'({to_sale.contract_number})'
                    )

                    if fix:
                        payment.amount = keep_amount
                        payment.save(update_fields=['amount'])
                        AnthillPayment.objects.create(
                            sale=to_sale,
                            source=payment.source,
                            xero_invoice_id=payment.xero_invoice_id,
                            xero_invoice_number=payment.xero_invoice_number,
                            invoice_total=payment.invoice_total,
                            invoice_amount_due=payment.invoice_amount_due,
                            invoice_status=payment.invoice_status,
                            anthill_payment_id=(
                                f'{payment.anthill_payment_id}_split'
                                if payment.anthill_payment_id else ''
                            ),
                            payment_type=payment.payment_type,
                            date=payment.date,
                            amount=move_amount,
                            status=payment.status,
                            location=payment.location,
                            user_name=payment.user_name,
                        )
                        self.stdout.write(self.style.SUCCESS(
                            '    -> Payment split applied.'
                        ))
                else:
                    self.stdout.write(self.style.WARNING(
                        f'\n  PROPOSED MOVE ({reason}):'
                    ))
                    self.stdout.write(
                        f'    Payment: £{payment.amount:.2f} '
                        f'({payment.xero_invoice_number or "?"} — '
                        f'{payment.payment_type}, {date_str})'
                    )
                    self.stdout.write(
                        f'    FROM  Sale {from_sale.anthill_activity_id} '
                        f'({from_sale.contract_number})'
                    )
                    self.stdout.write(
                        f'    TO    Sale {to_sale.anthill_activity_id} '
                        f'({to_sale.contract_number})'
                    )

                    if fix:
                        payment.sale = to_sale
                        payment.save(update_fields=['sale'])
                        self.stdout.write(self.style.SUCCESS(
                            '    -> Payment moved.'
                        ))

                total_moves += 1

            # Recalculate paid_in_full for all affected sales
            if fix and moves:
                affected_pks = set()
                for m in moves:
                    affected_pks.add(m[1].pk)
                    affected_pks.add(m[2].pk)
                for sale in AnthillSale.objects.filter(pk__in=affected_pks):
                    self._recalc_paid_in_full(sale)

        # ── Summary ────────────────────────────────────────────────────────
        self.stdout.write(f'\n{"=" * 60}')
        self.stdout.write(f'Customers with anomalies : {total_anomalies}')
        self.stdout.write(
            f'Payment moves {"applied" if fix else "proposed"} : {total_moves}'
        )
        if not fix and total_moves > 0:
            self.stdout.write(self.style.WARNING(
                'Run with --fix to apply these changes.'
            ))

    # ── Helpers ─────────────────────────────────────────────────────────────
    def _sale_states(self, sales):
        """Return a list of dicts summarising each sale's payment state."""
        states = []
        for sale in sales:
            payments = list(
                sale.payments
                .filter(source='xero', ignored=False)
                .exclude(amount__isnull=True)
            )
            total_paid = (
                sum(p.amount for p in payments) if payments else Decimal('0')
            )
            effective_value = (sale.sale_value or Decimal('0')) - (sale.discount or Decimal('0'))
            balance = effective_value - total_paid
            states.append({
                'sale': sale,
                'payments': payments,
                'total_paid': total_paid,
                'sale_value': effective_value,
                'balance': balance,          # +ve = underpaid, -ve = overpaid
            })
        return states

    def _print_sale_summary(self, s):
        """Pretty-print one sale's state."""
        if s['balance'] < -TOLERANCE:
            tag = self.style.ERROR(f'OVERPAID by £{abs(s["balance"]):.2f}')
        elif s['balance'] > TOLERANCE:
            tag = self.style.WARNING(f'UNDERPAID by £{s["balance"]:.2f}')
        else:
            tag = self.style.SUCCESS('BALANCED')

        self.stdout.write(
            f'  Sale {s["sale"].anthill_activity_id} '
            f'({s["sale"].contract_number})  '
            f'Value: £{s["sale_value"]:.2f}  '
            f'Paid: £{s["total_paid"]:.2f}  '
            f'({len(s["payments"])} payments)  '
            f'{tag}'
        )
        for p in s['payments']:
            dt = p.date.strftime('%d/%m/%Y') if p.date else '?'
            self.stdout.write(
                f'    {p.xero_invoice_number or "?":<10} '
                f'{p.payment_type:<20} {dt:<12} £{p.amount:.2f}'
            )

    def _analyse_customer(self, sales):
        """
        Analyse a single customer's sales and return proposed moves.

        Returns:
            (moves, anomaly)
            moves  : list of tuples (AnthillPayment, from_sale, to_sale, reason[, split_info])
            anomaly: bool — True if there's any imbalance worth reporting
        """
        # ── Pre-pass: detect cross-sale duplicate payments ─────────────
        # Two different sales for the same customer can end up with the
        # same Xero payment (same invoice, same amount, same date) because
        # the invoice reference matched both contracts.  Collect duplicates
        # and mark which ones should be flagged for removal.
        self._flag_cross_sale_duplicates(sales)

        states = self._sale_states(sales)

        overpaid = [s for s in states if s['balance'] < -TOLERANCE]
        underpaid = [s for s in states if s['balance'] > TOLERANCE]

        if not overpaid and not underpaid:
            return [], False
        if not overpaid:
            # No overpaid sales — check for mixed signal: some sales have
            # payments while siblings have none (possible cross-reference).
            has_payments = any(s['total_paid'] > 0 for s in states)
            has_zero = any(s['total_paid'] == 0 and s['sale_value'] > 0
                           for s in states)
            if has_payments and has_zero:
                return [], True   # worth a manual look
            return [], False      # all unpaid — not an anomaly
        if not underpaid:
            return [], True       # overpaid with nowhere to move — flag it

        moves = []
        moved_ids = set()

        # Strategy 1 — excess match
        # If a sale is overpaid by £X and has a payment of exactly £X,
        # that payment is almost certainly misplaced.
        for op in overpaid:
            excess = abs(op['balance'])
            newest_first = sorted(
                op['payments'],
                key=lambda p: p.date or datetime.min,
                reverse=True,
            )
            for payment in newest_first:
                if payment.pk in moved_ids:
                    continue
                if abs(payment.amount - excess) > TOLERANCE:
                    continue
                # Find the best underpaid recipient
                target = self._best_recipient(payment, underpaid, moved_ids)
                if target is not None:
                    moves.append((
                        payment, op['sale'], target['sale'], 'excess match'
                    ))
                    moved_ids.add(payment.pk)
                    op['balance'] += payment.amount
                    target['balance'] -= payment.amount
                    break        # one excess move per overpaid sale

        # Strategy 2 — shortfall match
        # Does any remaining payment from an overpaid sale exactly cover
        # an underpaid sale's remaining shortfall?
        for op in overpaid:
            if op['balance'] >= -TOLERANCE:
                continue          # already resolved
            newest_first = sorted(
                op['payments'],
                key=lambda p: p.date or datetime.min,
                reverse=True,
            )
            for payment in newest_first:
                if payment.pk in moved_ids:
                    continue
                for up in underpaid:
                    if up['balance'] <= TOLERANCE:
                        continue
                    if abs(payment.amount - up['balance']) <= TOLERANCE:
                        moves.append((
                            payment, op['sale'], up['sale'], 'shortfall match'
                        ))
                        moved_ids.add(payment.pk)
                        op['balance'] += payment.amount
                        up['balance'] -= payment.amount
                        break
                else:
                    continue
                break   # re-evaluate after each move

        # ── Strategy 3 — chronological fill for complete sales ──────
        # If a complete sale is still overpaid after S1/S2, keep the
        # oldest payments up to the sale value, split the boundary
        # payment, and move all surplus to underpaid siblings.
        for op in overpaid:
            if op['balance'] >= -TOLERANCE:
                continue
            if op['sale'].status not in COMPLETE_STATUSES:
                continue

            effective_value = op['sale_value']
            remaining_payments = sorted(
                [p for p in op['payments'] if p.pk not in moved_ids],
                key=lambda p: p.date or datetime.min,
            )

            running_total = Decimal('0')
            hit_boundary = False

            for i, payment in enumerate(remaining_payments):
                if hit_boundary:
                    target = self._pick_surplus_target(underpaid)
                    if target:
                        moves.append((
                            payment, op['sale'], target['sale'],
                            'complete-sale surplus', None,
                        ))
                        moved_ids.add(payment.pk)
                        op['balance'] += payment.amount
                        target['balance'] -= payment.amount
                    continue

                running_total += payment.amount

                if running_total > effective_value + TOLERANCE:
                    hit_boundary = True
                    keep_amount = effective_value - (
                        running_total - payment.amount
                    )
                    move_amount = payment.amount - keep_amount

                    if keep_amount < TOLERANCE:
                        # Whole payment is surplus
                        target = self._pick_surplus_target(underpaid)
                        if target:
                            moves.append((
                                payment, op['sale'], target['sale'],
                                'complete-sale surplus', None,
                            ))
                            moved_ids.add(payment.pk)
                            op['balance'] += payment.amount
                            target['balance'] -= payment.amount
                    elif move_amount > TOLERANCE:
                        # Split at the boundary
                        target = self._pick_surplus_target(underpaid)
                        if target:
                            moves.append((
                                payment, op['sale'], target['sale'],
                                'complete-sale split',
                                (keep_amount, move_amount),
                            ))
                            moved_ids.add(payment.pk)
                            op['balance'] += move_amount
                            target['balance'] -= move_amount

        return moves, True

    def _flag_cross_sale_duplicates(self, sales):
        """Detect and report payments duplicated across or within sales.

        A payment is considered a duplicate when two AnthillPayment records
        have the same xero_invoice_number, same amount, and same date.
        This happens when:
        - An invoice reference matches multiple contracts (cross-sale)
        - The same payment was imported twice on the same sale
        """
        from collections import defaultdict

        # Build fingerprint → list of (sale, payment) tuples
        fp_map = defaultdict(list)
        for sale in sales:
            payments = list(
                sale.payments
                .filter(source='xero', ignored=False)
                .exclude(amount__isnull=True)
            )
            for p in payments:
                key = (
                    p.xero_invoice_number or '',
                    p.amount,
                    p.date.date() if p.date else None,
                )
                if key[0]:  # only match on real invoice numbers
                    fp_map[key].append((sale, p))

        duplicates_found = 0
        for key, entries in fp_map.items():
            if len(entries) <= 1:
                continue

            inv_num, amount, dt = key
            dt_str = dt.strftime('%d/%m/%Y') if dt else '?'

            # Check if cross-sale or same-sale
            sale_pks = set(sale.pk for sale, _p in entries)
            if len(sale_pks) > 1:
                label = f'appears on {len(sale_pks)} sales'
            else:
                label = f'duplicated {len(entries)}x on same sale'

            self.stdout.write(self.style.ERROR(
                f'\n  DUPLICATE: {inv_num} £{amount:.2f} ({dt_str}) '
                f'{label}'
            ))
            for sale, p in entries:
                self.stdout.write(
                    f'    Payment #{p.pk} on Sale {sale.anthill_activity_id} '
                    f'({sale.contract_number})'
                )

            duplicates_found += 1

        if duplicates_found:
            self.stdout.write(self.style.ERROR(
                f'\n  {duplicates_found} duplicate(s) detected. '
                f'Delete the extra copy on the sale detail page.'
            ))

    def _pick_surplus_target(self, underpaid):
        """Pick the underpaid sale with the largest remaining shortfall."""
        candidates = [up for up in underpaid if up['balance'] > TOLERANCE]
        if not candidates:
            return None
        candidates.sort(key=lambda c: c['balance'], reverse=True)
        return candidates[0]

    def _best_recipient(self, payment, underpaid, moved_ids):
        """Pick the underpaid sale that best receives *payment*."""
        candidates = [
            up for up in underpaid
            if up['balance'] > TOLERANCE
            and payment.amount <= up['balance'] + TOLERANCE
        ]
        if not candidates:
            return None
        # Prefer the sale whose shortfall most closely matches the payment
        candidates.sort(key=lambda c: abs(c['balance'] - payment.amount))
        return candidates[0]

    def _recalc_paid_in_full(self, sale):
        """Recalculate paid_in_full for a sale after payment moves."""
        from stock_take.customer_views import _recalculate_sale_financials
        old_paid = sale.paid_in_full
        _recalculate_sale_financials(sale)
        sale.refresh_from_db(fields=['paid_in_full', 'balance_payable'])
        if not old_paid and sale.paid_in_full:
            self.stdout.write(self.style.SUCCESS(
                f'    Sale {sale.anthill_activity_id} marked paid_in_full.'
            ))
        elif old_paid and not sale.paid_in_full:
            self.stdout.write(self.style.WARNING(
                f'    Sale {sale.anthill_activity_id} unmarked paid_in_full '
                f'(balance: £{sale.balance_payable:.2f}).'
            ))
