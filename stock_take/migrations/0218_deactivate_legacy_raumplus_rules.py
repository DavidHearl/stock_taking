from django.db import migrations


# Rules removed from the Raumplus ordering planner. The planner now uses a single
# coverage rule (required + predicted usage over the window) plus the kept global
# rules and pair balancing, so these legacy fix-up rules are no longer needed.
REMOVED_RULES = [
    # Style buffer / style prediction
    'rule_apply_style_buffer',
    'rule_style_buffer',
    'rule_style_prediction_months',
    # Priority weighting
    'rule_apply_priority_weighting',
    'rule_top_roller_priority',
    'rule_bottom_roller_priority',
    'rule_frame_screw_priority',
    'rule_gasket_4mm_priority',
    'rule_gasket_6mm_priority',
    'rule_gasket_8mm_priority',
    # Exclusions
    'rule_apply_exclusions',
    # Gasket target
    'rule_apply_gasket_target',
    'rule_gasket_target_stock',
    # Track / rail divisors & buffers
    'rule_apply_track_rules',
    'rule_single_track_divisor',
    'rule_double_track_divisor',
    'rule_triple_track_divisor',
    'rule_bottom_rail_divisor',
    'rule_top_rail_divisor',
    'rule_track_buffer',
    'rule_rail_buffer',
    # Dividing rail extra pack
    'rule_apply_dividing_rail_pack',
    'rule_dividing_rail_threshold',
]


def deactivate_rules(apps, schema_editor):
    RaumplusOrderingRule = apps.get_model('stock_take', 'RaumplusOrderingRule')
    RaumplusOrderingRule.objects.filter(name__in=REMOVED_RULES).update(is_active=False)


def reactivate_rules(apps, schema_editor):
    RaumplusOrderingRule = apps.get_model('stock_take', 'RaumplusOrderingRule')
    RaumplusOrderingRule.objects.filter(name__in=REMOVED_RULES).update(is_active=True)


class Migration(migrations.Migration):

    dependencies = [
        ('stock_take', '0217_alter_purchaseorder_invoice_not_required'),
        ('stock_take', '0216_desktop_machine_metrics'),
    ]

    operations = [
        migrations.RunPython(deactivate_rules, reactivate_rules),
    ]
