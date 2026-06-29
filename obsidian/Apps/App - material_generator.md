---
tags: [app, material_generator]
aliases: [material_generator]
---

# App — `material_generator`

Handles **material generation for manufacturing**.

## Responsibilities
- Generates **PNX files** for board-cutting machines (`board_logic.py`)
- Generates **CSV files** for accessory ordering
- Provides a database validation utility for parts availability (`workguru_logic.py`)

## Key files
- `material_generator/board_logic.py` — board / PNX logic
- `material_generator/workguru_logic.py` — WorkGuru-related validation
- `material_generator/templatetags/material_filters.py` — template helpers

## Flow context
Feeds into the [[Order Lifecycle]]: generated CSV/PNX outputs become purchase orders for boards, accessories, and outsourced doors.

## Related
- [[App - stock_take]]
- [[Integrations]] (PNX / CAD)
