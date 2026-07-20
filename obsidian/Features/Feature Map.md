---
tags: [features, reference, moc]
---

# Feature Map

Each feature area maps to a `*_views.py` module in `stock_take/` and a URL prefix.

| Feature | View module | URL prefix | Purpose |
|---|---|---|---|
| Dashboard & Reports | `dashboard_view.py` | `/` | KPI dashboard, sales/stock/outstanding reports |
| Orders | `views.py` | `/ordering/` | Core order list/detail, creation workflow |
| Customers & Sales | `customer_views.py` | `/customers/`, `/sales/` | Customer CRUD, sales, payment reconciliation, Xero/Anthill linking |
| Invoicing | `invoice_views.py` | `/invoices/` | Sales invoice creation/management, Xero sync |
| Purchase Orders | `purchase_order_views.py` | `/purchase-orders/` | Full PO lifecycle, suppliers, PDF gen, emailing |
| Purchase Invoices | `purchase_invoice_views.py` | `/purchase-invoices/` | Supplier invoice processing, PDF parsing, Xero sync |
| Match Invoices | `match_invoices_views.py` | `/match-invoices/` | Email inbox (MS Graph), invoice extraction, PO matching |
| Products / Stock | `product_view.py` | `/products/`, `/stock/` | Product catalog, stock items, pricing, images, history |
| Stock Takes | `views.py` | `/schedules/` | Rolling / periodic counts, batch updates |
| Leads & Contacts | `lead_views.py` | `/leads/` | Lead CRUD, status, bulk ops, merge |
| Fit Board / Calendar | `dashboard_view.py`, `views.py` | `/fit-board/` | Visual calendar, appointments, drag-drop reschedule |
| Timesheets & Labour | `views.py` | `/timesheets/` | Install/mfg timesheets, rates, expense logging |
| Material Generation | (material_generator) | `/generate-pnx-csv/` | PNX/CSV generation, DB validation |
| Tickets | `ticket_views.py` | `/tickets/` | Internal support tickets, priority, image attachments |
| Claims Service | `claim_views.py` | `/claims/` | Claim doc upload/download, archive (franchise-facing) |
| Gallery | `gallery_views.py` | `/gallery/` | Project photo gallery |
| CAD Management | `cad_views.py` | `/cad/` | CAD database upload/download, status |
| IT Assets | `it_views.py` | `/it/` | Mobile/SIM/laptop/desktop asset inventory |
| Admin Console | `admin_views.py` | `/admin/` | User mgmt, role permissions, activity log, script execution |
| Website Enquiries | `enquiry_views.py` | `/enquiries/` | Website form submissions, status, notes |
| User Profile | `profile_views.py` | `/profile/` | Profile, password change |
| Xero Integration | `xero_views.py` | `/xero/` | OAuth, customer sync, GL code mapping |
| Payments | `payments_views.py` | `/payments/` | Payment tracking across sources |
| Overhead POs | `overhead_po_views.py` | `/overhead-po/` | Non-product overhead expenses |
| Location Map | `location_view.py` | `/map/` | Google Maps location view |
| About / Info | `about_views.py` | `/about/` | Project stats, LOC count, DB stats, media usage |
| Remedials | `views.py` | `/remedials/` | Remedial / warranty work tracking |

> URL prefixes are approximate — confirm against `stock_take/urls.py` for exact paths.

## Related
- [[App - stock_take]]
- [[Project Overview]]
