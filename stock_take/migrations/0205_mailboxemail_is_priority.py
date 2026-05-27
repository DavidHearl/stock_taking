from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('stock_take', '0204_boardspo_from_stock'),
    ]

    operations = [
        migrations.AddField(
            model_name='mailboxemail',
            name='is_priority',
            field=models.BooleanField(
                default=False,
                db_index=True,
                help_text='True if the email matches a priority rule and must be processed immediately',
            ),
        ),
    ]
