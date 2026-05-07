"""Microsoft Graph API helpers for the Accounts Payable mailbox integration.

Required Azure App Registration settings (set in .env):
    MS_GRAPH_TENANT_ID      – Azure AD / Entra ID tenant ID
    MS_GRAPH_CLIENT_ID      – App registration client (application) ID
    MS_GRAPH_CLIENT_SECRET  – App registration client secret value
    MS_GRAPH_MAILBOX        – Shared mailbox address (default: accounts.payable@sliderobes.com)

Required API permissions on the App Registration (Application type, NOT delegated):
    Mail.Read

After adding permissions, click "Grant admin consent" in the Azure portal.
"""

import base64
import logging

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

GRAPH_BASE = 'https://graph.microsoft.com/v1.0'
DEFAULT_MAILBOX = 'accounts.payable@sliderobes.com'


def _get_settings():
    return {
        'tenant_id': getattr(settings, 'MS_GRAPH_TENANT_ID', ''),
        'client_id': getattr(settings, 'MS_GRAPH_CLIENT_ID', ''),
        'client_secret': getattr(settings, 'MS_GRAPH_CLIENT_SECRET', ''),
        'mailbox': getattr(settings, 'MS_GRAPH_MAILBOX', DEFAULT_MAILBOX),
    }


def is_configured():
    """Return True if all required Graph API credentials are present."""
    s = _get_settings()
    return bool(s['tenant_id'] and s['client_id'] and s['client_secret'])


def _get_token():
    """Acquire an app-only access token via OAuth2 client credentials flow.

    Returns (access_token, None) on success or (None, error_message) on failure.
    """
    s = _get_settings()
    if not is_configured():
        return None, (
            'Microsoft Graph API credentials are not configured. '
            'Add MS_GRAPH_TENANT_ID, MS_GRAPH_CLIENT_ID and MS_GRAPH_CLIENT_SECRET to your .env file.'
        )

    token_url = f'https://login.microsoftonline.com/{s["tenant_id"]}/oauth2/v2.0/token'
    try:
        r = requests.post(
            token_url,
            data={
                'grant_type': 'client_credentials',
                'client_id': s['client_id'],
                'client_secret': s['client_secret'],
                'scope': 'https://graph.microsoft.com/.default',
            },
            timeout=15,
        )
        if r.status_code == 200:
            return r.json().get('access_token'), None
        err = r.json().get('error_description', r.text[:400])
        return None, f'Token error: {err}'
    except requests.RequestException as e:
        return None, f'Network error acquiring token: {e}'


def fetch_inbox_messages(mailbox=None, page_size=100, max_pages=100, since=None):
    """Fetch messages from the shared mailbox inbox, following pagination.

    Attachment metadata (id, name, contentType, size, isInline) is expanded
    inline in the same request, avoiding extra API round-trips.
    Paginates through @odata.nextLink up to max_pages (default 2000 emails).

    If `since` is provided (a timezone-aware datetime), only messages received
    after that time are fetched via an OData $filter.

    Returns (list_of_message_dicts, None) on success, or (None, error_str).
    """
    if not mailbox:
        mailbox = _get_settings()['mailbox']

    token, error = _get_token()
    if error:
        return None, error

    headers = {'Authorization': f'Bearer {token}'}
    first_params = {
        '$top': page_size,
        '$orderby': 'receivedDateTime desc',
        '$select': 'id,subject,from,receivedDateTime,hasAttachments,bodyPreview,isRead',
        '$expand': 'attachments($select=id,name,contentType,size,isInline)',
    }
    if since is not None:
        # OData datetime literal must be ISO-8601 UTC without microseconds
        since_str = since.strftime('%Y-%m-%dT%H:%M:%SZ')
        first_params['$filter'] = f"receivedDateTime gt {since_str}"

    all_messages = []
    url = f'{GRAPH_BASE}/users/{mailbox}/mailFolders/inbox/messages'
    params = first_params
    pages = 0

    try:
        while url and pages < max_pages:
            r = requests.get(url, headers=headers, params=params, timeout=30)
            if r.status_code == 404:
                return None, f'Mailbox not found: {mailbox}. Ensure the mailbox exists and the app has access.'
            if r.status_code == 403:
                return None, 'Permission denied. Ensure the Azure app has Mail.Read application permission and admin consent has been granted.'
            if r.status_code != 200:
                return None, f'Graph API returned {r.status_code}: {r.text[:500]}'

            data = r.json()
            all_messages.extend(data.get('value', []))
            url = data.get('@odata.nextLink')
            params = None  # nextLink already contains all query params
            pages += 1

        return all_messages, None
    except requests.RequestException as e:
        return None, f'Network error: {e}'


def download_attachment(mailbox, message_id, attachment_id):
    """Download a single attachment's content bytes from the Graph API.

    Returns (content_bytes, filename, content_type, None) on success,
    or (None, None, None, error_str) on failure.
    """
    if not mailbox:
        mailbox = _get_settings()['mailbox']

    token, error = _get_token()
    if error:
        return None, None, None, error

    headers = {'Authorization': f'Bearer {token}'}
    url = f'{GRAPH_BASE}/users/{mailbox}/messages/{message_id}/attachments/{attachment_id}'
    # No $select — contentBytes is on the fileAttachment subtype and is rejected
    # by the base attachment type's OData metadata when using $select.

    try:
        r = requests.get(url, headers=headers, timeout=60)
        if r.status_code == 200:
            data = r.json()
            b64 = data.get('contentBytes', '')
            content = base64.b64decode(b64) if b64 else b''
            return (
                content,
                data.get('name', 'attachment'),
                data.get('contentType', 'application/octet-stream'),
                None,
            )
        return None, None, None, f'Graph API {r.status_code}: {r.text[:300]}'
    except Exception as e:
        return None, None, None, str(e)
