# Generated manually for invoice_not_required flag

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('stock_take', '0215_desktopmachine_desktopcomponent'),
    ]

    operations = [
        migrations.AddField(
            model_name='purchaseorder',
            name='invoice_not_required',
            field=models.BooleanField(default=False, help_text='Marked as not requiring a supplier invoice (hidden from Awaiting Invoice list)'),
        ),
    ]
