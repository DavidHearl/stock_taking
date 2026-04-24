from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('stock_take', '0152_salecoversheet_extended_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='salecoversheet',
            name='door_details',
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name='salecoversheet',
            name='door_type',
            field=models.CharField(blank=True, max_length=30),
        ),
        migrations.AddField(
            model_name='salecoversheet',
            name='handle_details',
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name='salecoversheet',
            name='lighting_details',
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name='salecoversheet',
            name='track_colour',
            field=models.CharField(blank=True, max_length=100),
        ),
        migrations.AddField(
            model_name='salecoversheet',
            name='track_type',
            field=models.CharField(blank=True, max_length=20),
        ),
    ]
