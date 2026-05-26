from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('stock_take', '0203_add_glass_raumplus_validation_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='boardspo',
            name='from_stock',
            field=models.BooleanField(default=False, help_text='Boards for this PO were taken from existing stock'),
        ),
    ]
