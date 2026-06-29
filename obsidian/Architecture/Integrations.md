---
tags: [architecture, integrations]
---

# Integrations

External systems Atlas synchronises with.

## Anthill (CRM)
- Service: `stock_take/services/anthill_api.py`
- **Syncs:** customers, sales events (activities), leads, payments, remedial events
- Web scraping (via Playwright) for payment data and order status
- Orders linked by **contract number**; location-based filtering
- Related models: [[Data Models|AnthillSale, AnthillPayment, AnthillOrderToPlace]]

## Xero (accounting)
- Service: `stock_take/services/xero_api.py`
- **Syncs:** invoices, payments, contacts/customers, GL codes, chart of accounts
- OAuth token-based auth (stored in `XeroToken`)
- Invoices matched to sales via the **Reference** field (contract number)
- GL codes drive expense categorisation for purchase invoices
- See [[Financial Flow]]

## WorkGuru (project / scheduling)
- Customer IDs, project references, client metadata
- Order `workguru_id` tracks project assignment

## Microsoft Graph (email)
- Service: `stock_take/services/graph_api.py`
- Powers the **Accounts Payable** mailbox (`accounts.payable@sliderobes.com`)
- Email sync + PDF invoice attachment parsing + supplier email rules
- Related models: `MailboxEmail`, `MailboxEmailFilter`, `MailboxExemption`, `SupplierEmailRule`

## Manufacturing CAD / PNX files
- Proprietary board-cutting machine format (PNX)
- Parsed into `PNXItem` records (dimensions, profiles, angled boards)
- CSV variants generated for accessories — see [[App - material_generator]]

## DigitalOcean Spaces (S3-compatible storage)
- Bucket: `stock-taking-media` @ `https://ams3.digitaloceanspaces.com`
- Media served via `django-storages` + WhiteNoise

## Related
- [[Tech Stack]]
- [[Deployment]]
