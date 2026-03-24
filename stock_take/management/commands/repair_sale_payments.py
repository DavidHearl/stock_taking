"""
Management command: repair_sale_payments
────────────────────────────────────────
One-off repair tool for sales where Xero payments were incorrectly assigned.

For a given sale, this command:
  1. Checks if Xero has any invoices for that contract number
  2. If NO invoices exist, lists the orphaned payment records
  3. With --fix, deletes the orphaned xero-sourced payments
  4. With --resync, also triggers a fresh sync for any related sales

Usage:
    python manage.py repair_sale_payments 412490                  # Dry-run: show what would be cleaned
    python manage.py repair_sale_payments 412490 --fix            # Delete orphaned payments
    python manage.py repair_sale_payments 412490 --fix --resync   # Delete + resync related jobs
"""
import logging
from decimal import Decimal

from django.core.management.base import BaseCommand

from stock_take.models import AnthillSale, AnthillPayment
from stock_take.services import xero_api

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Repair incorrectly assigned Xero payments on a sale'

    def add_arguments(self, parser):
        parser.add_argument(
            'activity_ids',
            nargs='+',
            help='Anthill activity IDs to repair',
        )
        parser.add_argument(
            '--fix',
            action='store_true',
            help='Actually delete orphaned payments (default: dry-run)',
        )
        parser.add_argument(
            '--resync',
            action='store_true',
            help='After fixing, resync related customer sales from Xero',
        )

    def handle(self, *args, **options):
        fix = options['fix']
        resync = options['resync']

        access_token, _ = xero_api.get_valid_access_token()
        if not access_token:
            self.stderr.write(self.style.ERROR('No valid Xero connection.'))
            return

        for activity_id in options['activity_ids']:
            try:
                sale = AnthillSale.objects.get(anthill_activity_id=activity_id)
            except AnthillSale.DoesNotExist:
                self.stderr.write(f'Sale {activity_id} not found.')
                continue

            self.stdout.write(self.style.NOTICE(
                f'\n{"="*60}\n'
                f'Sale: {sale.contract_number} (Activity: {activity_id})\n'
                f'Customer: {sale.customer_name}\n'
                f'Sale Value: £{sale.sale_value or 0}\n'
                f'{"="*60}'
            ))

            # Check what Xero has
            invoice_data = xero_api.get_sale_payments_from_xero(
                contract_number=sale.contract_number,
                contact_name=None,
            )

            xero_payment_ids = set()
            for inv in invoice_data:
                for p in inv['payments']:
                    if p.get('payment_id'):
                        xero_payment_ids.add(p['payment_id'])
                    if p.get('base_payment_id'):
                        xero_payment_ids.add(p['base_payment_id'])

            local_xero_payments = list(
                sale.payments.filter(source='xero').order_by('date')
            )

            if not invoice_data:
                self.stdout.write(self.style.WARNING(
                    f'  No Xero invoices found for ref="{sale.contract_number}"'
                ))
                if local_xero_payments:
                    total = sum(p.amount for p in local_xero_payments if p.amount) or Decimal('0')
                    self.stdout.write(self.style.ERROR(
                        f'  But {len(local_xero_payments)} xero-sourced payment records exist (£{total})!'
                    ))
                    for p in local_xero_payments:
                        date_str = p.date.strftime('%d/%m/%Y') if p.date else '?'
                        self.stdout.write(
                            f'    {p.xero_invoice_number or "?":<10} '
                            f'£{p.amount or 0:<10} '
                            f'{date_str:<12} '
                            f'{p.payment_type}'
                        )

                    if fix:
                        count = sale.payments.filter(source='xero').count()
                        sale.payments.filter(source='xero').delete()
                        self.stdout.write(self.style.SUCCESS(
                            f'  DELETED {count} orphaned xero payment records.'
                        ))
                        # Recalculate financials
                        from stock_take.customer_views import _recalculate_sale_financials
                        _recalculate_sale_financials(sale)
                        self.stdout.write(f'  Recalculated sale financials.')
                    else:
                        self.stdout.write(self.style.WARNING(
                            '  Use --fix to delete these orphaned records.'
                        ))
            else:
                self.stdout.write(self.style.SUCCESS(
                    f'  {len(invoice_data)} Xero invoice(s) found — checking alignment...'
                ))

                # Check for local payments that don't match any Xero payment
                orphaned = []
                for p in local_xero_payments:
                    pid = p.anthill_payment_id or ''
                    if pid and pid not in xero_payment_ids:
                        orphaned.append(p)

                if orphaned:
                    self.stdout.write(self.style.ERROR(
                        f'  {len(orphaned)} local payment(s) have no matching Xero payment:'
                    ))
                    for p in orphaned:
                        date_str = p.date.strftime('%d/%m/%Y') if p.date else '?'
                        self.stdout.write(
                            f'    {p.xero_invoice_number or "?":<10} '
                            f'£{p.amount or 0:<10} '
                            f'{date_str:<12} '
                            f'pid={p.anthill_payment_id[:40] if p.anthill_payment_id else "?"}'
                        )

                    if fix:
                        for p in orphaned:
                            p.delete()
                        self.stdout.write(self.style.SUCCESS(
                            f'  DELETED {len(orphaned)} orphaned records.'
                        ))
                else:
                    self.stdout.write(self.style.SUCCESS('  All local payments match Xero ✓'))

                # Check for missing Xero payments
                local_pids = set(
                    p.anthill_payment_id for p in local_xero_payments if p.anthill_payment_id
                )
                missing_from_local = []
                for inv in invoice_data:
                    for p in inv['payments']:
                        pid = p.get('payment_id', '')
                        if pid and pid not in local_pids:
                            missing_from_local.append((inv, p))

                if missing_from_local:
                    self.stdout.write(self.style.WARNING(
                        f'  {len(missing_from_local)} Xero payment(s) missing from local DB:'
                    ))
                    for inv, p in missing_from_local:
                        date_str = p['date'].strftime('%d/%m/%Y') if p['date'] else '?'
                        self.stdout.write(
                            f'    {inv["invoice_number"]:<10} '
                            f'£{p["amount"]:<10} '
                            f'{date_str:<12} '
                            f'{p["reference"]}'
                        )
                    if fix or resync:
                        self.stdout.write('  Will be picked up by --resync.')

            # Resync related sales
            if resync and fix and sale.customer:
                related = sale.customer.anthill_sales.filter(
                    contract_number__startswith='BFS'
                ).exclude(contract_number='')
                for rs in related:
                    self.stdout.write(self.style.NOTICE(
                        f'\n  Resyncing {rs.contract_number} (Activity: {rs.anthill_activity_id})...'
                    ))
                    try:
                        inv_data = xero_api.get_sale_payments_from_xero(
                            contract_number=rs.contract_number,
                            contact_name=None,
                        )
                        if not inv_data:
                            self.stdout.write('    No Xero invoices.')
                            continue

                        created = 0
                        updated = 0
                        for inv in inv_data:
                            if inv.get('status', '').upper() in ('CANCELLED', 'VOIDED', 'DELETED'):
                                continue
                            for p in inv['payments']:
                                if p.get('status', '').upper() == 'CANCELLED':
                                    continue
                                pid = p.get('payment_id') or ''
                                defaults = {
                                    'source': 'xero',
                                    'xero_invoice_id': inv['invoice_id'],
                                    'xero_invoice_number': inv['invoice_number'],
                                    'invoice_total': inv['total'],
                                    'invoice_amount_due': inv['amount_due'],
                                    'invoice_status': inv['status'],
                                    'payment_type': p['reference'] or 'Payment',
                                    'date': p['date'],
                                    'amount': p['amount'],
                                    'status': p['status'],
                                    'location': '',
                                    'user_name': '',
                                }
                                if pid:
                                    obj, was_created = AnthillPayment.objects.update_or_create(
                                        sale=rs,
                                        anthill_payment_id=pid,
                                        defaults=defaults,
                                    )
                                else:
                                    obj, was_created = AnthillPayment.objects.update_or_create(
                                        sale=rs,
                                        xero_invoice_id=inv['invoice_id'],
                                        date=p['date'],
                                        defaults=defaults,
                                    )
                                if was_created:
                                    created += 1
                                else:
                                    updated += 1

                        self.stdout.write(self.style.SUCCESS(
                            f'    Created {created}, updated {updated} payment records.'
                        ))
                        from stock_take.customer_views import _recalculate_sale_financials
                        _recalculate_sale_financials(rs)

                    except Exception as exc:
                        self.stderr.write(f'    ERROR: {exc}')
