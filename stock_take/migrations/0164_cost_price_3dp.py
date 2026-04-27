from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('stock_take', '0163_stockitem_pack_size'),
    ]

    operations = [
        migrations.AlterField(
            model_name='stockitem',
            name='cost',
            field=models.DecimalField(decimal_places=3, max_digits=10),
        ),
        migrations.AlterField(
            model_name='pricehistory',
            name='old_price',
            field=models.DecimalField(decimal_places=3, max_digits=10),
        ),
        migrations.AlterField(
            model_name='pricehistory',
            name='new_price',
            field=models.DecimalField(decimal_places=3, max_digits=10),
        ),
    ]
