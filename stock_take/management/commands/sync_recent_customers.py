"""
Management command to sync new/updated customers from Anthill CRM
created within the last 7 days.

Designed to be run on a schedule (08:00 & 12:00 daily) to keep the
local database up to date with recent Anthill additions.

Usage:
    python manage.py sync_recent_customers                # Sync last 7 days
    python manage.py sync_recent_customers --days 14      # Sync last 14 days
    python manage.py sync_recent_customers --dry-run      # Preview without saving
"""

import time
import logging
from django.core.management.base import BaseCommand
from django.utils import timezone
from stock_take.services.anthill_api import AnthillAPI, AnthillAPIError
from stock_take.models import Customer, Lead

logger = logging.getLogger(__name__)


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

    def handle(self, *args, **options):
        days = options['days']
        dry_run = options['dry_run']
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
                    details = api.get_customer_details(int(anthill_id))
                    time.sleep(0.05)  # Rate limiting

                    if not details:
                        stats['errors'] += 1
                        continue

                    # Classify: customer (sale) or lead
                    cf = details.get('custom_fields', {})
                    wg_id = cf.get('WorkGuruClientID', '')

                    if wg_id:
                        # Has WorkGuru ID — save as Customer
                        result_action = self._save_customer(details, summary)
                        if result_action == 'created':
                            stats['customers_created'] += 1
                            self.stdout.write(
                                self.style.SUCCESS(f'  + Customer: {details["name"]} (#{anthill_id})')
                            )
                        elif result_action == 'updated':
                            stats['customers_updated'] += 1
                    else:
                        # No WorkGuru ID — save as Lead
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
        if stats['errors']:
            self.stdout.write(self.style.ERROR(f'  Errors             : {stats["errors"]:,}'))
        self.stdout.write(f'{"=" * 60}\n')

        if dry_run:
            new_count = stats['scanned'] - stats['skipped_existing']
            self.stdout.write(self.style.WARNING(
                f'  DRY RUN: {new_count:,} new records would be imported.'
            ))

    def _save_customer(self, details: dict, summary: dict) -> str:
        """Save Anthill customer as a Customer record. Returns 'created', 'updated', or 'exists'."""
        anthill_id = str(details.get('customer_id', summary['id']))
        cf = details.get('custom_fields', {})
        addr = details.get('address', {})

        existing = Customer.objects.filter(anthill_customer_id=anthill_id).first()
        if existing:
            return 'exists'

        # Check if Customer exists via WorkGuruClientID
        wg_id = cf.get('WorkGuruClientID', '')
        if wg_id:
            try:
                existing = Customer.objects.get(workguru_id=int(wg_id))
                existing.anthill_customer_id = anthill_id
                existing.location = summary.get('location', '') or existing.location
                existing.save(update_fields=['anthill_customer_id', 'location'])
                return 'updated'
            except (Customer.DoesNotExist, ValueError):
                pass

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

    def _update_existing(self, api: AnthillAPI, anthill_id: str, summary: dict):
        """Update location for existing records if missing."""
        location = summary.get('location', '')
        if not location:
            return

        # Update Customer
        customer = Customer.objects.filter(anthill_customer_id=anthill_id).first()
        if customer and not customer.location:
            customer.location = location
            customer.save(update_fields=['location'])
            return

        # Update Lead
        lead = Lead.objects.filter(anthill_customer_id=anthill_id).first()
        if lead and not lead.location:
            lead.location = location
            lead.save(update_fields=['location'])
