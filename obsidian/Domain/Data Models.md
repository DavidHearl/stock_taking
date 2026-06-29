---
tags: [domain, models, reference]
---

# Data Models

~85 models live in `stock_take/models.py`. Grouped by domain below.

## CRM & Sales
- `Customer` — master customer records (synced from WorkGuru)
- `Lead` — potential customers; convertible to `Customer`
- `AnthillSale` — sales activities imported from Anthill
- `SaleCoverSheet` / `SaleCoverSheetHistory` — digital job coversheets + revisions
- `AnthillPayment` — payments matched from Xero invoices
- `AnthillOrderToPlace` — Anthill workflow orders pending placement

## Orders
- `Order` — master order record (sale → order → installation)
- `OrderNote` — notes on orders
- `OrderWorkflowProgress` — multi-stage workflow tracking → [[Workflow System]]
- `OrderValidationRequest` — validation gates (design / ordering / fit)

## Materials & Assembly
- `BoardsPO` — purchase order for boards (PNX files)
- `PNXItem` — individual board items from PNX files (with cost calc)
- `OSDoor` — outsourced door components
- `Accessory` — order accessories with availability tracking
- `PurchaseOrderProduct` — line items within POs (all types)

## Stock & Inventory → [[Inventory Management]]
- `StockItem` — inventory items (dimensions, pricing, par levels)
- `Category` — hierarchical product categories
- `StockTakeGroup` — priority-weighted groups for stock-take scheduling
- `StockHistory` — audit trail of stock changes
- `PriceHistory` — historical pricing per item
- `StockItemNote` — notes on stock items
- `ProductLink` — cross-sell relationships (auto-include related item)

## Purchasing
- `PurchaseOrder` — master PO (Draft → Approved → Ordered → Received → Invoiced)
- `Supplier` / `SupplierContact` — supplier directory + contacts
- `PurchaseOrderAttachment` — file attachments (PNX, CSV, PDF)
- `PurchaseOrderInvoice` — invoices linked to PO lines
- `PurchaseOrderProject` — projects within POs
- `ProductCustomerAllocation` — allocate PO quantities to orders

## Invoicing → [[Financial Flow]]
- `Invoice` / `InvoiceLineItem` — sales invoices + lines
- `InvoicePayment` — payments received
- `PurchaseInvoice` / `PurchaseInvoiceLineItem` — supplier invoices + lines
- `OverheadPurchaseOrder` — non-stock overhead expenses

## Scheduling & Labour
- `FitAppointment` — fit dates on orders
- `CalendarBlock` — fitter calendar blocks (holidays, unavailable)
- `SalesAppointment` — sales team appointments
- `Fitter` / `FactoryWorker` — installers / manufacturing workers + rates
- `Timesheet` — hours × rate
- `Expense` — installation expenses (petrol, materials, sundries)
- `EmployeeCalendarEntry` / `EmployeeCalendarRule` — availability + recurring rules

## Workflow → [[Workflow System]]
- `WorkflowStage` — stage definition
- `WorkflowTask` — tasks/checkboxes within a stage
- `TaskCompletion` — completion records
- `WorkflowStageDate` — expected / actual stage dates

## Accounts Payable (email-to-invoice)
- `MailboxEmail` — emails synced from AP mailbox (MS Graph)
- `MailboxEmailFilter` / `MailboxExemption` — filter / exclusion rules
- `SupplierEmailRule` — supplier-specific parsing rules

## Other operational
- `Remedial` / `RemedialAccessory` — remedial / warranty work
- `Schedule` — stock-take schedules
- `Substitution` — SKU substitution rules
- `CSVSkipItem` — items skipped during CSV processing
- `SkuGroup` / `SkuGroupMember` — grouping related SKUs

## Config & Admin → [[People & Roles]]
- `Designer`, `Role`, `PagePermission`, `UserProfile`, `UserSiteRole`
- `Ticket`, `GalleryImage`, `ClaimDocument`
- `FitterUploadSubmission` / `FitterUploadPhoto`
- `RaumplusOption`, `OSDoorOption`, `RaumplusOrderingRule`, `RaumplusDraftOrder`
- `PhoneTemplate`, `MobileDevice`, `DesktopMachine`, `DesktopComponent`

## Logging & Integration
- `ActivityLog` — user action audit trail
- `SyncLog` — external sync operations
- `XeroToken` — Xero OAuth token
- `WebsiteEnquiry` — website form enquiries
- `GLCode` / `EnabledGLCode` — chart-of-accounts integration with Xero
