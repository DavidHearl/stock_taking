from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('stock_take', '0240_alter_pagepermission_page_codename'),
    ]

    operations = [
        migrations.AddField(
            model_name='remedial',
            name='record_type',
            field=models.CharField(
                choices=[('remedial', 'Remedial'), ('warranty', 'Warranty')],
                default='remedial',
                help_text='Whether this record is a remedial or a warranty claim; both share the same fields and workflow.',
                max_length=20,
            ),
        ),
    ]
