from django.db import migrations


def split_track_rail_buffer(apps, schema_editor):
    RaumplusOrderingRule = apps.get_model('stock_take', 'RaumplusOrderingRule')

    RaumplusOrderingRule.objects.update_or_create(
        name='rule_track_buffer',
        defaults={
            'label': 'Track buffer',
            'value_type': 'int',
            'default_value': '0',
            'enabled_name': 'rule_apply_track_rules',
            'enabled_default': True,
            'unit_prefix': '',
            'unit_suffix': 'units',
            'default_help_text': 'Extra lengths added to each track requirement after divisor calculation.',
            'help_text': 'Extra lengths added to each track requirement after divisor calculation.',
            'sort_order': 109,
            'is_active': True,
        },
    )

    RaumplusOrderingRule.objects.update_or_create(
        name='rule_rail_buffer',
        defaults={
            'label': 'Rail buffer',
            'value_type': 'int',
            'default_value': '0',
            'enabled_name': 'rule_apply_track_rules',
            'enabled_default': True,
            'unit_prefix': '',
            'unit_suffix': 'units',
            'default_help_text': 'Extra lengths added to each rail requirement after divisor calculation.',
            'help_text': 'Extra lengths added to each rail requirement after divisor calculation.',
            'sort_order': 110,
            'is_active': True,
        },
    )

    # Deactivate the old combined rule
    RaumplusOrderingRule.objects.filter(name='rule_track_rail_buffer').update(is_active=False)


class Migration(migrations.Migration):

    dependencies = [
        ('stock_take', '0172_track_rail_buffer_and_multi_units'),
    ]

    operations = [
        migrations.RunPython(split_track_rail_buffer, migrations.RunPython.noop),
    ]
