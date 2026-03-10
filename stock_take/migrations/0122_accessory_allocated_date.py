import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('stock_take', '0121_add_boards_accessories_not_required'),
    ]

    operations = [
        migrations.AddField(
            model_name='accessory',
            name='allocated_date',
            field=models.DateField(blank=True, null=True, help_text='Date when materials were physically taken from stock'),
        ),
        migrations.AlterField(
            model_name='stockhistory',
            name='created_at',
            field=models.DateTimeField(default=django.utils.timezone.now, db_index=True),
        ),
    ]
