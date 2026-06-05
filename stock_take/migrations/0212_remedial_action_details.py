from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('stock_take', '0211_add_stockitemnote'),
    ]

    operations = [
        migrations.AddField(
            model_name='remedial',
            name='order_boards_items',
            field=models.TextField(blank=True, default='', help_text='Boards to order for this remedial'),
        ),
        migrations.AddField(
            model_name='remedial',
            name='order_boards_po_ref',
            field=models.CharField(blank=True, default='', help_text='PO number/reference for the boards order', max_length=100),
        ),
        migrations.AddField(
            model_name='remedial',
            name='order_accessories_items',
            field=models.TextField(blank=True, default='', help_text='Accessories to order for this remedial'),
        ),
        migrations.AddField(
            model_name='remedial',
            name='order_accessories_po_ref',
            field=models.CharField(blank=True, default='', help_text='PO number/reference for the accessories order', max_length=100),
        ),
        migrations.AddField(
            model_name='remedial',
            name='order_glass_items',
            field=models.TextField(blank=True, default='', help_text='Glass to order for this remedial'),
        ),
        migrations.AddField(
            model_name='remedial',
            name='order_glass_po_ref',
            field=models.CharField(blank=True, default='', help_text='PO number/reference for the glass order', max_length=100),
        ),
        migrations.AddField(
            model_name='remedial',
            name='os_doors_items',
            field=models.TextField(blank=True, default='', help_text='OS Doors to order for this remedial'),
        ),
    ]
