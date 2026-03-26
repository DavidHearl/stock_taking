"""
Management command: sync_anthill_workflow
─────────────────────────────────────────
Wrapper around the standalone sync_anthill_workflow script,
so it can be called via ``python manage.py sync_anthill_workflow``
and scheduled by the run_scheduler command.

Refreshes sale data (status, financials, contract number, product info,
assigned user, fit dates) for existing Category 3 AnthillSale records
by calling the Anthill CRM GetSaleDetails endpoint.

Usage:
    python manage.py sync_anthill_workflow                    # All Category 3 sales
    python manage.py sync_anthill_workflow --days 180         # Sales active in last 180 days
    python manage.py sync_anthill_workflow --dry-run          # Preview without saving
"""

import os
import sys
import time
import logging
import xml.etree.ElementTree as ET
from datetime import timedelta
from decimal import Decimal, InvalidOperation

import requests
from django.core.management.base import BaseCommand
from django.db import close_old_connections
from django.utils import timezone

from stock_take.models import AnthillSale, Customer, Order, SyncLog

logger = logging.getLogger(__name__)

# ── Anthill config ──────────────────────────────────────────────────────
SUBDOMAIN = 'sliderobes'
BASE_URL = f'https://{SUBDOMAIN}.anthillcrm.com/api/v1.asmx'
NAMESPACE = 'http://www.anthill.co.uk/'
RETRY_DELAYS = [15, 30, 45, 60, 120]
MAX_RETRIES = len(RETRY_DELAYS)


def _soap_request(action: str, body_xml: str) -> str:
    """Send a SOAP request to Anthill with retry on transient errors."""
    username = os.getenv('ANTHILL_USERNAME', '')
    password = os.getenv('ANTHILL_PASSWORD', '')

    envelope = f'''<?xml version="1.0" encoding="utf-8"?>
<soap12:Envelope xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
                 xmlns:xsd="http://www.w3.org/2001/XMLSchema"
                 xmlns:soap12="http://www.w3.org/2003/05/soap-envelope">
  <soap12:Header>
    <AuthHeader xmlns="{NAMESPACE}">
      <Username>{username}</Username>
      <Password>{password}</Password>
    </AuthHeader>
  </soap12:Header>
  <soap12:Body>
    {body_xml}
  </soap12:Body>
</soap12:Envelope>'''

    headers = {
        'Content-Type': 'application/soap+xml; charset=utf-8',
        'SOAPAction': f'{NAMESPACE}{action}',
    }

    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.post(
                BASE_URL, data=envelope.encode('utf-8'),
                headers=headers, timeout=60,
            )
            if resp.status_code == 200:
                return resp.text
            if resp.status_code == 500:
                logger.debug('Server returned 500 for %s — skipping', action)
                return ''
            if resp.status_code >= 502 and attempt < MAX_RETRIES - 1:
                wait = RETRY_DELAYS[attempt]
                logger.warning('Server error %d (attempt %d/%d), retrying in %ds',
                               resp.status_code, attempt + 1, MAX_RETRIES, wait)
                time.sleep(wait)
                continue
            resp.raise_for_status()
        except requests.exceptions.Timeout:
            if attempt < MAX_RETRIES - 1:
                wait = RETRY_DELAYS[attempt]
                logger.warning('Timeout (attempt %d/%d), retrying in %ds',
                               attempt + 1, MAX_RETRIES, wait)
                time.sleep(wait)
            else:
                logger.error('Timeout after %d attempts.', MAX_RETRIES)
                raise
    return ''


def _safe_decimal(value: str):
    """Parse a string to Decimal, returning None on failure."""
    if not value:
        return None
    try:
        cleaned = value.replace('%', '').replace(',', '').strip()
        if not cleaned:
            return None
        return Decimal(cleaned)
    except (InvalidOperation, ValueError):
        return None


def _get_sale_details(sale_id: str) -> dict:
    """Fetch full details for a single Anthill sale."""
    body = f'''<GetSaleDetails xmlns="{NAMESPACE}">
  <saleId>{sale_id}</saleId>
</GetSaleDetails>'''

    text = _soap_request('GetSaleDetails', body)
    if not text:
        return {}

    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return {}

    result = root.find(f'.//{{{NAMESPACE}}}GetSaleDetailsResult')
    if result is None:
        return {}

    data = {
        'sale_id': result.findtext(f'{{{NAMESPACE}}}SaleId', '').strip(),
        'sale_type_id': result.findtext(f'{{{NAMESPACE}}}SaleTypeId', '').strip(),
        'status': result.findtext(f'{{{NAMESPACE}}}Status', '').strip(),
        'created': result.findtext(f'{{{NAMESPACE}}}Created', '').strip(),
        'assigned_to': result.findtext(f'{{{NAMESPACE}}}AssignedTo', '').strip(),
        'assigned_to_name': result.findtext(f'{{{NAMESPACE}}}AssignedToName', '').strip(),
    }

    custom_fields = {}
    for cf in result.findall(f'.//{{{NAMESPACE}}}CustomField'):
        key = cf.findtext(f'{{{NAMESPACE}}}Key', '').strip()
        value = cf.findtext(f'{{{NAMESPACE}}}Value', '').strip()
        if key:
            custom_fields[key] = value

    data['custom_fields'] = custom_fields
    return data


class Command(BaseCommand):
    help = 'Refresh sale details for existing AnthillSale records from Anthill CRM'

    def add_arguments(self, parser):
        parser.add_argument(
            '--days',
            type=int,
            default=None,
            help='Only refresh sales active/updated within this many days',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Preview changes without writing to the database',
        )

    def handle(self, *args, **options):
        from datetime import datetime as _dt
        from django.db.models import Q

        dry_run = options['dry_run']
        days = options['days']

        username = os.getenv('ANTHILL_USERNAME', '')
        if not username:
            self.stderr.write(self.style.ERROR(
                'ANTHILL_USERNAME not set in environment.'
            ))
            if not dry_run:
                SyncLog.objects.create(
                    script_name='sync_anthill_workflow',
                    status='error',
                    errors=1,
                    notes='ANTHILL_USERNAME not set in environment.',
                )
            return

        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN — no changes will be written.\n'))

        qs = AnthillSale.objects.filter(category='3')
        if days:
            cutoff = timezone.now() - timedelta(days=days)
            qs = qs.filter(
                Q(activity_date__gte=cutoff) | Q(updated_at__gte=cutoff)
            )

        # ── Discover missing sub-activities ──────────────────────────
        # Anthill's GetCustomerDetails only returns top-level activities.
        # Sub-activities (child sales nested under a parent) are invisible
        # there but DO appear in GetSalesModifiedSince.  This step finds
        # any such sales and creates the missing AnthillSale records.
        discovered = self._discover_missing_sales(dry_run=dry_run, days=days)
        if discovered:
            # Re-query so newly created records are included in the refresh
            qs = AnthillSale.objects.filter(category='3')
            if days:
                qs = qs.filter(
                    Q(activity_date__gte=cutoff) | Q(updated_at__gte=cutoff)
                )

        sales_list = list(qs.values_list('pk', 'anthill_activity_id', 'customer_name'))
        total = len(sales_list)
        self.stdout.write(f'Sales to refresh: {total}')

        stats = {'fetched': 0, 'updated': 0, 'unchanged': 0, 'errors': 0}
        error_notes = []

        for idx, (sale_pk, activity_id, cust_name) in enumerate(sales_list, start=1):
            prefix = f'  [{idx}/{total}] {activity_id} ({cust_name})'

            try:
                detail = _get_sale_details(activity_id)
            except Exception as exc:
                self.stderr.write(self.style.ERROR(f'{prefix} ERROR: {exc}'))
                stats['errors'] += 1
                error_notes.append(f'{activity_id}: {exc}')
                continue

            if not detail:
                continue

            stats['fetched'] += 1
            cf = detail.get('custom_fields', {})

            close_old_connections()
            try:
                sale = AnthillSale.objects.get(pk=sale_pk)
            except AnthillSale.DoesNotExist:
                continue

            changed_fields = []

            def update_field(field_name, new_value):
                old_value = getattr(sale, field_name)
                if new_value is not None and old_value != new_value:
                    setattr(sale, field_name, new_value)
                    changed_fields.append(field_name)

            update_field('status', detail.get('status', ''))
            update_field('sale_type_id', detail.get('sale_type_id', ''))
            update_field('assigned_to_id', detail.get('assigned_to', ''))
            update_field('assigned_to_name', detail.get('assigned_to_name', ''))
            update_field('sale_value', _safe_decimal(cf.get('Total Value Inc VAT', '')))
            update_field('profit', _safe_decimal(cf.get('Profit', '')))
            update_field('deposit_required', _safe_decimal(cf.get('Deposit Required', '')))
            update_field('balance_payable', _safe_decimal(cf.get('Balance Payable', '')))
            update_field('contract_number', cf.get('Contract Number', ''))
            update_field('source', cf.get('Source', ''))
            update_field('range_name', cf.get('Range', ''))
            update_field('door_type', cf.get('Door Type', ''))
            update_field('products_included', cf.get('Products Included', ''))
            update_field('fit_from_date', cf.get('Fit From Date', ''))
            update_field('goods_due_in', cf.get('Goods Due In', ''))

            # Parse fit_from_date text into fit_date
            raw_fit = cf.get('Fit From Date', '').strip()
            if raw_fit:
                _DATE_FMTS = ('%d/%m/%Y', '%d/%m/%y', '%Y-%m-%d', '%d-%m-%Y')
                parsed_fit = None
                for _fmt in _DATE_FMTS:
                    try:
                        parsed_fit = _dt.strptime(raw_fit, _fmt).date()
                        break
                    except ValueError:
                        continue
                if parsed_fit is not None and parsed_fit != sale.fit_date:
                    sale.fit_date = parsed_fit
                    changed_fields.append('fit_date')

            # Auto-link sale ↔ order
            if not sale.order_id:
                matching_order = Order.objects.filter(sale_number=sale.anthill_activity_id).first()
                if matching_order:
                    sale.order = matching_order
                    changed_fields.append('order')
                    if not sale.fit_date and matching_order.fit_date:
                        sale.fit_date = matching_order.fit_date
                        if 'fit_date' not in changed_fields:
                            changed_fields.append('fit_date')
                    if sale.fit_date and not matching_order.fit_date and not dry_run:
                        matching_order.fit_date = sale.fit_date
                        matching_order.save(update_fields=['fit_date'])

            if changed_fields:
                if not dry_run:
                    sale.save(update_fields=changed_fields + ['updated_at'])
                stats['updated'] += 1
                self.stdout.write(f'{prefix} updated {len(changed_fields)} field(s)')
            else:
                stats['unchanged'] += 1

            time.sleep(0.3)

        # Summary
        self.stdout.write(self.style.SUCCESS(
            f'\nDone.\n'
            f'  Fetched   : {stats["fetched"]}/{total}\n'
            f'  Updated   : {stats["updated"]}\n'
            f'  Unchanged : {stats["unchanged"]}\n'
            f'  Errors    : {stats["errors"]}'
        ))

        if not dry_run:
            log_status = (
                'success' if stats['errors'] == 0
                else ('error' if stats['fetched'] == 0 else 'warning')
            )
            notes = (
                f"Sales {stats['fetched']}/{total}, "
                f"updated {stats['updated']}, "
                f"unchanged {stats['unchanged']}."
            )
            if error_notes:
                notes += ' Errors: ' + '; '.join(error_notes[:5])
            SyncLog.objects.create(
                script_name='sync_anthill_workflow',
                status=log_status,
                records_created=discovered,
                records_updated=stats['updated'],
                errors=stats['errors'],
                notes=notes,
            )

    def _discover_missing_sales(self, dry_run: bool = False, days: int = None) -> int:
        """
        Use GetSalesModifiedSince to find sales that exist in Anthill but
        are missing from the local AnthillSale table.

        This catches sub-activities (child sales nested under a parent in
        Anthill) that GetCustomerDetails doesn't return.

        Returns:
            Number of newly created AnthillSale records.
        """
        from datetime import datetime as _dt, timezone as _tz
        from stock_take.services.anthill_api import AnthillAPI, AnthillAPIError

        # Determine how far back to look
        if days:
            since_dt = timezone.now() - timedelta(days=days)
        else:
            since_dt = timezone.now() - timedelta(days=365)
        since_str = since_dt.strftime('%Y-%m-%dT00:00:00')

        self.stdout.write(f'\nDiscovering missing sub-activities (since {since_str[:10]})...')

        try:
            api = AnthillAPI()
            all_sales = api.get_all_sales_since(since=since_str)
        except AnthillAPIError as e:
            self.stderr.write(self.style.ERROR(f'  Discovery failed: {e}'))
            return 0

        # Get existing activity IDs for fast lookup
        existing_ids = set(
            AnthillSale.objects.values_list('anthill_activity_id', flat=True)
        )

        missing = [s for s in all_sales if s['sale_id'] and s['sale_id'] not in existing_ids]
        if not missing:
            self.stdout.write(f'  No missing sales found ({len(all_sales):,} checked)')
            return 0

        self.stdout.write(self.style.WARNING(
            f'  Found {len(missing)} sales in Anthill not in local DB'
        ))

        created = 0
        for sale_info in missing:
            sale_id = sale_info['sale_id']
            cust_id = sale_info['customer_id']
            cust_name = sale_info['customer_name']

            # Parse activity date
            activity_date = None
            if sale_info.get('created'):
                for fmt in ('%Y-%m-%dT%H:%M:%S.%f', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d'):
                    try:
                        naive = _dt.strptime(sale_info['created'], fmt)
                        activity_date = naive.replace(tzinfo=_tz.utc)
                        break
                    except ValueError:
                        continue

            # Link to local customer
            local_customer = None
            if cust_id:
                local_customer = Customer.objects.filter(
                    anthill_customer_id=cust_id
                ).first()

            if dry_run:
                self.stdout.write(
                    f'  [DRY RUN] Would create: {sale_id} '
                    f'({cust_name}) ref={sale_info.get("external_ref", "")}'
                )
            else:
                AnthillSale.objects.create(
                    anthill_activity_id=sale_id,
                    anthill_customer_id=cust_id,
                    customer=local_customer,
                    activity_type='Room Sale',
                    status=sale_info.get('status', ''),
                    category='3',
                    customer_name=cust_name,
                    location=sale_info.get('location', ''),
                    activity_date=activity_date,
                )
                self.stdout.write(self.style.SUCCESS(
                    f'  + Created: {sale_id} ({cust_name}) '
                    f'ref={sale_info.get("external_ref", "")}'
                ))
            created += 1

        return created
