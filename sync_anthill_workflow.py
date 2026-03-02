#!/usr/bin/env python
"""
sync_anthill_workflow.py
────────────────────────
Standalone script to refresh workflow status / category for existing
AnthillSale records by fetching the latest activity data from Anthill CRM.

For each unique anthill_customer_id in AnthillSale, the script calls
GetCustomerDetails to retrieve the current activity status and category,
then updates any AnthillSale rows whose data has changed.

A SyncLog entry is written on completion.

Usage
─────
  # Sync all existing sale records
  python sync_anthill_workflow.py

  # Dry-run (report changes without writing to DB)
  python sync_anthill_workflow.py --dry-run

  # Limit to customers updated or created in last N days
  python sync_anthill_workflow.py --days 180
"""

import os
import sys
import argparse
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta

import requests
from dotenv import load_dotenv

# ── Django bootstrap ────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'stock_taking.settings')

import django
django.setup()

from stock_take.models import AnthillSale, SyncLog  # noqa: E402

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
# SOAP helpers  (mirrors sync_anthill_customers.py)
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
            if resp.status_code >= 500 and attempt < MAX_RETRIES - 1:
                wait = RETRY_DELAYS[attempt]
                print(f'  ⚠ Server error {resp.status_code} (attempt {attempt + 1}/{MAX_RETRIES}), retrying in {wait}s …')
                time.sleep(wait)
                continue
            resp.raise_for_status()
        except requests.exceptions.Timeout:
            if attempt < MAX_RETRIES - 1:
                wait = RETRY_DELAYS[attempt]
                print(f'  ⚠ Timeout (attempt {attempt + 1}/{MAX_RETRIES}), retrying in {wait}s …')
                time.sleep(wait)
            else:
                print(f'  ✗ Timeout after {MAX_RETRIES} attempts.')
                raise
    return ''


def get_customer_detail(customer_id) -> dict:
    """Fetch full details plus activities for a single Anthill customer."""
    body = f'''<GetCustomerDetails xmlns="{NAMESPACE}">
  <customerId>{customer_id}</customerId>
  <includeActivity>true</includeActivity>
</GetCustomerDetails>'''

    text = soap_request('GetCustomerDetails', body)
    if not text:
        return {}

    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return {}

    result = root.find(f'.//{{{NAMESPACE}}}GetCustomerDetailsResult')
    if result is None:
        return {}

    activities = []
    for act in result.findall(f'.//{{{NAMESPACE}}}RecentActivityModel'):
        activities.append({
            'id':       act.findtext(f'{{{NAMESPACE}}}Id', '').strip(),
            'type':     act.findtext(f'{{{NAMESPACE}}}Type', '').strip(),
            'category': act.findtext(f'{{{NAMESPACE}}}Category', '').strip(),
            'status':   act.findtext(f'{{{NAMESPACE}}}Status', '').strip(),
            'created':  act.findtext(f'{{{NAMESPACE}}}Created', '').strip(),
        })

    return {
        'customer_id': result.findtext(f'{{{NAMESPACE}}}CustomerId', '').strip(),
        'activities': activities,
    }


# ════════════════════════════════════════════════════════════════════════
# Core sync logic
# ════════════════════════════════════════════════════════════════════════

def sync_workflow(dry_run: bool = False, days: int = None):
    """
    Refresh status / category on existing AnthillSale records.

    For performance, group AnthillSale records by anthill_customer_id so
    each customer is only fetched once from the remote API.
    """
    from django.utils import timezone as tz
    from django.db.models import Q

    print('=' * 60)
    print('sync_anthill_workflow — workflow status refresh')
    print('=' * 60)
    if dry_run:
        print('[DRY-RUN] No changes will be written.\n')

    # Build base queryset
    qs = AnthillSale.objects.exclude(anthill_customer_id='').exclude(anthill_customer_id__isnull=True)

    if days:
        cutoff = tz.now() - timedelta(days=days)
        qs = qs.filter(
            Q(activity_date__gte=cutoff) | Q(updated_at__gte=cutoff)
        )

    # Get unique customer IDs to minimise API calls
    customer_ids = list(qs.values_list('anthill_customer_id', flat=True).distinct())
    total_customers = len(customer_ids)
    print(f'Customers to refresh: {total_customers}')

    stats = {
        'customers_fetched': 0,
        'activities_checked': 0,
        'updated': 0,
        'errors': 0,
    }
    error_notes = []

    for idx, cust_id in enumerate(customer_ids, start=1):
        print(f'  [{idx}/{total_customers}] Customer {cust_id} … ', end='', flush=True)

        try:
            detail = get_customer_detail(cust_id)
        except Exception as exc:
            print(f'ERROR — {exc}')
            stats['errors'] += 1
            error_notes.append(f'Customer {cust_id}: {exc}')
            continue

        if not detail or not detail.get('activities'):
            print('no data')
            continue

        stats['customers_fetched'] += 1

        # Build a lookup from activity_id → {status, category}
        remote = {
            act['id']: act
            for act in detail['activities']
            if act.get('id')
        }
        stats['activities_checked'] += len(remote)

        # Check each local AnthillSale for this customer
        local_sales = AnthillSale.objects.filter(anthill_customer_id=cust_id)
        updated_count = 0

        for sale in local_sales:
            act_data = remote.get(sale.anthill_activity_id)
            if not act_data:
                continue

            new_status = act_data.get('status', '')
            new_category = act_data.get('category', '')
            new_type = act_data.get('type', '')

            changed_fields = []
            if new_status and sale.status != new_status:
                sale.status = new_status
                changed_fields.append('status')
            if new_category and sale.category != new_category:
                sale.category = new_category
                changed_fields.append('category')
            if new_type and sale.activity_type != new_type:
                sale.activity_type = new_type
                changed_fields.append('activity_type')

            if changed_fields:
                if not dry_run:
                    sale.save(update_fields=changed_fields)
                updated_count += 1

        stats['updated'] += updated_count
        marker = '(dry)' if dry_run else ''
        print(f'{updated_count} sale(s) updated {marker}')

        # Small polite delay to avoid hammering the API
        time.sleep(0.5)

    print()
    print('─' * 60)
    print(f"Customers fetched : {stats['customers_fetched']}/{total_customers}")
    print(f"Activities checked: {stats['activities_checked']}")
    print(f"Sales updated     : {stats['updated']}")
    print(f"Errors            : {stats['errors']}")

    # Log to DB (unless dry-run)
    if not dry_run:
        log_status = 'success' if stats['errors'] == 0 else ('error' if stats['customers_fetched'] == 0 else 'warning')
        notes_text = (
            f"Customers {stats['customers_fetched']}/{total_customers}, "
            f"activities {stats['activities_checked']}, "
            f"sales updated {stats['updated']}."
        )
        if error_notes:
            notes_text += ' Errors: ' + '; '.join(error_notes[:5])
            if len(error_notes) > 5:
                notes_text += f' … (+{len(error_notes) - 5} more)'

        SyncLog.objects.create(
            script_name='sync_anthill_workflow',
            status=log_status,
            records_created=0,
            records_updated=stats['updated'],
            errors=stats['errors'],
            notes=notes_text,
        )
        print(f'\n✓ SyncLog entry written (status={log_status}).')

    print('Done.')


# ════════════════════════════════════════════════════════════════════════
# Entry point
# ════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description='Refresh Anthill workflow status for existing sales.')
    parser.add_argument('--dry-run', action='store_true', help='Report changes without writing to the database')
    parser.add_argument('--days', type=int, default=None, help='Only refresh sales active/updated within this many days')
    args = parser.parse_args()

    if not ANTHILL_USERNAME or not ANTHILL_PASSWORD:
        print('ERROR: ANTHILL_USERNAME / ANTHILL_PASSWORD not set in environment.')
        sys.exit(1)

    sync_workflow(dry_run=args.dry_run, days=args.days)


if __name__ == '__main__':
    main()
