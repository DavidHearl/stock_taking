"""
WorkGuru Invoice sync service.

Strategy
--------
*Incremental sync* (default):
    1. ``GetAllInvoices`` – single fast call to get every invoice summary.
    2. Compare returned IDs with what is already in the local DB.
    3. Only call ``GetInvoiceById`` for **new** invoices (not in DB).
    4. Existing invoices are skipped – no extra API calls.

*Full sync* (force=True):
    Same as above but calls ``GetInvoiceById`` for **every** invoice
    so payment / outstanding data is refreshed from the authoritative
    per-invoice endpoint.

``GetAllInvoices`` returns line items but **inaccurate** outstanding
amounts.  ``GetInvoiceById`` returns the **correct** outstanding and
the ``payments`` list.
"""

import logging
import requests
from datetime import datetime, date
from decimal import Decimal, InvalidOperation

from django.utils import timezone

from .workguru_api import WorkGuruAPI, WorkGuruAPIError

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------ helpers
def _dec(val, default="0"):
    """Safely convert a value to Decimal."""
    try:
        return Decimal(str(val)) if val is not None else Decimal(default)
    except (InvalidOperation, ValueError, TypeError):
        return Decimal(default)


def _parse_date(iso_str):
    """ISO string -> date or None.  Treats '1900-01-01' as null."""
    if not iso_str:
        return None
    try:
        d = datetime.fromisoformat(iso_str.replace("Z", "+00:00")).date()
        if d.year <= 1900:
            return None
        return d
    except (ValueError, AttributeError):
        try:
            d = datetime.strptime(iso_str.split("T")[0], "%Y-%m-%d").date()
            if d.year <= 1900:
                return None
            return d
        except (ValueError, AttributeError):
            return None


def _parse_datetime(iso_str):
    """ISO string -> datetime or None.  Treats 1900 dates as null."""
    if not iso_str:
        return None
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        if dt.year <= 1900:
            return None
        return dt
    except (ValueError, AttributeError):
        return None


# --------------------------------------------------------- API helpers
def fetch_all_invoice_ids(api):
    """
    Call GetAllInvoices and return the full list of invoice summary dicts.

    Each dict contains at minimum ``id`` and ``invoiceNumber``.
    """
    url = f"{api.base_url}/api/services/app/Invoice/GetAllInvoices"
    resp = requests.get(url, headers=api.headers, timeout=60)
    if resp.status_code != 200:
        raise WorkGuruAPIError(f"GetAllInvoices failed: HTTP {resp.status_code}")
    return resp.json().get("result", [])


def fetch_invoice_detail(api, invoice_id):
    """
    Fetch full invoice detail via GetInvoiceById.

    Returns the result dict (with accurate amountOutstanding and payments)
    or None on failure.
    """
    url = f"{api.base_url}/api/services/app/Invoice/GetInvoiceById"
    try:
        resp = requests.get(
            url,
            headers=api.headers,
            params={"id": invoice_id},
            timeout=15,
        )
        if resp.status_code == 200:
            result = resp.json().get("result")
            if result and isinstance(result, dict):
                return result
    except Exception:
        pass
    return None


# -------------------------------------------------------- single-invoice upsert
def upsert_invoice(inv):
    """
    Create or update a single Invoice from a full API detail dict.

    Returns (invoice_obj, created: bool).
    """
    from stock_take.models import Invoice, Customer, Order

    wg_id = inv.get("id")

    # -- Dates -----------------------------------------------
    invoice_date = _parse_date(inv.get("date"))
    due_date = _parse_date(inv.get("dueDate"))
    sent_acct = _parse_date(inv.get("sentToAccounting"))

    # -- Financial -------------------------------------------
    line_subtotal = Decimal("0")
    line_tax = Decimal("0")
    for key in ("productLineItems", "taskLineItems", "purchaseLineItems"):
        for li in (inv.get(key) or []):
            line_subtotal += _dec(li.get("lineAmount", li.get("unitAmount", 0)))
            line_tax += _dec(li.get("taxAmount", 0))
    line_total = line_subtotal + line_tax

    api_total = _dec(inv.get("total") or inv.get("baseCurrencyTotal", 0))
    total = api_total if api_total > 0 else line_total
    total_tax = _dec(inv.get("totalTax", 0)) or line_tax
    subtotal = total - total_tax

    outstanding = _dec(inv.get("amountOutstanding", 0))
    paid = total - outstanding

    if outstanding <= 0:
        pmt_status = "paid"
    elif paid > 0:
        pmt_status = "partial"
    else:
        pmt_status = "unpaid"

    is_overdue = bool(
        due_date and pmt_status != "paid" and due_date < date.today()
    )

    # -- Client ----------------------------------------------
    client_id = inv.get("clientId")
    client_name = ""
    if isinstance(inv.get("client"), dict):
        client_name = inv["client"].get("name", "")

    # -- Project ---------------------------------------------
    project_id = inv.get("projectId")
    description = inv.get("description", "") or ""

    # -- Extra fields ----------------------------------------
    invoice_reference = inv.get("reference", "") or ""
    client_po = inv.get("clientPurchaseOrder", "") or ""
    xero_id = inv.get("xeroId") or inv.get("qboId") or None

    defaults = {
        "invoice_number": inv.get("invoiceNumber", ""),
        "client_name": client_name,
        "client_id": client_id,
        "project_name": "",
        "project_number": "",
        "project_id": project_id,
        "date": invoice_date,
        "due_date": due_date,
        "sent_to_accounting": sent_acct,
        "status": inv.get("status", "Draft"),
        "description": description,
        "invoice_reference": invoice_reference,
        "client_po": client_po,
        "subtotal": subtotal,
        "total_tax": total_tax,
        "total": total,
        "amount_outstanding": outstanding,
        "amount_paid": paid,
        "payment_status": pmt_status,
        "is_overdue": is_overdue,
        "xero_id": xero_id,
        "raw_data": inv,
        "synced_at": timezone.now(),
    }

    invoice_obj, created = Invoice.objects.update_or_create(
        workguru_id=wg_id, defaults=defaults,
    )

    # -- Link to local Customer ------------------------------
    if client_id:
        try:
            customer = Customer.objects.get(workguru_id=client_id)
            if invoice_obj.customer_id != customer.id:
                invoice_obj.customer = customer
                invoice_obj.save(update_fields=["customer"])
        except Customer.DoesNotExist:
            pass

    # -- Link to local Order via project ID ------------------
    if project_id and not invoice_obj.order_id:
        order_qs = Order.objects.filter(workguru_id=str(project_id))
        if order_qs.exists():
            invoice_obj.order = order_qs.first()
            invoice_obj.save(update_fields=["order"])

    # -- Sync children ---------------------------------------
    sync_line_items(invoice_obj, inv)
    sync_payments(invoice_obj, inv)

    return invoice_obj, created


# ------------------------------------------------------------------ children
def sync_line_items(invoice_obj, inv):
    """
    Replace line items from productLineItems + taskLineItems + purchaseLineItems.
    """
    from stock_take.models import InvoiceLineItem

    all_lines = []
    for key in ("productLineItems", "taskLineItems", "purchaseLineItems"):
        for li in (inv.get(key) or []):
            all_lines.append(li)

    if not all_lines:
        return

    invoice_obj.line_items.all().delete()

    for idx, li in enumerate(all_lines):
        InvoiceLineItem.objects.create(
            invoice=invoice_obj,
            workguru_id=li.get("id"),
            name=li.get("name", li.get("productName", "")),
            description=li.get("description", "") or "",
            rate=_dec(li.get("unitAmount", li.get("rate", 0))),
            quantity=_dec(li.get("quantity", 0)),
            tax_name=li.get("taxName", "") or "",
            tax_rate=_dec(li.get("taxRate", 0)),
            tax_amount=_dec(li.get("taxAmount", 0)),
            line_total=_dec(li.get("lineAmount", li.get("lineTotal", 0))),
            sort_order=li.get("sortOrder", idx),
        )


def sync_payments(invoice_obj, inv):
    """Replace payments for an invoice."""
    from stock_take.models import InvoicePayment

    payments = inv.get("payments") or []
    if not payments:
        return

    invoice_obj.payments.all().delete()

    for pmt in payments:
        InvoicePayment.objects.create(
            invoice=invoice_obj,
            workguru_id=pmt.get("id"),
            amount=_dec(pmt.get("amount", 0)),
            name=pmt.get("note", pmt.get("name", "")),
            date=_parse_datetime(pmt.get("creationTime") or pmt.get("date")),
            sent_to_accounting=_parse_datetime(
                pmt.get("sentToXero") or pmt.get("sentToAccounting")
            ),
        )
