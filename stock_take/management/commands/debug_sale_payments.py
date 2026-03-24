"""
Management command: debug_sale_payments
────────────────────────────────────────
Diagnostic tool that shows EXACTLY what Xero returns for a sale's payments
vs what we have stored locally. Helps debug multi-job payment mismatches.

Usage:
    python manage.py debug_sale_payments 412490
    python manage.py debug_sale_payments 412490 402971
    python manage.py debug_sale_payments --contract BFS-NR-412490
"""
import logging
from decimal import Decimal

from django.core.management.base import BaseCommand

from stock_take.models import AnthillSale, AnthillPayment
from stock_take.services import xero_api

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Debug payment data: compare local DB vs live Xero data for one or more sales'

    def add_arguments(self, parser):
        parser.add_argument(
            'activity_ids',
            nargs='*',
            help='One or more Anthill activity IDs to inspect',
        )
        parser.add_argument(
            '--contract',
            type=str,
            default=None,
            help='Look up by contract number instead of activity ID',
        )

    def handle(self, *args, **options):
        activity_ids = options['activity_ids']
        contract = options['contract']

        # Check Xero connection
        access_token, _ = xero_api.get_valid_access_token()
        if not access_token:
            self.stderr.write(self.style.ERROR('No valid Xero connection.'))
            return

        sales = []
        if contract:
            sales = list(AnthillSale.objects.filter(contract_number=contract))
        elif activity_ids:
            sales = list(AnthillSale.objects.filter(anthill_activity_id__in=activity_ids))
        else:
            self.stderr.write('Provide activity IDs or --contract')
            return

        if not sales:
            self.stderr.write(self.style.ERROR('No matching sales found.'))
            return

        # Track all payment IDs across all jobs for this customer to detect sharing
        all_base_payment_ids = {}  # base_payment_id -> list of contract_numbers

        for sale in sales:
            self.stdout.write(self.style.NOTICE(
                f'\n{"="*70}\n'
                f'SALE: {sale.contract_number} (Activity: {sale.anthill_activity_id})\n'
                f'Customer: {sale.customer_name}\n'
                f'Sale Value: £{sale.sale_value or 0}\n'
                f'{"="*70}'
            ))

            # --- LOCAL DB PAYMENTS ---
            local_payments = list(sale.payments.all().order_by('date'))
            local_total = sum(p.amount for p in local_payments if p.amount and not p.ignored) or Decimal('0')
            self.stdout.write(self.style.WARNING(
                f'\n  LOCAL DB PAYMENTS ({len(local_payments)} records, total £{local_total}):'
            ))
            for p in local_payments:
                date_str = p.date.strftime('%d/%m/%Y') if p.date else '?'
                self.stdout.write(
                    f'    {p.xero_invoice_number or "?":<10} '
                    f'{p.payment_type:<15} '
                    f'{date_str:<12} '
                    f'£{p.amount or 0:<10} '
                    f'{p.status:<12} '
                    f'src={p.source:<8} '
                    f'pid={p.anthill_payment_id[:30] if p.anthill_payment_id else "?"}'
                    f'{"  [IGNORED]" if p.ignored else ""}'
                    f'{"  [FALLBACK]" if getattr(p, "is_fallback_amount", False) else ""}'
                )

            # --- LIVE XERO DATA ---
            self.stdout.write(self.style.WARNING(
                f'\n  LIVE XERO DATA for ref="{sale.contract_number}":'
            ))
            try:
                invoice_data = xero_api.get_sale_payments_from_xero(
                    contract_number=sale.contract_number,
                    contact_name=None,  # Skip name check for diagnostics
                )
            except Exception as exc:
                self.stderr.write(f'    ERROR: {exc}')
                continue

            if not invoice_data:
                self.stdout.write('    No invoices found in Xero.')
                continue

            xero_total_paid = Decimal('0')
            xero_total_due = Decimal('0')

            for inv in invoice_data:
                self.stdout.write(
                    f'\n    Invoice: {inv["invoice_number"]} ({inv["status"]})\n'
                    f'    Reference: {inv["reference"]}\n'
                    f'    Contact: {inv["contact_name"]}\n'
                    f'    Total: £{inv["total"]}  |  Paid: £{inv["amount_paid"]}  |  Due: £{inv["amount_due"]}'
                )
                xero_total_paid += inv['amount_paid']
                xero_total_due += inv['amount_due']

                for p in inv['payments']:
                    date_str = p['date'].strftime('%d/%m/%Y') if p['date'] else '?'
                    fallback_flag = '  ** FALLBACK **' if p.get('is_fallback') else ''
                    self.stdout.write(
                        f'      Payment: £{p["amount"]:<10} '
                        f'{date_str:<12} '
                        f'{p["reference"]:<15} '
                        f'pid={p["payment_id"][:40]}'
                        f'{fallback_flag}'
                    )
                    # Track base_payment_id for cross-job analysis
                    bpid = p.get('base_payment_id', '')
                    if bpid:
                        all_base_payment_ids.setdefault(bpid, []).append(sale.contract_number)

            xero_payment_sum = sum(
                p['amount'] for inv in invoice_data for p in inv['payments']
            )

            self.stdout.write(self.style.SUCCESS(
                f'\n    XERO INVOICE-LEVEL TOTALS: Paid £{xero_total_paid}, Due £{xero_total_due}'
            ))
            self.stdout.write(
                f'    XERO PAYMENT SUM (our parsing): £{xero_payment_sum}'
            )

            if abs(xero_payment_sum - xero_total_paid) > Decimal('0.50'):
                self.stdout.write(self.style.ERROR(
                    f'    *** MISMATCH: payment sum (£{xero_payment_sum}) != '
                    f'invoice-level paid (£{xero_total_paid}) — '
                    f'likely overpayment/prepayment allocation issue!'
                ))

            # Compare local vs Xero
            local_active = sum(
                p.amount for p in local_payments if p.amount and not p.ignored
            ) or Decimal('0')
            if abs(local_active - xero_total_paid) > Decimal('0.50'):
                self.stdout.write(self.style.ERROR(
                    f'    *** LOCAL vs XERO MISMATCH: Local £{local_active} vs Xero £{xero_total_paid}'
                ))
            else:
                self.stdout.write(self.style.SUCCESS(
                    f'    Local matches Xero invoice totals ✓'
                ))

        # Cross-job analysis: detect shared payments
        if len(sales) > 1:
            shared = {k: v for k, v in all_base_payment_ids.items() if len(set(v)) > 1}
            if shared:
                self.stdout.write(self.style.ERROR(
                    f'\n{"="*70}\n'
                    f'SHARED PAYMENTS DETECTED (same payment appears on multiple jobs):\n'
                    f'{"="*70}'
                ))
                for pid, contracts in shared.items():
                    self.stdout.write(
                        f'  Payment {pid[:50]} -> {", ".join(set(contracts))}'
                    )
                self.stdout.write(self.style.WARNING(
                    '\n  These shared payments may cause double-counting!\n'
                    '  The overpayment/prepayment was likely split across jobs\n'
                    '  but our system recorded the full amount on each.'
                ))
            else:
                self.stdout.write(self.style.SUCCESS(
                    '\n  No shared payments detected between jobs ✓'
                ))
