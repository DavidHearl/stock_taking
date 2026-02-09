"""
WorkGuru Accessories service.

Push accessories to a WorkGuru Project (not a PO).
"""

import json
import logging
import os
from datetime import datetime

from django.conf import settings

from .workguru_api import WorkGuruAPI, WorkGuruAPIError

logger = logging.getLogger(__name__)


def push_accessories_to_project(api: WorkGuruAPI, order) -> dict:
    """
    Push all accessories for *order* to its WorkGuru Project.

    Tries four methods in order:
      1. Batch ``AddProductsToProject``
      2. CSV import
      3. Multipart CSV upload
      4. Individual ``AddProductToProject`` calls with SKU lookups

    Returns a summary dict::

        {'success_count': int, 'error_count': int, 'errors': list[str], 'method': str}
    """
    project_id = int(order.workguru_id)
    log_file = os.path.join(settings.BASE_DIR, 'workguru_api_log.txt')

    # Build product list
    products_to_add = []
    for acc in order.accessories.all():
        cost = float(acc.cost_price) if acc.cost_price else 0.0
        sell = float(acc.sell_price) if acc.sell_price else 0.0
        qty = float(acc.quantity) if acc.quantity else 1.0
        products_to_add.append({
            'sku': acc.sku,
            'name': acc.name,
            'description': acc.description or acc.name,
            'quantity': qty,
            'costPrice': cost,
            'sellPrice': sell,
            'billable': False,
            'isStock': True,
        })

    if not products_to_add:
        raise WorkGuruAPIError('No accessories to push.')

    # --- Method 1: batch endpoint ---
    if api.add_products_to_project_batch(project_id, products_to_add):
        logger.info('Batch import successful!')
        return {
            'success_count': len(products_to_add),
            'error_count': 0,
            'errors': [],
            'method': 'batch',
        }

    # --- Method 2: CSV import ---
    import requests as _requests
    csv_content = "Sku,Name,Description,CostPrice,SellPrice,Quantity,Billable\n"
    for p in products_to_add:
        name = str(p['name']).replace('"', '""')
        desc = str(p['description']).replace('"', '""')
        csv_content += (
            f'"{p["sku"]}","{name}","{desc}",'
            f'{p["costPrice"]},{p["sellPrice"]},{p["quantity"]},FALSE\n'
        )

    csv_url = f"{api.base_url}/api/services/app/Project/ImportProductsFromCsv"
    try:
        resp = _requests.post(csv_url, headers=api.headers,
                              json={'projectId': project_id, 'csvContent': csv_content},
                              timeout=30)
        if resp.status_code in (200, 201):
            return {
                'success_count': len(products_to_add),
                'error_count': 0,
                'errors': [],
                'method': 'csv',
            }
    except Exception:
        pass

    # --- Method 3: multipart CSV ---
    multipart_url = f"{api.base_url}/api/services/app/Project/ImportProjectProductsFromCsv"
    try:
        mh = {
            'Authorization': f'Bearer {api.access_token}',
            'Abp.TenantId': str(api.headers.get('Abp.TenantId', '129')),
        }
        resp = _requests.post(multipart_url, headers=mh,
                              files={'file': ('accessories.csv', csv_content, 'text/csv')},
                              data={'projectId': project_id}, timeout=30)
        if resp.status_code in (200, 201):
            return {
                'success_count': len(products_to_add),
                'error_count': 0,
                'errors': [],
                'method': 'multipart',
            }
    except Exception:
        pass

    # --- Method 4: individual requests ---
    success_count = 0
    error_count = 0
    errors = []

    for product in products_to_add:
        wg_product = api.lookup_product_by_sku(product['sku'])

        if wg_product:
            product_id = wg_product['id']
            p_name = wg_product.get('name') or product['name']
            p_cost = wg_product.get('costPrice') or 0
            p_sell = wg_product.get('sellPrice') or 0

            payload = {
                'projectId': project_id,
                'productId': product_id,
                'sku': product['sku'],
                'quantity': product['quantity'],
                'forecastQuantity': product['quantity'],
                'forecastQty': product['quantity'],
                'name': p_name,
                'description': p_name,
                'costPrice': p_cost,
                'unitCost': p_cost,
                'forecastCost': p_cost * product['quantity'],
                'forecastUnitCost': p_cost,
                'sellPrice': p_sell,
                'billable': False,
            }

            with open(log_file, 'a') as f:
                f.write(f"\n{'=' * 80}\n")
                f.write(f"Timestamp: {datetime.now()}\n")
                f.write(f"AddProductToProject: {json.dumps(payload, indent=2)}\n")

            ok, msg = api.add_product_to_project(project_id, payload)
            if ok:
                success_count += 1
            else:
                error_count += 1
                errors.append(f"{product['sku']}: {msg}")
            continue

        # Fallback – no product ID
        payload = {
            'projectId': project_id,
            'sku': product['sku'],
            'quantity': product['quantity'],
            'billable': product['billable'],
        }
        ok, msg = api.add_product_to_project(project_id, payload)
        if ok:
            success_count += 1
        else:
            error_count += 1
            errors.append(f"{product['sku']}: {msg}")

    return {
        'success_count': success_count,
        'error_count': error_count,
        'errors': errors,
        'method': 'individual',
    }
