"""
Anthill CRM SOAP API client.

Handles authentication and provides methods for interacting with
the Anthill CRM system (customers, contacts, sales, etc.).
"""

import os
import time
import logging
import xml.etree.ElementTree as ET
import requests
from datetime import datetime

logger = logging.getLogger(__name__)

NAMESPACE = 'http://www.anthill.co.uk/'


class AnthillAPIError(Exception):
    """Raised when an Anthill API call fails."""
    pass


class AnthillAPI:
    """
    Wrapper around the Anthill CRM SOAP API.

    Usage::

        api = AnthillAPI()
        customers = api.find_customers(page=1, page_size=100)
        details = api.get_customer_details(273121)
    """

    def __init__(self):
        self.username = os.getenv('ANTHILL_USERNAME')
        self.password = os.getenv('ANTHILL_PASSWORD')
        self.subdomain = os.getenv('ANTHILL_SUBDOMAIN', 'sliderobes')
        self.base_url = f'https://{self.subdomain}.anthillcrm.com/api/v1.asmx'

        if not self.username or not self.password:
            raise AnthillAPIError('ANTHILL_USERNAME and ANTHILL_PASSWORD must be set in .env')

    # ------------------------------------------------------------------
    # Core SOAP transport
    # ------------------------------------------------------------------
    def _soap_request(self, action: str, body_xml: str, timeout: int = 60) -> ET.Element:
        """Send a SOAP request and return the parsed XML root element."""
        envelope = f'''<?xml version="1.0" encoding="utf-8"?>
<soap12:Envelope xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
                 xmlns:xsd="http://www.w3.org/2001/XMLSchema"
                 xmlns:soap12="http://www.w3.org/2003/05/soap-envelope">
  <soap12:Header>
    <AuthHeader xmlns="{NAMESPACE}">
      <Username>{self.username}</Username>
      <Password>{self.password}</Password>
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

        try:
            response = requests.post(
                self.base_url,
                data=envelope.encode('utf-8'),
                headers=headers,
                timeout=timeout,
            )
        except requests.exceptions.RequestException as exc:
            raise AnthillAPIError(f'Network error calling {action}: {exc}')

        if response.status_code != 200:
            # Try to extract SOAP fault message
            try:
                root = ET.fromstring(response.text)
                fault_text = root.find('.//{http://www.w3.org/2003/05/soap-envelope}Text')
                if fault_text is not None:
                    raise AnthillAPIError(f'{action} failed: {fault_text.text}')
            except ET.ParseError:
                pass
            raise AnthillAPIError(
                f'{action} failed with status {response.status_code}: {response.text[:500]}'
            )

        try:
            return ET.fromstring(response.text)
        except ET.ParseError as exc:
            raise AnthillAPIError(f'Failed to parse response for {action}: {exc}')

    def _get_text(self, element, tag: str) -> str:
        """Get text content from a child element, or empty string."""
        child = element.find(f'{{{NAMESPACE}}}{tag}')
        if child is not None and child.text:
            return child.text.strip()
        return ''

    # ------------------------------------------------------------------
    # Get Locations
    # ------------------------------------------------------------------
    def get_locations(self) -> list[dict]:
        """Return a list of all active locations."""
        body = f'<GetLocations xmlns="{NAMESPACE}" />'
        root = self._soap_request('GetLocations', body)

        locations = []
        for loc in root.findall(f'.//{{{NAMESPACE}}}Location'):
            locations.append({
                'id': int(self._get_text(loc, 'LocationId') or 0),
                'label': self._get_text(loc, 'Label'),
                'description': self._get_text(loc, 'Description'),
                'telephone': self._get_text(loc, 'Telephone'),
                'email': self._get_text(loc, 'Email'),
            })
        return locations

    # ------------------------------------------------------------------
    # Find Customers (paginated search)
    # ------------------------------------------------------------------
    def find_customers(
        self,
        page: int = 1,
        page_size: int = 1000,
        days_back: int = 3650,
    ) -> dict:
        """
        Search for customers. Returns a dict with pagination info and results.

        Args:
            page: Page number (1-based)
            page_size: Results per page (max 1000)
            days_back: How many days back to search (default ~10 years)

        Returns:
            {
                'total_records': int,
                'total_pages': int,
                'page_number': int,
                'records_per_page': int,
                'customers': [{'id': int, 'name': str, 'type': str, 'created': str, 'location': str}]
            }
        """
        body = f'''<FindCustomers xmlns="{NAMESPACE}">
          <searchCriteria>
            <SearchCriteria>
              <FieldName>Created</FieldName>
              <Operation>DaysBetween</Operation>
              <Args>0,{days_back}</Args>
            </SearchCriteria>
          </searchCriteria>
          <pageNumber>{page}</pageNumber>
          <pageSize>{page_size}</pageSize>
        </FindCustomers>'''

        root = self._soap_request('FindCustomers', body, timeout=120)
        result = root.find(f'.//{{{NAMESPACE}}}FindCustomersResult')

        if result is None:
            return {
                'total_records': 0,
                'total_pages': 0,
                'page_number': page,
                'records_per_page': page_size,
                'customers': [],
            }

        customers = []
        for csr in result.findall(f'.//{{{NAMESPACE}}}CustomerSearchResult'):
            customers.append({
                'id': int(self._get_text(csr, 'Id') or 0),
                'name': self._get_text(csr, 'Name'),
                'type': self._get_text(csr, 'Type'),
                'created': self._get_text(csr, 'Created'),
                'location': self._get_text(csr, 'Location'),
            })

        return {
            'total_records': int(self._get_text(result, 'TotalRecords') or 0),
            'total_pages': int(self._get_text(result, 'TotalPages') or 0),
            'page_number': int(self._get_text(result, 'PageNumber') or page),
            'records_per_page': int(self._get_text(result, 'RecordsPerPage') or page_size),
            'customers': customers,
        }

    # ------------------------------------------------------------------
    # Get Customer Details
    # ------------------------------------------------------------------
    def get_customer_details(self, customer_id: int, include_activity: bool = False) -> dict:
        """
        Get full details for a single customer.

        Returns:
            {
                'customer_id': int,
                'name': str,
                'location_id': int,
                'location_label': str,
                'custom_fields': {key: value, ...},
                'address': {address1, address2, city, county, country, postcode},
            }
        """
        body = f'''<GetCustomerDetails xmlns="{NAMESPACE}">
          <customerId>{customer_id}</customerId>
          <includeActivity>{'true' if include_activity else 'false'}</includeActivity>
        </GetCustomerDetails>'''

        root = self._soap_request('GetCustomerDetails', body, timeout=30)
        result = root.find(f'.//{{{NAMESPACE}}}GetCustomerDetailsResult')

        if result is None:
            raise AnthillAPIError(f'No details returned for customer {customer_id}')

        # Parse location
        location = result.find(f'{{{NAMESPACE}}}Location')
        location_id = 0
        location_label = ''
        if location is not None:
            location_id = int(self._get_text(location, 'LocationId') or 0)
            location_label = self._get_text(location, 'Label')

        # Parse custom fields
        custom_fields = {}
        for cf in result.findall(f'.//{{{NAMESPACE}}}CustomField'):
            key = self._get_text(cf, 'Key')
            value = self._get_text(cf, 'Value')
            if key:
                custom_fields[key] = value

        # Parse address
        address_el = result.find(f'{{{NAMESPACE}}}Address')
        address = {}
        if address_el is not None:
            for field in ['Address1', 'Address2', 'City', 'County', 'Country', 'Postcode']:
                address[field.lower()] = self._get_text(address_el, field)

        return {
            'customer_id': int(self._get_text(result, 'CustomerId') or customer_id),
            'name': self._get_text(result, 'Name'),
            'location_id': location_id,
            'location_label': location_label,
            'custom_fields': custom_fields,
            'address': address,
        }

    # ------------------------------------------------------------------
    # Get Sales Modified Since (for finding customers with sales)
    # ------------------------------------------------------------------
    def get_sales_customer_ids(self, since: str = '2016-02-13T00:00:00', type_id: int = 1) -> set[str]:
        """
        Iterate through all sales modified since a date and return
        the set of unique customer IDs that have at least one sale.

        Args:
            since: ISO datetime string for the start date
            type_id: Sale type ID (1 = "Room" for Sliderobes)

        Returns:
            Set of customer ID strings
        """
        import time

        customer_ids = set()
        total_sales = 0
        batch = 0
        current_since = since

        while True:
            body = f'''<GetSalesModifiedSince xmlns="{NAMESPACE}">
              <typeId>{type_id}</typeId>
              <since>{current_since}</since>
            </GetSalesModifiedSince>'''

            root = self._soap_request('GetSalesModifiedSince', body, timeout=120)
            result_el = root.find(f'.//{{{NAMESPACE}}}GetSalesModifiedSinceResult')

            if result_el is None:
                break

            # Parse inner XML for Activity elements
            result_xml = ET.tostring(result_el, encoding='unicode')
            try:
                inner = ET.fromstring(result_xml)
            except ET.ParseError:
                inner = result_el

            activities = inner.findall('.//Activity')
            if not activities:
                break

            batch += 1
            last_date = current_since

            for act in activities:
                total_sales += 1
                cust_el = act.find('Customer')
                if cust_el is not None:
                    cust_id = cust_el.get('id', '')
                    if cust_id:
                        customer_ids.add(cust_id)

                updated = act.findtext('LastUpdated') or act.findtext('Created') or ''
                if updated:
                    last_date = updated

            if len(activities) < 100:
                break
            else:
                current_since = last_date
                time.sleep(0.05)

        logger.info(f'Anthill: Found {total_sales:,} sales across {len(customer_ids):,} unique customers')
        return customer_ids

    # ------------------------------------------------------------------
    # Iterate all customers (generator)
    # ------------------------------------------------------------------
    def iter_all_customers(self, page_size: int = 1000, days_back: int = 36500):
        """
        Generator that yields all customer search results, auto-paginating.

        Args:
            page_size: Results per page (max 1000)
            days_back: How many days back to search (default ~100 years = all records)

        Yields dicts with: id, name, type, created, location
        """
        page = 1
        max_retries = 3
        while True:
            # Retry logic for transient timeouts
            result = None
            for attempt in range(1, max_retries + 1):
                try:
                    result = self.find_customers(page=page, page_size=page_size, days_back=days_back)
                    break
                except AnthillAPIError as e:
                    if 'Timeout' in str(e) or 'timed out' in str(e):
                        wait = 5 * attempt
                        logger.warning(
                            f'Anthill: Timeout on page {page} (attempt {attempt}/{max_retries}), '
                            f'retrying in {wait}s...'
                        )
                        time.sleep(wait)
                        if attempt == max_retries:
                            raise
                    else:
                        raise
            total = result['total_records']

            if page == 1:
                logger.info(f'Anthill: {total:,} total customers across {result["total_pages"]:,} pages')

            for customer in result['customers']:
                yield customer

            if page >= result['total_pages']:
                break
            page += 1

    # ------------------------------------------------------------------
    # Get Customer Types (field schema)
    # ------------------------------------------------------------------
    def get_customer_types(self) -> str:
        """Return the raw XML string of customer type definitions."""
        body = f'<GetCustomerTypes xmlns="{NAMESPACE}" />'
        root = self._soap_request('GetCustomerTypes', body)
        result = root.find(f'.//{{{NAMESPACE}}}GetCustomerTypesResult')
        if result is not None:
            return ET.tostring(result, encoding='unicode')
        return ''
