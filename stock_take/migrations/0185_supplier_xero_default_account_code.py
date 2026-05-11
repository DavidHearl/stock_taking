from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('stock_take', '0184_add_supplier_reference_to_purchase_invoice'),
    ]

    operations = [
        migrations.AddField(
            model_name='supplier',
            name='xero_default_account_code',
            field=models.CharField(
                blank=True,
                default='',
                help_text='Default Xero GL account code for purchase invoices from this supplier',
                max_length=20,
            ),
        ),
    ]
