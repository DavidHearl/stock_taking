# Generated manually

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('stock_take', '0111_add_sale_detail_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='timesheet',
            name='purchase_invoice_line',
            field=models.ForeignKey(
                blank=True,
                help_text='Purchase invoice line that generated this timesheet',
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='timesheets',
                to='stock_take.purchaseinvoicelineitem',
            ),
        ),
    ]
