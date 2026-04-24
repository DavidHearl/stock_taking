from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('stock_take', '0153_salecoversheet_design_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='salecoversheet',
            name='board_colour_backs',
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.AddField(
            model_name='salecoversheet',
            name='board_colour_exterior',
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.AddField(
            model_name='salecoversheet',
            name='board_colour_fronts',
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.AddField(
            model_name='salecoversheet',
            name='board_colour_interior',
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.AddField(
            model_name='salecoversheet',
            name='electrics_utilities_required',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='salecoversheet',
            name='fit_on',
            field=models.CharField(blank=True, max_length=30),
        ),
        migrations.AddField(
            model_name='salecoversheet',
            name='installation_design_type',
            field=models.CharField(blank=True, max_length=100),
        ),
        migrations.AddField(
            model_name='salecoversheet',
            name='installation_products_included',
            field=models.CharField(blank=True, max_length=100),
        ),
        migrations.AddField(
            model_name='salecoversheet',
            name='measured_on',
            field=models.CharField(blank=True, max_length=30),
        ),
        migrations.AddField(
            model_name='salecoversheet',
            name='underfloor_heating',
            field=models.BooleanField(default=False),
        ),
    ]
