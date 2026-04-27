from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('stock_take', '0162_website_enquiry_extra_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='stockitem',
            name='pack_size',
            field=models.PositiveIntegerField(default=1, help_text='Number of individual units per supplier pack — PO quantities must be a multiple of this'),
        ),
    ]
