---
tags: [architecture, templates, frontend]
---

# Page Construction

How every page in Atlas is assembled — from URL hit to rendered HTML.

## Request lifecycle

```
Browser request
  → Django URL router (stock_taking/urls.py → stock_take/urls.py)
  → Middleware stack (Security, Session, CSRF, Impersonation, RolePermission, ActivityLogging)
  → View function
      @login_required   — redirect to /accounts/login/ if unauthenticated
      @page_permission_required('codename')  — return 403 if no permission
      → DB queries (models.py)
      → render(request, 'stock_take/<template>.html', context)
  → Template engine
      → Context processors inject role_perms, is_impersonating, etc.
      → {% extends "base.html" %} resolved
      → Blocks filled, {% url %} tags resolved
  → HTTP Response (HTML / PDF / JSON)
```

## Template inheritance tree

```
templates/base.html                  ← root shell (sidebar, nav, blobs)
  └── stock_take/<page>.html         ← page template ({% extends "base.html" %})
        └── partials/<fragment>.html ← HTMX-loaded partial (no extends)
```

`auth_base.html` is a separate root used for login / allauth pages (no sidebar).

## `base.html` blocks

| Block | Purpose |
|---|---|
| `title` | `<title>` tag content |
| `page_title` | Heading displayed in the page header area |
| `extra_css` | Additional `<link>` / `<style>` tags injected into `<head>` |
| `content` | Main page body |
| `extra_js` | Scripts injected before `</body>` |
| `nav_*_active` | Filled with `active` by child template to highlight the correct sidebar item |

Active sidebar example in a child template:
```django
{% block nav_purchase_orders_active %}active{% endblock %}
```

## Context processors

Every template automatically receives these variables (from `stock_take/context_processors.py`):

| Variable | Type | Description |
|---|---|---|
| `role_perms` | dict | `{ 'orders': { 'can_view': True, 'can_edit': True, … }, … }` |
| `is_role_admin` | bool | True if the user's role is `admin` or they are a superuser |
| `is_impersonating` | bool | True when an admin is impersonating another user |
| `current_location` | str | User's selected location (e.g. `nottingham`) |
| `available_locations` | list[str] | All locations from the Customer table |
| `linked_fitter` | Fitter \| None | Fitter record linked to this user, if any |
| `nav_sections` | list | Filtered sidebar sections for this user's role |

Sidebar visibility is controlled by:
```django
{% if role_perms.orders.can_view or is_role_admin %}
<a href="{% url 'ordering' %}">...</a>
{% endif %}
```

## View function pattern

Most views follow this shape:

```python
@login_required
@page_permission_required('codename')          # optional action='edit'
def my_view(request):
    items = MyModel.objects.select_related('fk').filter(...)
    return render(request, 'stock_take/my_page.html', {
        'items': items,
        'extra_var': value,
    })
```

Action views (form submissions) use POST-Redirect-GET:
```python
@login_required
@require_POST
def save_something(request):
    # validate + save
    return redirect('some_list_view')
```

AJAX/HTMX endpoints return `JsonResponse` or an HTML partial:
```python
def search_api(request):
    results = ...
    return JsonResponse({'results': [...] })

def htmx_partial(request):
    return render(request, 'stock_take/partials/my_fragment.html', ctx)
```

PDF views return a raw `HttpResponse`:
```python
def download_pdf(request, pk):
    buffer = generate_pdf(...)
    return HttpResponse(buffer, content_type='application/pdf', headers={
        'Content-Disposition': 'attachment; filename="..."'
    })
```

## HTMX partials

Partials live in `stock_take/templates/stock_take/partials/`. They are rendered server-side and swapped into the DOM by HTMX (`hx-get`, `hx-post`, `hx-swap`).

| Partial | Purpose |
|---|---|
| `entity_hero_card.html` | Re-usable hero card shown at the top of detail pages |
| `order_tab_content.html` | Tab content for order detail (accessories, boards, financials) |
| `order_detail_row.html` | Single row in the order list, refreshed in-place |
| `sale_tab_content.html` | Tab content for sale detail |
| `sale_details_card_body.html` | Body of the sale info card |
| `_sale_row.html` | Single row in the sales list |
| `documents_card.html` | Documents / attachments card on detail pages |
| `invoice_modal.html` | Invoice creation modal loaded on demand |
| `activity_log_rows.html` | Activity log table rows |
| `_validator_modal.html` | Validation request modal |
| `remedial_actions_section.html` | Remedial actions section |

## Sidebar navigation structure

The sidebar in `base.html` is divided into named sections, each gated by `role_perms`:

| Section | Nav links |
|---|---|
| General | Dashboard, Calendar, Reports |
| Projects | Active, Sales, Customers, Map, Gantt |
| Products & Stock | Products, Stock Take, Shortages, Validation |
| Purchase | Purchase Orders, Suppliers |
| Accounting | Invoices, Payments, Match Invoices, Xero, Timesheets |
| IT | Mobile, Laptops, Desktops |
| Internal | Gallery, Fitter Upload, Tickets, Claims, Enquiries |
| Admin | Users, Roles, Activity Log, Settings, About |

## CSS & JS loading

- Global CSS: `static/css/styles.css` + `static/css/light-mode.css` + `bootstrap-icons.min.css` (all in `base.html`)
- Page-specific CSS: loaded via `{% block extra_css %}` — e.g. `dashboard.css`, `fitter_schedule.css`
- HTMX: loaded in `base.html` from CDN or static
- Plotly / Chart.js: loaded in `{% block extra_css %}` of individual pages that use charts
- Bootstrap is **not** loaded as a full bundle — the design system uses bespoke CSS inspired by Bootstrap conventions

## Related
- [[URL Routing]]
- [[Database Schema]]
- [[Middleware & Auth]]
- [[Feature Map]]
- [[CSS Design System]]
