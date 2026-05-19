from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('stock_take', '0194_skugroup'),
    ]

    operations = [
        migrations.AddField(
            model_name='ordervalidationrequest',
            name='boards_checked',
            field=models.BooleanField(default=False, help_text='Validator confirmed boards are correct'),
        ),
        migrations.AddField(
            model_name='ordervalidationrequest',
            name='accessories_checked',
            field=models.BooleanField(default=False, help_text='Validator confirmed accessories are correct'),
        ),
        migrations.AddField(
            model_name='ordervalidationrequest',
            name='os_doors_checked',
            field=models.BooleanField(default=False, help_text='Validator confirmed OS doors are correct'),
        ),
        migrations.AddField(
            model_name='ordervalidationrequest',
            name='glass_checked',
            field=models.BooleanField(default=False, help_text='Validator confirmed glass is correct'),
        ),
    ]
