"""
Management command: backfill_validator_notifications

Creates missing OrderValidationRequest records for all site validators on
orders that are already marked all_items_ordered=True and not yet finished.

Usage:
    python manage.py backfill_validator_notifications
    python manage.py backfill_validator_notifications --site Belfast
    python manage.py backfill_validator_notifications --dry-run
"""
from django.core.management.base import BaseCommand
from stock_take.models import UserSiteRole, OrderValidationRequest, Order


class Command(BaseCommand):
    help = 'Backfill missing validator notifications for orders already marked all_items_ordered.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--site',
            type=str,
            default=None,
            help='Limit backfill to a specific site (e.g. Belfast). Defaults to all sites.',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be created without making any changes.',
        )

    def handle(self, *args, **options):
        site_filter = options['site']
        dry_run = options['dry_run']

        roles_qs = UserSiteRole.objects.filter(role_name='validator')
        if site_filter:
            roles_qs = roles_qs.filter(site=site_filter)

        sites = roles_qs.values_list('site', flat=True).distinct()

        total_created = 0
        for site in sites:
            validator_ids = list(
                UserSiteRole.objects.filter(site=site, role_name='validator')
                .values_list('user_id', flat=True)
            )
            if not validator_ids:
                continue

            pending_orders = Order.objects.filter(
                all_items_ordered=True,
                job_finished=False,
                anthill_sale__location=site,
            ).distinct()

            for order in pending_orders:
                for uid in validator_ids:
                    exists = OrderValidationRequest.objects.filter(
                        order=order, recipient_id=uid
                    ).exists()
                    if not exists:
                        if not dry_run:
                            OrderValidationRequest.objects.create(
                                order=order,
                                recipient_id=uid,
                                created_by=None,
                                is_dismissed=False,
                            )
                        total_created += 1
                        self.stdout.write(
                            f"{'[DRY RUN] Would create' if dry_run else 'Created'} notification: "
                            f"order {order.id} ({order.sale_number}) → user id {uid} (site: {site})"
                        )

        if total_created == 0:
            self.stdout.write(self.style.SUCCESS('No missing notifications found.'))
        else:
            action = 'Would create' if dry_run else 'Created'
            self.stdout.write(self.style.SUCCESS(f'{action} {total_created} notification(s).'))
