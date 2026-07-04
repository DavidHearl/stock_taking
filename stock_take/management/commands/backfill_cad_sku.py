"""Seed StockItem.cad_sku from the legacy products SQLite database.

The old order-generator kept a standalone products.db whose `products` table
mapped CAD component codes (cad_sku / CODCOMP) to product SKUs (wg_sku).
That mapping never came across when the product catalogue moved onto the site,
so this one-off backfill copies each cad_sku onto the matching StockItem
(matched by StockItem.sku == products.wg_sku).

Usage:
	python manage.py backfill_cad_sku --db products-2.db
	python manage.py backfill_cad_sku --db products-2.db --dry-run
"""

import os
import sqlite3

from django.core.management.base import BaseCommand, CommandError

from stock_take.models import StockItem


class Command(BaseCommand):
	help = "Backfill StockItem.cad_sku from a legacy products SQLite database"

	def add_arguments(self, parser):
		parser.add_argument(
			'--db',
			default='products-2.db',
			help='Path to the legacy products SQLite file (default: products-2.db)',
		)
		parser.add_argument(
			'--dry-run',
			action='store_true',
			help='Report what would change without writing to the database',
		)
		parser.add_argument(
			'--overwrite',
			action='store_true',
			help='Overwrite a cad_sku that is already set (default: only fill blanks)',
		)

	def handle(self, *args, **options):
		db_path = options['db']
		dry_run = options['dry_run']
		overwrite = options['overwrite']

		if not os.path.exists(db_path):
			raise CommandError(f'Products database not found at: {db_path}')

		conn = sqlite3.connect(db_path)
		conn.row_factory = sqlite3.Row
		try:
			rows = conn.execute(
				"SELECT wg_sku, cad_sku FROM products WHERE TRIM(COALESCE(cad_sku, '')) <> ''"
			).fetchall()
		finally:
			conn.close()

		# Index StockItems by sku for O(1) lookups.
		by_sku = {}
		for item in StockItem.objects.all():
			by_sku.setdefault(item.sku, item)

		updated = 0
		skipped_existing = 0
		conflicts = 0
		unmatched = []

		for row in rows:
			wg_sku = (row['wg_sku'] or '').strip()
			cad_sku = (row['cad_sku'] or '').strip()
			if not wg_sku or not cad_sku:
				continue

			item = by_sku.get(wg_sku)
			if item is None:
				unmatched.append((wg_sku, cad_sku))
				continue

			current = (item.cad_sku or '').strip()
			if current and not overwrite:
				if current != cad_sku:
					conflicts += 1
					self.stdout.write(
						f'  conflict: {wg_sku} already has cad_sku={current!r}, '
						f'legacy file has {cad_sku!r} (kept existing)'
					)
				else:
					skipped_existing += 1
				continue

			if not dry_run:
				item.cad_sku = cad_sku
				item.save(update_fields=['cad_sku'])
			updated += 1

		self.stdout.write('')
		self.stdout.write(self.style.SUCCESS(
			f'{"[dry-run] would update" if dry_run else "Updated"} {updated} product(s) with a cad_sku'
		))
		if skipped_existing:
			self.stdout.write(f'{skipped_existing} already had the same cad_sku (unchanged)')
		if conflicts:
			self.stdout.write(self.style.WARNING(
				f'{conflicts} product(s) already had a different cad_sku (kept existing; use --overwrite to replace)'
			))
		if unmatched:
			self.stdout.write(self.style.WARNING(
				f'{len(unmatched)} cad-bearing product(s) had no matching StockItem.sku:'
			))
			for wg_sku, cad_sku in unmatched:
				self.stdout.write(f'  no StockItem for wg_sku={wg_sku!r} (cad_sku={cad_sku!r})')
