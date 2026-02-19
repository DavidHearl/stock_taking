"""
Management command to fetch invoices from Xero for customers with a confirmed
Xero Contact ID, and update/create local Invoice records with payment status.

Usage:
    python manage.py sync_xero_invoices               # Full sync
    python manage.py sync_xero_invoices --dry-run      # Preview without saving
    python manage.py sync_xero_invoices --customer 123 # Sync one customer only (by PK)
"""

import logging
from datetime import datetime
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.utils import timezone

from stock_take.models import Customer, Invoice
from stock_take.services import xero_api

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Fetch invoices from Xero for customers with a confirmed Xero ID and update local records'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Preview what would be synced without saving to database',
        )
        parser.add_argument(
            '--customer',
            type=int,
            default=None,
            help='Sync invoices for a single customer (by database PK)',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        customer_pk = options['customer']

        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN — no changes will be saved\n'))

        # Check Xero connection
        access_token, tenant_id = xero_api.get_valid_access_token()
        if not access_token:
            self.stdout.write(self.style.ERROR(
                'No valid Xero connection. Please connect via the Xero Status page first.'
            ))
            return

        # Get customers with Xero IDs
        qs = Customer.objects.exclude(xero_id__isnull=True).exclude(xero_id='')
        if customer_pk:
            qs = qs.filter(pk=customer_pk)

        customers = list(qs)
        total_customers = len(customers)

        if not customers:
            self.stdout.write(self.style.WARNING('No customers with Xero IDs found.'))
            return

        self.stdout.write(f'Fetching invoices for {total_customers} customer(s) from Xero...\n')

        total_invoices = 0
        total_created = 0
        total_updated = 0
        total_skipped = 0

        for i, customer in enumerate(customers, 1):
            display_name = customer.name or f"{customer.first_name} {customer.last_name}".strip()
            self.stdout.write(f'[{i}/{total_customers}] {display_name} (Xero: {customer.xero_id[:8]}...)')

            try:
                xero_invoices = xero_api.get_invoices_for_contact(customer.xero_id)
            except Exception as e:
                self.stdout.write(self.style.ERROR(f'  Error fetching invoices: {e}'))
                continue

            if not xero_invoices:
                self.stdout.write('  No invoices found')
                continue

            self.stdout.write(f'  Found {len(xero_invoices)} invoice(s)')

            for inv in xero_invoices:
                total_invoices += 1
                xero_invoice_id = inv.get("InvoiceID", "")
                invoice_number = inv.get("InvoiceNumber", "")
                xero_status = inv.get("Status", "")

                # Map Xero status to local status
                status_map = {
                    'DRAFT': 'Draft',
                    'SUBMITTED': 'Approved',
                    'AUTHORISED': 'Approved',
                    'PAID': 'Paid',
                    'VOIDED': 'Void',
                    'DELETED': 'Void',
                }
                local_status = status_map.get(xero_status, 'Draft')

                # Financial fields
                total_amount = Decimal(str(inv.get("Total", 0)))
                amount_due = Decimal(str(inv.get("AmountDue", 0)))
                amount_paid = Decimal(str(inv.get("AmountPaid", 0)))
                subtotal = Decimal(str(inv.get("SubTotal", 0)))
                total_tax = Decimal(str(inv.get("TotalTax", 0)))

                # Payment status
                if xero_status == 'PAID' or amount_due == 0:
                    payment_status = 'paid'
                elif amount_paid > 0:
                    payment_status = 'partial'
                else:
                    payment_status = 'unpaid'

                # Overdue check
                is_overdue = inv.get("HasErrors", False)
                due_date_str = inv.get("DueDateString", "")
                date_str = inv.get("DateString", "")
                due_date = None
                inv_date = None

                if due_date_str:
                    try:
                        due_date = datetime.strptime(due_date_str, "%Y-%m-%d").date()
                        if due_date < datetime.now().date() and payment_status != 'paid':
                            is_overdue = True
                    except (ValueError, TypeError):
                        pass

                if date_str:
                    try:
                        inv_date = datetime.strptime(date_str, "%Y-%m-%d").date()
                    except (ValueError, TypeError):
                        pass

                reference = inv.get("Reference", "")

                # Try to match existing invoice by xero_id first, then by invoice_number
                existing = None
                if xero_invoice_id:
                    existing = Invoice.objects.filter(xero_id=xero_invoice_id).first()
                if not existing and invoice_number:
                    existing = Invoice.objects.filter(invoice_number=invoice_number).first()

                if existing:
                    # Update existing invoice with Xero payment data
                    if not dry_run:
                        existing.xero_id = xero_invoice_id
                        existing.status = local_status
                        existing.payment_status = payment_status
                        existing.amount_paid = amount_paid
                        existing.amount_outstanding = amount_due
                        existing.is_overdue = is_overdue
                        existing.subtotal = subtotal
                        existing.total_tax = total_tax
                        existing.total = total_amount
                        if due_date:
                            existing.due_date = due_date
                        if inv_date:
                            existing.date = inv_date
                        existing.synced_at = timezone.now()
                        existing.save()
                    total_updated += 1
                    self.stdout.write(f'    ↻ Updated: {invoice_number} — {local_status} ({payment_status})')
                else:
                    # Create new invoice from Xero data
                    if not dry_run:
                        Invoice.objects.create(
                            xero_id=xero_invoice_id,
                            invoice_number=invoice_number or f"XERO-{xero_invoice_id[:8]}",
                            client_name=display_name,
                            customer=customer,
                            date=inv_date,
                            due_date=due_date,
                            status=local_status,
                            description=inv.get("LineItems", [{}])[0].get("Description", "") if inv.get("LineItems") else "",
                            invoice_reference=reference,
                            subtotal=subtotal,
                            total_tax=total_tax,
                            total=total_amount,
                            amount_outstanding=amount_due,
                            amount_paid=amount_paid,
                            payment_status=payment_status,
                            is_overdue=is_overdue,
                            synced_at=timezone.now(),
                        )
                    total_created += 1
                    self.stdout.write(
                        self.style.SUCCESS(f'    + Created: {invoice_number} — {local_status} ({payment_status})')
                    )

        self.stdout.write('')
        self.stdout.write('=' * 50)
        self.stdout.write(f'  Customers processed:  {total_customers}')
        self.stdout.write(f'  Invoices found:       {total_invoices}')
        self.stdout.write(f'  Updated existing:     {total_updated}')
        self.stdout.write(f'  Created new:          {total_created}')
        self.stdout.write('=' * 50)

        if dry_run:
            self.stdout.write(self.style.WARNING(
                '\nDry run complete. Re-run without --dry-run to save changes.'
            ))
        else:
            self.stdout.write(self.style.SUCCESS('\nInvoice sync complete.'))
