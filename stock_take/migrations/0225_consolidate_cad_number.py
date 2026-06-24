from django.db import migrations


def copy_cad_to_order(apps, schema_editor):
    """Preserve existing coversheet CAD numbers by copying them onto the linked
    Order.customer_number, which becomes the single source of truth."""
    SaleCoverSheet = apps.get_model('stock_take', 'SaleCoverSheet')
    for cs in SaleCoverSheet.objects.select_related('sale__order').exclude(cad_number=''):
        order = cs.sale.order if cs.sale_id else None
        if order is None:
            continue
        if (order.customer_number or '').strip():
            continue
        order.customer_number = (cs.cad_number or '').strip()[:6]
        order.save(update_fields=['customer_number'])


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('stock_take', '0224_alter_purchaseinvoicelineitem_rate'),
    ]

    operations = [
        migrations.RunPython(copy_cad_to_order, noop),
        migrations.RemoveField(
            model_name='salecoversheet',
            name='cad_number',
        ),
    ]
