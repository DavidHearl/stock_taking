---
tags: [architecture, urls, routing, navigation]
---

# URL Routing

How the Django URL configuration maps browser paths to view functions, and how pages link to each other.

## Root router (`stock_taking/urls.py`)

```
/admin/           → Django admin site
/accounts/        → django-allauth (login, logout, OAuth, password reset)
/                 → stock_take.urls  (everything else)
/__debug__/       → Django Debug Toolbar (dev only)
```

## App router (`stock_take/urls.py`)

The app router defines **200+ named URL patterns**, grouped below by area. All names can be referenced with `{% url 'name' %}` in templates.

### Dashboard & Reports
| Pattern | Name | View module |
|---|---|---|
| `/` | `dashboard` | `dashboard_view.py` |
| `/dashboard/monthly-sales/` | `dashboard_monthly_sales` | `dashboard_view.py` |
| `/dashboard/sales-after/` | `dashboard_sales_after` | `dashboard_view.py` |
| `/dashboard/outstanding-report/` | `dashboard_outstanding_report` | `dashboard_view.py` |
| `/dashboard/week-report/` | `dashboard_week_report` | `dashboard_view.py` |
| `/reports/outstanding/` | `report_outstanding_page` | `dashboard_view.py` |
| `/reports/stock/` | `report_stock_page` | `dashboard_view.py` |
| `/reports/monthly/` | `report_monthly_page` | `dashboard_view.py` |

### Projects
| Pattern | Name |
|---|---|
| `/ordering/` | `ordering` / `active_projects` |
| `/ordering/<id>/` | `order_details` |
| `/customers/` | `customers_list` |
| `/customers/<id>/` | `customer_detail` |
| `/sales/` | `sales_list` |
| `/sales/<id>/` | `sale_detail` |
| `/leads/` | `leads_list` |
| `/leads/<id>/` | `lead_detail` |
| `/map/` | `map` |
| `/fit-board/` | `calendar_weekly` |
| `/fit-board/gantt/` | `gantt_chart` |
| `/remedials/` | `remedials` |
| `/remedials/<id>/` | `remedial_detail` |

### Products & Stock
| Pattern | Name |
|---|---|
| `/stock/` | `stock_list` |
| `/products/<id>/` | `product_detail` |
| `/stock-takes/` | `stock_take_list` |
| `/stock-takes/<id>/` | `stock_take_detail` |
| `/shortages/` | `shortages` |
| `/validation-history/` | `validation_history` |

### Purchase
| Pattern | Name |
|---|---|
| `/purchase-orders/` | `purchase_orders_list` |
| `/purchase-orders/<id>/` | `purchase_order_detail` |
| `/purchase-orders/<id>/pdf/` | `purchase_order_download_pdf` |
| `/suppliers/` | `suppliers_list` |
| `/suppliers/<id>/` | `supplier_detail` |
| `/purchase-invoices/` | `purchase_invoices_list` |
| `/purchase-invoices/<id>/` | `purchase_invoice_detail` |

### Accounting
| Pattern | Name |
|---|---|
| `/invoices/` | `invoices_list` |
| `/invoices/<id>/` | `invoice_detail` |
| `/payments/` | `payments_list` |
| `/accounts-payable/` | `accounts_payable_inbox` |
| `/overhead-po/` | `overhead_po_list` |
| `/xero/` | `xero_status` |
| `/timesheets/` | `timesheets` |

### Internal & Admin
| Pattern | Name |
|---|---|
| `/tickets/` | `tickets_list` |
| `/tickets/<id>/` | `ticket_detail` |
| `/gallery/` | `gallery` |
| `/claims/` | `claim_service` |
| `/enquiries/` | `website_enquiries_list` |
| `/upload/` | `fitter_upload` |
| `/cad/` | `cad_db_status` |
| `/it/mobile/` | `mobile_devices` |
| `/it/laptops/` | `laptop_devices` |
| `/it/desktops/` | `desktop_devices` |
| `/schedule/` | `fitter_schedule` |
| `/admin-panel/users/` | `admin_users` |
| `/admin-panel/roles/` | `admin_roles` |
| `/admin-panel/activity/` | `admin_activity_log` |
| `/admin-panel/settings/` | `admin_settings` |
| `/about/` | `about_page` |
| `/profile/` | `user_profile` |

## URL pattern types

### 1. List views
Return paginated or full querysets. Usually accessed from the sidebar.
```
/purchase-orders/     → purchase_orders_list
/customers/           → customers_list
```

### 2. Detail views
Show all data for a single record. Always include a primary key or slug.
```
/purchase-orders/<int:pk>/        → purchase_order_detail
/customers/<int:customer_id>/     → customer_detail
```

### 3. Action endpoints (POST only)
Form submissions or HTMX mutations. Return redirects or JSON.
```
/purchase-orders/<id>/save/       → purchase_order_save
/purchase-orders/<id>/receive/    → purchase_order_receive
/customers/<id>/delete/           → customer_delete
```

### 4. AJAX / HTMX endpoints
Return JSON or HTML partials. Hit via `hx-get` / `hx-post` / JS `fetch`.
```
/purchase-orders/product-search/  → product_search  (JSON)
/orders/<id>/detail-row/          → load_order_details_ajax  (HTML partial)
/dashboard/sales-after/           → dashboard_sales_after  (JSON)
```

### 5. File download endpoints
Return `application/pdf` or `text/csv` responses.
```
/purchase-orders/<id>/pdf/            → purchase_order_download_pdf
/stock/catalog-pdf/                   → product_catalog_pdf
/invoices/<id>/pdf/                   → invoice_detail (with ?format=pdf)
```

### 6. Streaming endpoints (SSE)
Used for long-running sync operations. Returns `StreamingHttpResponse`.
```
/purchase-orders/sync-stream/     → sync_purchase_orders_stream
/invoices/sync-stream/            → sync_invoices_stream
```

## How pages link to each other

### Sidebar (base.html)
The sidebar is the primary navigation. Each link uses `{% url 'name' %}`:
```django
<a href="{% url 'purchase_orders_list' %}">Purchase Orders</a>
```
The active state is set by the child template:
```django
{% block nav_purchase_orders_active %}active{% endblock %}
```

### In-template links
Detail pages link to related detail pages via `{% url %}`:
```django
<!-- On sale_detail.html, link to the linked order -->
<a href="{% url 'order_details' order.pk %}">{{ order.sale_number }}</a>

<!-- On order detail, link back to the customer -->
<a href="{% url 'customer_detail' order.customer_id %}">{{ order.customer }}</a>
```

### Post-form redirects
After a save, views redirect to the detail or list page:
```python
return redirect('purchase_order_detail', pk=po.pk)
return redirect('customers_list')
```

### HTMX in-page navigation
Some detail pages use HTMX to swap tabs or update cards without a full reload:
```html
<button hx-get="{% url 'order_tab_accessories' order.pk %}"
        hx-target="#tab-content"
        hx-swap="innerHTML">
  Accessories
</button>
```

### Search & global search
The global search bar POSTs to `global_search` or `search_results` and returns matching records across models. Individual feature areas have their own search AJAX endpoints (e.g. `purchase_order_search`, `customer_search`).

## Permission gating in URLs

Views are protected at two levels:
1. **`@login_required`** — all views; unauthenticated users go to `/accounts/login/`
2. **`@page_permission_required('codename')`** — checked against `Role.page_permissions`; returns 403 with the custom `stock_take/403.html` template
3. **`RolePermissionMiddleware`** — maps URL names to codenames via `URL_TO_PAGE` dict in `permissions.py`; applies to URLs not explicitly decorated

## Related
- [[Page Construction]]
- [[Middleware & Auth]]
- [[Feature Map]]
- [[App - stock_take]]
