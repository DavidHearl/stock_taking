# Sliderobes Atlas

Internal operations platform for Sliderobes — order management, stock control, purchasing, scheduling, and reporting.

Built with **Django 5.2** and deployed on DigitalOcean App Platform behind Traefik.

---

## Table of Contents

- [Features & Pages](#features--pages)
- [Project Structure](#project-structure)
- [Setup](#setup)
- [Environment Variables](#environment-variables)
- [Management Commands](#management-commands)
- [External Integrations](#external-integrations)
- [Role-Based Access Control](#role-based-access-control)
- [Deployment](#deployment)
- [Colour Palette](#colour-palette)

---

## Features & Pages

### Dashboard (`/`)
KPI overview showing fits per week, weekly sales value, pending purchase orders, and total stock value. Monthly board-cost chart over the last 12 months. Franchise users are redirected straight to the Claim Service.

### Orders / Ordering (`/ordering/`)
Central order pipeline. Each order tracks: customer, sale number, designer, fit date, order type (sale / remedial / warranty), boards PO, OS doors PO, accessories, CSV files, financial breakdown (materials, installation, manufacturing costs, VAT, profit), and fit-completion checkboxes. Search across customers and orders.

### Order Details (`/order/<id>/`)
Full order view with: customer info, financial summary, PNX board items, accessories list (with stock availability and incoming PO quantities), OS doors, CSV upload/processing (with substitution and skip-item resolution for missing SKUs), boards PO file management (PNX & CSV preview, re-import, regeneration), fit status, timesheets, expenses, and workflow progress.

### Customers (`/customers/`)
Customer database synced from Anthill CRM. Supports create, edit, delete, bulk delete, merge, and search. Each customer links to orders, invoices, and Xero contacts. Stores Anthill CRM ID, Xero ID, and address/financial details.

### Contacts (`/leads/`)
Contact records without associated sales. Synced from Anthill CRM (customers without a WorkGuruClientID). Status tracking (New → Contacted → Qualified → Proposal → Converted → Lost). Contacts can be converted to customers. Supports CRUD, bulk delete, and merge.

### Events (`/sales/`)
Anthill CRM activity records (sales, remedials, enquiries, etc.) linked to local customers and orders. Searchable with date-bracket filters and location filtering.

### Invoices (`/invoices/`)
Invoice records synced from Xero. Shows payment status (paid/partial/unpaid), overdue tracking, line items, and payment history. Sync triggered via streaming SSE endpoint.

### Purchase Orders (`/purchase-orders/`)
Full purchase order management. Create, edit, receive, track status (Draft → Approved → Ordered → Received → Invoiced). Product line items with SKU, pricing, quantities, and customer allocations. File attachments (PNX, CSV, PDF). Email POs directly to supplier contacts. PDF generation. Boards and OS Doors PO creation links orders to POs. Streaming sync available.

### Suppliers (`/suppliers/`)
Supplier directory with contact management (multiple contacts per supplier, default email recipient). Linked to purchase orders. Address, financial terms, lead time tracking.

### Boards Summary (`/boards-summary/`)
Overview of all boards purchase orders — PNX items, received status, and order linkage.

### OS Doors Summary (`/os-doors-summary/`)
Overview of all outsourced door orders — styles, colours, dimensions, quantities, received status.

### Products & Stock

- **Stock List** (`/stock/`) — Full inventory with CSV import/export, quantity tracking, and category filtering.
- **Product Detail** (`/product/<id>/`) — Individual product view with dimensions, box/packaging info, supplier code, image upload, stock history graph, and linked PO lines.
- **Stock Items Manager** (`/stock-items-manager/`) — Batch update stock item tracking types and par levels.
- **Categories** (`/categories/`) — Hierarchical category system with colour coding. Parent/child categories.
- **Stock Take Groups** — Priority-weighted groups within categories. Auto-schedule threshold triggers stock takes when items drop below minimum levels.
- **Stock Take** (`/schedules/`) — Two-section layout: **Stock Items** (everyday counting plan) and **Non-Stock Items** (periodic counting). Stock items support an everyday rolling plan; non-stock items can be counted periodically. Counts update the database directly — no CSV export needed.
- **Completed Stock Takes** (`/schedules/completed/`) — History of completed stock takes.
- **Stock Take Detail** (`/stock-take/<id>/`) — Count items during a stock take, updates stock directly.
- **Import History** (`/import-history/`) — Track CSV imports with rollback capability.
- **Substitutions** (`/substitutions/`) — Define SKU substitution rules for when items are missing. Used during CSV processing to auto-replace unavailable products.

### Material Reports

- **Material Report** (`/material-report/`) — Aggregate material usage analysis.
- **Material Shortage** (`/material-shortage/`) — Stock shortage alerts for items below par level.
- **Raumplus Storage** (`/raumplus-storage/`) — Raumplus-specific shortage tracking.
- **Costing Report** (`/costing-report/`) — Order costing analysis with profit margins.

### Remedials (`/remedials/`)
Remedial events from Anthill CRM, filtered from the Events data where `activity_type` contains "Remedial" (e.g. Doors Remedial, Interiors Remedial, Exposed Pieces Remedial). Shows open/closed status with search, pagination, and location filtering. Also displays local remedial orders linked to original orders with boards PO, scheduling, and completion tracking.

### Remedial Report (`/remedial-report/`)
Reporting view for remedial work statistics.

### Calendar & Scheduling

- **Fit Board** (`/fit-board/`) — Visual calendar showing fit appointments by fitter (Ross, Gavin, Stuart, Paddy). Drag appointments between dates, bulk import fit dates.
- **Timesheets** (`/timesheets/`) — Track installation and manufacturing timesheets. Installation uses fixed pricing; manufacturing uses hours × hourly rate. Manage fitters and factory workers with hourly rates.

### Workflow (`/workflow/`)
Configurable multi-stage workflow system. Define stages (Enquiry → Lead → Sale phases), assign roles (Customer Support, Design, Fitter, Operations, Manufacturing), set expected durations. Each stage has tasks (checkboxes, attachments, radio buttons, dropdowns, decision matrices). Orders progress through stages with requirement gates.

### Map (`/map/`)
Interactive Google Maps view showing all order locations. Geocoding results are cached. Northern Ireland focused with fit-all and reset-view controls.

### Generate PNX & CSV (`/generate-pnx-csv/`)
Material generator tool (separate `material_generator` app). Generates PNX files for board cutting machines and CSV files for accessory ordering. Includes database check utility.

### Tickets (`/tickets/`)
Internal support ticket system. Users submit issues with title, description, image, and priority (Low/Medium/High). Status tracking (Open → In Progress → Resolved → Closed). Admin read tracking.

### Claim Service (`/claims/`)
Document management for franchise claims. Upload/download PDF documents grouped by customer. ZIP download for grouped documents. Download tracking per user.

### Xero Integration (`/xero/status/`)
OAuth2 connection to Xero accounting. Connect/disconnect, view token status, test API, create customers in Xero, search Xero contacts.

### User Profile (`/profile/`)
User preferences: dark mode toggle, password change. Site location selector.

### Admin Panel (`/admin-panel/`)
- **Users** (`/admin-panel/users/`) — User management with role assignment and impersonation.
- **Roles** (`/admin-panel/roles/`) — Role-based access control with per-page CRUD permissions.
- **Templates** (`/admin-panel/templates/`) — Document template management.
- **Settings** (`/admin-panel/settings/`) — Application-wide settings.

### Global Search (`/global-search/`)
Cross-entity search across orders, customers, products, and purchase orders.

---

## Project Structure

```
stock_taking/              # Django project settings (settings.py, urls.py, wsgi.py)
stock_take/                # Main application
  ├── models.py            # All database models
  ├── views.py             # Core views (ordering, stock, boards, map, etc.)
  ├── urls.py              # URL routing
  ├── admin.py             # Django admin configuration
  ├── forms.py             # Django forms
  ├── middleware.py         # Custom middleware
  ├── permissions.py        # Permission decorators
  ├── context_processors.py # Template context processors
  ├── dashboard_view.py     # Dashboard KPIs
  ├── customer_views.py     # Customer CRUD + sync
  ├── lead_views.py         # Lead CRUD + conversion
  ├── invoice_views.py      # Invoice list + sync
  ├── purchase_order_views.py # PO management + sync
  ├── product_view.py       # Product detail
  ├── ticket_views.py       # Support tickets
  ├── claim_views.py        # Claim service
  ├── profile_views.py      # User profile
  ├── admin_views.py        # Admin panel views
  ├── xero_views.py         # Xero OAuth + API
  ├── dark_mode_view.py     # Dark mode toggle
  ├── location_view.py      # Site location selector
  ├── pdf_generator.py      # Order summary PDFs
  ├── po_pdf_generator.py   # Purchase order PDFs
  ├── services/
  │   ├── anthill_api.py    # Anthill CRM SOAP API client
  │   └── xero_api.py       # Xero REST API client
  ├── management/commands/  # CLI commands (see below)
  ├── templates/            # HTML templates
  └── templatetags/         # Custom template filters
material_generator/         # PNX & CSV generation app
  ├── board_logic.py        # Board cutting logic
  ├── views.py              # Generation views
  └── db_check_view.py      # Database integrity checker
static/                     # CSS, JS, fonts, images
templates/                  # Base templates & error pages
media/                      # Uploaded files (PNX, CSV, images)
```

---

## Setup

### Prerequisites

- Python 3.10+
- PostgreSQL (production) or SQLite (development)

### Installation

```bash
# Clone the repository
git clone <repo-url>
cd stock_taking

# Create virtual environment
python -m venv .venv
.venv\Scripts\Activate.ps1       # Windows PowerShell
# source .venv/bin/activate      # Linux/Mac

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env             # Edit with your values

# Run migrations
python manage.py migrate

# Create superuser
python manage.py createsuperuser

# Run development server
python manage.py runserver
```

---

## Environment Variables

Create a `.env` file in the project root:

| Variable | Description |
|----------|-------------|
| `SECRET_KEY` | Django secret key |
| `DEBUG` | `1` or `True` for development |
| `ALLOWED_HOSTS` | Comma-separated hostnames |
| `DATABASE_URL` | PostgreSQL connection string |
| `GOOGLE_MAPS_API_KEY` | Google Maps JavaScript + Geocoding API key |
| `ANTHILL_USERNAME` | Anthill CRM API username |
| `ANTHILL_PASSWORD` | Anthill CRM API password |
| `XERO_CLIENT_ID` | Xero OAuth2 client ID |
| `XERO_CLIENT_SECRET` | Xero OAuth2 client secret |
| `BUCKET_ACCESS_ID` | DigitalOcean Spaces access key |
| `BUCKET_SECRET_KEY` | DigitalOcean Spaces secret key |
| `BUCKET_NAME` | DigitalOcean Spaces bucket name |
| `BUCKET_ENDPOINT_URL` | DigitalOcean Spaces endpoint URL |
| `BUCKET_REGION` | DigitalOcean Spaces region |

---

## Management Commands

All commands are run with `python manage.py <command>`.

### Anthill CRM Sync

#### Full Sync — All Customers

Pulls **all** customer records from Anthill CRM (~275k), fetches full details for each, and creates/updates local `Customer` records.

**Smart-skip:** By default the command pre-loads every `anthill_customer_id` already in the database. Customers that already exist are **skipped entirely** — no API detail call, no DB write. Only genuinely new customers trigger API queries, making repeat runs fast. Use `--force` to override this and re-fetch/update every customer.

**Truncation safety:** All string values are automatically truncated to their model `max_length` before saving, preventing `varchar` overflow errors.

**File:** `stock_take/management/commands/sync_anthill_customers.py`

```bash
# Sync new customers only (skips already-synced)
python manage.py sync_anthill_customers

# Re-sync ALL customers, including already-synced ones
python manage.py sync_anthill_customers --force

# Dry run — preview without saving
python manage.py sync_anthill_customers --dry-run

# Limit to first 100 customers (for testing)
python manage.py sync_anthill_customers --limit 100

# Skip fetching full customer details (faster, less data)
python manage.py sync_anthill_customers --skip-details
```

#### Scheduled Sync — Recent Customers

Syncs customers from Anthill CRM created within the last 7 days. Runs automatically at **08:00** and **12:00** daily via the `scheduler` Docker service. Customers with a `WorkGuruClientID` are saved as `Customer`; all others are saved as `Contact` (Lead).

**File:** `stock_take/management/commands/sync_recent_customers.py`

```bash
# Sync last 7 days (default)
python manage.py sync_recent_customers

# Sync last 14 days
python manage.py sync_recent_customers --days 14

# Dry run — preview without saving
python manage.py sync_recent_customers --dry-run
```

> **Docker:** The `scheduler` service in `docker-compose.yml` runs `sync_recent_customers` automatically. Ensure `ANTHILL_USERNAME` and `ANTHILL_PASSWORD` are set in your `.env` file.

#### Standalone Script (legacy)

```bash
# Two-phase import (customers + sales)
python sync_anthill_customers.py

# Only sync sales for existing customers
python sync_anthill_customers.py --sales-only

# Only import new customers (skip sales phase)
python sync_anthill_customers.py --skip-sales

# Import last 365 days only
python sync_anthill_customers.py --days 365

# Dry run
python sync_anthill_customers.py --dry-run
```

### Xero Integration

> **Prerequisite:** Connect to Xero via `/xero/status/` in the web app first.
> This establishes the OAuth2 token needed for all API calls.

#### Sync Customers to Xero

Fetches all contacts from Xero and matches them to local Customer records by name. Stores the Xero Contact ID on each matched customer.

```bash
python manage.py sync_xero_customers

# Dry run
python manage.py sync_xero_customers --dry-run
```

#### Sync Invoices from Xero

For every customer with a Xero Contact ID, fetches their invoices and creates/updates local Invoice records with payment status.

```bash
python manage.py sync_xero_invoices

# Dry run
python manage.py sync_xero_invoices --dry-run

# Single customer (by database PK)
python manage.py sync_xero_invoices --customer 123
```

### Google Maps Usage

```bash
# Check current API usage vs free tier limits
python manage.py check_maps_usage

# Check last 60 days
python manage.py check_maps_usage --days 60

# Alert at 50% threshold
python manage.py check_maps_usage --alert-threshold 50
```

### Maintenance Commands

```bash
# Clean up duplicate auto-generated stock take schedules
python manage.py cleanup_duplicate_schedules
python manage.py cleanup_duplicate_schedules --dry-run

# Set completion dates on legacy completed schedules
python manage.py set_legacy_completion_dates
python manage.py set_legacy_completion_dates --days-ago 35 --dry-run
```

---

## External Integrations

### Anthill CRM
SOAP API integration for customer and sales data import. Customers are classified as either `Customer` (has sales) or `Lead` based on their activity history. Sales activities are stored as `AnthillSale` records linked to customers and orders.

- **Env vars:** `ANTHILL_USERNAME`, `ANTHILL_PASSWORD`
- **API client:** `stock_take/services/anthill_api.py`
- **Subdomain:** `sliderobes.anthillcrm.com`

### Xero
OAuth2 integration for accounting. Syncs contacts for customer matching and fetches invoices with payment data. Token refresh is handled automatically (access tokens expire every 30 minutes, refresh tokens last 60 days).

- **Env vars:** `XERO_CLIENT_ID`, `XERO_CLIENT_SECRET`
- **API client:** `stock_take/services/xero_api.py`
- **Token storage:** `XeroToken` model
- **Connect via:** `/xero/status/` in the web app

### Google Maps
Maps JavaScript API and Geocoding API for the order location map. Geocoding results are cached to minimise API calls.

- **Env var:** `GOOGLE_MAPS_API_KEY`
- **Free tier:** 10,000 loads/month (Dynamic Maps), 10,000 requests/month (Geocoding)

---

## Role-Based Access Control

Five roles with granular per-page CRUD permissions:

| Role | Description |
|------|-------------|
| **Admin** | Full access to all pages and actions |
| **Director** | Configurable access — typically everything |
| **Accounting** | Financial pages: invoices, costing, purchase orders |
| **User** | Standard operational access |
| **Franchise** | Restricted — redirected to Claim Service only |

Permissions are configured per page (view/create/edit/delete) via the Admin Panel → Roles page.

---

## Deployment

### Docker

```bash
docker-compose up --build -d
```

Runs behind Traefik reverse proxy at `stock-taking.mediaservers.co.uk`. Static files served from `/srv/static/stock_taking`, media from `/srv/media/stock_taking`.

### DigitalOcean App Platform

Production deployment at `atlas-gxbq5.ondigitalocean.app`. Uses DigitalOcean Spaces for media storage (S3-compatible via `django-storages` + `boto3`).

---

## Colour Palette

| Element | Hex | RGB |
|---------|-----|-----|
| Background | `#272831` | 39, 40, 49 |
| Accent | `#32323b` | 50, 50, 59 |
