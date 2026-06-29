---
tags: [app, stock_take]
aliases: [stock_take]
---

# App — `stock_take`

The **primary** Django app. Holds core business logic for all order, inventory, financial, and operational features.

## Sub-domains
- **Customers & Sales** — customer DB, leads, Anthill CRM sales integration
- **Orders** — full order pipeline with materials, assembly, costing
- **Purchasing** — purchase orders, suppliers, receiving, invoicing
- **Inventory** — stock items, categories, stock takes, pricing history → [[Inventory Management]]
- **Invoicing** — sales invoices (from orders), purchase invoices (from POs)
- **Workflow** — multi-stage workflow with tasks & gates → [[Workflow System]]
- **Scheduling & Timesheets** — fit appointments, fitter scheduling, employee timesheets
- **Reporting** — KPI dashboards, material shortages, costing analysis
- **Finance / Accounts Payable** — invoice reconciliation, payment tracking, email-to-invoice
- **Admin & System** — user management, roles/permissions, activity logging
- **Ancillary** — gallery, fitter uploads, claim documents, IT assets, tickets, website enquiries

## View modules
See [[Feature Map]] for the full breakdown of `*_views.py` files.

## Key files
- `stock_take/models.py` — ~85 models → [[Data Models]]
- `stock_take/urls.py` — routing → [[Feature Map]]
- `stock_take/services/` — Anthill, Xero, MS Graph clients → [[Integrations]]
- `stock_take/middleware.py` — custom middleware → [[Middleware & Auth]]

## Related
- [[App - material_generator]]
- [[Project Overview]]
