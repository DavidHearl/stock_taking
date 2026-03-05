"""
Management command: sync_anthill_payments

  NOT CURRENTLY FUNCTIONAL  Anthill API limitation.

The Anthill CRM SOAP API (v1.asmx) does NOT expose any endpoint for
reading payment history.  The only payment-related methods available are:

  AddPayment           add a payment to a sale
  AddPaymentByUserId   add a payment (specifying the user by ID)

There is no GetSalePayments, GetPayments, or equivalent GET method.
Payment history visible in the Anthill UI is not accessible via the API.

This command is kept as a placeholder. If Anthill expose a read endpoint
in a future API version, implement it here using the existing
AnthillPayment model and anthill_api.get_sale_payments().
"""

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = (
        'PLACEHOLDER  Anthill API does not expose a payment read endpoint. '
        'See command docstring for details.'
    )

    def handle(self, *args, **options):
        self.stderr.write(self.style.ERROR(
            '\n'
            '  Anthill API limitation: no payment read endpoint exists.\n'
            '\n'
            'The Anthill CRM SOAP API only provides AddPayment / AddPaymentByUserId.\n'
            'There is no GetSalePayments or equivalent method to retrieve payment history.\n'
            'Payment history visible in the Anthill UI is not accessible via the API.\n'
            '\n'
            'This command is a placeholder for a future Anthill API version.\n'
        ))
