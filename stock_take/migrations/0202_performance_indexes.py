"""
Performance indexes for sale/order detail page load.

- AnthillSale.status: speeds up the open-sales navbar count query
- AnthillSale.category: speeds up the open-remedials navbar count query
- Customer.location: speeds up the nav available-locations dropdown query
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('stock_take', '0201_purchaseinvoice_amendment_fields'),
    ]

    operations = [
        migrations.AddIndex(
            model_name='anthillsale',
            index=models.Index(fields=['status'], name='stock_take__anthsale_status_idx'),
        ),
        migrations.AddIndex(
            model_name='anthillsale',
            index=models.Index(fields=['category'], name='stock_take__anthsale_category_idx'),
        ),
        migrations.AddIndex(
            model_name='customer',
            index=models.Index(fields=['location'], name='stock_take__customer_location_idx'),
        ),
    ]
