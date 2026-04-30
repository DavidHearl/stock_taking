from django.db import migrations


def add_track_rail_buffer_and_update_units(apps, schema_editor):
    RaumplusOrderingRule = apps.get_model('stock_take', 'RaumplusOrderingRule')

    RaumplusOrderingRule.objects.update_or_create(
        name='rule_track_rail_buffer',
        defaults={
            'label': 'Track and rail buffer',
            'value_type': 'int',
            'default_value': '0',
            'enabled_name': 'rule_apply_track_rules',
            'enabled_default': True,
            'unit_prefix': '',
            'unit_suffix': 'units',
            'default_help_text': 'Extra lengths added to each track/rail requirement after divisor calculation.',
            'help_text': 'Extra lengths added to each track/rail requirement after divisor calculation.',
            'sort_order': 109,
            'is_active': True,
        },
    )

    RaumplusOrderingRule.objects.filter(
        name__in=[
            'rule_top_roller_priority',
            'rule_bottom_roller_priority',
            'rule_frame_screw_priority',
            'rule_gasket_4mm_priority',
            'rule_gasket_6mm_priority',
            'rule_gasket_8mm_priority',
        ]
    ).update(unit_suffix='multi')


class Migration(migrations.Migration):

    dependencies = [
        ('stock_take', '0171_top_rail_divisor_rule'),
    ]

    operations = [
        migrations.RunPython(add_track_rail_buffer_and_update_units, migrations.RunPython.noop),
    ]
