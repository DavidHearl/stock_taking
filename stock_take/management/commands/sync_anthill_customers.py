"""
Management command to sync customers from Anthill CRM.

Only syncs customers who have at least one sale in the last 10 years.

Usage:
    python manage.py sync_anthill_customers                # Full sync with details
    python manage.py sync_anthill_customers --dry-run      # Preview without saving
    python manage.py sync_anthill_customers --limit 100    # Sync first 100 customers only
    python manage.py sync_anthill_customers --skip-details # Skip fetching full customer details
"""

import time
from django.core.management.base import BaseCommand
from stock_take.services.anthill_api import AnthillAPI, AnthillAPIError
from stock_take.models import Customer


class Command(BaseCommand):
    help = 'Sync customers with sales from Anthill CRM into the local database'

    def add_arguments(self, parser):
        parser.add_argument(
            '--limit',
            type=int,
            default=0,
            help='Maximum number of customers to sync (0 = all)',
        )
        parser.add_argument(
            '--skip-details',
            action='store_true',
            help='Skip fetching full details per customer (faster but less data)',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Preview what would be synced without saving to database',
        )

    def handle(self, *args, **options):
        limit = options['limit']
        skip_details = options['skip_details']
        dry_run = options['dry_run']

        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN - no changes will be saved'))

        try:
            api = AnthillAPI()
        except AnthillAPIError as e:
            self.stderr.write(self.style.ERROR(f'Failed to initialise Anthill API: {e}'))
            return

        # Step 1: Get all customer IDs that have sales in the last 10 years
        self.stdout.write('Step 1: Finding customers with sales (last 10 years)...')
        customer_ids = api.get_sales_customer_ids(since='2016-02-13T00:00:00')
        target = min(len(customer_ids), limit) if limit else len(customer_ids)

        self.stdout.write(self.style.SUCCESS(
            f'Found {len(customer_ids):,} unique customers with sales. Will sync {target:,}.'
        ))

        if not skip_details:
            est_minutes = target * 0.15 / 60
            self.stdout.write(f'Estimated time: ~{est_minutes:.0f} minutes ({target:,} API calls)')

        # Step 2: Fetch details and save each customer
        self.stdout.write(f'\nStep 2: Syncing customer data...')

        created_count = 0
        updated_count = 0
        error_count = 0
        processed = 0

        for anthill_id in sorted(customer_ids, key=int):
            if limit and processed >= limit:
                break

            processed += 1

            try:
                details = None
                if not skip_details:
                    try:
                        details = api.get_customer_details(int(anthill_id))
                        time.sleep(0.05)  # Rate limiting
                    except AnthillAPIError as e:
                        self.stderr.write(f'  Error fetching details for #{anthill_id}: {e}')
                        error_count += 1

                customer_data = self._build_customer_data(anthill_id, details)
                customer_name = details['name'] if details else f'Customer #{anthill_id}'

                if dry_run:
                    self.stdout.write(f'  [DRY RUN] Would sync: {customer_name} (Anthill #{anthill_id})')
                    continue

                # Try to find existing customer by anthill_customer_id
                existing = Customer.objects.filter(anthill_customer_id=anthill_id).first()

                if existing:
                    for key, value in customer_data.items():
                        if value:  # Only update non-empty values
                            setattr(existing, key, value)
                    existing.save()
                    updated_count += 1
                else:
                    customer_data['anthill_customer_id'] = anthill_id
                    Customer.objects.create(**customer_data)
                    created_count += 1

            except Exception as e:
                self.stderr.write(self.style.ERROR(
                    f'  Error syncing customer #{anthill_id}: {e}'
                ))
                error_count += 1

            # Progress update every 250 records
            if processed % 250 == 0:
                self.stdout.write(
                    f'  Progress: {processed:,}/{target:,} '
                    f'(created: {created_count}, updated: {updated_count}, errors: {error_count})'
                )

        # Summary
        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('=' * 50))
        self.stdout.write(self.style.SUCCESS('Anthill Customer Sync Complete'))
        self.stdout.write(self.style.SUCCESS('=' * 50))
        self.stdout.write(f'  Processed:  {processed:,}')
        if not dry_run:
            self.stdout.write(f'  Created:    {created_count:,}')
            self.stdout.write(f'  Updated:    {updated_count:,}')
        self.stdout.write(f'  Errors:     {error_count:,}')

    def _build_customer_data(self, anthill_id: str, details: dict | None) -> dict:
        """Build a dict of Customer model fields from Anthill data."""
        data = {}

        if details:
            cf = details.get('custom_fields', {})
            addr = details.get('address', {})

            data['name'] = details.get('name', '')
            data['first_name'] = cf.get('First Name', '')
            data['last_name'] = cf.get('Last Name', '')
            data['email'] = cf.get('Email', '') or None
            data['phone'] = cf.get('Telephone', '') or None

            # Address
            data['address_1'] = addr.get('address1', '') or None
            data['address_2'] = addr.get('address2', '') or None
            data['city'] = addr.get('city', '') or None
            data['state'] = addr.get('county', '') or None
            data['postcode'] = addr.get('postcode', '') or ''
            data['country'] = addr.get('country', '') or None

            # Build legacy address field
            address_parts = [addr.get('address1', ''), addr.get('city', ''), addr.get('postcode', '')]
            data['address'] = ', '.join(p for p in address_parts if p)

            # Link to WorkGuru if available
            workguru_id = cf.get('WorkGuruClientID', '')
            if workguru_id and workguru_id.isdigit():
                data['workguru_id'] = int(workguru_id)

            # Store raw data
            data['raw_data'] = {
                'anthill_custom_fields': cf,
                'anthill_address': addr,
                'anthill_location_id': details.get('location_id'),
                'anthill_location': details.get('location_label'),
            }

        return data
