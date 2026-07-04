"""Build the products lookup that the accessory generator needs.

Historically the generator (material_generator/accessories_logic.py) ATTACHed a
standalone products.db SQLite file and JOINed CAD component codes to products on
`cad_sku`. That file no longer exists — the product catalogue lives on the site
in StockItem, and each product's CAD code is stored in StockItem.cad_sku.

Rather than change the generator's intricate CAD SQL, we project the current
StockItem rows into a throwaway SQLite database with the same `products` table
shape the generator expects. The data is therefore always live (no stale external
file), and the temp file is deleted by the caller after generation.
"""

import sqlite3
import tempfile

from stock_take.models import StockItem


def build_products_db_from_stock_items():
	"""Create a temp SQLite DB with a `products` table projected from StockItem.

	Only products that carry a CAD code (cad_sku) are included — those are the
	only rows the generator can match against CAD component codes.

	Column mapping (products table -> StockItem):
		wg_sku      <- sku            (the product SKU; column name kept for the generator SQL)
		cad_sku     <- cad_sku        (the CAD component code / join key)
		name        <- name
		description <- description
		cost_price  <- cost           (unit cost, authoritative on the site)
		sell_price  <- 0             (historically blank in the import so the
									   downstream system applies its own sell price)

	Returns:
		str: path to the temp SQLite file. The caller is responsible for
		deleting it once generation is done.
	"""
	tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
	tmp.close()

	conn = sqlite3.connect(tmp.name)
	try:
		conn.execute(
			"""
			CREATE TABLE products (
				wg_sku TEXT,
				cad_sku TEXT,
				name TEXT,
				description TEXT,
				cost_price REAL,
				sell_price REAL
			)
			"""
		)
		rows = (
			StockItem.objects
			.exclude(cad_sku='')
			.values_list('sku', 'cad_sku', 'name', 'description', 'cost')
		)
		conn.executemany(
			"INSERT INTO products (wg_sku, cad_sku, name, description, cost_price, sell_price) "
			"VALUES (?, ?, ?, ?, ?, 0)",
			[
				(sku, cad_sku, name, description, float(cost or 0))
				for sku, cad_sku, name, description, cost in rows
			],
		)
		conn.execute("CREATE INDEX idx_products_cad_sku ON products(cad_sku)")
		conn.commit()
	finally:
		conn.close()

	return tmp.name
