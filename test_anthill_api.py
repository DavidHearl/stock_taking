"""
Test script for Anthill CRM SOAP API.

Tests the connection and pulls customers using the FindCustomers method.
Usage: python test_anthill_api.py
"""

import os
import xml.etree.ElementTree as ET
from dotenv import load_dotenv
import requests

load_dotenv()

# ── Config ──────────────────────────────────────────────────────────────
ANTHILL_USERNAME = os.getenv('ANTHILL_USERNAME')
ANTHILL_PASSWORD = os.getenv('ANTHILL_PASSWORD')
SUBDOMAIN = 'sliderobes'
BASE_URL = f'https://{SUBDOMAIN}.anthillcrm.com/api/v1.asmx'

NAMESPACE = 'http://www.anthill.co.uk/'


def soap_request(action: str, body_xml: str) -> str:
    """Send a SOAP request to Anthill and return the response text."""
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

    response = requests.post(BASE_URL, data=envelope.encode('utf-8'), headers=headers, timeout=30)
    print(f"\n{'='*60}")
    print(f"Action: {action}")
    print(f"Status: {response.status_code}")

    if response.status_code != 200:
        print(f"Error Response:\n{response.text[:2000]}")

    return response.text


def test_get_locations():
    """Test 1: Get Locations - simplest call to verify auth works."""
    print("\n" + "="*60)
    print("TEST 1: GetLocations (verify authentication)")
    print("="*60)

    body = f'<GetLocations xmlns="{NAMESPACE}" />'
    response_text = soap_request('GetLocations', body)

    # Parse the response
    root = ET.fromstring(response_text)
    ns = {'ah': NAMESPACE}

    locations = root.findall('.//ah:Location', ns)
    if locations:
        print(f"\n✓ Authentication successful! Found {len(locations)} locations:")
        for loc in locations:
            loc_id = loc.find('ah:LocationId', ns)
            label = loc.find('ah:Label', ns)
            print(f"  - ID: {loc_id.text if loc_id is not None else 'N/A'}, "
                  f"Label: {label.text if label is not None else 'N/A'}")
    else:
        print("\n✗ No locations found. Check credentials or response:")
        print(response_text[:1000])


def test_get_customer_types():
    """Test 2: Get Customer Types - see what fields are available."""
    print("\n" + "="*60)
    print("TEST 2: GetCustomerTypes (see available fields)")
    print("="*60)

    body = f'<GetCustomerTypes xmlns="{NAMESPACE}" />'
    response_text = soap_request('GetCustomerTypes', body)

    # Pretty print the XML
    try:
        root = ET.fromstring(response_text)
        print("\nCustomer type fields found in response:")
        # Print the raw result section for inspection
        result = root.find('.//{%s}GetCustomerTypesResult' % NAMESPACE)
        if result is not None:
            print(ET.tostring(result, encoding='unicode')[:3000])
        else:
            print(response_text[:2000])
    except ET.ParseError:
        print(response_text[:2000])


def test_find_customers(page=1, page_size=10):
    """Test 3: FindCustomers - pull a page of customers."""
    print("\n" + "="*60)
    print(f"TEST 3: FindCustomers (page {page}, size {page_size})")
    print("="*60)

    # Use a broad search criterion - find all customers created in the last ~10 years
    body = f'''<FindCustomers xmlns="{NAMESPACE}">
      <searchCriteria>
        <SearchCriteria>
          <FieldName>Created</FieldName>
          <Operation>DaysBetween</Operation>
          <Args>0,3650</Args>
        </SearchCriteria>
      </searchCriteria>
      <pageNumber>{page}</pageNumber>
      <pageSize>{page_size}</pageSize>
    </FindCustomers>'''

    response_text = soap_request('FindCustomers', body)

    try:
        root = ET.fromstring(response_text)
        # Print raw result for inspection
        result = root.find('.//{%s}FindCustomersResult' % NAMESPACE)
        if result is not None:
            result_text = ET.tostring(result, encoding='unicode')
            print(f"\nResponse ({len(result_text)} chars):")
            print(result_text[:3000])
        else:
            print("\nNo FindCustomersResult found. Full response:")
            print(response_text[:3000])
    except ET.ParseError:
        print(response_text[:3000])


def test_get_customer_details(customer_id: int):
    """Test 4: GetCustomerDetails - get full details for one customer."""
    print("\n" + "="*60)
    print(f"TEST 4: GetCustomerDetails (customer ID: {customer_id})")
    print("="*60)

    body = f'''<GetCustomerDetails xmlns="{NAMESPACE}">
      <customerId>{customer_id}</customerId>
      <includeActivity>false</includeActivity>
    </GetCustomerDetails>'''

    response_text = soap_request('GetCustomerDetails', body)

    try:
        root = ET.fromstring(response_text)
        result = root.find('.//{%s}GetCustomerDetailsResult' % NAMESPACE)
        if result is not None:
            # Extract key fields
            cust_id = result.find('{%s}CustomerId' % NAMESPACE)
            name = result.find('{%s}Name' % NAMESPACE)
            print(f"\nCustomer ID: {cust_id.text if cust_id is not None else 'N/A'}")
            print(f"Name: {name.text if name is not None else 'N/A'}")

            # Print custom fields
            custom_fields = result.findall('.//{%s}CustomField' % NAMESPACE)
            if custom_fields:
                print(f"\nCustom Fields ({len(custom_fields)}):")
                for cf in custom_fields:
                    key = cf.find('{%s}Key' % NAMESPACE)
                    val = cf.find('{%s}Value' % NAMESPACE)
                    print(f"  {key.text if key is not None else '?'}: "
                          f"{val.text if val is not None else ''}")

            # Print full XML for inspection
            print(f"\nFull XML:")
            print(ET.tostring(result, encoding='unicode')[:3000])
        else:
            print("\nNo result found. Full response:")
            print(response_text[:3000])
    except ET.ParseError:
        print(response_text[:3000])


if __name__ == '__main__':
    if not ANTHILL_USERNAME or not ANTHILL_PASSWORD:
        print("ERROR: Set ANTHILL_USERNAME and ANTHILL_PASSWORD in your .env file")
        exit(1)

    print(f"Anthill API Test")
    print(f"URL: {BASE_URL}")
    print(f"Username: {ANTHILL_USERNAME}")

    # Test 1: Verify auth with GetLocations
    test_get_locations()

    # Test 2: See customer field schema
    test_get_customer_types()

    # Test 3: Pull first page of customers
    test_find_customers(page=1, page_size=5)

    # Test 4: Get details for a specific customer (uncomment and set ID after Test 3)
    test_get_customer_details(273121)

    print("\n" + "="*60)
    print("DONE - Review output above to understand the data structure")
    print("="*60)
