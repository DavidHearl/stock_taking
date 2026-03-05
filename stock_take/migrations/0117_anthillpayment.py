import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('stock_take', '0116_order_additional_os_doors_pos'),
    ]

    operations = [
        migrations.CreateModel(
            name='AnthillPayment',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('anthill_payment_id', models.CharField(blank=True, db_index=True, help_text='Anthill payment ID (from API)', max_length=30)),
                ('payment_type', models.CharField(blank=True, help_text='e.g. "Deposit", "Stock Payment"', max_length=100)),
                ('date', models.DateTimeField(blank=True, help_text='Date and time of the payment', null=True)),
                ('location', models.CharField(blank=True, help_text='e.g. "Belfast"', max_length=100)),
                ('user_name', models.CharField(blank=True, help_text='User who recorded the payment', max_length=150)),
                ('amount', models.DecimalField(blank=True, decimal_places=2, help_text='Payment amount (GBP)', max_digits=12, null=True)),
                ('status', models.CharField(blank=True, help_text='"Confirmed" or "Unconfirmed"', max_length=50)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('sale', models.ForeignKey(
                    help_text='The Anthill sale this payment belongs to',
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='payments',
                    to='stock_take.anthillsale',
                )),
            ],
            options={
                'verbose_name': 'Anthill Payment',
                'verbose_name_plural': 'Anthill Payments',
                'ordering': ['date'],
            },
        ),
    ]
