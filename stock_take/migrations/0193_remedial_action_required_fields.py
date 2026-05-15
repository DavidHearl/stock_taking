from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('stock_take', '0192_mailbox_email_tab_and_filter'),
    ]

    operations = [
        migrations.AddField(
            model_name='remedial',
            name='order_boards_required',
            field=models.BooleanField(default=False, help_text='Boards need to be ordered for this remedial'),
        ),
        migrations.AddField(
            model_name='remedial',
            name='order_accessories_required',
            field=models.BooleanField(default=False, help_text='Accessories need to be ordered for this remedial'),
        ),
        migrations.AddField(
            model_name='remedial',
            name='order_glass_required',
            field=models.BooleanField(default=False, help_text='Glass needs to be ordered for this remedial'),
        ),
    ]
