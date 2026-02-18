from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('stock_take', '0086_add_selected_location_to_profile'),
    ]

    operations = [
        migrations.CreateModel(
            name='SupplierContact',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('first_name', models.CharField(blank=True, default='', max_length=100)),
                ('last_name', models.CharField(blank=True, default='', max_length=100)),
                ('email', models.EmailField(blank=True, default='', max_length=255)),
                ('phone', models.CharField(blank=True, default='', max_length=100)),
                ('position', models.CharField(blank=True, default='', max_length=150)),
                ('is_default', models.BooleanField(default=False, help_text='Use this contact as the default email recipient for POs')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('supplier', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='contacts', to='stock_take.supplier')),
            ],
            options={
                'ordering': ['-is_default', 'last_name', 'first_name'],
            },
        ),
    ]
