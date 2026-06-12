# Add a database-level default to invoice_not_required so inserts that omit the
# column (e.g. during rolling deploys) don't violate the NOT NULL constraint.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('stock_take', '0216_purchaseorder_invoice_not_required'),
    ]

    operations = [
        migrations.AlterField(
            model_name='purchaseorder',
            name='invoice_not_required',
            field=models.BooleanField(default=False, db_default=False, help_text='Marked as not requiring a supplier invoice (hidden from Awaiting Invoice list)'),
        ),
    ]
