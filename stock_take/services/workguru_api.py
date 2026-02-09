"""
Shared WorkGuru API client.

Handles authentication and provides common helpers for all WorkGuru
API integrations (boards, accessories, OS doors, etc.).
"""

import os
import json
import logging
import requests
from datetime import datetime
from django.conf import settings

logger = logging.getLogger(__name__)

BASE_URL = "https://ukapi.workguru.io"
AUTH_URL = f"{BASE_URL}/api/ClientTokenAuth/Authenticate/api/client/v1/tokenauth"
TENANT_ID = 129


class WorkGuruAPIError(Exception):
    """Raised when a WorkGuru API call fails."""
    pass


class WorkGuruAPI:
    """
    Thin wrapper around the WorkGuru REST API.

    Usage::

        api = WorkGuruAPI.authenticate()          # raises WorkGuruAPIError
        po_id = api.create_purchase_order(payload)
        api.upload_file_to_po(po_id, filename, content)
    """

    def __init__(self, access_token: str):
        self.access_token = access_token
        self.base_url = BASE_URL
        self.headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {access_token}',
            'Abp.TenantId': str(TENANT_ID),
        }
        self.log_file = os.path.join(settings.BASE_DIR, 'workguru_api_log.txt')

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------
    @classmethod
    def authenticate(cls) -> 'WorkGuruAPI':
        """Authenticate with WorkGuru and return an API client instance."""
        api_key = os.getenv('WORKGURU_API_KEY')
        secret_key = os.getenv('WORKGURU_SECRET_KEY')

        if not api_key or not secret_key:
            raise WorkGuruAPIError('WorkGuru API credentials not configured.')

        try:
            resp = requests.post(
                AUTH_URL,
                json={'apiKey': api_key, 'secret': secret_key},
                timeout=10,
            )
        except requests.exceptions.RequestException as exc:
            raise WorkGuruAPIError(f'Error authenticating with WorkGuru: {exc}')

        if resp.status_code != 200:
            raise WorkGuruAPIError(
                f'WorkGuru authentication failed: {resp.status_code} – {resp.text[:200]}'
            )

        data = resp.json()
        token = (
            data.get('result', {}).get('accessToken')
            or data.get('accessToken')
        )
        if not token:
            raise WorkGuruAPIError('No access token received from WorkGuru.')

        logger.info('Successfully authenticated with WorkGuru')
        return cls(token)

    # ------------------------------------------------------------------
    # Logging helper
    # ------------------------------------------------------------------
    def log(self, message: str):
        """Append a line to the persistent API log file."""
        with open(self.log_file, 'a') as f:
            f.write(message)

    def log_section(self, title: str):
        """Start a new log section with a separator."""
        self.log(f"\n{'=' * 60}\n{title} - {datetime.now()}\n")

    # ------------------------------------------------------------------
    # Purchase Order helpers
    # ------------------------------------------------------------------
    def get_project(self, project_id: int) -> dict:
        """Fetch a project by ID."""
        url = f"{self.base_url}/api/services/app/Project/GetProjectById"
        resp = requests.get(url, headers=self.headers, params={'id': project_id}, timeout=10)
        if resp.status_code == 200:
            return resp.json().get('result', {})
        return {}

    def lookup_po_by_number(self, po_number: str) -> int | None:
        """Return the WorkGuru PO ID for a given display number, or None."""
        url = f"{self.base_url}/api/services/app/PurchaseOrder/GetPurchaseOrdersForMob"
        resp = requests.get(url, headers=self.headers, params={
            'Filter': po_number,
            'MaxResultCount': 5,
            'SkipCount': 0,
        }, timeout=15)

        if resp.status_code != 200:
            return None

        for item in resp.json().get('result', {}).get('items', []):
            if item.get('displayNumber') == po_number or item.get('number') == po_number:
                return item.get('id')
        return None

    def get_po_details(self, po_id: int) -> dict:
        """Fetch full PO details by ID."""
        url = f"{self.base_url}/api/services/app/PurchaseOrder/GetPurchaseOrderByIdForMob"
        resp = requests.get(url, headers=self.headers, params={'id': po_id}, timeout=15)
        if resp.status_code != 200:
            raise WorkGuruAPIError(f'Could not fetch PO details: {resp.status_code}')
        return resp.json().get('result', {})

    def create_or_update_po(self, payload: dict) -> int:
        """
        Call AddOrEditPurchaseOrder. Returns the PO id on success.
        """
        url = f"{self.base_url}/api/services/app/PurchaseOrder/AddOrEditPurchaseOrder"
        self.log(f"PO payload: {json.dumps(payload, indent=2, default=str)[:3000]}\n")

        resp = requests.post(url, headers=self.headers, json=payload, timeout=30)
        self.log(f"PO response status: {resp.status_code}\n")
        self.log(f"PO response body: {resp.text[:1000]}\n")

        if resp.status_code != 200:
            raise WorkGuruAPIError(
                f'Failed PO create/update: {resp.status_code} – {resp.text[:200]}'
            )

        result = resp.json()
        if not result.get('success', True):
            msg = result.get('error', {}).get('message', 'Unknown error')
            raise WorkGuruAPIError(f'WorkGuru error: {msg}')

        return result.get('result')

    def get_po_display_number(self, po_id: int) -> str:
        """Fetch the display number for a newly created PO."""
        try:
            data = self.get_po_details(po_id)
            return data.get('displayNumber') or data.get('number') or f'WG-{po_id}'
        except WorkGuruAPIError:
            return f'WG-{po_id}'

    # ------------------------------------------------------------------
    # Product lookup
    # ------------------------------------------------------------------
    def lookup_product_by_sku(self, sku: str) -> dict | None:
        """
        Look up a product by SKU. Returns the result dict or None.
        """
        url = f"{self.base_url}/api/services/app/Product/GetProductBySku"
        try:
            resp = requests.get(url, headers=self.headers, params={'sku': sku}, timeout=10)
            if resp.status_code == 200:
                result = resp.json().get('result')
                if result and result.get('id'):
                    return result
        except Exception:
            pass
        return None

    # ------------------------------------------------------------------
    # File uploads
    # ------------------------------------------------------------------
    def upload_file_to_po(self, po_id: int, project_id: int,
                          filename: str, content: bytes,
                          content_type: str = 'application/octet-stream',
                          description: str = '') -> bool:
        """Upload a file to a PO via the EzzyBills endpoint. Returns True on success."""
        url = f"{self.base_url}/api/services/app/EzzyBills/UploadReceiptFileAndReturnId"
        multipart_headers = {
            'Authorization': f'Bearer {self.access_token}',
            'Abp.TenantId': str(TENANT_ID),
        }
        files = {'File': (filename, content, content_type)}
        form_data = {
            'TenantId': str(TENANT_ID),
            'PurchaseOrderId': str(po_id),
            'FileName': filename,
            'Description': description,
            'ProjectId': str(project_id),
        }

        try:
            resp = requests.post(url, headers=multipart_headers,
                                 files=files, data=form_data, timeout=30)
            self.log(f"File upload '{filename}' status: {resp.status_code}\n")
            return resp.status_code == 200
        except Exception as exc:
            self.log(f"File upload error for '{filename}': {exc}\n")
            return False

    # ------------------------------------------------------------------
    # Project product helpers (for accessories)
    # ------------------------------------------------------------------
    def add_products_to_project_batch(self, project_id: int,
                                      products: list[dict]) -> bool:
        """Try the batch AddProductsToProject endpoint."""
        url = f"{self.base_url}/api/services/app/Project/AddProductsToProject"
        payload = {'projectId': project_id, 'products': products}
        try:
            resp = requests.post(url, headers=self.headers, json=payload, timeout=30)
            return resp.status_code in (200, 201)
        except Exception:
            return False

    def add_product_to_project(self, project_id: int,
                               payload: dict) -> tuple[bool, str]:
        """Add a single product to a project. Returns (success, message)."""
        url = f"{self.base_url}/api/services/app/Project/AddProductToProject"
        try:
            resp = requests.post(url, headers=self.headers, json=payload, timeout=10)
            if resp.status_code in (200, 201):
                return True, 'OK'
            if resp.status_code == 400 and 'already exists' in resp.text.lower():
                return True, 'already exists'
            return False, f'{resp.status_code} – {resp.text[:200]}'
        except Exception as exc:
            return False, str(exc)

    # ------------------------------------------------------------------
    # Warehouse helpers
    # ------------------------------------------------------------------
    def resolve_warehouse_id(self, project_id: int,
                             supplier_id: int) -> int:
        """Try to find a warehouse ID from project, existing POs, or warehouse list."""
        # 1. From project
        proj = self.get_project(project_id)
        wh = proj.get('warehouseId')
        if wh:
            return wh

        # 2. From existing POs for this supplier
        url = f"{self.base_url}/api/services/app/PurchaseOrder/GetPurchaseOrdersForMob"
        try:
            resp = requests.get(url, headers=self.headers, params={
                'SupplierId': supplier_id, 'MaxResultCount': 1, 'SkipCount': 0,
            }, timeout=15)
            if resp.status_code == 200:
                items = resp.json().get('result', {}).get('items', [])
                if items and items[0].get('warehouseId'):
                    return items[0]['warehouseId']
        except Exception:
            pass

        # 3. From warehouse list
        try:
            wh_url = f"{self.base_url}/api/services/app/Warehouse/GetAllWarehouses"
            resp = requests.get(wh_url, headers=self.headers, params={'IsActive': True}, timeout=10)
            if resp.status_code == 200:
                warehouses = resp.json().get('result', [])
                if warehouses:
                    return warehouses[0].get('id', 132)
        except Exception:
            pass

        return 132  # hardcoded fallback

    # ------------------------------------------------------------------
    # Date helpers
    # ------------------------------------------------------------------
    @staticmethod
    def format_date(iso_date_str: str | None, fallback: str = '') -> str:
        """Convert an ISO date string to DD/MM/YYYY."""
        if not iso_date_str:
            return fallback or datetime.now().strftime('%d/%m/%Y')
        try:
            dt = datetime.fromisoformat(iso_date_str.split('T')[0])
            return dt.strftime('%d/%m/%Y')
        except (ValueError, AttributeError):
            return fallback or datetime.now().strftime('%d/%m/%Y')
