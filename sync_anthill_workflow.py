#!/usr/bin/env python
"""
sync_anthill_workflow.py
────────────────────────
Standalone script to refresh sale data for existing AnthillSale records
by fetching details from the Anthill CRM GetSaleDetails endpoint.

For each AnthillSale record, the script calls GetSaleDetails to retrieve
the current status, assigned user, financial data, and product info,
then updates any fields that have changed.

A SyncLog entry is written on completion.

Usage
─────
  # Sync all existing sale records
  python sync_anthill_workflow.py

  # Dry-run (report changes without writing to DB)
  python sync_anthill_workflow.py --dry-run

  # Limit to sales updated or created in last N days
  python sync_anthill_workflow.py --days 180
"""

import os
import sys
import argparse
import time
import logging
import xml.etree.ElementTree as ET
from datetime import timedelta
from decimal import Decimal, InvalidOperation

import requests
from dotenv import load_dotenv

# ── Django bootstrap ────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'stock_taking.settings')

import django
django.setup()

from django.db import close_old_connections
from stock_take.models import AnthillSale, Order, SyncLog  # noqa: E402

# ── Logging setup ───────────────────────────────────────────────────────
LOG_DIR = os.path.join(BASE_DIR, 'logs')
os.makedirs(LOG_DIR, exist_ok=True)

logger = logging.getLogger('sync_anthill_workflow')
logger.setLevel(logging.DEBUG)

# File handler — detailed log
fh = logging.FileHandler(os.path.join(LOG_DIR, 'sync_anthill_workflow.log'))
fh.setLevel(logging.DEBUG)
fh.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s'))
logger.addHandler(fh)

# Console handler — summary
ch = logging.StreamHandler()
ch.setLevel(logging.INFO)
ch.setFormatter(logging.Formatter('%(message)s'))
logger.addHandler(ch)


# ── Anthill config ──────────────────────────────────────────────────────
load_dotenv(os.path.join(BASE_DIR, '.env'))
ANTHILL_USERNAME = os.getenv('ANTHILL_USERNAME')
ANTHILL_PASSWORD = os.getenv('ANTHILL_PASSWORD')
SUBDOMAIN = 'sliderobes'
BASE_URL = f'https://{SUBDOMAIN}.anthillcrm.com/api/v1.asmx'
NAMESPACE = 'http://www.anthill.co.uk/'

RETRY_DELAYS = [15, 30, 45, 60, 120]
MAX_RETRIES = len(RETRY_DELAYS)


# ════════════════════════════════════════════════════════════════════════
# SOAP helpers
# ════════════════════════════════════════════════════════════════════════

def soap_request(action: str, body_xml: str) -> str:
    """Send a SOAP request to Anthill with retry on transient errors."""
    envelope = f'''<?xml version="1.0" encoding="utf-8"?>
<soap12:Envelope xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
                 xmlns:xsd="http://www.w3.org/2001/XMLSchema"
                 xmlns:soap12="http://www.w3.org/2003/05/soap-envelope">
  <soap12:Header>
    <AuthHeader xmlns="{NAMESPACE}">
      <Username>{ANTHILL_USERNAME}</Username>
      <Password>{ANTHILL_PASSWORD}</Password>
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
                # 500 usually means the activity is not a sale type —
                # return empty immediately rather than retrying.
                logger.debug(f'Server returned 500 for {action} — skipping')
                return ''
            if resp.status_code >= 502 and attempt < MAX_RETRIES - 1:
                wait = RETRY_DELAYS[attempt]
                logger.warning(f'Server error {resp.status_code} (attempt {attempt + 1}/{MAX_RETRIES}), retrying in {wait}s')
                time.sleep(wait)
                continue
            resp.raise_for_status()
        except requests.exceptions.Timeout:
            if attempt < MAX_RETRIES - 1:
                wait = RETRY_DELAYS[attempt]
                logger.warning(f'Timeout (attempt {attempt + 1}/{MAX_RETRIES}), retrying in {wait}s')
                time.sleep(wait)
            else:
                logger.error(f'Timeout after {MAX_RETRIES} attempts.')
                raise
    return ''


def safe_decimal(value: str):
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


def get_sale_details(sale_id: str) -> dict:
    """Fetch full details for a single Anthill sale."""
    body = f'''<GetSaleDetails xmlns="{NAMESPACE}">
  <saleId>{sale_id}</saleId>
</GetSaleDetails>'''

    text = soap_request('GetSaleDetails', body)
    if not text:
        return {}

    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return {}

    result = root.find(f'.//{{{NAMESPACE}}}GetSaleDetailsResult')
    if result is None:
        return {}

    # Extract top-level fields
    data = {
        'sale_id': result.findtext(f'{{{NAMESPACE}}}SaleId', '').strip(),
        'sale_type_id': result.findtext(f'{{{NAMESPACE}}}SaleTypeId', '').strip(),
        'status': result.findtext(f'{{{NAMESPACE}}}Status', '').strip(),
        'created': result.findtext(f'{{{NAMESPACE}}}Created', '').strip(),
        'assigned_to': result.findtext(f'{{{NAMESPACE}}}AssignedTo', '').strip(),
        'assigned_to_name': result.findtext(f'{{{NAMESPACE}}}AssignedToName', '').strip(),
    }

    # Extract custom fields into a dict
    custom_fields = {}
    for cf in result.findall(f'.//{{{NAMESPACE}}}CustomField'):
        key = cf.findtext(f'{{{NAMESPACE}}}Key', '').strip()
        value = cf.findtext(f'{{{NAMESPACE}}}Value', '').strip()
        if key:
            custom_fields[key] = value

    data['custom_fields'] = custom_fields
    return data


# ════════════════════════════════════════════════════════════════════════
# Core sync logic
# ════════════════════════════════════════════════════════════════════════

def sync_workflow(dry_run: bool = False, days: int = None):
    """
    Refresh sale data on existing AnthillSale records using GetSaleDetails.

    For each sale, calls GetSaleDetails to get the current status, assigned user,
    financial data, and product info. Updates any fields that have changed.
    """
    from django.utils import timezone as tz
    from django.db.models import Q

    logger.info('=' * 60)
    logger.info('sync_anthill_workflow — sale details refresh')
    logger.info('=' * 60)
    if dry_run:
        logger.info('[DRY-RUN] No changes will be written.\n')

    # Build base queryset — only Category 3 records are actual sales
    # Category 1 = Enquiries, 2 = Product Leads, 8 = Remedials
    # GetSaleDetails only works for Category 3 (Room Sale + Historic Sale)
    qs = AnthillSale.objects.filter(category='3')

    if days:
        cutoff = tz.now() - timedelta(days=days)
        qs = qs.filter(
            Q(activity_date__gte=cutoff) | Q(updated_at__gte=cutoff)
        )

    # Materialise queryset to a list of (id, anthill_activity_id, customer_name)
    # This prevents DB connection drop during long API loops
    sales_list = list(qs.values_list('pk', 'anthill_activity_id', 'customer_name'))
    total_sales = len(sales_list)
    logger.info(f'Sales to refresh: {total_sales}')

    stats = {
        'sales_fetched': 0,
        'updated': 0,
        'unchanged': 0,
        'errors': 0,
    }
    error_notes = []

    for idx, (sale_pk, activity_id, cust_name) in enumerate(sales_list, start=1):
        logger.info(f'  [{idx}/{total_sales}] Sale {activity_id} ({cust_name})')

        try:
            detail = get_sale_details(activity_id)
        except Exception as exc:
            logger.error(f'    ERROR fetching sale {activity_id}: {exc}')
            stats['errors'] += 1
            error_notes.append(f'Sale {activity_id}: {exc}')
            continue

        if not detail:
            logger.warning(f'    No data returned for sale {activity_id}')
            continue

        stats['sales_fetched'] += 1
        cf = detail.get('custom_fields', {})

        # Log the sale data retrieved
        logger.debug(f'    Status: {detail.get("status")}')
        logger.debug(f'    Assigned to: {detail.get("assigned_to_name")} (ID: {detail.get("assigned_to")})')
        logger.debug(f'    Sale value: {cf.get("Total Value Inc VAT", "N/A")}')
        logger.debug(f'    Contract: {cf.get("Contract Number", "N/A")}')
        logger.debug(f'    Source: {cf.get("Source", "N/A")}')
        logger.debug(f'    Range: {cf.get("Range", "N/A")}')
        logger.debug(f'    Door Type: {cf.get("Door Type", "N/A")}')
        logger.debug(f'    Fit From Date: {cf.get("Fit From Date", "N/A")}')
        logger.debug(f'    Goods Due In: {cf.get("Goods Due In", "N/A")}')

        # Close old DB connections before writing (prevents connection drop)
        close_old_connections()

        # Reload the sale fresh from DB
        try:
            sale = AnthillSale.objects.get(pk=sale_pk)
        except AnthillSale.DoesNotExist:
            logger.warning(f'    Sale {activity_id} no longer exists in DB')
            continue

        # Track changes
        changed_fields = []

        def update_field(field_name, new_value):
            old_value = getattr(sale, field_name)
            if new_value is not None and old_value != new_value:
                setattr(sale, field_name, new_value)
                changed_fields.append(field_name)
                logger.debug(f'    {field_name}: {old_value!r} -> {new_value!r}')

        # Top-level fields
        update_field('status', detail.get('status', ''))
        update_field('sale_type_id', detail.get('sale_type_id', ''))
        update_field('assigned_to_id', detail.get('assigned_to', ''))
        update_field('assigned_to_name', detail.get('assigned_to_name', ''))

        # Financial custom fields
        update_field('sale_value', safe_decimal(cf.get('Total Value Inc VAT', '')))
        update_field('profit', safe_decimal(cf.get('Profit', '')))
        update_field('deposit_required', safe_decimal(cf.get('Deposit Required', '')))
        update_field('balance_payable', safe_decimal(cf.get('Balance Payable', '')))

        # Contract & source
        update_field('contract_number', cf.get('Contract Number', ''))
        update_field('source', cf.get('Source', ''))

        # Product info
        update_field('range_name', cf.get('Range', ''))
        update_field('door_type', cf.get('Door Type', ''))
        update_field('products_included', cf.get('Products Included', ''))

        # Dates (stored as text since Anthill format varies)
        update_field('fit_from_date', cf.get('Fit From Date', ''))
        update_field('goods_due_in', cf.get('Goods Due In', ''))

        # --- Parse fit_from_date text into fit_date ---
        # The Anthill "Fit From Date" custom field (DD/MM/YYYY) is the source of
        # truth for installation dates. Parse it into the proper date field.
        raw_fit = cf.get('Fit From Date', '').strip()
        if raw_fit:
            from datetime import datetime as _dt
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
                logger.debug(f'    fit_date (from Fit From Date): {parsed_fit}')

        # --- Auto-link sale ↔ order and sync fit_date ---
        if not sale.order_id:
            matching_order = Order.objects.filter(sale_number=sale.anthill_activity_id).first()
            if matching_order:
                sale.order = matching_order
                changed_fields.append('order')
                logger.info(f'    Linked to Order {matching_order.sale_number} (pk={matching_order.pk})')
                # Sync fit_date from order if sale still has none
                if not sale.fit_date and matching_order.fit_date:
                    sale.fit_date = matching_order.fit_date
                    if 'fit_date' not in changed_fields:
                        changed_fields.append('fit_date')
                    logger.info(f'    fit_date from Order: {matching_order.fit_date}')
                # Sync fit_date TO order if order has none
                if sale.fit_date and not matching_order.fit_date and not dry_run:
                    matching_order.fit_date = sale.fit_date
                    matching_order.save(update_fields=['fit_date'])
                    logger.info(f'    fit_date → Order: {sale.fit_date}')

        if changed_fields:
            if not dry_run:
                sale.save(update_fields=changed_fields + ['updated_at'])
            stats['updated'] += 1
            logger.info(f'    Updated {len(changed_fields)} field(s): {", ".join(changed_fields)}')
        else:
            stats['unchanged'] += 1
            logger.debug(f'    No changes')

        # Small polite delay to avoid hammering the API
        time.sleep(0.3)

    logger.info('')
    logger.info('-' * 60)
    logger.info(f"Sales fetched : {stats['sales_fetched']}/{total_sales}")
    logger.info(f"Updated       : {stats['updated']}")
    logger.info(f"Unchanged     : {stats['unchanged']}")
    logger.info(f"Errors        : {stats['errors']}")

    # Log to DB (unless dry-run)
    if not dry_run:
        log_status = 'success' if stats['errors'] == 0 else ('error' if stats['sales_fetched'] == 0 else 'warning')
        notes_text = (
            f"Sales {stats['sales_fetched']}/{total_sales}, "
            f"updated {stats['updated']}, "
            f"unchanged {stats['unchanged']}."
        )
        if error_notes:
            notes_text += ' Errors: ' + '; '.join(error_notes[:5])
            if len(error_notes) > 5:
                notes_text += f' ... (+{len(error_notes) - 5} more)'

        SyncLog.objects.create(
            script_name='sync_anthill_workflow',
            status=log_status,
            records_created=0,
            records_updated=stats['updated'],
            errors=stats['errors'],
            notes=notes_text,
        )
        logger.info(f'\nSyncLog entry written (status={log_status}).')

    logger.info('Done.')


# ════════════════════════════════════════════════════════════════════════
# Entry point
# ════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description='Refresh Anthill sale details for existing sales.')
    parser.add_argument('--dry-run', action='store_true', help='Report changes without writing to the database')
    parser.add_argument('--days', type=int, default=None, help='Only refresh sales active/updated within this many days')
    args = parser.parse_args()

    if not ANTHILL_USERNAME or not ANTHILL_PASSWORD:
        logger.error('ANTHILL_USERNAME / ANTHILL_PASSWORD not set in environment.')
        sys.exit(1)

    sync_workflow(dry_run=args.dry_run, days=args.days)


if __name__ == '__main__':
    main()
