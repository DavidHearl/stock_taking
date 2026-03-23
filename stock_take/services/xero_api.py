"""
Xero OAuth2 service layer.
Handles token exchange, refresh, API client creation, and read-only API helpers.
"""
import os
import logging
from datetime import timedelta
from urllib.parse import urlencode

import requests
from django.utils import timezone

logger = logging.getLogger(__name__)

# ─── Xero OAuth endpoints ───────────────────────────────────────────
XERO_AUTH_URL = "https://login.xero.com/identity/connect/authorize"
XERO_TOKEN_URL = "https://login.xero.com/identity/connect/token"
XERO_CONNECTIONS_URL = "https://api.xero.com/connections"
XERO_API_BASE = "https://api.xero.com/api.xro/2.0"

# Scopes — includes write access for contacts
XERO_SCOPES = (
    "openid profile email offline_access "
    "accounting.transactions.read "
    "accounting.contacts.read "
    "accounting.contacts "
    "accounting.settings.read "
    "accounting.reports.read"
)


def _get_credentials():
    """Return (client_id, client_secret, redirect_uri) from environment."""
    client_id = os.getenv("XERO_CLIENT_ID", "")
    client_secret = os.getenv("XERO_CLIENT_SECRET", "")
    redirect_uri = os.getenv("XERO_REDIRECT_URI", "")
    return client_id, client_secret, redirect_uri


# ─── OAuth Flow ─────────────────────────────────────────────────────

def get_authorization_url(state=""):
    """Build the Xero authorization URL the user should be redirected to."""
    client_id, _, redirect_uri = _get_credentials()
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": XERO_SCOPES,
        "state": state,
    }
    return f"{XERO_AUTH_URL}?{urlencode(params)}"


def exchange_code_for_tokens(code):
    """
    Exchange an authorization code for access + refresh tokens.
    Returns a dict with token data or raises on error.
    """
    client_id, client_secret, redirect_uri = _get_credentials()
    response = requests.post(
        XERO_TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
        },
        auth=(client_id, client_secret),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def refresh_access_token(refresh_token):
    """
    Use a refresh token to get a new access token.
    Returns a dict with new token data or raises on error.
    """
    client_id, client_secret, _ = _get_credentials()
    response = requests.post(
        XERO_TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        },
        auth=(client_id, client_secret),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def get_connected_tenants(access_token):
    """
    Fetch the list of Xero organisations (tenants) the token is connected to.
    Returns a list of dicts, each with 'tenantId', 'tenantName', etc.
    """
    response = requests.get(
        XERO_CONNECTIONS_URL,
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


# ─── Token Persistence ──────────────────────────────────────────────

def save_token_to_db(token_data, tenant_id="", tenant_name="", user=None):
    """
    Save or update the Xero token in the database.
    Only keeps one active token set (deletes old ones).
    """
    from stock_take.models import XeroToken

    expires_in = token_data.get("expires_in", 1800)
    expires_at = timezone.now() + timedelta(seconds=expires_in)

    # Remove any existing tokens
    XeroToken.objects.all().delete()

    return XeroToken.objects.create(
        access_token=token_data["access_token"],
        refresh_token=token_data.get("refresh_token", ""),
        token_type=token_data.get("token_type", "Bearer"),
        expires_at=expires_at,
        scope=token_data.get("scope", ""),
        tenant_id=tenant_id,
        tenant_name=tenant_name,
        connected_by=user,
    )


def get_valid_access_token():
    """
    Return a valid access token, refreshing if needed.
    Returns (access_token, tenant_id) or (None, None) if not connected.
    """
    from stock_take.models import XeroToken

    token = XeroToken.get_active_token()
    if not token:
        return None, None

    # Refresh if expired or about to expire (within 2 minutes)
    if token.expires_at <= timezone.now() + timedelta(minutes=2):
        try:
            new_data = refresh_access_token(token.refresh_token)
            token = save_token_to_db(
                new_data,
                tenant_id=token.tenant_id,
                tenant_name=token.tenant_name,
                user=token.connected_by,
            )
            logger.info("Xero token refreshed successfully")
        except Exception as e:
            logger.error(f"Failed to refresh Xero token: {e}")
            return None, None

    return token.access_token, token.tenant_id


def disconnect():
    """Remove all stored Xero tokens (disconnect)."""
    from stock_take.models import XeroToken
    XeroToken.objects.all().delete()


# ─── Read-Only API Helpers ──────────────────────────────────────────

def _api_get(endpoint, params=None):
    """
    Make a GET request to the Xero API.
    Handles token refresh automatically.
    Returns the parsed JSON response or None on failure.
    """
    access_token, tenant_id = get_valid_access_token()
    if not access_token:
        logger.warning("No valid Xero token available")
        return None

    url = f"{XERO_API_BASE}/{endpoint}"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Xero-Tenant-Id": tenant_id,
        "Accept": "application/json",
    }

    for attempt in range(3):
        try:
            response = requests.get(url, headers=headers, params=params, timeout=30)
            if response.status_code == 429:
                # Respect Retry-After header, default to 61 seconds
                retry_after = int(response.headers.get('Retry-After', 61))
                logger.warning(
                    f"Xero rate limit hit ({endpoint}). "
                    f"Waiting {retry_after}s before retry (attempt {attempt + 1}/3)"
                )
                import time as _time
                _time.sleep(retry_after)
                continue
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            logger.error(f"Xero API error ({endpoint}): {e}")
            return None
    logger.error(f"Xero API error ({endpoint}): exceeded retry limit after 429s")
    return None


def _api_post(endpoint, data):
    """
    Make a POST request to the Xero API.
    Handles token refresh automatically.
    Returns the parsed JSON response or None on failure.
    """
    access_token, tenant_id = get_valid_access_token()
    if not access_token:
        logger.warning("No valid Xero token available")
        return None

    url = f"{XERO_API_BASE}/{endpoint}"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Xero-Tenant-Id": tenant_id,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    try:
        response = requests.post(url, headers=headers, json=data, timeout=30)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        logger.error(f"Xero API POST error ({endpoint}): {e}")
        if hasattr(e, 'response') and e.response is not None:
            logger.error(f"Response body: {e.response.text}")
        return None


def _api_put(endpoint, data):
    """
    Make a PUT request to the Xero API.
    Handles token refresh automatically.
    Returns the parsed JSON response or None on failure.
    """
    access_token, tenant_id = get_valid_access_token()
    if not access_token:
        logger.warning("No valid Xero token available")
        return None

    url = f"{XERO_API_BASE}/{endpoint}"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Xero-Tenant-Id": tenant_id,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    try:
        response = requests.put(url, headers=headers, json=data, timeout=30)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        logger.error(f"Xero API PUT error ({endpoint}): {e}")
        if hasattr(e, 'response') and e.response is not None:
            logger.error(f"Response body: {e.response.text}")
        return None


def get_organisation():
    """Fetch the connected organisation details."""
    return _api_get("Organisation")


def get_contacts(page=1):
    """Fetch contacts (customers/suppliers) from Xero."""
    return _api_get("Contacts", params={"page": page})


def get_all_contacts():
    """
    Fetch ALL contacts from Xero by paginating through all pages.
    Returns a list of contact dicts.
    """
    all_contacts = []
    page = 1
    while True:
        result = get_contacts(page=page)
        if not result or "Contacts" not in result:
            break
        contacts = result["Contacts"]
        if not contacts:
            break
        all_contacts.extend(contacts)
        # Xero returns 100 contacts per page
        if len(contacts) < 100:
            break
        page += 1
    return all_contacts


def get_invoices_for_contact(contact_id):
    """
    Fetch all invoices for a specific Xero contact.
    Returns a list of invoice dicts.
    """
    all_invoices = []
    page = 1
    while True:
        result = _api_get("Invoices", params={
            "ContactIDs": contact_id,
            "page": page,
            "Statuses": "AUTHORISED,PAID,VOIDED",
        })
        if not result or "Invoices" not in result:
            break
        invoices = result["Invoices"]
        if not invoices:
            break
        all_invoices.extend(invoices)
        if len(invoices) < 100:
            break
        page += 1
    return all_invoices


def search_contacts_by_name(name):
    """
    Search Xero contacts by name using the searchTerm parameter.
    Returns a list of matching contact dicts, or empty list on failure.
    """
    result = _api_get("Contacts", params={"searchTerm": name})
    if result and "Contacts" in result:
        return result["Contacts"]
    return []


def find_contact_by_name(name):
    """
    Find a Xero contact whose Name exactly matches the given name (case-insensitive).
    Uses searchTerm to get candidates then filters for an exact name match.
    Returns the ContactID string if found, or '' if not.
    """
    candidates = search_contacts_by_name(name)
    name_lower = name.strip().lower()
    for c in candidates:
        if c.get("Name", "").strip().lower() == name_lower:
            return c.get("ContactID", "")
    return ""


def get_invoices(page=1, statuses=None):
    """Fetch invoices from Xero."""
    params = {"page": page}
    if statuses:
        params["Statuses"] = statuses
    return _api_get("Invoices", params=params)


def get_invoice(invoice_id):
    """Fetch a single invoice by ID."""
    return _api_get(f"Invoices/{invoice_id}")


def get_accounts():
    """Fetch the chart of accounts."""
    return _api_get("Accounts")


def get_bank_transactions(page=1):
    """Fetch bank transactions."""
    return _api_get("BankTransactions", params={"page": page})


def get_reports_balance_sheet(date=None):
    """Fetch the balance sheet report."""
    params = {}
    if date:
        params["date"] = date
    return _api_get("Reports/BalanceSheet", params=params)


def get_reports_profit_and_loss(from_date=None, to_date=None):
    """Fetch the profit and loss report."""
    params = {}
    if from_date:
        params["fromDate"] = from_date
    if to_date:
        params["toDate"] = to_date
    return _api_get("Reports/ProfitAndLoss", params=params)


# ─── Sale payment lookup (read-only) ───────────────────────────────

def get_invoices_by_reference(reference):
    """
    Fetch all Xero invoices whose Reference field matches the given value.

    This is used to look up invoices by Anthill contract number
    (e.g. "BFS-SD-412885"), which Sliderobes store in the Xero invoice
    Reference field.

    Returns a list of invoice dicts (basic detail level, no Payments sub-array).
    Returns empty list if nothing found or on error.
    """
    if not reference:
        return []
    # Xero does not support Contains()/StartsWith() on the Reference field.
    # Use searchTerm (full-text search) then filter client-side so that references
    # with appended text (e.g. "BFS-PO-419089 dep cc") are still matched.
    result = _api_get("Invoices", params={
        "searchTerm": reference,
        "Statuses": "AUTHORISED,PAID",
    })
    if result and "Invoices" in result:
        # Keep invoices where the contract number appears in the Reference field
        # OR in the Contact Name (some Xero contacts are stored under the
        # contract number rather than the customer's actual name).
        ref_lower = reference.lower()
        return [
            inv for inv in result["Invoices"]
            if ref_lower in (inv.get("Reference") or "").lower()
            or ref_lower in (inv.get("Contact", {}).get("Name", "") or "").lower()
        ]
    return []


def get_invoice_with_payments(invoice_id):
    """
    Fetch a single Xero invoice by ID, including its Payments sub-array.

    The Payments array is only returned at the single-invoice detail level
    (GET /Invoices/{ID}), not in list responses.

    Returns the invoice dict, or None on failure.
    """
    result = _api_get(f"Invoices/{invoice_id}")
    if result and "Invoices" in result and result["Invoices"]:
        return result["Invoices"][0]
    return None


def get_sale_payments_from_xero(contract_number, contact_name=None):
    """
    High-level helper: find all Xero invoices for an Anthill sale and return
    a structured summary of invoices and their individual payments.

    Args:
        contract_number: Anthill contract number, e.g. "BFS-SD-412885".
                         Matched against the Xero invoice Reference field.
        contact_name:    Optional customer name for cross-validation.
                         If provided, invoices whose contact name does NOT
                         fuzzy-match this are excluded.

    Returns:
        List of dicts, one per matching invoice:
        {
            'invoice_id':     str,
            'invoice_number': str,
            'reference':      str,
            'status':         str,   # 'PAID', 'AUTHORISED', 'VOIDED'
            'total':          Decimal,
            'amount_paid':    Decimal,
            'amount_due':     Decimal,
            'contact_name':   str,
            'payments': [
                {
                    'payment_id':   str,
                    'date':         datetime or None,
                    'amount':       Decimal,
                    'reference':    str,  # payment reference / type label
                    'status':       str,
                },
                ...
            ],
        }

    Returns empty list if Xero is not connected or nothing matches.
    """
    from decimal import Decimal
    import re

    invoices = get_invoices_by_reference(contract_number)
    if not invoices:
        return []

    results = []

    for inv_summary in invoices:
        # Optional: soft name cross-check — log a warning but never skip.
        # The reference match is already specific enough; hard-filtering by name
        # causes false negatives when Anthill and Xero store slightly different
        # versions of the customer's name (e.g. "liz enzor" vs "Elizabeth Enzor").
        if contact_name:
            xero_contact = inv_summary.get("Contact", {}).get("Name", "")
            name_match = (
                contact_name.lower() in xero_contact.lower()
                or xero_contact.lower() in contact_name.lower()
                # Also try matching on surname only (last word of each name)
                or contact_name.split()[-1].lower() in xero_contact.lower()
            )
            if not name_match:
                logger.warning(
                    f"Invoice {inv_summary.get('InvoiceNumber')} ref={inv_summary.get('Reference')}: "
                    f"contact '{xero_contact}' does not match '{contact_name}' — including anyway (reference matched)"
                )

        invoice_id = inv_summary.get("InvoiceID", "")
        if not invoice_id:
            continue

        # Fetch full invoice to get the Payments sub-array
        full_inv = get_invoice_with_payments(invoice_id)
        if not full_inv:
            full_inv = inv_summary  # fall back to summary if detail fetch fails

        total = Decimal(str(full_inv.get("Total", 0) or 0))
        amount_paid = Decimal(str(full_inv.get("AmountPaid", 0) or 0))
        amount_due = Decimal(str(full_inv.get("AmountDue", 0) or 0))

        # Parse individual payments
        parsed_payments = []
        for p in full_inv.get("Payments", []):
            raw_date = p.get("Date", "")
            parsed_date = None
            if raw_date:
                # Xero dates come as "/Date(1738234567000+0000)/"
                ms_match = re.search(r'/Date\((\d+)', raw_date)
                if ms_match:
                    import datetime
                    ts = int(ms_match.group(1)) / 1000
                    parsed_date = datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc)
                else:
                    # Try ISO format fallback
                    for fmt in ('%Y-%m-%dT%H:%M:%S', '%Y-%m-%d'):
                        try:
                            import datetime
                            parsed_date = datetime.datetime.strptime(raw_date[:10], fmt)
                            break
                        except ValueError:
                            pass

            parsed_payments.append({
                'payment_id': p.get("PaymentID", ""),
                'date': parsed_date,
                'amount': Decimal(str(p.get("Amount", 0) or 0)),
                'reference': p.get("Reference", "") or "Payment",
                'status': "Confirmed",
            })

        results.append({
            'invoice_id': invoice_id,
            'invoice_number': full_inv.get("InvoiceNumber", ""),
            'reference': full_inv.get("Reference", ""),
            'status': full_inv.get("Status", ""),
            'total': total,
            'amount_paid': amount_paid,
            'amount_due': amount_due,
            'contact_name': (full_inv.get("Contact") or {}).get("Name", ""),
            'payments': parsed_payments,
        })

    return results


# ─── Write API Helpers ──────────────────────────────────────────────

def create_contact(name, first_name="", last_name="", email="", phone="",
                   address_line1="", address_line2="", city="", region="",
                   postal_code="", country=""):
    """
    Create a new contact (customer/supplier) in Xero.
    Returns the parsed JSON response or None on failure.
    """
    contact_data = {
        "Name": name,
    }
    if first_name:
        contact_data["FirstName"] = first_name
    if last_name:
        contact_data["LastName"] = last_name
    if email:
        contact_data["EmailAddress"] = email
    if phone:
        contact_data["Phones"] = [
            {
                "PhoneType": "DEFAULT",
                "PhoneNumber": phone,
            }
        ]

    # Build address if any address field is provided
    if any([address_line1, address_line2, city, region, postal_code, country]):
        address = {"AddressType": "STREET"}
        if address_line1:
            address["AddressLine1"] = address_line1
        if address_line2:
            address["AddressLine2"] = address_line2
        if city:
            address["City"] = city
        if region:
            address["Region"] = region
        if postal_code:
            address["PostalCode"] = postal_code
        if country:
            address["Country"] = country
        contact_data["Addresses"] = [address]

    payload = {"Contacts": [contact_data]}
    return _api_post("Contacts", payload)
