from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('stock_take', '0241_remedial_record_type'),
    ]

    operations = [
        migrations.AlterField(
            model_name='fitappointment',
            name='fit_duration',
            field=models.DecimalField(
                decimal_places=1,
                default=1,
                help_text='Number of days for the fit, in half-day steps (1 = single day). For orders this mirrors the sale coversheet fit_days; for remedials/warranties it is the record own duration.',
                max_digits=3,
            ),
        ),
    ]
