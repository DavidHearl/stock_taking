from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('stock_take', '0108_add_factory_worker_display_order'),
    ]

    operations = [
        migrations.AlterField(
            model_name='timesheet',
            name='order',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='timesheets',
                to='stock_take.order',
            ),
        ),
    ]
