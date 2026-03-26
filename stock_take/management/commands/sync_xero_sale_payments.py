"""
Management command: sync_xero_sale_payments
────────────────────────────────────────────
Fetches payment data from Xero for Anthill sales by matching the sale's
contract number against the Xero invoice Reference field (read-only).

For each AnthillSale with a contract_number (e.g. "BFS-SD-412885"),
this command:

  1. Queries Xero: GET /Invoices?where=Reference="<contract_number>"
  2. For each matching invoice, fetches the full detail (including Payments[])
  3. Optionally cross-checks the Xero contact name against the sale's customer name
  4. Creates/updates AnthillPayment records with source='xero'

No data is written to Xero — this is entirely read-only.

Prerequisites:
  - Xero must be connected (go to /xero/status/ and authorize)
  - Sales must have a contract_number populated (run sync_anthill_workflow first)

Usage:
    python manage.py sync_xero_sale_payments                      # All Category 3 outstanding sales
    python manage.py sync_xero_sale_payments --days 180           # Outstanding sales active within last 180 days
    python manage.py sync_xero_sale_payments --sale-id 417437     # Single sale by Anthill activity ID
    python manage.py sync_xero_sale_payments --dry-run            # Preview without saving
    python manage.py sync_xero_sale_payments --no-name-check      # Skip contact name validation
    python manage.py sync_xero_sale_payments --include-paid       # Also re-check fully-paid sales
"""

import logging
import time
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import close_old_connections
from django.utils import timezone
from django.db.models import Q, Exists, OuterRef

from stock_take.models import AnthillSale, AnthillPayment, SyncLog
from stock_take.services import xero_api

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Sync payment history for Anthill sales from Xero invoices (read-only)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--days',
            type=int,
            default=None,
            help='Only sync sales active/updated within this many days',
        )
        parser.add_argument(
            '--sale-id',
            type=str,
            default=None,
            dest='sale_id',
            help='Sync a single sale by its Anthill activity ID',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Fetch data from Xero but do not write to the database',
        )
        parser.add_argument(
            '--no-name-check',
            action='store_true',
            dest='no_name_check',
            help='Skip contact name cross-validation (match by reference only)',
        )
        parser.add_argument(
            '--include-paid',
            action='store_true',
            dest='include_paid',
            help='Also re-check sales whose Xero invoice is already fully paid (default: skip them)',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        days = options['days']
        sale_id = options['sale_id']
        no_name_check = options['no_name_check']
        include_paid = options['include_paid']

        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN - no changes will be written.\n'))

        # Check Xero connection
        access_token, _ = xero_api.get_valid_access_token()
        if not access_token:
            self.stderr.write(self.style.ERROR(
                'No valid Xero connection.\n'
                'Please connect via /xero/status/ first.'
            ))
            if not dry_run:
                SyncLog.objects.create(
                    script_name='sync_xero_sale_payments',
                    status='error',
                    errors=1,
                    notes='No valid Xero connection — token missing or expired.',
                )
            return

        # Build queryset
        if sale_id:
            qs = AnthillSale.objects.filter(anthill_activity_id=sale_id)
            if not qs.exists():
                self.stderr.write(self.style.ERROR(
                    f'No sale found with activity ID "{sale_id}"'
                ))
                return
            sales_list = list(
                qs.values_list('pk', 'anthill_activity_id', 'customer_name', 'contract_number')
            )
            pass_labels = [('Single sale', sales_list)]
        else:
            # Base queryset: Belfast Category 3 sales with a contract number
            base_qs = (
                AnthillSale.objects
                .filter(category='3')
                .filter(contract_number__startswith='BFS')
                .exclude(contract_number='')
                .exclude(contract_number__isnull=True)
            )
            if days:
                cutoff = timezone.now() - timedelta(days=days)
                base_qs = base_qs.filter(
                    Q(activity_date__gte=cutoff) | Q(updated_at__gte=cutoff)
                )

            has_any_xero = Exists(
                AnthillPayment.objects.filter(
                    sale=OuterRef('pk'),
                    source='xero',
                )
            )
            has_outstanding = Exists(
                AnthillPayment.objects.filter(
                    sale=OuterRef('pk'),
                    source='xero',
                    invoice_amount_due__gt=0,
                )
            )
            has_stale_placeholder = Exists(
                AnthillPayment.objects.filter(
                    sale=OuterRef('pk'),
                    source='xero',
                    payment_type='Invoice Payment',
                    amount=0,
                )
            )

            # PASS 0: Sales with stale £0 "Invoice Payment" placeholder records.
            # These indicate invoices found in Xero that were paid via
            # overpayments/credits but the individual payment items were never
            # imported.  Always resync these regardless of paid_in_full.
            pass0_qs = base_qs.filter(has_stale_placeholder)
            pass0_list = list(
                pass0_qs.values_list('pk', 'anthill_activity_id', 'customer_name', 'contract_number')
            )

            # PASS 1: Sales that already have Xero payment records
            # (customers who have paid something — check for updates)
            if include_paid:
                pass1_qs = base_qs.filter(has_any_xero)
            else:
                # Include sales with outstanding Xero invoices, PLUS sales that
                # have Xero records but aren't marked paid_in_full yet.
                # This catches the "dead zone" where Xero invoice shows PAID
                # (amount_due=0) but individual payment records are incomplete.
                pass1_qs = base_qs.filter(has_any_xero).filter(
                    Q(Exists(
                        AnthillPayment.objects.filter(
                            sale=OuterRef('pk'),
                            source='xero',
                            invoice_amount_due__gt=0,
                        )
                    )) | Q(paid_in_full=False)
                )

            pass1_list = list(
                pass1_qs.exclude(pk__in=[r[0] for r in pass0_list])
                .values_list('pk', 'anthill_activity_id', 'customer_name', 'contract_number')
            )

            # PASS 2: Sales with no Xero records yet (discover new invoices)
            pass2_qs = base_qs.filter(~has_any_xero)
            pass2_list = list(
                pass2_qs.values_list('pk', 'anthill_activity_id', 'customer_name', 'contract_number')
            )

            pass_labels = []
            if pass0_list:
                pass_labels.append(('Pass 0 - Stale placeholders (resync overpayments)', pass0_list))
            if pass1_list:
                pass_labels.append(('Pass 1 - Existing Xero payments (update)', pass1_list))
            if pass2_list:
                pass_labels.append(('Pass 2 - New sales (discover invoices)', pass2_list))

            sales_list = pass0_list + pass1_list + pass2_list

        total = len(sales_list)

        if total == 0:
            self.stdout.write(self.style.WARNING(
                'No sales match the criteria. '
                'Make sure Category 3 sales with a BFS-* contract number exist '
                '(run sync_anthill_workflow first if needed). '
                + ('' if include_paid else 'All matching sales may already be fully paid - use --include-paid to re-check them.')
            ))
            if not dry_run:
                SyncLog.objects.create(
                    script_name='sync_xero_sale_payments',
                    status='success',
                    notes='No sales matched the criteria — nothing to process.',
                )
            return

        self.stdout.write(f'Sales to process: {total}\n')
        for label, plist in pass_labels:
            self.stdout.write(f'  {label}: {len(plist)} sales')
        self.stdout.write('')

        # ── Pre-fetch all BFS invoices in bulk ──────────────────────────
        # Instead of 1 API call per sale to search by reference, we fetch
        # ALL BFS invoices upfront (~10-20 paginated calls) and look up
        # contract numbers locally.  This saves ~700+ API calls per run.
        reference_index = {}
        if not sale_id:
            self.stdout.write('Fetching invoice index from Xero (bulk)...')
            try:
                all_bfs_invoices = xero_api.bulk_fetch_invoices(search_prefix="BFS")
                reference_index = xero_api.build_reference_index(all_bfs_invoices)
                self.stdout.write(self.style.SUCCESS(
                    f'  Indexed {len(all_bfs_invoices)} invoices '
                    f'({len(reference_index)} unique contract refs) '
                    f'[{xero_api.get_api_call_count()} API calls used]'
                ))
            except xero_api.XeroDailyLimitExceeded as exc:
                self.stderr.write(self.style.ERROR(f'\n{exc}'))
                if not dry_run:
                    SyncLog.objects.create(
                        script_name='sync_xero_sale_payments',
                        status='error',
                        errors=1,
                        notes=f'Xero daily API limit exceeded during bulk fetch: {exc}',
                    )
                return
        self.stdout.write('')

        stats = {
            'sales_with_invoices': 0,
            'invoices_found': 0,
            'payments_created': 0,
            'payments_updated': 0,
            'no_invoice': 0,
            'errors': 0,
        }
        error_notes = []
        global_idx = 0
        daily_limit_hit = False

        for pass_label, pass_list in pass_labels:
            if daily_limit_hit:
                break
            self.stdout.write(self.style.NOTICE(f'\n-- {pass_label} ({len(pass_list)} sales) --'))
            for idx, (sale_pk, activity_id, cust_name, contract_number) in enumerate(pass_list, start=1):
                global_idx += 1
                prefix = f'  [{global_idx}/{total}] {activity_id} - {contract_number}'
                self.stdout.write(prefix, ending='\r')

                # Use pre-fetched index if available (0 API calls for the search step)
                prefetched = reference_index.get(contract_number.upper()) if reference_index else None

                try:
                    invoice_data = xero_api.get_sale_payments_from_xero(
                        contract_number=contract_number,
                        contact_name=None if no_name_check else cust_name,
                        prefetched_invoices=prefetched,
                    )
                except xero_api.XeroDailyLimitExceeded as exc:
                    self.stderr.write(self.style.ERROR(f'\n{exc}'))
                    self.stderr.write(self.style.WARNING(
                        f'Processed {global_idx - 1}/{total} sales before limit was reached.'
                    ))
                    daily_limit_hit = True
                    break
                except Exception as exc:
                    self.stderr.write(self.style.ERROR(
                        f'\n  ERROR {activity_id} ({contract_number}): {exc}'
                    ))
                    stats['errors'] += 1
                    error_notes.append(f'{activity_id}: {exc}')
                    time.sleep(1)
                    continue

                if not invoice_data:
                    stats['no_invoice'] += 1
                    time.sleep(1.0)
                    continue

                # Found at least one invoice — always print this line
                inv_count = sum(len(inv['payments']) for inv in invoice_data)
                inv_nums = ', '.join(inv['invoice_number'] for inv in invoice_data)
                self.stdout.write(self.style.SUCCESS(
                    f'{prefix} -> {inv_nums} ({inv_count} payment(s))'
                ))
                stats['sales_with_invoices'] += 1
                stats['invoices_found'] += len(invoice_data)

                if dry_run:
                    for inv in invoice_data:
                        self.stdout.write(
                            f'    {inv["invoice_number"]} | {inv["status"]} | '
                            f'Total £{inv["total"]} | Paid £{inv["amount_paid"]} | '
                            f'Due £{inv["amount_due"]} | Contact: {inv["contact_name"]}'
                        )
                        for p in inv['payments']:
                            date_str = p['date'].strftime('%d/%m/%Y') if p['date'] else '?'
                            self.stdout.write(
                                f'      Payment: £{p["amount"]} on {date_str} - {p["reference"]}'
                            )
                    time.sleep(0.25)
                    continue

                # Write to DB
                close_old_connections()
                try:
                    sale = AnthillSale.objects.get(pk=sale_pk)
                except AnthillSale.DoesNotExist:
                    continue

                for inv in invoice_data:
                    # Skip cancelled/voided invoices — nothing to record
                    if inv.get('status', '').upper() in ('CANCELLED', 'VOIDED', 'DELETED'):
                        self.stdout.write(f'    Skipping {inv["invoice_number"]} (status: {inv["status"]})')
                        continue

                    for p in inv['payments']:
                        # Skip cancelled individual payments
                        if p.get('status', '').upper() == 'CANCELLED':
                            continue
                        pid = p.get('payment_id') or ''
                        base_pid = p.get('base_payment_id') or ''
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
                            # For compound IDs (e.g. overpayment_id + invoice_id),
                            # check if an old record exists with just the base ID
                            # from a previous sync format.  Migrate it to the new key.
                            if base_pid and pid != base_pid:
                                old_rec = AnthillPayment.objects.filter(
                                    sale=sale, anthill_payment_id=base_pid,
                                ).first()
                                if old_rec:
                                    old_rec.anthill_payment_id = pid
                                    old_rec.save(update_fields=['anthill_payment_id'])

                            obj, created = AnthillPayment.objects.update_or_create(
                                sale=sale,
                                anthill_payment_id=pid,
                                defaults=defaults,
                            )
                        else:
                            # No payment ID — use invoice ID + date as surrogate key
                            obj, created = AnthillPayment.objects.update_or_create(
                                sale=sale,
                                xero_invoice_id=inv['invoice_id'],
                                date=p['date'],
                                defaults=defaults,
                            )

                        if created:
                            stats['payments_created'] += 1
                        else:
                            stats['payments_updated'] += 1

                    # If invoice has no individual payments, record an invoice-level summary row
                    # so we always capture invoice_total / invoice_amount_due even for
                    # AUTHORISED invoices that haven't been paid yet.
                    if not inv['payments']:
                        defaults = {
                            'source': 'xero',
                            'xero_invoice_id': inv['invoice_id'],
                            'xero_invoice_number': inv['invoice_number'],
                            'invoice_total': inv['total'],
                            'invoice_amount_due': inv['amount_due'],
                            'invoice_status': inv['status'],
                            'payment_type': 'Invoice Payment',
                            'date': None,
                            'amount': inv['amount_paid'],
                            'status': inv['status'],
                            'location': '',
                            'user_name': '',
                        }
                        obj, created = AnthillPayment.objects.update_or_create(
                            sale=sale,
                            xero_invoice_id=inv['invoice_id'],
                            date=None,
                            defaults=defaults,
                        )
                        if created:
                            stats['payments_created'] += 1
                        else:
                            stats['payments_updated'] += 1
                    else:
                        # Real payments found — clean up any stale £0 "Invoice Payment"
                        # fallback records that were created before individual payments
                        # were discovered.
                        stale_deleted, _ = AnthillPayment.objects.filter(
                            sale=sale,
                            xero_invoice_id=inv['invoice_id'],
                            payment_type='Invoice Payment',
                            amount=0,
                            anthill_payment_id='',
                        ).delete()
                        if stale_deleted:
                            stats['stale_cleaned'] = stats.get('stale_cleaned', 0) + stale_deleted

                # Recalculate paid_in_full for this sale after syncing payments
                # Uses the same credit-matching logic as the UI to stay consistent
                if not dry_run:
                    from stock_take.customer_views import _recalculate_sale_financials
                    _recalculate_sale_financials(sale)

                time.sleep(0.3)  # brief pause between sales; rate limiter in xero_api handles throttling

        # Final summary
        self.stdout.write('')
        api_calls = xero_api.get_api_call_count()
        self.stdout.write(self.style.SUCCESS(
            f'\nDone.\n'
            f'  Sales processed       : {global_idx}/{total}\n'
            f'  Sales with invoices   : {stats["sales_with_invoices"]}\n'
            f'  Sales without invoices: {stats["no_invoice"]}\n'
            f'  Invoices found        : {stats["invoices_found"]}\n'
            f'  Payments created      : {stats["payments_created"]}\n'
            f'  Payments updated      : {stats["payments_updated"]}\n'
            f'  Stale records cleaned : {stats.get("stale_cleaned", 0)}\n'
            f'  Errors                : {stats["errors"]}\n'
            f'  Xero API calls used   : {api_calls}'
        ))

        if not dry_run:
            log_status = (
                'success' if stats['errors'] == 0
                else ('error' if stats['invoices_found'] == 0 else 'warning')
            )
            notes = (
                f"Processed {total} sales. "
                f"Found invoices for {stats['sales_with_invoices']}. "
                f"Created {stats['payments_created']}, "
                f"updated {stats['payments_updated']} payment records."
            )
            if error_notes:
                notes += ' Errors: ' + '; '.join(error_notes[:5])
                if len(error_notes) > 5:
                    notes += f' (+{len(error_notes) - 5} more)'

            SyncLog.objects.create(
                script_name='sync_xero_sale_payments',
                status=log_status,
                records_created=stats['payments_created'],
                records_updated=stats['payments_updated'],
                errors=stats['errors'],
                notes=notes,
            )
            self.stdout.write(f'SyncLog entry written (status={log_status}).')
