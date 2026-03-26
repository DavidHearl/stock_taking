"""
Management command to sync new/updated customers from Anthill CRM
created within the last 7 days, and to promote any existing Leads
that have acquired a qualifying sale activity since they were first synced.

Anthill is the source of truth.  Two passes run on every execution:

  Pass 1 — Recent scan
    Fetches all customers created within the lookback window from Anthill.
    New customers with a sale activity are saved as Customer; others as Lead.
    Customers already in the database are passed to _update_existing() for a
    location update (+ promotion if they appeared in Anthill recently).

  Pass 2 — Lead upgrade
    Iterates every Lead in the database that has an Anthill Customer ID and
    has not yet been converted.  Re-fetches the full Anthill detail for each
    and promotes the Lead to a Customer if a qualifying sale activity is found.
    This catches the common case where a Lead was created months ago but has
    only recently received a sale in Anthill — those records never re-appear
    in the recent scan window.

Designed to be run on a schedule (08:00 & 12:00 daily) to keep the
local database up to date with Anthill.

Usage:
    python manage.py sync_recent_customers                          # Scan 7 days + upgrade leads (last 90 days)
    python manage.py sync_recent_customers --days 14                # Scan last 14 days + upgrade leads
    python manage.py sync_recent_customers --upgrade-days 180       # Upgrade leads from last 180 days
    python manage.py sync_recent_customers --upgrade-days 0         # Upgrade ALL leads (slow — use sparingly)
    python manage.py sync_recent_customers --skip-upgrade           # Scan only, skip lead upgrade
    python manage.py sync_recent_customers --dry-run                # Preview without saving
"""

import time
import logging
from datetime import timedelta
from django.core.management.base import BaseCommand
from django.utils import timezone
from stock_take.services.anthill_api import AnthillAPI, AnthillAPIError
from stock_take.models import Customer, Lead, AnthillSale, SyncLog

logger = logging.getLogger(__name__)

# Activity types that indicate a sale (any status counts — open, completed, etc.)
SALE_ACTIVITY_TYPES_KEYWORDS = {'sale'}
# Statuses that should NOT count (e.g. cancelled/lost sales)
SALE_EXCLUDE_STATUSES = {'cancelled', 'canceled', 'lost', 'deleted'}


class Command(BaseCommand):
    help = 'Sync new customers from Anthill CRM (last 7 days by default)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--days',
            type=int,
            default=7,
            help='Number of days to look back (default: 7)',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Preview what would be synced without saving to database',
        )
        parser.add_argument(
            '--skip-upgrade',
            action='store_true',
            help='Skip Pass 2 (lead upgrade check) — only scan for new records',
        )
        parser.add_argument(
            '--upgrade-days',
            type=int,
            default=90,
            help='Pass 2: only check leads created within this many days (default: 90). Use 0 for all leads.',
        )

    def handle(self, *args, **options):
        days = options['days']
        dry_run = options['dry_run']
        skip_upgrade = options['skip_upgrade']
        upgrade_days = options['upgrade_days']
        start_time = time.time()

        self.stdout.write(f'\n{"=" * 60}')
        self.stdout.write(f'  Anthill Recent Customer Sync')
        self.stdout.write(f'  Looking back: {days} days')
        self.stdout.write(f'  Started: {timezone.now().strftime("%Y-%m-%d %H:%M:%S")}')
        if dry_run:
            self.stdout.write(self.style.WARNING('  Mode: DRY RUN'))
        self.stdout.write(f'{"=" * 60}\n')

        try:
            api = AnthillAPI()
        except AnthillAPIError as e:
            self.stderr.write(self.style.ERROR(f'Failed to initialise Anthill API: {e}'))
            if not dry_run:
                SyncLog.objects.create(
                    script_name='sync_recent_customers',
                    status='error',
                    errors=1,
                    notes=f'Failed to initialise Anthill API: {e}',
                )
            return

        # Pre-load existing Anthill IDs for fast duplicate checking
        existing_customer_ids = set(
            Customer.objects.exclude(anthill_customer_id='')
            .exclude(anthill_customer_id__isnull=True)
            .values_list('anthill_customer_id', flat=True)
        )
        existing_lead_ids = set(
            Lead.objects.exclude(anthill_customer_id='')
            .exclude(anthill_customer_id__isnull=True)
            .values_list('anthill_customer_id', flat=True)
        )
        existing_all = existing_customer_ids | existing_lead_ids
        self.stdout.write(f'  Known records in DB: {len(existing_all):,}')

        # Fetch customers created in the last N days
        self.stdout.write(f'\nFetching customers from last {days} days...')

        stats = {
            'scanned': 0,
            'skipped_existing': 0,
            'customers_created': 0,
            'customers_updated': 0,
            'leads_created': 0,
            'leads_updated': 0,
            'leads_upgraded': 0,
            'sales_created': 0,
            'errors': 0,
        }

        page = 1
        page_size = 200

        while True:
            try:
                result = api.find_customers(page=page, page_size=page_size, days_back=days)
            except AnthillAPIError as e:
                self.stderr.write(self.style.ERROR(f'API error on page {page}: {e}'))
                stats['errors'] += 1
                break

            total_records = result['total_records']
            total_pages = result['total_pages']
            customers = result['customers']

            if page == 1:
                self.stdout.write(f'  Found {total_records:,} customers across {total_pages} pages\n')

            if not customers:
                break

            for summary in customers:
                stats['scanned'] += 1
                anthill_id = str(summary['id'])

                # Skip already-known records — but still update them
                is_existing = anthill_id in existing_all

                if is_existing:
                    stats['skipped_existing'] += 1
                    # Still update existing records with latest data
                    if not dry_run:
                        try:
                            self._update_existing(api, anthill_id, summary)
                        except Exception as e:
                            logger.debug(f'Update skipped for {anthill_id}: {e}')
                    continue

                if dry_run:
                    self.stdout.write(f'  [DRY RUN] New: {summary["name"]} (#{anthill_id})')
                    continue

                # Fetch full details and save
                try:
                    details = api.get_customer_details(int(anthill_id), include_activity=True)
                    time.sleep(0.05)  # Rate limiting

                    if not details:
                        stats['errors'] += 1
                        continue

                    # Classify: customer (sale) or lead
                    # A customer is a sale if they have a completed sale activity
                    if self._is_sale(details):
                        result_action = self._save_customer(details, summary)
                        if result_action == 'created':
                            stats['customers_created'] += 1
                            self.stdout.write(
                                self.style.SUCCESS(f'  + Customer: {details["name"]} (#{anthill_id})')
                            )
                        elif result_action == 'updated':
                            stats['customers_updated'] += 1
                        # Save sale activities
                        customer_obj = Customer.objects.filter(
                            anthill_customer_id=anthill_id
                        ).first()
                        sales_count = self._save_sales(details, summary, customer_obj)
                        stats['sales_created'] += sales_count
                    else:
                        # No sale indicators — save as Lead
                        result_action = self._save_lead(details, summary)
                        if result_action == 'created':
                            stats['leads_created'] += 1
                            self.stdout.write(
                                self.style.SUCCESS(f'  + Lead: {details["name"]} (#{anthill_id})')
                            )
                        elif result_action == 'updated':
                            stats['leads_updated'] += 1

                    existing_all.add(anthill_id)

                except AnthillAPIError as e:
                    stats['errors'] += 1
                    self.stderr.write(f'  Error fetching #{anthill_id}: {e}')
                except Exception as e:
                    stats['errors'] += 1
                    self.stderr.write(self.style.ERROR(f'  Error on #{anthill_id}: {e}'))

            self.stdout.write(
                f'  Page {page}/{total_pages} | '
                f'Scanned: {stats["scanned"]:,} | '
                f'New: {stats["customers_created"] + stats["leads_created"]} | '
                f'Existing: {stats["skipped_existing"]}'
            )

            if page >= total_pages:
                break
            page += 1

        # ════════════════════════════════════════════════════════
        # Pass 2 — Upgrade existing Leads that now have a sale
        # Runs over ALL leads regardless of when they were created,
        # catching records that fall outside the scan window above.
        # ════════════════════════════════════════════════════════
        if not skip_upgrade:
            leads_qs = (
                Lead.objects
                .exclude(anthill_customer_id='')
                .exclude(anthill_customer_id__isnull=True)
                .exclude(status='converted')
            )
            if upgrade_days > 0:
                cutoff = timezone.now() - timedelta(days=upgrade_days)
                leads_qs = leads_qs.filter(anthill_created_date__gte=cutoff)
            leads_to_check = list(leads_qs.values_list('pk', 'anthill_customer_id', 'name'))

            upgrade_window = f'last {upgrade_days} days' if upgrade_days > 0 else 'all time'
            self.stdout.write(f'\nPass 2 - checking {len(leads_to_check):,} leads for promotion ({upgrade_window})...')

            pass2_checked = 0
            pass2_start = time.time()

            for lead_pk, anthill_id, lead_name in leads_to_check:
                pass2_checked += 1

                # Progress heartbeat every 500 leads
                if pass2_checked % 500 == 0 or pass2_checked == len(leads_to_check):
                    elapsed2 = time.time() - pass2_start
                    rate = pass2_checked / elapsed2 if elapsed2 > 0 else 0
                    remaining = (len(leads_to_check) - pass2_checked) / rate if rate > 0 else 0
                    eta_str = f'{int(remaining // 60)}m {int(remaining % 60)}s' if remaining > 0 else '--'
                    self.stdout.write(
                        f'  [{pass2_checked:,}/{len(leads_to_check):,}] '
                        f'upgraded={stats["leads_upgraded"]} '
                        f'rate={rate:.0f}/s eta={eta_str}'
                    )

                try:
                    details = api.get_customer_details(int(anthill_id), include_activity=True)
                    time.sleep(0.05)
                except AnthillAPIError as e:
                    stats['errors'] += 1
                    self.stderr.write(f'  [ERROR] API error for lead #{anthill_id}: {e}')
                    continue
                except Exception as e:
                    stats['errors'] += 1
                    self.stderr.write(self.style.ERROR(f'  [ERROR] Error for lead #{anthill_id}: {e}'))
                    continue

                if not details or not self._is_sale(details):
                    continue

                self.stdout.write(
                    self.style.SUCCESS(f'  [PROMOTED] Lead -> Customer: {lead_name} (#{anthill_id})')
                )

                if dry_run:
                    stats['leads_upgraded'] += 1
                    continue

                result_action = self._save_customer(
                    details,
                    {'id': anthill_id, 'name': lead_name, 'location': '', 'created': ''},
                )
                new_customer = Customer.objects.filter(anthill_customer_id=anthill_id).first()
                if not new_customer:
                    continue

                # Save Anthill sale activities
                sales_count = self._save_sales(
                    details,
                    {'id': anthill_id, 'name': lead_name, 'location': ''},
                    new_customer,
                )
                stats['sales_created'] += sales_count

                # Link any pre-existing AnthillSale records (customer=None)
                AnthillSale.objects.filter(
                    anthill_customer_id=anthill_id,
                    customer__isnull=True,
                ).update(customer=new_customer)

                # Mark lead as converted
                Lead.objects.filter(pk=lead_pk).update(
                    status='converted',
                    converted_to_customer=new_customer,
                )
                stats['leads_upgraded'] += 1

        # Summary
        elapsed = time.time() - start_time
        self.stdout.write(f'\n{"=" * 60}')
        self.stdout.write(self.style.SUCCESS('  SYNC COMPLETE'))
        self.stdout.write(f'{"=" * 60}')
        self.stdout.write(f'  Time elapsed       : {elapsed:.1f}s')
        self.stdout.write(f'  Records scanned    : {stats["scanned"]:,}')
        self.stdout.write(f'  Skipped (existing) : {stats["skipped_existing"]:,}')
        self.stdout.write(f'  Customers created  : {stats["customers_created"]:,}')
        self.stdout.write(f'  Customers updated  : {stats["customers_updated"]:,}')
        self.stdout.write(f'  Leads created      : {stats["leads_created"]:,}')
        self.stdout.write(f'  Leads updated      : {stats["leads_updated"]:,}')
        if stats['leads_upgraded']:
            self.stdout.write(self.style.SUCCESS(f'  Leads upgraded     : {stats["leads_upgraded"]:,}'))
        self.stdout.write(f'  Sales created      : {stats["sales_created"]:,}')
        if stats['errors']:
            self.stdout.write(self.style.ERROR(f'  Errors             : {stats["errors"]:,}'))
        self.stdout.write(f'{"=" * 60}\n')

        # Write SyncLog entry so the admin API page shows run history
        if not dry_run:
            log_status = 'success'
            if stats['errors'] > 0:
                total_created = (
                    stats['customers_created'] + stats['leads_created']
                    + stats['leads_upgraded'] + stats['sales_created']
                )
                log_status = 'warning' if total_created > 0 else 'error'
            notes_text = (
                f"Scanned {stats['scanned']}, "
                f"customers created {stats['customers_created']}, updated {stats['customers_updated']}, "
                f"leads created {stats['leads_created']}, updated {stats['leads_updated']}, "
                f"leads upgraded {stats['leads_upgraded']}, "
                f"sales created {stats['sales_created']}, errors {stats['errors']}. "
                f"Lookback: {days} days."
            )
            SyncLog.objects.create(
                script_name='sync_recent_customers',
                status=log_status,
                records_created=(
                    stats['customers_created'] + stats['leads_created']
                    + stats['leads_upgraded'] + stats['sales_created']
                ),
                records_updated=stats['customers_updated'] + stats['leads_updated'],
                errors=stats['errors'],
                notes=notes_text,
            )
            self.stdout.write(self.style.SUCCESS('  SyncLog entry written.'))

        if dry_run:
            new_count = stats['scanned'] - stats['skipped_existing']
            self.stdout.write(self.style.WARNING(
                f'  DRY RUN: {new_count:,} new records would be imported, '
                f'{stats["leads_upgraded"]} leads would be promoted.'
            ))

    @staticmethod
    def _is_sale(details: dict) -> bool:
        """Determine if a customer detail represents a sale (vs lead).

        A customer is considered a sale if they have ANY sale-type activity,
        regardless of status (open, completed, etc.).  Only explicitly
        cancelled/lost sales are excluded.
        """
        for act in details.get('activities', []):
            status = (act.get('status') or '').lower()
            act_type = (act.get('type') or '').lower()
            # Skip cancelled/lost sales
            if status in SALE_EXCLUDE_STATUSES:
                continue
            if any(kw in act_type for kw in SALE_ACTIVITY_TYPES_KEYWORDS):
                return True

        return False

    def _save_customer(self, details: dict, summary: dict) -> str:
        """Save Anthill customer as a Customer record. Returns 'created', 'updated', or 'exists'."""
        anthill_id = str(details.get('customer_id', summary['id']))
        cf = details.get('custom_fields', {})
        addr = details.get('address', {})

        existing = Customer.objects.filter(anthill_customer_id=anthill_id).first()
        if existing:
            return 'exists'

        # Build address string
        address_parts = [addr.get('address1', ''), addr.get('city', ''), addr.get('postcode', '')]
        address_str = ', '.join(p for p in address_parts if p)

        Customer.objects.create(
            anthill_customer_id=anthill_id,
            first_name=cf.get('First Name', ''),
            last_name=cf.get('Last Name', ''),
            name=details.get('name', '') or summary.get('name', ''),
            email=cf.get('Email', '') or None,
            phone=cf.get('Telephone', '') or None,
            address_1=addr.get('address1', '') or None,
            address_2=addr.get('address2', '') or None,
            city=addr.get('city', '') or None,
            state=addr.get('county', '') or None,
            postcode=addr.get('postcode', '') or '',
            country=addr.get('country', '') or None,
            address=address_str,
            location=summary.get('location', ''),
            is_active=True,
        )
        return 'created'

    def _save_lead(self, details: dict, summary: dict) -> str:
        """Save Anthill customer as a Lead record. Returns 'created', 'updated', or 'exists'."""
        anthill_id = str(details.get('customer_id', summary['id']))
        cf = details.get('custom_fields', {})
        addr = details.get('address', {})

        existing = Lead.objects.filter(anthill_customer_id=anthill_id).first()
        if existing:
            return 'exists'

        Lead.objects.create(
            anthill_customer_id=anthill_id,
            name=details.get('name', '') or summary.get('name', ''),
            email=cf.get('Email', '') or None,
            phone=cf.get('Telephone', '') or None,
            mobile=cf.get('Mobile', '') or None,
            address_1=addr.get('address1', '') or None,
            address_2=addr.get('address2', '') or None,
            city=addr.get('city', '') or None,
            state=addr.get('county', '') or None,
            postcode=addr.get('postcode', '') or None,
            country=addr.get('country', '') or None,
            location=summary.get('location', ''),
            status='new',
            source='anthill',
        )
        return 'created'

    @staticmethod
    def _save_sales(details: dict, summary: dict, customer_obj=None) -> int:
        """Save sale activities from a customer's detail record as AnthillSale objects.
        Returns the number of sales created."""
        from datetime import datetime, timezone as _tz

        created_count = 0
        anthill_customer_id = str(details.get('customer_id') or summary.get('id', ''))
        customer_name = details.get('name', '') or summary.get('name', '')
        location = summary.get('location', '')

        for act in details.get('activities', []):
            act_id = act.get('id', '')
            if not act_id:
                continue

            if AnthillSale.objects.filter(anthill_activity_id=act_id).exists():
                continue

            # Parse activity date
            activity_date = None
            created_str = act.get('created', '')
            if created_str:
                for fmt in ('%Y-%m-%dT%H:%M:%S.%f', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d'):
                    try:
                        naive = datetime.strptime(created_str, fmt)
                        activity_date = naive.replace(tzinfo=_tz.utc)
                        break
                    except ValueError:
                        continue

            local_customer = customer_obj
            if not local_customer and anthill_customer_id:
                local_customer = Customer.objects.filter(
                    anthill_customer_id=anthill_customer_id
                ).first()

            AnthillSale.objects.create(
                anthill_activity_id=act_id,
                anthill_customer_id=anthill_customer_id,
                customer=local_customer,
                activity_type=act.get('type', ''),
                status=act.get('status', ''),
                category=act.get('category', ''),
                customer_name=customer_name,
                location=location,
                activity_date=activity_date,
            )
            created_count += 1

        return created_count

    def _update_existing(self, api: AnthillAPI, anthill_id: str, summary: dict):
        """Update existing records — location, and promote Lead → Customer if
        the person now has a completed sale activity."""
        location = summary.get('location', '')

        # Update Customer location if missing
        customer = Customer.objects.filter(anthill_customer_id=anthill_id).first()
        if customer:
            if location and not customer.location:
                customer.location = location
                customer.save(update_fields=['location'])
            return  # already a customer — nothing more to do

        # Check if this Lead should be promoted to Customer
        lead = Lead.objects.filter(anthill_customer_id=anthill_id).first()
        if not lead:
            return

        # Update lead location
        if location and not lead.location:
            lead.location = location
            lead.save(update_fields=['location'])

        # Re-fetch activities to check for sale promotion
        try:
            details = api.get_customer_details(int(anthill_id), include_activity=True)
            time.sleep(0.05)
        except Exception:
            return

        if not details:
            return

        if self._is_sale(details):
            # Promote: create Customer and migrate the Lead
            result_action = self._save_customer(details, summary)
            if result_action == 'created':
                self.stdout.write(
                    self.style.SUCCESS(
                        f'  [PROMOTED] Lead -> Customer: {lead.name} (#{anthill_id})'
                    )
                )
                new_customer = Customer.objects.filter(
                    anthill_customer_id=anthill_id
                ).first()
                self._save_sales(details, summary, new_customer)
                # Link any pre-existing AnthillSale records (customer=None)
                AnthillSale.objects.filter(
                    anthill_customer_id=anthill_id,
                    customer__isnull=True,
                ).update(customer=new_customer)
                # Mark lead as converted
                lead.status = 'converted'
                lead.converted_to_customer = new_customer
                lead.save(update_fields=['status', 'converted_to_customer'])
