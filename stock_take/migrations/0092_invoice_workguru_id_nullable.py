from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('stock_take', '0091_add_additional_boards_pos'),
    ]

    operations = [
        migrations.AlterField(
            model_name='invoice',
            name='workguru_id',
            field=models.IntegerField(
                blank=True,
                help_text='WorkGuru Invoice ID',
                null=True,
                unique=True,
            ),
        ),
    ]
