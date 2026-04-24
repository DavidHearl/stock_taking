from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('stock_take', '0155_salecoversheet_electrics_notes'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='salecoversheet',
            name='cad_number',
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.AddField(
            model_name='salecoversheet',
            name='revision_number',
            field=models.PositiveIntegerField(default=1),
        ),
        migrations.CreateModel(
            name='SaleCoverSheetHistory',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('revision_number', models.PositiveIntegerField()),
                ('changes', models.JSONField(blank=True, default=dict)),
                ('changed_at', models.DateTimeField(auto_now_add=True)),
                ('changed_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='sale_coversheet_history_entries', to=settings.AUTH_USER_MODEL)),
                ('coversheet', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='history_entries', to='stock_take.salecoversheet')),
            ],
            options={
                'verbose_name': 'Sale Coversheet History',
                'verbose_name_plural': 'Sale Coversheet History',
                'ordering': ['-changed_at'],
            },
        ),
    ]
