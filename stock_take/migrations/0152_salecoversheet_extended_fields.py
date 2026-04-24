from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('stock_take', '0151_salecoversheet'),
    ]

    operations = [
        migrations.AddField(
            model_name='salecoversheet',
            name='access_check_required',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='salecoversheet',
            name='design_check_passed_date',
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='salecoversheet',
            name='fit_days',
            field=models.DecimalField(blank=True, decimal_places=1, max_digits=3, null=True),
        ),
        migrations.AddField(
            model_name='salecoversheet',
            name='goods_due_in_date',
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='salecoversheet',
            name='new_build_property',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='salecoversheet',
            name='ordering_passed_date',
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='salecoversheet',
            name='parking_situation',
            field=models.CharField(blank=True, max_length=40),
        ),
        migrations.AddField(
            model_name='salecoversheet',
            name='pfp_passed_date',
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='salecoversheet',
            name='remeasure_required',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='salecoversheet',
            name='rip_out_required',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='salecoversheet',
            name='two_man_lift_required',
            field=models.BooleanField(default=False),
        ),
    ]
