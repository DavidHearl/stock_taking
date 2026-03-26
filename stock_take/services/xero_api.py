"""
Xero OAuth2 service layer.
Handles token exchange, refresh, API client creation, and read-only API helpers.
"""
import os
import logging
import time as _time
from collections import deque
from datetime import timedelta
from urllib.parse import urlencode

import requests
from django.utils import timezone

logger = logging.getLogger(__name__)


class XeroDailyLimitExceeded(Exception):
    """Raised when Xero's daily API call limit (5,000/day) is exhausted."""
    pass


# ─── Rate Limiter ────────────────────────────────────────────────────
# Xero allows 60 API calls per minute per tenant.
# We target 45/minute to leave headroom and avoid 429s.
_RATE_LIMIT = 45
_RATE_WINDOW = 60          # seconds
_call_timestamps = deque()  # timestamps of recent API calls
_api_call_count = 0         # total API calls this process


def get_api_call_count():
    """Return the total number of Xero API calls made this process."""
    return _api_call_count


def _rate_limit_wait():
    """Sleep if necessary to stay within the Xero rate limit."""
    now = _time.monotonic()
    # Purge timestamps older than the window
    while _call_timestamps and _call_timestamps[0] <= now - _RATE_WINDOW:
        _call_timestamps.popleft()
    if len(_call_timestamps) >= _RATE_LIMIT:
        # Wait until the oldest call in the window expires
        sleep_for = _call_timestamps[0] - (now - _RATE_WINDOW) + 0.5
        if sleep_for > 0:
            logger.info(f"Rate limiter: {len(_call_timestamps)} calls in last {_RATE_WINDOW}s, sleeping {sleep_for:.1f}s")
            _time.sleep(sleep_for)
    _call_timestamps.append(_time.monotonic())

# ─── Xero OAuth endpoints ───────────────────────────────────────────
XERO_AUTH_URL = "https://login.xero.com/identity/connect/authorize"
XERO_TOKEN_URL = "https://login.xero.com/identity/connect/token"
XERO_CONNECTIONS_URL = "https://api.xero.com/connections"
XERO_API_BASE = "https://api.xero.com/api.xro/2.0"

# Scopes — includes write access for contacts and transactions (purchase orders)
XERO_SCOPES = (
    "openid profile email offline_access "
    "accounting.transactions "
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

    global _api_call_count
    for attempt in range(3):
        _rate_limit_wait()
        try:
            _api_call_count += 1
            response = requests.get(url, headers=headers, params=params, timeout=30)
            if response.status_code == 429:
                # Check if this is a daily limit (X-Rate-Limit-Problem header)
                limit_problem = response.headers.get('X-Rate-Limit-Problem', '').lower()
                raw_retry = int(response.headers.get('Retry-After', 61))

                if limit_problem == 'daily' or raw_retry > 3600:
                    raise XeroDailyLimitExceeded(
                        f"Xero daily API limit reached ({_api_call_count} calls this session). "
                        f"Resets in ~{raw_retry // 3600}h {(raw_retry % 3600) // 60}m. "
                        f"Reduce API calls or try again later."
                    )

                # Per-minute limit: Xero sometimes returns milliseconds
                retry_after = raw_retry / 1000 if raw_retry > 120 else raw_retry
                retry_after = max(1, min(retry_after, 120))  # clamp 1-120s
                logger.warning(
                    f"Xero rate limit hit ({endpoint}). "
                    f"Retry-After={raw_retry}, waiting {retry_after:.0f}s (attempt {attempt + 1}/3)"
                )
                _time.sleep(retry_after)
                continue
            response.raise_for_status()
            return response.json()
        except XeroDailyLimitExceeded:
            raise
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

    global _last_api_error
    try:
        response = requests.post(url, headers=headers, json=data, timeout=30)
        response.raise_for_status()
        _last_api_error = None
        return response.json()
    except requests.RequestException as e:
        error_detail = str(e)
        if hasattr(e, 'response') and e.response is not None:
            error_detail = e.response.text
            logger.error(f"Response body: {error_detail}")
        logger.error(f"Xero API POST error ({endpoint}): {e}")
        _last_api_error = error_detail
        return None


_last_api_error = None


def get_last_api_error():
    """Return the last API error message for user-facing diagnostics."""
    return _last_api_error


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

    global _last_api_error
    try:
        response = requests.put(url, headers=headers, json=data, timeout=30)
        response.raise_for_status()
        _last_api_error = None
        return response.json()
    except requests.RequestException as e:
        error_detail = str(e)
        if hasattr(e, 'response') and e.response is not None:
            error_detail = e.response.text
            logger.error(f"Response body: {error_detail}")
        logger.error(f"Xero API PUT error ({endpoint}): {e}")
        _last_api_error = error_detail
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


def get_bank_transactions_for_contact(contact_id):
    """
    Fetch all RECEIVE-type bank transactions for a specific Xero contact.
    These are "Receive money" entries that don't appear as invoices.
    Returns a list of bank transaction dicts.
    """
    all_txns = []
    page = 1
    while True:
        result = _api_get("BankTransactions", params={
            "where": f'Contact.ContactID==guid("{contact_id}") AND Type=="RECEIVE"',
            "page": page,
        })
        if not result or "BankTransactions" not in result:
            break
        txns = result["BankTransactions"]
        if not txns:
            break
        all_txns.extend(txns)
        if len(txns) < 100:
            break
        page += 1
    return all_txns


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
        # Keep invoices where the contract number appears as a complete token
        # in the Reference or Contact Name.  A "complete token" means the
        # match is followed by end-of-string, a space, or other non-alnum
        # character.  This prevents "BFS-PO-4083" from matching
        # "BFS-PO-408357" (the digits continue beyond the search term).
        import re
        ref_lower = reference.lower()
        # Escape for regex, then require a word boundary after the match
        pattern = re.compile(re.escape(ref_lower) + r'(?![0-9])', re.IGNORECASE)
        return [
            inv for inv in result["Invoices"]
            if pattern.search(inv.get("Reference") or "")
            or pattern.search((inv.get("Contact", {}).get("Name", "") or ""))
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


def get_overpayment_detail(overpayment_id):
    """Fetch a single overpayment by ID to get full Allocations detail."""
    result = _api_get(f"Overpayments/{overpayment_id}")
    if result and "Overpayments" in result and result["Overpayments"]:
        return result["Overpayments"][0]
    return None


def get_prepayment_detail(prepayment_id):
    """Fetch a single prepayment by ID to get full Allocations detail."""
    result = _api_get(f"Prepayments/{prepayment_id}")
    if result and "Prepayments" in result and result["Prepayments"]:
        return result["Prepayments"][0]
    return None


def get_creditnote_detail(creditnote_id):
    """Fetch a single credit note by ID to get full Allocations detail."""
    result = _api_get(f"CreditNotes/{creditnote_id}")
    if result and "CreditNotes" in result and result["CreditNotes"]:
        return result["CreditNotes"][0]
    return None


# ─── Bulk invoice fetching (reduces API calls dramatically) ─────────

def bulk_fetch_invoices(search_prefix="BFS", statuses="AUTHORISED,PAID"):
    """
    Fetch all invoices matching search_prefix via Xero's searchTerm,
    paginating through all pages.

    For 736 sales this replaces 736 individual searchTerm queries
    with ~10-20 paginated calls (100 invoices per page).

    Returns a list of invoice summary dicts (no Payments sub-array).
    """
    all_invoices = []
    page = 1
    while True:
        result = _api_get("Invoices", params={
            "searchTerm": search_prefix,
            "Statuses": statuses,
            "page": page,
        })
        if not result or "Invoices" not in result:
            break
        invoices = result["Invoices"]
        if not invoices:
            break
        all_invoices.extend(invoices)
        logger.info(f"Bulk fetch page {page}: {len(invoices)} invoices ({len(all_invoices)} total)")
        if len(invoices) < 100:
            break
        page += 1
    return all_invoices


def build_reference_index(invoices):
    """
    Build a dict mapping contract number (upper-cased) -> list of invoice dicts.

    Extracts BFS-XX-NNNNNN patterns from each invoice's Reference field.
    This allows O(1) lookup instead of per-sale API calls.
    """
    import re
    index = {}
    pattern = re.compile(r'BFS-[A-Z]+-\d+', re.IGNORECASE)
    for inv in invoices:
        ref = inv.get("Reference", "") or ""
        for match in pattern.finditer(ref):
            key = match.group().upper()
            index.setdefault(key, []).append(inv)
    return index


def get_sale_payments_from_xero(contract_number, contact_name=None, prefetched_invoices=None):
    """
    High-level helper: find all Xero invoices for an Anthill sale and return
    a structured summary of invoices and their individual payments.

    Args:
        contract_number: Anthill contract number, e.g. "BFS-SD-412885".
                         Matched against the Xero invoice Reference field.
        contact_name:    Optional customer name for cross-validation.
                         If provided, invoices whose contact name does NOT
                         fuzzy-match this are excluded.
        prefetched_invoices: Optional list of invoice summary dicts from a bulk
                         fetch.  When provided, skips the per-sale API search
                         and uses these directly (saving 1 API call per sale).

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
    import datetime

    def _parse_xero_date(raw_date):
        """Parse a Xero date string into a datetime, or None."""
        if not raw_date:
            return None
        ms_match = re.search(r'/Date\((\d+)', raw_date)
        if ms_match:
            ts = int(ms_match.group(1)) / 1000
            return datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc)
        for fmt in ('%Y-%m-%dT%H:%M:%S', '%Y-%m-%d'):
            try:
                return datetime.datetime.strptime(raw_date[:10], fmt)
            except ValueError:
                pass
        return None

    # ── Step 1: find invoices by contract-number reference ──
    if prefetched_invoices is not None:
        # Use pre-fetched data from bulk_fetch_invoices — no API call needed
        all_invoices = prefetched_invoices
    else:
        # Fallback: individual API search (expensive — 1 call per sale)
        all_invoices = get_invoices_by_reference(contract_number)

    # Extract contact_id for bank transaction lookup later
    contact_id = None
    for inv in all_invoices:
        cid = (inv.get("Contact") or {}).get("ContactID")
        if cid:
            contact_id = cid
            break

    if not contact_id and contact_name:
        contact_id = find_contact_by_name(contact_name)

    if not all_invoices and not contact_id:
        return []

    results = []

    for inv_summary in all_invoices:
        # Skip voided/deleted invoices
        status = (inv_summary.get("Status") or "").upper()
        if status in ("VOIDED", "DELETED"):
            continue

        invoice_id = inv_summary.get("InvoiceID", "")
        if not invoice_id:
            continue

        # Fetch full invoice to get Payments, Overpayments, Prepayments, CreditNotes
        full_inv = get_invoice_with_payments(invoice_id)
        if not full_inv:
            full_inv = inv_summary

        total = Decimal(str(full_inv.get("Total", 0) or 0))
        amount_paid = Decimal(str(full_inv.get("AmountPaid", 0) or 0))
        amount_due = Decimal(str(full_inv.get("AmountDue", 0) or 0))

        # Parse direct payments
        parsed_payments = []
        for p in full_inv.get("Payments", []):
            pid = p.get("PaymentID", "")
            parsed_payments.append({
                'payment_id': pid,
                'base_payment_id': pid,
                'is_fallback': False,
                'date': _parse_xero_date(p.get("Date", "")),
                'amount': Decimal(str(p.get("Amount", 0) or 0)),
                'reference': p.get("Reference", "") or "Payment",
                'status': "Confirmed",
            })

        # Parse overpayment allocations (e.g. "Receive money" applied to invoice)
        for op in full_inv.get("Overpayments", []):
            op_id = op.get("OverpaymentID", "")
            # Determine the amount allocated to THIS specific invoice.
            # The Allocations sub-array shows per-invoice amounts.
            allocated_amount = Decimal('0')
            alloc_date = None
            is_fallback = False
            allocations = op.get("Allocations", [])
            # If the nested response lacks Allocations, fetch the full overpayment
            if not allocations and op_id:
                full_op = get_overpayment_detail(op_id)
                if full_op:
                    allocations = full_op.get("Allocations", [])
            for alloc in allocations:
                alloc_inv = (alloc.get("Invoice") or {}).get("InvoiceID", "")
                if alloc_inv == invoice_id:
                    allocated_amount = Decimal(str(alloc.get("Amount", 0) or 0))
                    alloc_date = _parse_xero_date(alloc.get("Date", ""))
                    break
            if allocated_amount <= 0 and allocations:
                # No matching allocation found but allocations exist — skip
                # (this overpayment wasn't allocated to this invoice)
                continue
            if allocated_amount <= 0:
                # No Allocations array — fallback to AppliedAmount or Total
                allocated_amount = Decimal(str(op.get("AppliedAmount", 0) or op.get("Total", 0) or 0))
                is_fallback = True
            if allocated_amount <= 0:
                continue
            # Make payment_id unique per invoice allocation to avoid collisions
            unique_pid = f"{op_id}_{invoice_id}" if op_id else ""
            parsed_payments.append({
                'payment_id': unique_pid,
                'base_payment_id': op_id,
                'is_fallback': is_fallback,
                'date': alloc_date or _parse_xero_date(op.get("Date", "")),
                'amount': allocated_amount,
                'reference': "Overpayment",
                'status': "Confirmed",
            })

        # Parse prepayment allocations
        for pp in full_inv.get("Prepayments", []):
            pp_id = pp.get("PrepaymentID", "")
            allocated_amount = Decimal('0')
            alloc_date = None
            is_fallback = False
            allocations = pp.get("Allocations", [])
            if not allocations and pp_id:
                full_pp = get_prepayment_detail(pp_id)
                if full_pp:
                    allocations = full_pp.get("Allocations", [])
            for alloc in allocations:
                alloc_inv = (alloc.get("Invoice") or {}).get("InvoiceID", "")
                if alloc_inv == invoice_id:
                    allocated_amount = Decimal(str(alloc.get("Amount", 0) or 0))
                    alloc_date = _parse_xero_date(alloc.get("Date", ""))
                    break
            if allocated_amount <= 0 and allocations:
                continue
            if allocated_amount <= 0:
                allocated_amount = Decimal(str(pp.get("AppliedAmount", 0) or pp.get("Total", 0) or 0))
                is_fallback = True
            if allocated_amount <= 0:
                continue
            unique_pid = f"{pp_id}_{invoice_id}" if pp_id else ""
            parsed_payments.append({
                'payment_id': unique_pid,
                'base_payment_id': pp_id,
                'is_fallback': is_fallback,
                'date': alloc_date or _parse_xero_date(pp.get("Date", "")),
                'amount': allocated_amount,
                'reference': "Prepayment",
                'status': "Confirmed",
            })

        # Parse credit note allocations
        for cn in full_inv.get("CreditNotes", []):
            cn_id = cn.get("CreditNoteID", "")
            allocated_amount = Decimal('0')
            alloc_date = None
            is_fallback = False
            allocations = cn.get("Allocations", [])
            if not allocations and cn_id:
                full_cn = get_creditnote_detail(cn_id)
                if full_cn:
                    allocations = full_cn.get("Allocations", [])
            for alloc in allocations:
                alloc_inv = (alloc.get("Invoice") or {}).get("InvoiceID", "")
                if alloc_inv == invoice_id:
                    allocated_amount = Decimal(str(alloc.get("Amount", 0) or 0))
                    alloc_date = _parse_xero_date(alloc.get("Date", ""))
                    break
            if allocated_amount <= 0 and allocations:
                continue
            if allocated_amount <= 0:
                allocated_amount = Decimal(str(cn.get("AppliedAmount", 0) or cn.get("Total", 0) or 0))
                is_fallback = True
            if allocated_amount <= 0:
                continue
            unique_pid = f"{cn_id}_{invoice_id}" if cn_id else ""
            parsed_payments.append({
                'payment_id': unique_pid,
                'base_payment_id': cn_id,
                'is_fallback': is_fallback,
                'date': alloc_date or _parse_xero_date(cn.get("Date", "")),
                'amount': allocated_amount,
                'reference': "Credit Note",
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

    # ── Step 3: fetch "Receive money" bank transactions matching this contract ──
    # These are direct bank receipts that don't appear as invoices.
    # Only include transactions whose reference contains the contract number
    # to avoid cross-contamination between different jobs for the same customer.
    if contact_id:
        # Collect all payment IDs we already have to avoid duplicates
        existing_payment_ids = set()
        for r in results:
            for p in r['payments']:
                if p['payment_id']:
                    existing_payment_ids.add(p['payment_id'])

        contract_lower = contract_number.lower()
        bank_txns = get_bank_transactions_for_contact(contact_id)
        for txn in bank_txns:
            txn_ref = txn.get("Reference", "") or ""
            # Only include transactions whose reference matches this contract
            if contract_lower not in txn_ref.lower():
                continue
            txn_id = txn.get("BankTransactionID", "")
            if not txn_id or txn_id in existing_payment_ids:
                continue
            txn_status = (txn.get("Status") or "").upper()
            if txn_status in ("VOIDED", "DELETED"):
                continue
            txn_total = Decimal(str(txn.get("Total", 0) or 0))
            if txn_total <= 0:
                continue
            results.append({
                'invoice_id': txn_id,
                'invoice_number': 'BankTxn',
                'reference': txn_ref,
                'status': txn_status or 'PAID',
                'total': txn_total,
                'amount_paid': txn_total,
                'amount_due': Decimal('0'),
                'contact_name': (txn.get("Contact") or {}).get("Name", ""),
                'payments': [{
                    'payment_id': txn_id,
                    'base_payment_id': txn_id,
                    'date': _parse_xero_date(txn.get("Date", "")),
                    'amount': txn_total,
                    'reference': 'Receive Money',
                    'status': 'Confirmed',
                }],
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


def create_purchase_order(contact_name, po_number, line_items, date=None,
                          delivery_date=None, reference="", currency="GBP",
                          status="AUTHORISED", delivery_address=None):
    """
    Create a purchase order in Xero with status AUTHORISED (approved).

    Looks up the supplier by name in Xero to get their ContactID.
    """
    # Look up the contact by name to get the ContactID
    contact_id = find_contact_by_name(contact_name)
    if not contact_id:
        global _last_api_error
        _last_api_error = f'Supplier "{contact_name}" not found in Xero. Create the contact in Xero first.'
        return None

    po_data = {
        "Contact": {"ContactID": contact_id},
        "PurchaseOrderNumber": po_number,
        "Status": status,
        "CurrencyCode": currency,
        "LineItems": [],
    }

    if date:
        po_data["Date"] = date
    if delivery_date:
        po_data["DeliveryDate"] = delivery_date
    if reference:
        po_data["Reference"] = reference

    if delivery_address:
        po_data["DeliveryAddress"] = delivery_address

    for item in line_items:
        line = {
            "Description": item.get("description", ""),
            "Quantity": str(item.get("quantity", 1)),
            "UnitAmount": str(item.get("unit_amount", 0)),
        }
        if item.get("account_code"):
            line["AccountCode"] = item["account_code"]
        if item.get("tax_type"):
            line["TaxType"] = item["tax_type"]
        if item.get("item_code"):
            line["ItemCode"] = item["item_code"]
        po_data["LineItems"].append(line)

    payload = {"PurchaseOrders": [po_data]}
    result = _api_put("PurchaseOrders", payload)
    if result is None:
        # Retry with POST — some Xero configurations prefer POST for creation
        logger.info("PUT failed for PurchaseOrders, retrying with POST")
        result = _api_post("PurchaseOrders", payload)
    return result
