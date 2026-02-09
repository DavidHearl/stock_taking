"""
WorkGuru OS Doors PO service.

Create a PO for OS Doors and push door line items to it.
"""

import json
import logging
from datetime import datetime

from .workguru_api import WorkGuruAPI, WorkGuruAPIError, TENANT_ID

logger = logging.getLogger(__name__)

# OS Doors supplier
OS_DOORS_SUPPLIER_ID = 17877


def create_os_doors_po_in_workguru(api: WorkGuruAPI, order) -> str:
    """
    Create a blank Draft PO in WorkGuru for OS Doors and store the PO
    number on the order's ``os_doors_po`` field.

    Returns the PO display number (e.g. "PO1500").
    """
    api.log_section("CREATE OS DOORS PO")
    api.log(f"Order: {order.sale_number}, WorkGuru Project: {order.workguru_id}\n")

    project_id = int(order.workguru_id)
    warehouse_id = api.resolve_warehouse_id(project_id, OS_DOORS_SUPPLIER_ID)

    # Customer name for description
    if order.customer:
        customer_name = f"{order.customer.first_name} {order.customer.last_name}".strip()
    else:
        customer_name = f"{order.first_name} {order.last_name}".strip()

    today = datetime.now().strftime('%d/%m/%Y')

    po_payload = {
        'tenantId': TENANT_ID,
        'revision': 0,
        'supplierId': OS_DOORS_SUPPLIER_ID,
        'projectId': project_id,
        'status': 'Draft',
        'issueDate': today,
        'expectedDate': today,
        'description': f'OS Doors order for {customer_name} - Sale {order.sale_number}',
        'billable': False,
        'total': 0,
        'currency': 'GBP',
        'exchangeRate': 1.0,
        'warehouseId': warehouse_id,
        'products': [],
    }

    po_id = api.create_or_update_po(po_payload)
    po_number = api.get_po_display_number(po_id)

    # Save the PO number on the order
    order.os_doors_po = po_number
    order.save()

    # Also update individual OSDoor records with the PO number
    order.os_doors.filter(po_number__isnull=True).update(po_number=po_number)
    order.os_doors.filter(po_number='').update(po_number=po_number)

    api.log(f"Created OS Doors PO {po_number} (id={po_id})\n")
    logger.info(f'Created OS Doors PO {po_number} for order {order.sale_number}')
    return po_number


def push_os_doors_to_po(api: WorkGuruAPI, order) -> dict:
    """
    Push OS Door line items to the existing WorkGuru PO.

    Returns a summary dict::

        {
            'line_count': int,
            'stock_count': int,
            'adhoc_count': int,
        }
    """
    po_number = order.os_doors_po
    if not po_number:
        raise WorkGuruAPIError('This order does not have an OS Doors PO.')

    os_doors = order.os_doors.all()
    if not os_doors.exists():
        raise WorkGuruAPIError('No OS door items found for this order.')

    api.log_section("PUSH OS DOORS TO PO")
    api.log(f"Order: {order.sale_number}, OS Doors PO: {po_number}\n")
    api.log(f"OS Door items count: {os_doors.count()}\n")

    # Look up WorkGuru PO
    wg_po_id = api.lookup_po_by_number(po_number)
    if not wg_po_id:
        raise WorkGuruAPIError(f'Could not find WorkGuru PO {po_number}.')
    api.log(f"WorkGuru PO lookup: found ID={wg_po_id}\n")

    existing_po = api.get_po_details(wg_po_id)

    # Build line items — one per OSDoor record
    po_line_items = []
    stock_count = 0
    adhoc_count = 0
    line_sort_order = 0

    # Try to find the generic OS Doors product
    GENERIC_OS_DOORS_SKU = "DOR_VNL_OSD_MTM"
    generic_product = api.lookup_product_by_sku(GENERIC_OS_DOORS_SKU)
    generic_product_id = generic_product['id'] if generic_product else None

    for door in os_doors:
        qty = float(door.quantity)
        cost = float(door.cost_price) if door.cost_price else 0.0

        description = (
            f"{door.door_style} - {door.style_colour} - "
            f"{door.height}x{door.width}mm - {door.colour}"
        )

        # Use the generic OS doors product
        product_id = generic_product_id
        sku = GENERIC_OS_DOORS_SKU

        if product_id:
            stock_count += 1
        else:
            adhoc_count += 1
            product_id = None  # Will need manual handling

        line_item = {
            'tenantId': TENANT_ID,
            'purchaseOrderId': wg_po_id,
            'name': f"OS Door - {door.door_style}",
            'description': description[:200],
            'sku': sku,
            'supplierCode': door.door_style,
            'orderQuantity': qty,
            'notes': door.item_description[:200] if door.item_description else description[:200],
            'buyPrice': cost,
            'taxType': 'NONE',
            'expenseAccountCode': 'H1020',
            'accountCode': 'G1001',
            'sortOrder': line_sort_order,
        }

        if product_id:
            line_item['productId'] = product_id

        line_sort_order += 1
        po_line_items.append(line_item)

    if not po_line_items:
        raise WorkGuruAPIError('No OS door line items could be created.')

    api.log(f"Line items built: {len(po_line_items)} ({stock_count} stock, {adhoc_count} adhoc)\n")
    api.log(f"Line items: {json.dumps(po_line_items, indent=2, default=str)[:3000]}\n")

    # Update the PO
    today = datetime.now().strftime('%d/%m/%Y')
    issue_date = api.format_date(existing_po.get('issueDate'), today)
    expected_date = api.format_date(existing_po.get('expectedDate'), today)

    update_payload = {
        'id': wg_po_id,
        'tenantId': TENANT_ID,
        'number': existing_po.get('number', ''),
        'revision': existing_po.get('revision', 0),
        'supplierId': existing_po.get('supplierId', OS_DOORS_SUPPLIER_ID),
        'projectId': int(order.workguru_id),
        'status': existing_po.get('status', 'Draft'),
        'issueDate': issue_date,
        'expectedDate': expected_date,
        'description': existing_po.get('description') or f'OS Doors order for Sale {order.sale_number}',
        'billable': existing_po.get('billable', False),
        'currency': existing_po.get('currency', 'GBP'),
        'exchangeRate': existing_po.get('exchangeRate', 1.0),
        'warehouseId': existing_po.get('warehouseId', 132),
        'products': po_line_items,
    }

    api.create_or_update_po(update_payload)

    # Mark doors as ordered
    os_doors.update(ordered=True)

    summary = {
        'line_count': len(po_line_items),
        'stock_count': stock_count,
        'adhoc_count': adhoc_count,
    }

    msg = (
        f"SUCCESS: {summary['line_count']} OS door line item(s) added to PO {po_number}"
        f" | {summary['stock_count']} stock | {summary['adhoc_count']} adhoc"
    )
    api.log(f"{msg}\n")
    logger.info(msg)
    return summary
