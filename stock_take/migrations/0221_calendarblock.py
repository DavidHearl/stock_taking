from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('stock_take', '0220_fitappointment_is_provisional'),
    ]

    operations = [
        migrations.CreateModel(
            name='CalendarBlock',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('date', models.DateField(db_index=True)),
                ('fitter_code', models.CharField(help_text='Fitter lane code (matches FitAppointment.fitter)', max_length=5)),
                ('note', models.CharField(blank=True, default='', max_length=100)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'ordering': ['date', 'fitter_code'],
                'unique_together': {('date', 'fitter_code')},
            },
        ),
    ]
