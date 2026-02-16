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

# Read-only scopes only — no write permissions
XERO_SCOPES = (
    "openid profile email offline_access "
    "accounting.transactions.read "
    "accounting.contacts.read "
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

    try:
        response = requests.get(url, headers=headers, params=params, timeout=30)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        logger.error(f"Xero API error ({endpoint}): {e}")
        return None


def get_organisation():
    """Fetch the connected organisation details."""
    return _api_get("Organisation")


def get_contacts(page=1):
    """Fetch contacts (customers/suppliers) from Xero."""
    return _api_get("Contacts", params={"page": page})


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
