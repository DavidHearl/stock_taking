from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('stock_take', '0159_raumplusoption'),
    ]

    operations = [
        migrations.AddField(
            model_name='raumplusoption',
            name='image',
            field=models.ImageField(blank=True, null=True, upload_to='raumplus_options/'),
        ),
    ]