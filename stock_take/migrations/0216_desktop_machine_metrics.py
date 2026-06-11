from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('stock_take', '0215_desktopmachine_desktopcomponent'),
    ]

    operations = [
        migrations.AddField(
            model_name='desktopmachine',
            name='pflops',
            field=models.DecimalField(blank=True, decimal_places=3, max_digits=10, null=True),
        ),
        migrations.AddField(
            model_name='desktopmachine',
            name='vram_gb',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=8, null=True),
        ),
    ]