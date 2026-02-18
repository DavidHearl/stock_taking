from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('stock_take', '0088_add_supplier_code_to_stockitem'),
    ]

    operations = [
        migrations.AddField(
            model_name='customer',
            name='title',
            field=models.CharField(blank=True, help_text='Title e.g. Mr, Mrs, Dr', max_length=20, null=True),
        ),
    ]
