"""
WorkGuru Boards PO service.

Create a PO for boards and push board line items (from PNX data) to it.
"""

import json
import logging
import os
from collections import defaultdict
from datetime import datetime
from decimal import Decimal

from django.conf import settings
from django.db import models

from .workguru_api import WorkGuruAPI, WorkGuruAPIError, TENANT_ID

logger = logging.getLogger(__name__)

# Carnehill Joinery Ltd
BOARDS_SUPPLIER_ID = 11465
GENERIC_BOARDS_PRODUCT_ID = 16447   # "Carnehill Boards Order per Job"
GENERIC_BOARDS_SKU = "BOARDS_CH"
PRICE_PER_SQM = 50.0


def create_boards_po_in_workguru(api: WorkGuruAPI, order) -> str:
    """
    Create a blank Draft PO in WorkGuru for boards and return the PO
    display number (e.g. "PO1476").

    Also creates the local ``BoardsPO`` record and links it to *order*.
    """
    from stock_take.models import BoardsPO

    api.log_section(f"CREATE BOARDS PO")
    api.log(f"Order: {order.sale_number}, WorkGuru Project: {order.workguru_id}\n")

    project_id = int(order.workguru_id)
    warehouse_id = api.resolve_warehouse_id(project_id, BOARDS_SUPPLIER_ID)

    # Build customer name for description
    if order.customer:
        customer_name = f"{order.customer.first_name} {order.customer.last_name}".strip()
    else:
        customer_name = f"{order.first_name} {order.last_name}".strip()

    today = datetime.now().strftime('%d/%m/%Y')

    po_payload = {
        'tenantId': TENANT_ID,
        'revision': 0,
        'supplierId': BOARDS_SUPPLIER_ID,
        'projectId': project_id,
        'status': 'Draft',
        'issueDate': today,
        'expectedDate': today,
        'description': f'Boards order for {customer_name} - Sale {order.sale_number}',
        'billable': False,
        'total': 0,
        'currency': 'GBP',
        'exchangeRate': 1.0,
        'warehouseId': warehouse_id,
        'products': [],
    }

    po_id = api.create_or_update_po(po_payload)
    po_number = api.get_po_display_number(po_id)

    # Create local record
    boards_po, _ = BoardsPO.objects.get_or_create(
        po_number=po_number,
        defaults={'boards_ordered': False},
    )
    order.boards_po = boards_po
    order.save()

    api.log(f"Created boards PO {po_number} (id={po_id})\n")
    logger.info(f'Created BoardsPO {po_number} for order {order.sale_number}')
    return po_number


def push_boards_to_po(api: WorkGuruAPI, order) -> dict:
    """
    Push board line items and PNX/CSV files to the existing WorkGuru PO.

    Returns a summary dict::

        {
            'line_count': int,
            'stock_count': int,
            'adhoc_count': int,
            'files_uploaded': int,
        }
    """
    boards_po = order.boards_po

    api.log_section("PUSH BOARDS TO PO")
    api.log(f"Order: {order.sale_number}, Boards PO: {boards_po.po_number}\n")

    # Fetch PNX items for this order
    order_pnx_items = boards_po.pnx_items.filter(
        models.Q(customer__icontains=order.sale_number)
        | models.Q(ordername__icontains=order.sale_number)
    )
    if not order_pnx_items.exists():
        raise WorkGuruAPIError('No board items found for this order.')

    api.log(f"PNX items count: {order_pnx_items.count()}\n")

    # --- Look up the WG PO ---
    wg_po_id = api.lookup_po_by_number(boards_po.po_number)
    if not wg_po_id:
        raise WorkGuruAPIError(f'Could not find WorkGuru PO {boards_po.po_number}.')
    api.log(f"WorkGuru PO lookup: found ID={wg_po_id}\n")

    existing_po = api.get_po_details(wg_po_id)

    # --- Group board items by matname + dimensions ---
    board_groups = defaultdict(lambda: {
        'barcode': '',
        'matname': '',
        'total_qty': Decimal('0'),
        'total_area_sqm': Decimal('0'),
        'cleng': 0,
        'cwidth': 0,
    })

    for item in order_pnx_items:
        key = (item.matname, float(item.cleng), float(item.cwidth))
        grp = board_groups[key]
        grp['barcode'] = item.barcode
        grp['matname'] = item.matname
        grp['total_qty'] += item.cnt
        grp['cleng'] = float(item.cleng)
        grp['cwidth'] = float(item.cwidth)
        length_m = item.cleng / 1000
        width_m = item.cwidth / 1000
        grp['total_area_sqm'] += length_m * width_m * item.cnt

    api.log(f"Board groups: {len(board_groups)} unique material+dimension combos\n")

    # --- Build PO line items ---
    po_line_items = []
    adhoc_count = 0
    stock_count = 0
    line_sort_order = 0

    for key, grp in board_groups.items():
        matname = grp['matname']
        total_qty = float(grp['total_qty'])
        total_area = float(grp['total_area_sqm'])
        buy_price = round(total_area * PRICE_PER_SQM / total_qty, 2) if total_qty > 0 else 0

        # Try to resolve stock product
        product_id = None
        supplier_code = matname
        # Build SKU from material name + width (e.g. SHT_MFC_EGG_U899ST9_18_1000)
        sku = f"{matname.rstrip('_')}_{int(grp['cwidth'])}"

        result = api.lookup_product_by_sku(matname)
        if result:
            product_id = result['id']
            supplier_code = result.get('supplierCode') or matname
            # Keep the material+width SKU even when we find a stock match
            stock_count += 1
            api.log(f"  Stock match: {matname} -> productId={product_id}\n")

        if product_id is None:
            product_id = GENERIC_BOARDS_PRODUCT_ID
            adhoc_count += 1
            api.log(f"  No stock match for {matname} – using generic product, SKU={sku}\n")

        description = f"{matname} - {grp['cleng']:.0f}x{grp['cwidth']:.0f}mm x{total_qty:.0f}"

        po_line_items.append({
            'tenantId': TENANT_ID,
            'purchaseOrderId': wg_po_id,
            'productId': product_id,
            'name': matname,
            'description': description[:200],
            'sku': sku,
            'supplierCode': supplier_code,
            'orderQuantity': total_qty,
            'notes': f"{matname} - {grp['cleng']:.0f}x{grp['cwidth']:.0f}mm",
            'buyPrice': buy_price,
            'taxType': 'NONE',
            'expenseAccountCode': 'H1020',
            'accountCode': 'G1001',
            'sortOrder': line_sort_order,
        })
        line_sort_order += 1

    if not po_line_items:
        raise WorkGuruAPIError('No board line items could be created.')

    api.log(f"Line items built: {len(po_line_items)} ({stock_count} stock, {adhoc_count} generic)\n")
    api.log(f"Line items: {json.dumps(po_line_items, indent=2, default=str)[:3000]}\n")

    # --- Update the PO ---
    today = datetime.now().strftime('%d/%m/%Y')
    issue_date = api.format_date(existing_po.get('issueDate'), today)
    expected_date = api.format_date(existing_po.get('expectedDate'), today)

    update_payload = {
        'id': wg_po_id,
        'tenantId': TENANT_ID,
        'number': existing_po.get('number', ''),
        'revision': existing_po.get('revision', 0),
        'supplierId': existing_po.get('supplierId', BOARDS_SUPPLIER_ID),
        'projectId': int(order.workguru_id),
        'status': existing_po.get('status', 'Draft'),
        'issueDate': issue_date,
        'expectedDate': expected_date,
        'description': existing_po.get('description') or f'Boards order for Sale {order.sale_number}',
        'billable': existing_po.get('billable', False),
        'currency': existing_po.get('currency', 'GBP'),
        'exchangeRate': existing_po.get('exchangeRate', 1.0),
        'warehouseId': existing_po.get('warehouseId', 132),
        'products': po_line_items,
    }

    api.create_or_update_po(update_payload)

    # --- Upload files ---
    project_id = int(order.workguru_id)
    files_uploaded = 0

    if boards_po.file:
        try:
            boards_po.file.open('rb')
            content = boards_po.file.read()
            boards_po.file.close()
            fname = os.path.basename(boards_po.file.name)
            if api.upload_file_to_po(wg_po_id, project_id, fname, content,
                                     description=f'PNX file for {order.sale_number}'):
                files_uploaded += 1
        except Exception as exc:
            api.log(f"PNX file upload error: {exc}\n")

    if boards_po.csv_file:
        try:
            boards_po.csv_file.open('rb')
            content = boards_po.csv_file.read()
            boards_po.csv_file.close()
            fname = os.path.basename(boards_po.csv_file.name)
            if api.upload_file_to_po(wg_po_id, project_id, fname, content,
                                     content_type='text/csv',
                                     description=f'CSV file for {order.sale_number}'):
                files_uploaded += 1
        except Exception as exc:
            api.log(f"CSV file upload error: {exc}\n")

    summary = {
        'line_count': len(po_line_items),
        'stock_count': stock_count,
        'adhoc_count': adhoc_count,
        'files_uploaded': files_uploaded,
    }

    msg = (
        f"SUCCESS: {summary['line_count']} board line item(s) added to PO {boards_po.po_number}"
        f" | {summary['adhoc_count']} generic | {summary['files_uploaded']} file(s) uploaded"
    )
    api.log(f"{msg}\n")
    logger.info(msg)
    return summary
