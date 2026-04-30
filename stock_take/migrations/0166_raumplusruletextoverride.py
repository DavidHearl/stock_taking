from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('stock_take', '0165_stockitem_order_source_and_pack_cost_price'),
    ]

    operations = [
        migrations.CreateModel(
            name='RaumplusRuleTextOverride',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('rule_name', models.CharField(db_index=True, max_length=100, unique=True)),
                ('help_text', models.TextField(blank=True, default='-')),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'verbose_name': 'Raumplus Rule Text Override',
                'verbose_name_plural': 'Raumplus Rule Text Overrides',
                'ordering': ['rule_name'],
            },
        ),
    ]
