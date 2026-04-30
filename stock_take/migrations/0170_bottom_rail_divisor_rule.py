from django.db import migrations


def add_bottom_rail_divisor_rule(apps, schema_editor):
    RaumplusOrderingRule = apps.get_model('stock_take', 'RaumplusOrderingRule')

    double_rule = RaumplusOrderingRule.objects.filter(name='rule_double_track_divisor').first()
    fallback_default = '4'
    if double_rule and double_rule.default_value not in (None, ''):
        fallback_default = str(double_rule.default_value)

    RaumplusOrderingRule.objects.update_or_create(
        name='rule_bottom_rail_divisor',
        defaults={
            'label': 'Bottom rail divisor',
            'value_type': 'int',
            'default_value': fallback_default,
            'enabled_name': 'rule_apply_track_rules',
            'enabled_default': True,
            'unit_prefix': '',
            'unit_suffix': 'doors',
            'default_help_text': 'Divisor for bottom rails. Example: 4 means ceil(total doors / 4).',
            'help_text': 'Divisor for bottom rails. Example: 4 means ceil(total doors / 4).',
            'sort_order': 113,
            'is_active': True,
        },
    )

    RaumplusOrderingRule.objects.filter(name='rule_single_track_divisor').update(sort_order=110)
    RaumplusOrderingRule.objects.filter(name='rule_double_track_divisor').update(sort_order=111)
    RaumplusOrderingRule.objects.filter(name='rule_triple_track_divisor').update(sort_order=112)


class Migration(migrations.Migration):

    dependencies = [
        ('stock_take', '0169_track_divisor_rules_split'),
    ]

    operations = [
        migrations.RunPython(add_bottom_rail_divisor_rule, migrations.RunPython.noop),
    ]
