from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('stock_take', '0186_overhead_purchase_order'),
    ]

    operations = [
        # Add gl_code field to OverheadPurchaseOrder
        migrations.AddField(
            model_name='overheadpurchaseorder',
            name='gl_code',
            field=models.CharField(
                blank=True,
                default='',
                help_text='Xero GL account code for this PO',
                max_length=20,
            ),
        ),

        # Create EnabledGLCode model
        migrations.CreateModel(
            name='EnabledGLCode',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('code', models.CharField(max_length=20, unique=True)),
                ('name', models.CharField(max_length=255)),
                ('account_type', models.CharField(blank=True, max_length=50)),
                ('enabled', models.BooleanField(default=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'verbose_name': 'Enabled GL Code',
                'verbose_name_plural': 'Enabled GL Codes',
                'ordering': ['code'],
            },
        ),
    ]
