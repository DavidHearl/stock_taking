from decimal import Decimal
from django.db import migrations, models


def backfill_pack_cost_price(apps, schema_editor):
    StockItem = apps.get_model('stock_take', 'StockItem')
    for item in StockItem.objects.all().only('id', 'cost', 'pack_size', 'pack_cost_price'):
        pack_size = item.pack_size or 1
        if pack_size < 1:
            pack_size = 1
        item.pack_size = pack_size
        item.pack_cost_price = (Decimal(str(item.cost or 0)) * Decimal(str(pack_size))).quantize(Decimal('0.001'))
        item.save(update_fields=['pack_size', 'pack_cost_price'])


class Migration(migrations.Migration):

    dependencies = [
        ('stock_take', '0164_cost_price_3dp'),
    ]

    operations = [
        migrations.AddField(
            model_name='stockitem',
            name='order_source',
            field=models.CharField(choices=[('item', 'Item Order'), ('website', 'Website Order')], db_index=True, default='item', max_length=20),
        ),
        migrations.AddField(
            model_name='stockitem',
            name='pack_cost_price',
            field=models.DecimalField(blank=True, decimal_places=3, help_text='Supplier pack price. Unit cost is derived from pack_cost_price / pack_size', max_digits=10, null=True),
        ),
        migrations.RunPython(backfill_pack_cost_price, migrations.RunPython.noop),
    ]
