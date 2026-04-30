from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('stock_take', '0167_raumplusorderingrule'),
    ]

    operations = [
        migrations.AddField(
            model_name='raumplusorderingrule',
            name='applicable_products',
            field=models.ManyToManyField(blank=True, related_name='raumplus_rules', to='stock_take.stockitem'),
        ),
    ]
