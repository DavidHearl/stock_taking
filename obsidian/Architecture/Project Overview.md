---
tags: [architecture, overview]
---

# Project Overview

**Name:** Sliderobes Atlas
**Purpose:** Internal operations platform that tracks the complete order lifecycle from customer enquiry through manufacturing, installation, and invoicing.

Sliderobes makes sliding wardrobes / fitted furniture. Atlas is the production-critical system the business runs on daily.

## What it does
- Order pipeline management (quote → installation → invoice)
- Customer & contact database (synced with [[Integrations|Anthill CRM]])
- Stock inventory with real-time availability tracking — see [[Inventory Management]]
- Purchase order (PO) workflow for materials & outsourced components
- Sales & purchase invoicing with [[Integrations|Xero]] integration
- Fitter scheduling and timesheet tracking
- Material generation (PNX/CSV for board-cutting machines) — see [[App - material_generator]]
- Financial reporting and KPI dashboards
- Claims service for warranty / remedial work
- Multi-stage [[Workflow System|workflow automation]]

## High-level shape
- Two Django apps: [[App - stock_take]] (core) and [[App - material_generator]].
- ~85 models in `stock_take/models.py` — see [[Data Models]].
- 15+ specialised `*_views.py` modules — see [[Feature Map]].

## Related
- [[Order Lifecycle]]
- [[Financial Flow]]
- [[Tech Stack]]
- [[Integrations]]
