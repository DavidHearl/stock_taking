from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('stock_take', '0209_mailboxemail_matched_po'),
    ]

    operations = [
        migrations.AddField(
            model_name='purchaseinvoicelineitem',
            name='po_product',
            field=models.ForeignKey(
                blank=True,
                help_text='PO product line this invoice line was created from',
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='invoice_lines',
                to='stock_take.purchaseorderproduct',
            ),
        ),
    ]
