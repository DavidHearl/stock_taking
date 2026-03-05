from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('stock_take', '0118_anthillpayment_xero_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='anthillsale',
            name='paid_in_full',
            field=models.BooleanField(
                default=False,
                help_text='Manually marked as fully paid — excluded from outstanding balance report and dashboard total',
            ),
        ),
    ]
