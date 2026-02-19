#!/usr/bin/env python
"""
sync_anthill_customers.py
─────────────────────────
Standalone script to import customers from Anthill CRM into the
stock_taking Django database.

Two-phase sync:
  Phase 1 — Sales: Scan all pages and fetch sale activities for
            customers already in the local database.
  Phase 2 — New Customers: Scan all pages again and import any
            customers/leads not yet in the database.

Classification:
  • Customers with a WorkGuruClientID or a completed "Sale" activity
    are saved as Customer (sale).
  • All others are saved as Lead.

Usage
─────
  # Full sync (both phases)
  python sync_anthill_customers.py

  # Only sync sales for existing customers
  python sync_anthill_customers.py --sales-only

  # Only import new customers (skip sales phase)
  python sync_anthill_customers.py --skip-sales

  # Import last 365 days only
  python sync_anthill_customers.py --days 365

  # Dry-run (count only, don't write to DB)
  python sync_anthill_customers.py --dry-run
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
# Allow this file to be run from the project root.
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'stock_taking.settings')

import django
django.setup()

from stock_take.models import Customer, Lead, AnthillSale  # noqa: E402

# ── Anthill config ──────────────────────────────────────────────────────
load_dotenv(os.path.join(BASE_DIR, '.env'))
ANTHILL_USERNAME = os.getenv('ANTHILL_USERNAME')
ANTHILL_PASSWORD = os.getenv('ANTHILL_PASSWORD')
SUBDOMAIN = 'sliderobes'
BASE_URL = f'https://{SUBDOMAIN}.anthillcrm.com/api/v1.asmx'
NAMESPACE = 'http://www.anthill.co.uk/'
NS = {'ah': NAMESPACE}

# Activity statuses / types that indicate a completed sale
SALE_ACTIVITY_STATUSES = {'complete', 'completed', 'sold', 'won'}
SALE_ACTIVITY_TYPES_KEYWORDS = {'sale'}


# ════════════════════════════════════════════════════════════════════════
# Anthill SOAP helpers
# ════════════════════════════════════════════════════════════════════════

def soap_request(action: str, body_xml: str, retries: int = 3) -> str:
    """Send a SOAP request to Anthill, with retry on transient errors."""
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

    for attempt in range(1, retries + 1):
        try:
            resp = requests.post(
                BASE_URL, data=envelope.encode('utf-8'),
                headers=headers, timeout=60,
            )
            if resp.status_code == 200:
                return resp.text
            if resp.status_code >= 500 and attempt < retries:
                wait = 2 ** attempt
                print(f'  ⚠ Server error {resp.status_code}, retrying in {wait}s …')
                time.sleep(wait)
                continue
            resp.raise_for_status()
        except requests.exceptions.Timeout:
            if attempt < retries:
                wait = 2 ** attempt
                print(f'  ⚠ Timeout, retrying in {wait}s …')
                time.sleep(wait)
            else:
                raise
    return ''


def find_customers(page: int, page_size: int, days: int) -> tuple:
    """
    Call FindCustomers and return (total_records, total_pages, list_of_dicts).
    Each dict has: id, name, type, created, location
    """
    body = f'''<FindCustomers xmlns="{NAMESPACE}">
  <searchCriteria>
    <SearchCriteria>
      <FieldName>Created</FieldName>
      <Operation>DaysBetween</Operation>
      <Args>0,{days}</Args>
    </SearchCriteria>
  </searchCriteria>
  <pageNumber>{page}</pageNumber>
  <pageSize>{page_size}</pageSize>
</FindCustomers>'''

    text = soap_request('FindCustomers', body)
    root = ET.fromstring(text)

    total_records = int(_text(root, './/ah:TotalRecords') or '0')
    total_pages = int(_text(root, './/ah:TotalPages') or '0')

    results = []
    for node in root.findall('.//ah:CustomerSearchResult', NS):
        results.append({
            'id': _text(node, 'ah:Id'),
            'name': _text(node, 'ah:Name'),
            'type': _text(node, 'ah:Type'),
            'created': _text(node, 'ah:Created'),
            'location': _text(node, 'ah:Location'),
        })

    return total_records, total_pages, results


def get_customer_detail(customer_id: int) -> dict:
    """Fetch full details for a single customer."""
    body = f'''<GetCustomerDetails xmlns="{NAMESPACE}">
  <customerId>{customer_id}</customerId>
  <includeActivity>true</includeActivity>
</GetCustomerDetails>'''

    text = soap_request('GetCustomerDetails', body)
    root = ET.fromstring(text)
    result = root.find(f'.//{{{NAMESPACE}}}GetCustomerDetailsResult')
    if result is None:
        return {}

    # Parse custom fields into a dict
    custom = {}
    for cf in result.findall(f'.//{{{NAMESPACE}}}CustomField'):
        key = cf.findtext(f'{{{NAMESPACE}}}Key', '')
        val = cf.findtext(f'{{{NAMESPACE}}}Value', '')
        if key:
            custom[key] = val or ''

    # Parse address
    addr_node = result.find(f'{{{NAMESPACE}}}Address')
    address = {}
    if addr_node is not None:
        address = {
            'address_1': addr_node.findtext(f'{{{NAMESPACE}}}Address1', ''),
            'address_2': addr_node.findtext(f'{{{NAMESPACE}}}Address2', ''),
            'city': addr_node.findtext(f'{{{NAMESPACE}}}City', '').strip(),
            'county': addr_node.findtext(f'{{{NAMESPACE}}}County', '').strip(),
            'country': addr_node.findtext(f'{{{NAMESPACE}}}Country', '').strip(),
        }

    # Parse activities
    activities = []
    for act in result.findall(f'.//{{{NAMESPACE}}}RecentActivityModel'):
        activities.append({
            'category': act.findtext(f'{{{NAMESPACE}}}Category', ''),
            'id': act.findtext(f'{{{NAMESPACE}}}Id', ''),
            'type': act.findtext(f'{{{NAMESPACE}}}Type', ''),
            'created': act.findtext(f'{{{NAMESPACE}}}Created', ''),
            'status': act.findtext(f'{{{NAMESPACE}}}Status', ''),
        })

    return {
        'customer_id': result.findtext(f'{{{NAMESPACE}}}CustomerId', ''),
        'name': result.findtext(f'{{{NAMESPACE}}}Name', ''),
        'custom_fields': custom,
        'address': address,
        'activities': activities,
    }


def _text(node, path: str) -> str:
    """Safe findtext with namespace map."""
    el = node.find(path, NS)
    return el.text.strip() if el is not None and el.text else ''


# ════════════════════════════════════════════════════════════════════════
# Classification
# ════════════════════════════════════════════════════════════════════════

def is_sale(detail: dict) -> bool:
    """Determine if a customer detail represents a sale (vs lead)."""
    # 1. Has WorkGuruClientID → definitely a sale
    wg_id = detail.get('custom_fields', {}).get('WorkGuruClientID', '')
    if wg_id:
        return True

    # 2. Has an activity whose status indicates a completed sale
    for act in detail.get('activities', []):
        status = (act.get('status') or '').lower()
        act_type = (act.get('type') or '').lower()
        if status in SALE_ACTIVITY_STATUSES and any(kw in act_type for kw in SALE_ACTIVITY_TYPES_KEYWORDS):
            return True

    return False


def parse_datetime(s: str):
    """Parse an Anthill datetime string like '2025-09-18T11:44:30.58'."""
    if not s:
        return None
    from datetime import timezone as _tz
    # Anthill returns varying precision on fractional seconds
    for fmt in ('%Y-%m-%dT%H:%M:%S.%f', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d'):
        try:
            naive = datetime.strptime(s, fmt)
            return naive.replace(tzinfo=_tz.utc)
        except ValueError:
            continue
    return None


# ════════════════════════════════════════════════════════════════════════
# Database operations
# ════════════════════════════════════════════════════════════════════════

def save_as_customer(detail: dict, summary: dict) -> str:
    """
    Save the Anthill customer as a Customer record.
    Returns 'created', 'exists', or 'updated'.
    """
    anthill_id = str(detail.get('customer_id') or summary['id'])
    cf = detail.get('custom_fields', {})
    addr = detail.get('address', {})
    anthill_date = parse_datetime(summary.get('created', ''))

    existing = Customer.objects.filter(anthill_customer_id=anthill_id).first()
    if existing:
        # Update anthill_created_date if not set
        if not existing.anthill_created_date and anthill_date:
            existing.anthill_created_date = anthill_date
            existing.location = summary.get('location', '') or existing.location
            existing.save(update_fields=['anthill_created_date', 'location'])
            return 'updated'
        return 'exists'

    # Also check if there's a Customer via WorkGuruClientID
    wg_id = cf.get('WorkGuruClientID', '')
    if wg_id:
        try:
            existing = Customer.objects.get(workguru_id=int(wg_id))
            # Link the anthill_customer_id
            existing.anthill_customer_id = anthill_id
            existing.anthill_created_date = anthill_date
            existing.location = summary.get('location', '') or existing.location
            existing.save(update_fields=['anthill_customer_id', 'anthill_created_date', 'location'])
            return 'updated'
        except (Customer.DoesNotExist, ValueError):
            pass

    Customer.objects.create(
        anthill_customer_id=anthill_id,
        first_name=cf.get('First Name', ''),
        last_name=cf.get('Last Name', ''),
        name=detail.get('name', '') or summary.get('name', ''),
        email=cf.get('Email', '') or None,
        phone=cf.get('Telephone', '') or None,
        address_1=addr.get('address_1', '') or None,
        address_2=addr.get('address_2', '') or None,
        city=addr.get('city', '') or None,
        state=addr.get('county', '') or None,
        country=addr.get('country', '') or None,
        anthill_created_date=anthill_date,
        location=summary.get('location', ''),
        is_active=True,
    )
    return 'created'


def save_as_lead(detail: dict, summary: dict) -> str:
    """
    Save the Anthill customer as a Lead record.
    Returns 'created', 'exists', or 'updated'.
    """
    anthill_id = str(detail.get('customer_id') or summary['id'])
    cf = detail.get('custom_fields', {})
    addr = detail.get('address', {})
    anthill_date = parse_datetime(summary.get('created', ''))

    existing = Lead.objects.filter(anthill_customer_id=anthill_id).first()
    if existing:
        if not existing.anthill_created_date and anthill_date:
            existing.anthill_created_date = anthill_date
            existing.location = summary.get('location', '') or existing.location
            existing.save(update_fields=['anthill_created_date', 'location'])
            return 'updated'
        return 'exists'

    # Determine status from activities
    lead_status = 'new'
    activities = detail.get('activities', [])
    if activities:
        latest_status = (activities[0].get('status') or '').lower()
        if latest_status in ('open',):
            lead_status = 'new'
        elif latest_status in ('converted',):
            lead_status = 'qualified'
        elif latest_status in ('closed', 'lost', 'cancelled'):
            lead_status = 'lost'
        elif latest_status in ('complete', 'completed'):
            lead_status = 'converted'

    Lead.objects.create(
        anthill_customer_id=anthill_id,
        name=detail.get('name', '') or summary.get('name', ''),
        email=cf.get('Email', '') or None,
        phone=cf.get('Telephone', '') or None,
        mobile=cf.get('Mobile', '') or None,
        address_1=addr.get('address_1', '') or None,
        address_2=addr.get('address_2', '') or None,
        city=addr.get('city', '') or None,
        state=addr.get('county', '') or None,
        postcode=None,
        country=addr.get('country', '') or None,
        anthill_created_date=anthill_date,
        location=summary.get('location', ''),
        status=lead_status,
        source='other',
        notes=cf.get('Notes', '') or None,
    )
    return 'created'


def save_sales_from_activities(detail: dict, summary: dict, customer_obj=None) -> int:
    """
    Save sale activities from a customer's detail record as AnthillSale objects.
    Returns the number of sales created.
    """
    created_count = 0
    anthill_customer_id = str(detail.get('customer_id') or summary.get('id', ''))
    customer_name = detail.get('name', '') or summary.get('name', '')
    location = summary.get('location', '')

    for act in detail.get('activities', []):
        act_id = act.get('id', '')
        if not act_id:
            continue

        # Only save if it doesn't already exist
        if AnthillSale.objects.filter(anthill_activity_id=act_id).exists():
            continue

        activity_date = parse_datetime(act.get('created', ''))

        # Try to find a local customer if not provided
        local_customer = customer_obj
        if not local_customer and anthill_customer_id:
            local_customer = Customer.objects.filter(anthill_customer_id=anthill_customer_id).first()

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


# ════════════════════════════════════════════════════════════════════════
# Main
# ════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description='Import customers from Anthill CRM into the local database.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Examples:
  python sync_anthill_customers.py                       # Last 10 years
  python sync_anthill_customers.py --days 365            # Last 1 year
  python sync_anthill_customers.py --from-date 2024-01-01 --to-date 2024-12-31
  python sync_anthill_customers.py --dry-run             # Count only
  python sync_anthill_customers.py --start-page 50       # Resume from page 50
        ''',
    )
    parser.add_argument('--days', type=int, default=3650,
                        help='Number of days to look back (default: 3650 ≈ 10 years)')
    parser.add_argument('--from-date', type=str, default=None,
                        help='Start date filter YYYY-MM-DD (alternative to --days)')
    parser.add_argument('--to-date', type=str, default=None,
                        help='End date filter YYYY-MM-DD (used with --from-date)')
    parser.add_argument('--page-size', type=int, default=200,
                        help='Records per API page (default: 200)')
    parser.add_argument('--start-page', type=int, default=1,
                        help='Page to start from (for resuming)')
    parser.add_argument('--max-pages', type=int, default=0,
                        help='Max pages to process (0 = all)')
    parser.add_argument('--dry-run', action='store_true',
                        help='Count records without saving to DB')
    parser.add_argument('--skip-detail', action='store_true',
                        help='Only save summary data (faster, but less info)')
    parser.add_argument('--skip-sales', action='store_true',
                        help='Skip Phase 1 (sales sync) and go straight to new customers')
    parser.add_argument('--sales-only', action='store_true',
                        help='Only run Phase 1 (sales sync), skip new customer import')
    args = parser.parse_args()

    # ── Validate credentials ──
    if not ANTHILL_USERNAME or not ANTHILL_PASSWORD:
        print('ERROR: Set ANTHILL_USERNAME and ANTHILL_PASSWORD in your .env file')
        sys.exit(1)

    # ── Calculate days from date range if provided ──
    days = args.days
    if args.from_date:
        try:
            from_dt = datetime.strptime(args.from_date, '%Y-%m-%d')
            to_dt = datetime.strptime(args.to_date, '%Y-%m-%d') if args.to_date else datetime.now()
            days = (datetime.now() - from_dt).days
        except ValueError:
            print('ERROR: Dates must be in YYYY-MM-DD format')
            sys.exit(1)
    else:
        from_dt = None
        to_dt = None

    # ── Initial scan ──
    print('═' * 65)
    print('  Anthill CRM → Local Database Sync')
    print('═' * 65)
    print(f'  API URL   : {BASE_URL}')
    print(f'  Lookback  : {days} days')
    if from_dt:
        print(f'  Date range: {from_dt.strftime("%Y-%m-%d")} → {(to_dt or datetime.now()).strftime("%Y-%m-%d")}')
    print(f'  Page size : {args.page_size}')
    print(f'  Dry run   : {"Yes" if args.dry_run else "No"}')
    print('─' * 65)

    print('\nScanning Anthill for customers …')
    total_records, total_pages, first_page = find_customers(1, args.page_size, days)
    print(f'  Total records : {total_records:,}')
    print(f'  Total pages   : {total_pages:,} (at {args.page_size}/page)')

    # ── Pre-load existing Anthill IDs for fast duplicate checking ──
    print('\nLoading existing records from database …')
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
    print(f'  Existing customers : {len(existing_customer_ids):,}')
    print(f'  Existing leads     : {len(existing_lead_ids):,}')
    print(f'  Total known IDs    : {len(existing_all):,}')

    # ── Process pages ──
    stats = {
        'scanned': 0,
        'skipped_existing': 0,
        'skipped_date': 0,
        'customers_created': 0,
        'customers_updated': 0,
        'leads_created': 0,
        'leads_updated': 0,
        'sales_created': 0,
        'errors': 0,
    }

    end_page = total_pages
    if args.max_pages > 0:
        end_page = min(args.start_page + args.max_pages - 1, total_pages)

    page_range_label = f'{args.start_page} → {end_page}'
    start_time = time.time()

    # ════════════════════════════════════════════════════════════════════
    # PHASE 1 — Sync sales for existing customers
    # ════════════════════════════════════════════════════════════════════
    if not args.skip_sales:
        print(f'\n╔═══════════════════════════════════════════════════════════════╗')
        print(f'║  PHASE 1 — Syncing sales for {len(existing_customer_ids):,} known customers')
        print(f'╚═══════════════════════════════════════════════════════════════╝')
        print(f'  Pages {page_range_label}\n')

        phase1_scanned = 0
        phase1_processed = 0

        for page_num in range(args.start_page, end_page + 1):
            if page_num == 1 and args.start_page == 1:
                summaries = first_page
            else:
                _, _, summaries = find_customers(page_num, args.page_size, days)

            if not summaries:
                break

            page_sales = 0

            for summary in summaries:
                phase1_scanned += 1
                anthill_id = str(summary['id'])

                # ── Date filter ──
                if from_dt or to_dt:
                    created = parse_datetime(summary.get('created', ''))
                    if created:
                        if from_dt and created < from_dt:
                            continue
                        if to_dt and created > to_dt:
                            continue

                # Only process existing customers in Phase 1
                if anthill_id not in existing_customer_ids:
                    continue

                if args.dry_run or args.skip_detail:
                    phase1_processed += 1
                    continue

                try:
                    detail = get_customer_detail(int(anthill_id))
                    if detail and detail.get('activities'):
                        customer_obj = Customer.objects.filter(
                            anthill_customer_id=anthill_id
                        ).first()
                        created_count = save_sales_from_activities(
                            detail, summary, customer_obj
                        )
                        stats['sales_created'] += created_count
                        page_sales += created_count
                    phase1_processed += 1
                except Exception as e:
                    stats['errors'] += 1
                    print(f'  ✗ Error fetching sales for {anthill_id}: {e}')

            # ── Page progress ──
            elapsed = time.time() - start_time
            rate = phase1_scanned / elapsed if elapsed > 0 else 0
            remaining = (total_records - phase1_scanned) / rate if rate > 0 else 0

            print(
                f'  Page {page_num:>5}/{end_page} | '
                f'Scanned: {phase1_scanned:>7,} | '
                f'Customers: {phase1_processed:>5,} | '
                f'Sales: {page_sales:>3} | '
                f'Rate: {rate:.0f}/s | '
                f'ETA: {_format_time(remaining)}'
            )

        phase1_elapsed = time.time() - start_time
        print(f'\n  Phase 1 complete in {_format_time(phase1_elapsed)}')
        print(f'  Sales created: {stats["sales_created"]:,}')

    # ════════════════════════════════════════════════════════════════════
    # PHASE 2 — Import new customers / leads
    # ════════════════════════════════════════════════════════════════════
    if not args.sales_only:
        phase2_start = time.time()
        print(f'\n╔═══════════════════════════════════════════════════════════════╗')
        print(f'║  PHASE 2 — Importing new customers & leads')
        print(f'╚═══════════════════════════════════════════════════════════════╝')
        print(f'  Pages {page_range_label}\n')

        phase2_scanned = 0

        for page_num in range(args.start_page, end_page + 1):
            if page_num == 1 and args.start_page == 1:
                summaries = first_page
            else:
                _, _, summaries = find_customers(page_num, args.page_size, days)

            if not summaries:
                break

            page_new = 0
            page_skip = 0

            for summary in summaries:
                phase2_scanned += 1
                stats['scanned'] += 1
                anthill_id = str(summary['id'])

                # ── Date filter ──
                if from_dt or to_dt:
                    created = parse_datetime(summary.get('created', ''))
                    if created:
                        if from_dt and created < from_dt:
                            stats['skipped_date'] += 1
                            continue
                        if to_dt and created > to_dt:
                            stats['skipped_date'] += 1
                            continue

                # Skip already-known records
                if anthill_id in existing_all:
                    stats['skipped_existing'] += 1
                    page_skip += 1
                    continue

                if args.dry_run:
                    page_new += 1
                    continue

                # ── Fetch detail ──
                try:
                    if args.skip_detail:
                        detail = {
                            'customer_id': anthill_id,
                            'name': summary.get('name', ''),
                            'custom_fields': {},
                            'address': {},
                            'activities': [],
                        }
                    else:
                        detail = get_customer_detail(int(anthill_id))
                        if not detail:
                            stats['errors'] += 1
                            continue

                    # ── Classify and save ──
                    if is_sale(detail):
                        result = save_as_customer(detail, summary)
                        if result == 'created':
                            stats['customers_created'] += 1
                        elif result == 'updated':
                            stats['customers_updated'] += 1
                        customer_obj = Customer.objects.filter(
                            anthill_customer_id=anthill_id
                        ).first()
                    else:
                        result = save_as_lead(detail, summary)
                        if result == 'created':
                            stats['leads_created'] += 1
                        elif result == 'updated':
                            stats['leads_updated'] += 1
                        customer_obj = None

                    # ── Save sale activities ──
                    if detail.get('activities'):
                        created_count = save_sales_from_activities(detail, summary, customer_obj)
                        stats['sales_created'] += created_count

                    existing_all.add(anthill_id)
                    page_new += 1

                except Exception as e:
                    stats['errors'] += 1
                    print(f'  ✗ Error on customer {anthill_id}: {e}')

            # ── Page progress ──
            elapsed = time.time() - phase2_start
            rate = phase2_scanned / elapsed if elapsed > 0 else 0
            remaining = (total_records - phase2_scanned) / rate if rate > 0 else 0

            print(
                f'  Page {page_num:>5}/{end_page} | '
                f'Scanned: {phase2_scanned:>7,} | '
                f'New: {page_new:>3} | Skip: {page_skip:>3} | '
                f'Rate: {rate:.0f}/s | '
                f'ETA: {_format_time(remaining)}'
            )

    # ── Summary ──
    total_elapsed = time.time() - start_time
    print('\n' + '═' * 65)
    print('  SYNC COMPLETE')
    print('═' * 65)
    print(f'  Time elapsed       : {_format_time(total_elapsed)}')
    print(f'  Records scanned    : {stats["scanned"]:,}')
    print(f'  Skipped (existing) : {stats["skipped_existing"]:,}')
    if stats['skipped_date']:
        print(f'  Skipped (date)     : {stats["skipped_date"]:,}')
    print(f'  Customers created  : {stats["customers_created"]:,}')
    print(f'  Customers updated  : {stats["customers_updated"]:,}')
    print(f'  Leads created      : {stats["leads_created"]:,}')
    print(f'  Leads updated      : {stats["leads_updated"]:,}')
    print(f'  Sales created      : {stats["sales_created"]:,}')
    if stats['errors']:
        print(f'  Errors             : {stats["errors"]:,}')
    print('═' * 65)

    if args.dry_run:
        new_count = stats['scanned'] - stats['skipped_existing'] - stats['skipped_date']
        print(f'\n  DRY RUN: {new_count:,} new records would be imported.')
        print('  Run again without --dry-run to save.')


def _format_time(seconds: float) -> str:
    """Format seconds into a human-readable string."""
    if seconds < 60:
        return f'{seconds:.0f}s'
    elif seconds < 3600:
        return f'{seconds / 60:.1f}m'
    else:
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        return f'{h}h {m}m'


if __name__ == '__main__':
    main()
