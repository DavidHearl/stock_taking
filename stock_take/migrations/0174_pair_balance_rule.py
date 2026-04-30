from django.db import migrations


def add_pair_balance_rule(apps, schema_editor):
    RaumplusOrderingRule = apps.get_model('stock_take', 'RaumplusOrderingRule')

    RaumplusOrderingRule.objects.update_or_create(
        name='rule_pair_balance_packs',
        defaults={
            'label': 'Top/Bottom pair balancing packs',
            'value_type': 'int',
            'default_value': '1',
            'enabled_name': 'rule_apply_track_rules',
            'enabled_default': True,
            'unit_prefix': '',
            'unit_suffix': 'packs',
            'default_help_text': 'When top/bottom track or rail counts are out of balance, add this many pack increments to the lower side in threshold triggers.',
            'help_text': 'When top/bottom track or rail counts are out of balance, add this many pack increments to the lower side in threshold triggers.',
            'sort_order': 111,
            'is_active': True,
        },
    )


class Migration(migrations.Migration):

    dependencies = [
        ('stock_take', '0173_split_track_rail_buffer'),
    ]

    operations = [
        migrations.RunPython(add_pair_balance_rule, migrations.RunPython.noop),
    ]
