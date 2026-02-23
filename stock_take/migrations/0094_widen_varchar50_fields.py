"""
Widen varchar(50) fields to varchar(100) to prevent truncation errors
during Anthill CRM sync. Affects Customer (phone, fax, code, abn,
credit_terms_type) and Lead (phone, mobile).
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('stock_take', '0093_add_cut_size_to_accessory'),
    ]

    operations = [
        # Customer fields
        migrations.AlterField(
            model_name='customer',
            name='phone',
            field=models.CharField(blank=True, max_length=100, null=True),
        ),
        migrations.AlterField(
            model_name='customer',
            name='fax',
            field=models.CharField(blank=True, max_length=100, null=True),
        ),
        migrations.AlterField(
            model_name='customer',
            name='code',
            field=models.CharField(blank=True, help_text='Client code', max_length=100, null=True),
        ),
        migrations.AlterField(
            model_name='customer',
            name='abn',
            field=models.CharField(blank=True, help_text='Tax / ABN / VAT number', max_length=100, null=True),
        ),
        migrations.AlterField(
            model_name='customer',
            name='credit_terms_type',
            field=models.CharField(blank=True, max_length=100, null=True),
        ),
        # Lead fields
        migrations.AlterField(
            model_name='lead',
            name='phone',
            field=models.CharField(blank=True, max_length=100, null=True),
        ),
        migrations.AlterField(
            model_name='lead',
            name='mobile',
            field=models.CharField(blank=True, max_length=100, null=True),
        ),
    ]
