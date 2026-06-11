from pathlib import Path

from django.db import migrations, models


def _latest_migration_name():
    migrations_dir = Path(__file__).resolve().parent
    current = Path(__file__).stem
    candidates = []

    for migration_file in migrations_dir.glob('[0-9][0-9][0-9][0-9]_*.py'):
        name = migration_file.stem
        if name != current:
            candidates.append(name)

    if not candidates:
        return '0001_initial'

    return sorted(candidates)[-1]


class Migration(migrations.Migration):

    dependencies = [
        ('stock_take', _latest_migration_name()),
    ]

    operations = [
        migrations.AddField(
            model_name='desktopmachine',
            name='pflops',
            field=models.DecimalField(blank=True, decimal_places=3, max_digits=10, null=True),
        ),
        migrations.AddField(
            model_name='desktopmachine',
            name='vram_gb',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=8, null=True),
        ),
    ]