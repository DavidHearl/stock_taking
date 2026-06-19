from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('stock_take', '0219_alter_pagepermission_page_codename'),
    ]

    operations = [
        migrations.AddField(
            model_name='fitappointment',
            name='is_provisional',
            field=models.BooleanField(default=False, help_text='Provisional fit date — dragged from job list, not yet confirmed'),
        ),
    ]
