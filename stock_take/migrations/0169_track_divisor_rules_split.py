from django.db import migrations


def split_track_divisor_rules(apps, schema_editor):
    RaumplusOrderingRule = apps.get_model('stock_take', 'RaumplusOrderingRule')

    old_bottom = RaumplusOrderingRule.objects.filter(name='rule_bottom_track_divisor').first()
    old_top = RaumplusOrderingRule.objects.filter(name='rule_top_track_divisor').first()

    def _default_value(rule_obj, fallback):
        if rule_obj and rule_obj.default_value not in (None, ''):
            return str(rule_obj.default_value)
        return str(fallback)

    def _help_text(rule_obj, fallback):
        if rule_obj and (rule_obj.help_text or '').strip() not in ('', '-'):
            return (rule_obj.help_text or '').strip()
        return fallback

    double_default = _default_value(old_bottom, 4)
    single_default = _default_value(old_top, 8)
    triple_default = _default_value(old_top, 8)

    double_help = _help_text(
        old_bottom,
        'Divisor for double tracks (and bottom rails). Example: 4 means ceil(total doors / 4).',
    )
    single_help = _help_text(
        old_top,
        'Divisor for single tracks. Example: 8 means ceil(total doors / 8).',
    )
    triple_help = _help_text(
        old_top,
        'Divisor for triple tracks. Example: 8 means ceil(total doors / 8).',
    )

    defaults = [
        {
            'name': 'rule_double_track_divisor',
            'label': 'Double track divisor',
            'default_value': double_default,
            'sort_order': 110,
            'help_text': double_help,
        },
        {
            'name': 'rule_single_track_divisor',
            'label': 'Single track divisor',
            'default_value': single_default,
            'sort_order': 120,
            'help_text': single_help,
        },
        {
            'name': 'rule_triple_track_divisor',
            'label': 'Triple track divisor',
            'default_value': triple_default,
            'sort_order': 130,
            'help_text': triple_help,
        },
    ]

    for item in defaults:
        RaumplusOrderingRule.objects.update_or_create(
            name=item['name'],
            defaults={
                'label': item['label'],
                'value_type': 'int',
                'default_value': item['default_value'],
                'enabled_name': 'rule_apply_track_rules',
                'enabled_default': True,
                'unit_prefix': '',
                'unit_suffix': 'doors',
                'default_help_text': item['help_text'],
                'help_text': item['help_text'],
                'sort_order': item['sort_order'],
                'is_active': True,
            },
        )

    RaumplusOrderingRule.objects.filter(name__in=['rule_bottom_track_divisor', 'rule_top_track_divisor']).update(is_active=False)


class Migration(migrations.Migration):

    dependencies = [
        ('stock_take', '0168_raumplusorderingrule_applicable_products'),
    ]

    operations = [
        migrations.RunPython(split_track_divisor_rules, migrations.RunPython.noop),
    ]
