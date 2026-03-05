#!/usr/bin/env python
"""
test_payments_api.py
─────────────────────
Discover the correct Anthill SOAP endpoint for payment history.

Tries several candidate endpoint names and dumps the raw XML response
so you can identify the right one and the exact field names to use
in get_sale_payments() / sync_anthill_payments.

Usage:
    python test_payments_api.py
    python test_payments_api.py --activity-id 417437
"""

import os
import sys
import argparse
import xml.etree.ElementTree as ET

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

from dotenv import load_dotenv
load_dotenv(os.path.join(BASE_DIR, '.env'))

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'stock_taking.settings')
import django
django.setup()

import requests
from stock_take.models import AnthillSale

USERNAME = os.getenv('ANTHILL_USERNAME')
PASSWORD = os.getenv('ANTHILL_PASSWORD')
NAMESPACE = 'http://www.anthill.co.uk/'
BASE_URL = 'https://sliderobes.anthillcrm.com/api/v1.asmx'


def soap_request(action: str, body_xml: str):
    envelope = f'''<?xml version="1.0" encoding="utf-8"?>
<soap12:Envelope xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
                 xmlns:xsd="http://www.w3.org/2001/XMLSchema"
                 xmlns:soap12="http://www.w3.org/2003/05/soap-envelope">
  <soap12:Header>
    <AuthHeader xmlns="{NAMESPACE}">
      <Username>{USERNAME}</Username>
      <Password>{PASSWORD}</Password>
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
    resp = requests.post(BASE_URL, data=envelope.encode('utf-8'), headers=headers, timeout=30)
    return resp


def print_xml(text: str, max_chars: int = 4000):
    """Pretty-print XML."""
    try:
        root = ET.fromstring(text)
        print(ET.tostring(root, encoding='unicode')[:max_chars])
    except ET.ParseError:
        print(text[:max_chars])


def print_all_children(node, indent=0):
    """Recursively print all XML children (for structure discovery)."""
    prefix = '  ' * indent
    tag = node.tag.replace(f'{{{NAMESPACE}}}', '').replace('{http://www.w3.org/2003/05/soap-envelope}', 'soap:')
    text = (node.text or '').strip()
    attrs = ' '.join(f'{k}={v!r}' for k, v in node.attrib.items())
    line = f'{prefix}<{tag}'
    if attrs:
        line += f' {attrs}'
    if text:
        line += f'>  →  {text[:100]}'
    else:
        line += '>'
    print(line)
    for child in node:
        print_all_children(child, indent + 1)


def try_endpoint(action: str, body_xml: str, label: str):
    print(f'\n{"=" * 60}')
    print(f'  Trying: {label}  →  {action}')
    print(f'{"=" * 60}')
    resp = soap_request(action, body_xml)
    print(f'  HTTP {resp.status_code}')

    if resp.status_code == 200:
        try:
            root = ET.fromstring(resp.text)
        except ET.ParseError:
            print('  ✗ Could not parse XML')
            print(resp.text[:500])
            return False

        # Check for a SOAP fault
        fault = root.find('.//{http://www.w3.org/2003/05/soap-envelope}Fault')
        if fault is not None:
            reason = fault.findtext('.//{http://www.w3.org/2003/05/soap-envelope}Text') or ''
            code = fault.findtext('.//{http://www.w3.org/2003/05/soap-envelope}Value') or ''
            print(f'  ✗ SOAP Fault  code={code}  reason={reason[:200]}')
            return False

        # Check for a result element
        result = root.find(f'.//{{{NAMESPACE}}}{action}Result')
        if result is None:
            # Generic search
            body_el = root.find('.//{http://www.w3.org/2003/05/soap-envelope}Body')
            if body_el is not None and len(body_el) > 0:
                print('  ✓ Got a response — dumping body structure:')
                print_all_children(body_el, indent=2)
            else:
                print('  ? Response body empty or unexpected — raw (truncated):')
                print(resp.text[:800])
            return True
        else:
            print(f'  ✓ Found {action}Result — dumping children:')
            print_all_children(result, indent=2)
            # Show raw XML too for copy/paste
            raw = ET.tostring(result, encoding='unicode')
            print(f'\n  Raw XML (first 3000 chars):\n{raw[:3000]}')
            return True
    else:
        print(f'  ✗ HTTP {resp.status_code}')
        print(resp.text[:300])
        return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--activity-id', type=str, default=None,
                        help='Anthill activity ID to test (defaults to most recent Category 3 sale)')
    args = parser.parse_args()

    if not USERNAME or not PASSWORD:
        print('ERROR: ANTHILL_USERNAME / ANTHILL_PASSWORD not set in .env')
        sys.exit(1)

    # Pick a test sale
    if args.activity_id:
        activity_id = args.activity_id
        print(f'Using provided activity ID: {activity_id}')
    else:
        sale = (
            AnthillSale.objects.filter(category='3')
            .exclude(anthill_activity_id='')
            .order_by('-activity_date')
            .first()
        )
        if not sale:
            sale = AnthillSale.objects.exclude(anthill_activity_id='').order_by('-activity_date').first()
        if not sale:
            print('No AnthillSale records found in the database.')
            sys.exit(1)
        activity_id = sale.anthill_activity_id
        print(f'Using sale: {activity_id} ({sale.customer_name}, status={sale.status})')

    print(f'\nTesting payment endpoints for activity ID: {activity_id}')
    print()

    # Candidate endpoints — try most likely first
    candidates = [
        (
            'GetSalePayments',
            f'<GetSalePayments xmlns="{NAMESPACE}"><saleId>{activity_id}</saleId></GetSalePayments>',
            'GetSalePayments (saleId)',
        ),
        (
            'GetSalePayments',
            f'<GetSalePayments xmlns="{NAMESPACE}"><activityId>{activity_id}</activityId></GetSalePayments>',
            'GetSalePayments (activityId)',
        ),
        (
            'GetActivityPayments',
            f'<GetActivityPayments xmlns="{NAMESPACE}"><activityId>{activity_id}</activityId></GetActivityPayments>',
            'GetActivityPayments (activityId)',
        ),
        (
            'GetPayments',
            f'<GetPayments xmlns="{NAMESPACE}"><saleId>{activity_id}</saleId></GetPayments>',
            'GetPayments (saleId)',
        ),
        (
            'GetSaleDeposits',
            f'<GetSaleDeposits xmlns="{NAMESPACE}"><saleId>{activity_id}</saleId></GetSaleDeposits>',
            'GetSaleDeposits (saleId)',
        ),
    ]

    found_any = False
    for action, body, label in candidates:
        ok = try_endpoint(action, body, label)
        if ok:
            found_any = True
            cont = input('\nContinue trying other endpoints? [Y/n] ').strip().lower()
            if cont == 'n':
                break

    if not found_any:
        print('\n✗ None of the candidate endpoints returned data.')
        print('  Try checking the Anthill WSDL:')
        print(f'  curl "{BASE_URL}?WSDL" | grep -i payment')
    else:
        print('\n✓ Done. Update anthill_api.py:get_sale_payments() and sync_anthill_payments.py')
        print('  with the confirmed endpoint name and XML field names above.')


if __name__ == '__main__':
    main()
