from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('stock_take', '0157_salecoversheet_remeasure_date'),
    ]

    operations = [
        migrations.AddField(
            model_name='salecoversheet',
            name='fit_days_decided_by',
            field=models.CharField(blank=True, max_length=120),
        ),
    ]
