from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('stock_take', '0154_salecoversheet_installation_board_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='salecoversheet',
            name='electrics_utilities_notes',
            field=models.TextField(blank=True),
        ),
    ]
