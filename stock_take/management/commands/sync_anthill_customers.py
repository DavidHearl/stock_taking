"""
Management command to sync ALL customers from Anthill CRM.

Iterates through all Anthill customer records (paginated search),
fetches full details for each, and creates/updates the local Customer model.

By default, customers already in the database (matched by anthill_customer_id)
are SKIPPED — only new customers trigger an API detail call. Use --force to
re-fetch and update every customer.

Usage:
    python manage.py sync_anthill_customers                # Sync new customers only
    python manage.py sync_anthill_customers --force        # Re-sync ALL customers
    python manage.py sync_anthill_customers --dry-run      # Preview without saving
    python manage.py sync_anthill_customers --limit 100    # Sync first 100 customers only
    python manage.py sync_anthill_customers --skip-details # Skip fetching full customer details
"""

import time
from django.core.management.base import BaseCommand
from django.db import connections
from stock_take.services.anthill_api import AnthillAPI, AnthillAPIError
from stock_take.models import Customer


# Maximum character lengths for Customer model fields.
# Used to truncate values before saving, preventing varchar overflow errors.
FIELD_MAX_LENGTHS = {
    'name': 255,
    'first_name': 100,
    'last_name': 100,
    'phone': 100,
    'fax': 100,
    'code': 100,
    'abn': 100,
    'address': 255,
    'address_1': 255,
    'address_2': 255,
    'city': 100,
    'state': 100,
    'suburb': 100,
    'postcode': 20,
    'country': 100,
    'location': 100,
    'anthill_customer_id': 20,
    'credit_terms_type': 100,
    'title': 20,
}


def truncate_fields(data: dict) -> dict:
    """Truncate string values to their model max_length to prevent DB errors."""
    for key, value in data.items():
        if isinstance(value, str) and key in FIELD_MAX_LENGTHS:
            max_len = FIELD_MAX_LENGTHS[key]
            if len(value) > max_len:
                data[key] = value[:max_len]
    return data


class Command(BaseCommand):
    help = 'Sync all customers from Anthill CRM into the local database'

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
        parser.add_argument(
            '--force',
            action='store_true',
            help='Re-sync ALL customers, including those already in the database',
        )

    def handle(self, *args, **options):
        limit = options['limit']
        skip_details = options['skip_details']
        dry_run = options['dry_run']
        force = options['force']

        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN - no changes will be saved'))

        try:
            api = AnthillAPI()
        except AnthillAPIError as e:
            self.stderr.write(self.style.ERROR(f'Failed to initialise Anthill API: {e}'))
            return

        # Step 1: Discover total customers
        self.stdout.write('Step 1: Discovering all Anthill customers...')
        first_page = api.find_customers(page=1, page_size=1, days_back=36500)
        total_records = first_page['total_records']
        target = min(total_records, limit) if limit else total_records

        self.stdout.write(self.style.SUCCESS(
            f'Found {total_records:,} total customers in Anthill. Will sync {target:,}.'
        ))

        # Step 1b: Pre-load existing anthill IDs to skip already-synced customers
        existing_ids = set(
            Customer.objects.filter(anthill_customer_id__isnull=False)
            .exclude(anthill_customer_id='')
            .values_list('anthill_customer_id', flat=True)
        )
        self.stdout.write(f'Found {len(existing_ids):,} customers already in database.')

        if force:
            self.stdout.write(self.style.WARNING('--force: Will re-sync ALL customers'))
        else:
            self.stdout.write(f'Will skip already-synced customers (use --force to re-sync all)')

        if not skip_details:
            est_new = max(target - len(existing_ids), 0) if not force else target
            est_minutes = est_new * 0.15 / 60
            self.stdout.write(f'Estimated API detail calls: ~{est_new:,} (~{est_minutes:.0f} minutes)')

        # Step 2: Iterate all customers and sync
        self.stdout.write(f'\nStep 2: Syncing customer data...')

        created_count = 0
        updated_count = 0
        skipped_count = 0
        error_count = 0
        processed = 0

        # Per-batch counters (reset every 100)
        batch_created = 0
        batch_updated = 0
        batch_skipped = 0
        batch_errors = 0

        for customer_record in api.iter_all_customers(page_size=1000, days_back=36500):
            if limit and processed >= limit:
                break

            anthill_id = str(customer_record['id'])
            processed += 1

            # Skip customers already in the database (unless --force)
            if not force and anthill_id in existing_ids:
                skipped_count += 1
                batch_skipped += 1
                if processed % 100 == 0:
                    self.stdout.write(
                        f'  Progress: {processed:,}/{target:,} | '
                        f'Last 100: created={batch_created}, updated={batch_updated}, '
                        f'skipped={batch_skipped}, errors={batch_errors} | '
                        f'Total: created={created_count}, updated={updated_count}, '
                        f'skipped={skipped_count}, errors={error_count}'
                    )
                    batch_created = batch_updated = batch_skipped = batch_errors = 0
                continue

            try:
                details = None
                if not skip_details:
                    # Retry API detail calls up to 3 times with backoff
                    max_detail_retries = 3
                    for attempt in range(1, max_detail_retries + 1):
                        try:
                            details = api.get_customer_details(int(anthill_id))
                            time.sleep(0.05)  # Rate limiting
                            break
                        except AnthillAPIError as e:
                            if attempt < max_detail_retries:
                                wait = 5 * attempt
                                self.stderr.write(
                                    f'  API error for #{anthill_id} (attempt {attempt}/{max_detail_retries}), '
                                    f'retrying in {wait}s: {e}'
                                )
                                time.sleep(wait)
                            else:
                                self.stderr.write(f'  Error fetching details for #{anthill_id} after {max_detail_retries} attempts: {e}')
                                error_count += 1
                                batch_errors += 1

                customer_data = self._build_customer_data(anthill_id, details)

                # Use location from search result if details didn't provide it
                if not details and customer_record.get('location'):
                    customer_data['location'] = customer_record['location']
                if not details and customer_record.get('name'):
                    customer_data['name'] = customer_record['name']

                # Truncate values to prevent varchar overflow errors
                customer_data = truncate_fields(customer_data)

                customer_name = customer_data.get('name', '') or f'Customer #{anthill_id}'

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
                    batch_updated += 1
                else:
                    customer_data['anthill_customer_id'] = anthill_id
                    Customer.objects.create(**customer_data)
                    created_count += 1
                    batch_created += 1

            except Exception as e:
                self.stderr.write(self.style.ERROR(
                    f'  Error syncing customer #{anthill_id}: {e}'
                ))
                error_count += 1
                batch_errors += 1
                # Reset stale DB connections so subsequent saves don't cascade-fail
                for conn in connections.all():
                    conn.close()
                time.sleep(2)

            # Progress update every 100 records
            if processed % 100 == 0:
                self.stdout.write(
                    f'  Progress: {processed:,}/{target:,} | '
                    f'Last 100: created={batch_created}, updated={batch_updated}, '
                    f'skipped={batch_skipped}, errors={batch_errors} | '
                    f'Total: created={created_count}, updated={updated_count}, '
                    f'skipped={skipped_count}, errors={error_count}'
                )
                batch_created = batch_updated = batch_skipped = batch_errors = 0

        # Summary
        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('=' * 50))
        self.stdout.write(self.style.SUCCESS('Anthill Customer Sync Complete'))
        self.stdout.write(self.style.SUCCESS('=' * 50))
        self.stdout.write(f'  Processed:  {processed:,}')
        if not dry_run:
            self.stdout.write(f'  Created:    {created_count:,}')
            self.stdout.write(f'  Updated:    {updated_count:,}')
            self.stdout.write(f'  Skipped:    {skipped_count:,}')
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
