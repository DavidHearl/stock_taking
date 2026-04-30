from django.db import migrations


def add_top_rail_divisor_rule(apps, schema_editor):
    RaumplusOrderingRule = apps.get_model('stock_take', 'RaumplusOrderingRule')

    single_rule = RaumplusOrderingRule.objects.filter(name='rule_single_track_divisor').first()
    fallback_default = '8'
    if single_rule and single_rule.default_value not in (None, ''):
        fallback_default = str(single_rule.default_value)

    RaumplusOrderingRule.objects.update_or_create(
        name='rule_top_rail_divisor',
        defaults={
            'label': 'Top rail divisor',
            'value_type': 'int',
            'default_value': fallback_default,
            'enabled_name': 'rule_apply_track_rules',
            'enabled_default': True,
            'unit_prefix': '',
            'unit_suffix': 'doors',
            'default_help_text': 'Divisor for top rails. Example: 8 means ceil(total doors / 8).',
            'help_text': 'Divisor for top rails. Example: 8 means ceil(total doors / 8).',
            'sort_order': 114,
            'is_active': True,
        },
    )


class Migration(migrations.Migration):

    dependencies = [
        ('stock_take', '0170_bottom_rail_divisor_rule'),
    ]

    operations = [
        migrations.RunPython(add_top_rail_divisor_rule, migrations.RunPython.noop),
    ]
