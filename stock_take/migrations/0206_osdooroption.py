from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('stock_take', '0205_mailboxemail_is_priority'),
    ]

    operations = [
        migrations.CreateModel(
            name='OSDoorOption',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('option_type', models.CharField(choices=[('style', 'Door Style'), ('colour', 'Colour')], db_index=True, max_length=20)),
                ('name', models.CharField(max_length=120)),
                ('image', models.ImageField(blank=True, null=True, upload_to='os_door_options/')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'ordering': ['option_type', 'name'],
                'unique_together': {('option_type', 'name')},
            },
        ),
    ]
