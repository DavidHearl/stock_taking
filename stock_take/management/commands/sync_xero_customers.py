"""
Management command to match local customers against Xero contacts.

Fetches all contacts from Xero and matches them to local Customer records
by name. Updates the Customer.xero_id field for all matches.

Usage:
    python manage.py sync_xero_customers               # Full sync
    python manage.py sync_xero_customers --dry-run      # Preview without saving
"""

import logging
from django.core.management.base import BaseCommand
from stock_take.models import Customer
from stock_take.services import xero_api

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Match local customers to Xero contacts and store their Xero Contact IDs'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Preview matches without saving to database',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']

        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN — no changes will be saved\n'))

        # Check Xero connection
        access_token, tenant_id = xero_api.get_valid_access_token()
        if not access_token:
            self.stdout.write(self.style.ERROR(
                'No valid Xero connection. Please connect via the Xero Status page first.'
            ))
            return

        self.stdout.write('Fetching all contacts from Xero...')
        xero_contacts = xero_api.get_all_contacts()
        self.stdout.write(f'  Found {len(xero_contacts)} Xero contacts\n')

        if not xero_contacts:
            self.stdout.write(self.style.WARNING('No contacts returned from Xero. Aborting.'))
            return

        # Build lookup: lowercase name → ContactID
        xero_lookup = {}
        for contact in xero_contacts:
            name = contact.get("Name", "").strip().lower()
            if name:
                xero_lookup[name] = contact.get("ContactID", "")

        # Get all local customers
        customers = Customer.objects.all()
        total = customers.count()
        self.stdout.write(f'Checking {total} local customers against Xero...\n')

        matched = 0
        already_linked = 0
        updated = 0
        not_found = 0

        for customer in customers:
            display_name = customer.name or f"{customer.first_name} {customer.last_name}".strip()
            if not display_name:
                continue

            name_lower = display_name.strip().lower()

            if customer.xero_id:
                already_linked += 1
                continue

            xero_id = xero_lookup.get(name_lower, "")
            if xero_id:
                matched += 1
                if not dry_run:
                    Customer.objects.filter(pk=customer.pk).update(xero_id=xero_id)
                    updated += 1
                self.stdout.write(
                    self.style.SUCCESS(f'  ✓ Matched: {display_name} → {xero_id}')
                )
            else:
                not_found += 1

        self.stdout.write('')
        self.stdout.write('=' * 50)
        self.stdout.write(f'  Total customers:    {total}')
        self.stdout.write(f'  Already linked:     {already_linked}')
        self.stdout.write(f'  Newly matched:      {matched}')
        if not dry_run:
            self.stdout.write(f'  Updated in DB:      {updated}')
        self.stdout.write(f'  No match in Xero:   {not_found}')
        self.stdout.write('=' * 50)

        if dry_run:
            self.stdout.write(self.style.WARNING(
                '\nDry run complete. Re-run without --dry-run to save changes.'
            ))
        else:
            self.stdout.write(self.style.SUCCESS('\nSync complete.'))
