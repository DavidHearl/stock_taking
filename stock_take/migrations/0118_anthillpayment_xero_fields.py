from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('stock_take', '0117_anthillpayment'),
    ]

    operations = [
        # Expand anthill_payment_id max_length (now used for Xero PaymentID UUIDs too)
        migrations.AlterField(
            model_name='anthillpayment',
            name='anthill_payment_id',
            field=models.CharField(
                blank=True, db_index=True,
                help_text='Payment ID from the source system (Xero PaymentID, etc.)',
                max_length=50,
            ),
        ),
        # Source field
        migrations.AddField(
            model_name='anthillpayment',
            name='source',
            field=models.CharField(
                blank=True, default='xero', max_length=20,
                help_text='Data source for this payment record (e.g. "xero")',
            ),
        ),
        # Xero invoice context
        migrations.AddField(
            model_name='anthillpayment',
            name='xero_invoice_id',
            field=models.CharField(
                blank=True, db_index=True, max_length=50,
                help_text='Xero InvoiceID (UUID)',
            ),
        ),
        migrations.AddField(
            model_name='anthillpayment',
            name='xero_invoice_number',
            field=models.CharField(
                blank=True, max_length=50,
                help_text='Xero invoice number, e.g. "INV-0001"',
            ),
        ),
        migrations.AddField(
            model_name='anthillpayment',
            name='invoice_total',
            field=models.DecimalField(
                blank=True, decimal_places=2, max_digits=12, null=True,
                help_text='Total value of the parent Xero invoice',
            ),
        ),
        migrations.AddField(
            model_name='anthillpayment',
            name='invoice_amount_due',
            field=models.DecimalField(
                blank=True, decimal_places=2, max_digits=12, null=True,
                help_text='Amount still outstanding on the parent Xero invoice',
            ),
        ),
        migrations.AddField(
            model_name='anthillpayment',
            name='invoice_status',
            field=models.CharField(
                blank=True, max_length=20,
                help_text='Xero invoice status: AUTHORISED, PAID, VOIDED, etc.',
            ),
        ),
    ]
