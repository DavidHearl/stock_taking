"""Create 'shadow' login accounts for fitters that don't have one.

Each active fitter with a calendar code is given a linked User account assigned
the 'fitter' role so they can sign in to view their Schedule. Existing links are
left untouched, so the command is safe to run repeatedly.
"""

import re

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils.text import slugify

from stock_take.models import Fitter, Role


class Command(BaseCommand):
    help = "Create shadow User accounts for fitters without a linked login."

    def add_arguments(self, parser):
        parser.add_argument(
            '--include-inactive',
            action='store_true',
            help='Also create accounts for inactive fitters.',
        )

    def _unique_username(self, base):
        username = base or 'fitter'
        candidate = username
        suffix = 1
        while User.objects.filter(username=candidate).exists():
            suffix += 1
            candidate = f'{username}{suffix}'
        return candidate

    @transaction.atomic
    def handle(self, *args, **options):
        fitter_role, _ = Role.objects.get_or_create(
            name='fitter',
            defaults={'description': 'Installation fitter — Schedule access only.'},
        )

        fitters = Fitter.objects.filter(user__isnull=True)
        if not options['include_inactive']:
            fitters = fitters.filter(active=True)
        # Only fitters that map to a calendar code can have a meaningful schedule.
        fitters = fitters.exclude(code='')

        created = 0
        skipped = 0
        for fitter in fitters.order_by('name'):
            base = slugify(fitter.name).replace('-', '.')
            base = re.sub(r'\.+', '.', base).strip('.') or f'fitter.{fitter.code.lower()}'
            username = self._unique_username(base)

            parts = fitter.name.split()
            first_name = parts[0] if parts else fitter.name
            last_name = ' '.join(parts[1:]) if len(parts) > 1 else ''

            user = User.objects.create(
                username=username,
                email=fitter.email or '',
                first_name=first_name[:150],
                last_name=last_name[:150],
                is_active=True,
            )
            # Shadow account — no usable password until an admin sets one.
            user.set_unusable_password()
            user.save(update_fields=['password'])

            # post_save signal creates a profile; ensure it has the fitter role.
            profile = user.profile
            profile.role = fitter_role
            profile.save(update_fields=['role'])

            fitter.user = user
            fitter.save(update_fields=['user'])

            created += 1
            self.stdout.write(self.style.SUCCESS(
                f'Created user "{username}" for fitter {fitter.name} ({fitter.code})'
            ))

        skipped = Fitter.objects.filter(user__isnull=False).count()
        self.stdout.write(self.style.SUCCESS(
            f'Done. {created} shadow user(s) created; {skipped} fitter(s) already linked.'
        ))
