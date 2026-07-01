"""
backfill_order_materials_cost
─────────────────────────────
One-off / periodic management command to populate Order.materials_cost from the
order's own source records (boards PNX items, accessories and OS doors) using
Order.calculate_materials_cost().

This is the one cost category on the Costing Report that can be recovered from
data already in the system - installation and manufacturing come from logged
timesheets and cannot be reconstructed if they were never entered. Older /
imported orders frequently have materials_cost left at 0 even though the boards,
accessories and doors are all recorded, which understates their cost (and so
overstates profit) on the report.

By default only orders with materials_cost == 0 are touched. Use --force to
recompute every order.

Usage
─────
  # Dry run (no DB writes) - see what would change
  python manage.py backfill_order_materials_cost --dry-run

  # Apply to orders that currently have no materials cost
  python manage.py backfill_order_materials_cost

  # Recompute for every completed order, overwriting existing values
  python manage.py backfill_order_materials_cost --force

  # Restrict to completed jobs only (job_finished / fully_costed)
  python manage.py backfill_order_materials_cost --completed-only
"""

from datetime import date
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db.models import Q

from stock_take.models import Order


class Command(BaseCommand):
	help = 'Populate Order.materials_cost from boards / accessories / OS doors source records'

	def add_arguments(self, parser):
		parser.add_argument(
			'--dry-run', action='store_true',
			help='Report what would change without writing to DB',
		)
		parser.add_argument(
			'--force', action='store_true',
			help='Recompute and overwrite materials_cost even where already set',
		)
		parser.add_argument(
			'--completed-only', action='store_true',
			help='Only process completed jobs (job_finished, fit date passed, or fully_costed)',
		)
		parser.add_argument(
			'--price-per-sqm', type=float, default=12,
			help='Board price per square metre used for the boards portion (default 12)',
		)

	def handle(self, *args, **options):
		dry_run = options['dry_run']
		force = options['force']
		completed_only = options['completed_only']
		price_per_sqm = options['price_per_sqm']

		if dry_run:
			self.stdout.write('[DRY-RUN] No changes will be written.\n')

		orders = Order.objects.all().prefetch_related(
			'accessories', 'os_doors', 'additional_boards_pos',
		)
		if completed_only:
			today = date.today()
			orders = orders.filter(
				Q(job_finished=True) | Q(fit_date__lte=today) | Q(fully_costed=True)
			).distinct()
		if not force:
			orders = orders.filter(materials_cost=0)

		total = orders.count()
		self.stdout.write(f'Orders to evaluate: {total}')

		updated = 0
		skipped_zero = 0
		bulk_updates = []
		for order in orders.iterator():
			try:
				new_cost = order.calculate_materials_cost(price_per_sqm=price_per_sqm)
			except Exception as exc:  # noqa: BLE001 - keep going, report the bad one
				self.stderr.write(f'  ! {order.sale_number}: failed to calculate ({exc})')
				continue

			new_cost = (new_cost or Decimal('0.00')).quantize(Decimal('0.01'))
			old_cost = order.materials_cost or Decimal('0.00')

			# Nothing to derive (no boards/accessories/doors recorded) - leave as-is
			if new_cost <= 0:
				skipped_zero += 1
				continue
			if new_cost == old_cost:
				continue

			self.stdout.write(
				f'  {order.sale_number}: {old_cost} -> {new_cost}'
			)
			order.materials_cost = new_cost
			bulk_updates.append(order)
			updated += 1

			if not dry_run and len(bulk_updates) >= 500:
				Order.objects.bulk_update(bulk_updates, ['materials_cost'])
				bulk_updates = []

		if not dry_run and bulk_updates:
			Order.objects.bulk_update(bulk_updates, ['materials_cost'])

		self.stdout.write(
			self.style.SUCCESS(
				f'\n{"[DRY-RUN] Would update" if dry_run else "Updated"} {updated} order(s). '
				f'{skipped_zero} had no derivable materials data.'
			)
		)
