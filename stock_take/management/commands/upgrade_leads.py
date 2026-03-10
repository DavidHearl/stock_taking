"""
Management command to re-check all existing Lead records and promote any
that now have a qualifying sale activity in Anthill CRM to Customer status.

A Lead is promoted when Anthill returns at least one activity whose type
contains 'sale' and whose status is NOT cancelled / canceled / lost / deleted.

For each promoted lead the command will:
  - Create a new Customer record (copying name, contact details, anthill_customer_id)
  - Save any AnthillSale activities fetched from Anthill for that customer
  - Link any pre-existing AnthillSale records (customer=None) to the new Customer
  - Mark the Lead as status='converted' and set converted_to_customer

Usage:
    python manage.py upgrade_leads                # Check & upgrade all eligible leads
    python manage.py upgrade_leads --dry-run      # Preview without saving
    python manage.py upgrade_leads --limit 50     # Process at most 50 leads (for testing)
"""

import time
import logging
from django.core.management.base import BaseCommand
from django.utils import timezone
from stock_take.services.anthill_api import AnthillAPI, AnthillAPIError
from stock_take.models import Customer, Lead, AnthillSale, SyncLog

logger = logging.getLogger(__name__)

# Activity types that indicate a sale
SALE_ACTIVITY_TYPES_KEYWORDS = {'sale'}
# Statuses that should NOT count as a sale
SALE_EXCLUDE_STATUSES = {'cancelled', 'canceled', 'lost', 'deleted'}


class Command(BaseCommand):
    help = 'Re-check existing Leads against Anthill CRM and promote any with a sale activity to Customer'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Preview upgrades without saving to database',
        )
        parser.add_argument(
            '--limit',
            type=int,
            default=0,
            help='Maximum number of leads to check (0 = all)',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        limit = options['limit']
        start_time = time.time()

        self.stdout.write(f'\n{"=" * 60}')
        self.stdout.write(f'  Anthill Lead -> Customer Upgrade')
        self.stdout.write(f'  Started: {timezone.now().strftime("%Y-%m-%d %H:%M:%S")}')
        if dry_run:
            self.stdout.write(self.style.WARNING('  Mode: DRY RUN'))
        self.stdout.write(f'{"=" * 60}\n')

        try:
            api = AnthillAPI()
        except AnthillAPIError as e:
            self.stderr.write(self.style.ERROR(f'Failed to initialise Anthill API: {e}'))
            return

        # Only check leads that have an Anthill ID and are not already converted
        leads_qs = (
            Lead.objects
            .exclude(anthill_customer_id='')
            .exclude(anthill_customer_id__isnull=True)
            .exclude(status='converted')
            .order_by('pk')
        )
        if limit > 0:
            leads_qs = leads_qs[:limit]

        leads = list(leads_qs.values_list('pk', 'anthill_customer_id', 'name'))
        total = len(leads)
        self.stdout.write(f'  Leads to check: {total:,}\n')

        stats = {
            'checked': 0,
            'upgraded': 0,
            'sales_created': 0,
            'errors': 0,
        }

        for lead_pk, anthill_id, lead_name in leads:
            stats['checked'] += 1

            try:
                details = api.get_customer_details(int(anthill_id), include_activity=True)
                time.sleep(0.05)  # polite rate limit
            except AnthillAPIError as e:
                stats['errors'] += 1
                self.stderr.write(f'  [ERROR] API error for #{anthill_id}: {e}')
                continue
            except Exception as e:
                stats['errors'] += 1
                self.stderr.write(self.style.ERROR(f'  [ERROR] Error for #{anthill_id}: {e}'))
                continue

            if not details or not self._is_sale(details):
                continue

            # This lead qualifies — promote it
            self.stdout.write(
                self.style.SUCCESS(f'  [>>] [{stats["checked"]}/{total}] {lead_name} (#{anthill_id})')
            )

            if dry_run:
                stats['upgraded'] += 1
                continue

            # Build summary dict for _save_customer
            summary = {
                'id': anthill_id,
                'name': lead_name,
                'location': '',
                'created': '',
            }
            result = self._save_customer(details, summary)
            new_customer = Customer.objects.filter(anthill_customer_id=anthill_id).first()

            if not new_customer:
                continue

            # Save activities as AnthillSale records
            sales_count = self._save_sales(details, summary, new_customer)
            stats['sales_created'] += sales_count

            # Link any pre-existing AnthillSale records (customer=None) to the new customer
            AnthillSale.objects.filter(
                anthill_customer_id=anthill_id,
                customer__isnull=True,
            ).update(customer=new_customer)

            # Mark lead as converted
            Lead.objects.filter(pk=lead_pk).update(
                status='converted',
                converted_to_customer=new_customer,
            )
            stats['upgraded'] += 1

        # Summary
        elapsed = time.time() - start_time
        self.stdout.write(f'\n{"=" * 60}')
        self.stdout.write(self.style.SUCCESS('  UPGRADE COMPLETE'))
        self.stdout.write(f'{"=" * 60}')
        self.stdout.write(f'  Time elapsed    : {elapsed:.1f}s')
        self.stdout.write(f'  Leads checked   : {stats["checked"]:,}')
        self.stdout.write(f'  Leads upgraded  : {stats["upgraded"]:,}')
        self.stdout.write(f'  Sales created   : {stats["sales_created"]:,}')
        if stats['errors']:
            self.stdout.write(self.style.ERROR(f'  Errors          : {stats["errors"]:,}'))
        self.stdout.write(f'{"=" * 60}\n')

        if not dry_run:
            log_status = 'success' if stats['errors'] == 0 else (
                'warning' if stats['upgraded'] > 0 else 'error'
            )
            SyncLog.objects.create(
                script_name='upgrade_leads',
                status=log_status,
                records_created=stats['upgraded'] + stats['sales_created'],
                records_updated=0,
                errors=stats['errors'],
                notes=(
                    f"Checked {stats['checked']} leads, "
                    f"upgraded {stats['upgraded']} to Customer, "
                    f"sales created {stats['sales_created']}, "
                    f"errors {stats['errors']}."
                ),
            )
            self.stdout.write(self.style.SUCCESS('  SyncLog entry written.'))

        if dry_run:
            self.stdout.write(self.style.WARNING(
                f'  DRY RUN: {stats["upgraded"]} leads would be promoted to Customer.'
            ))

    @staticmethod
    def _is_sale(details: dict) -> bool:
        for act in details.get('activities', []):
            status = (act.get('status') or '').lower()
            act_type = (act.get('type') or '').lower()
            if status in SALE_EXCLUDE_STATUSES:
                continue
            if any(kw in act_type for kw in SALE_ACTIVITY_TYPES_KEYWORDS):
                return True
        return False

    @staticmethod
    def _save_customer(details: dict, summary: dict) -> str:
        anthill_id = str(details.get('customer_id') or summary['id'])
        cf = details.get('custom_fields', {})
        addr = details.get('address', {})

        if Customer.objects.filter(anthill_customer_id=anthill_id).exists():
            return 'exists'

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

    @staticmethod
    def _save_sales(details: dict, summary: dict, customer_obj) -> int:
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

            AnthillSale.objects.create(
                anthill_activity_id=act_id,
                anthill_customer_id=anthill_customer_id,
                customer=customer_obj,
                activity_type=act.get('type', ''),
                status=act.get('status', ''),
                category=act.get('category', ''),
                customer_name=customer_name,
                location=location,
                activity_date=activity_date,
            )
            created_count += 1

        return created_count
