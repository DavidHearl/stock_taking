"""
Migration to:
1. Create PagePermission for new 'claim_service' codename on all existing roles.
2. Ensure Franchise role exists with view-only access to tickets + claim_service only.
"""
from django.db import migrations


def setup_franchise_permissions(apps, schema_editor):
    Role = apps.get_model('stock_take', 'Role')
    PagePermission = apps.get_model('stock_take', 'PagePermission')

    # Ensure 'claim_service' PagePermission exists for all roles
    for role in Role.objects.all():
        PagePermission.objects.get_or_create(
            role=role,
            page_codename='claim_service',
            defaults={
                'can_view': role.name in ('admin', 'franchise', 'director', 'user', 'accounting'),
                'can_create': role.name in ('admin',),
                'can_edit': role.name in ('admin',),
                'can_delete': role.name in ('admin',),
            },
        )

    # Ensure Franchise role exists
    franchise, _ = Role.objects.get_or_create(
        name='franchise',
        defaults={'description': 'External franchise users — tickets and claim service only.'},
    )

    # Wipe all existing franchise permissions and set only tickets + claim_service
    PagePermission.objects.filter(role=franchise).delete()

    for codename in ('tickets', 'claim_service'):
        PagePermission.objects.create(
            role=franchise,
            page_codename=codename,
            can_view=True,
            can_create=(codename == 'tickets'),  # Can create tickets
            can_edit=False,
            can_delete=False,
        )


class Migration(migrations.Migration):

    dependencies = [
        ('stock_take', '0077_backfill_group_key'),
    ]

    operations = [
        migrations.RunPython(setup_franchise_permissions, migrations.RunPython.noop),
    ]
