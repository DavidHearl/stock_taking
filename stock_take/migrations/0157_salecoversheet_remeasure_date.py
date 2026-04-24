from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('stock_take', '0156_salecoversheet_revision_and_history'),
    ]

    operations = [
        migrations.AddField(
            model_name='salecoversheet',
            name='remeasure_date',
            field=models.DateField(blank=True, null=True),
        ),
    ]
