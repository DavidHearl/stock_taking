---
tags: [domain, inventory, stock]
---

# Inventory Management

How Atlas tracks stock availability and allocation.

## Availability formula
```
available = current stock + incoming (approved POs) − allocated orders
```

## Key behaviours
- **Stock allocation:** once `is_allocated=True`, stock is deducted from the available pool.
- **SKU substitution:** `Substitution` rules engine handles missing items during CSV processing.
- **Par-level alerts:** stock takes triggered when items fall below thresholds.
- **Stock takes:** rolling / periodic counts via `Schedule` + `StockTakeGroup` (priority-weighted).

## Audit & history
- `StockHistory` — every change to stock quantity.
- `PriceHistory` — historical pricing per item.

## Related models
- `StockItem`, `Category`, `StockTakeGroup`, `StockHistory`, `PriceHistory`, `ProductLink`
- See [[Data Models]]

## Related
- [[Order Lifecycle]]
- [[App - material_generator]]
