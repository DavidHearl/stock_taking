# CLAUDE.md

Sliderobes Atlas — internal ops platform (Django 5.2). These are the standing rules for working on this codebase. Follow existing patterns over inventing new ones; this file documents what's already established so it stays consistent as the site grows.

## Running things

```bash
source virtual_environment/bin/activate
python manage.py test_summary --keepdb --noinput   # preferred test runner, clean pass/fail table
python manage.py test                                # standard Django runner also works
```

See [docs/testing.md](docs/testing.md) for full testing docs.

## Views

- **Function-based views only.** No CBVs except the built-in `PasswordReset*` views in `urls.py`. Don't introduce CBVs for new features.
- Every view module starts with `logger = logging.getLogger(__name__)`. Use it for errors/warnings — don't use `print()`.
- **Don't add permission decorators to individual views.** RBAC is enforced globally by `RolePermissionMiddleware` (URL → page_codename → permission), not per-view. Only `@login_required` belongs on a view; add new pages to the permission/page-codename mapping instead of gating in the view body.
- Use `@require_POST` / `@require_http_methods` to constrain HTTP methods on mutating endpoints.
- Response conventions:
  - `render(request, ...)` for full page loads.
  - `JsonResponse({...}, status=...)` for AJAX/API endpoints — including errors: `JsonResponse({'error': 'msg'}, status=4xx)`.
  - `StreamingHttpResponse` for file downloads / SSE sync progress.
  - `HttpResponse` for raw content (generated PDFs).
- Wrap external calls (Anthill, Xero, Graph) and risky operations in try/except, log the failure, return a `JsonResponse` error for AJAX callers rather than letting it 500 silently.
- **Business logic (API calls, data transforms) belongs in `stock_take/services/`**, not inline in views. See `anthill_api.py`, `xero_api.py`, `graph_api.py` as the pattern — views call into services, they don't reimplement API logic.
- Mutations (POST/PUT/PATCH/DELETE) are auto-logged by `ActivityLoggingMiddleware` — don't hand-roll duplicate audit logging in views.

### File size

`stock_take/views.py` is ~17k lines and `purchase_order_views.py` is ~6k — both too large already. **Do not keep adding to `views.py`.** New feature areas get their own `<feature>_views.py` module (mirrors `xero_views.py`, `schedule_views.py`, `product_view.py`, `upload_views.py`), wired into `urls.py` the same way. When touching an existing oversized file, prefer extracting the surrounding feature into its own module over adding more to it, if the change is non-trivial.

## URLs (`stock_take/urls.py`)

- URL paths: hyphenated, e.g. `purchase-orders/`, `purchase-order/<int:po_id>/receive/`.
- URL `name=` values: snake_case, e.g. `name='purchase_orders_list'`.
- AJAX/API endpoints prefixed `api/`, e.g. `api/product-search/`.
- Detail routes take `<int:pk>` or a descriptive `<int:x_id>` and are named `<feature>_detail`.
- Group new routes near their feature's existing block rather than appending to the end of the file.

## Models (`stock_take/models.py`)

- Fields: snake_case. Categorical fields use a class-level UPPERCASE constant, e.g. `STATUS_CHOICES = [('new', 'New'), ...]`.
- No abstract base model — models inherit `models.Model` directly. Don't introduce a base class without discussing it first (it'd be a deliberate refactor, not a one-off).
- FKs to users: `models.ForeignKey('auth.User', on_delete=models.SET_NULL, null=True, blank=True, related_name=...)`. `related_name` is plural/feature-descriptive.
- Money: `DecimalField` — `decimal_places=3` for cost fields, `decimal_places=2` for price fields, always with explicit `max_digits=10`. Never use `FloatField` for money.
- Add `help_text` to non-obvious fields, especially financial ones (existing fields are heavily documented — match that).
- Add `Meta.indexes` for fields used in heavy/frequent queries (see `StockHistory` for the pattern).
- No custom managers currently — if you need one, that's worth a deliberate discussion, not a silent addition.

## Templates (`stock_take/templates/stock_take/`)

- One template per feature, snake_case, e.g. `purchase_order_detail.html`, `admin_users.html`.
- Reusable fragments go in `partials/`.
- Extend the existing base template — don't duplicate `<head>`/nav markup in a new top-level template.

## CSS / Design system (`static/css/`)

**Full design system is documented in [`obsidian/CSS Design System.md`](obsidian/CSS Design System.md). Read it before writing CSS.** The token definitions themselves live in `static/css/styles.css` (`:root` = dark) and `static/css/light-mode.css` (`body.light-mode` overrides). There's also a live reference page at Admin → Design Rules (`admin_design_rules.html`).

- One CSS file per page/feature, matching the template/view name (e.g. `dashboard.css`, `customers.css`, `admin_pages.css`). `styles.css` is global chrome + token definitions only — don't put feature styles there.
- **Never hardcode a colour, radius, shadow, spacing, or transition value.** Always reference a token: colour (`var(--bg-secondary)`, `var(--text-primary)`, `var(--primary-color)`, `var(--border-color)`, semantic `--success/--warning/--danger/--info/--purple-*`), spacing (`var(--sp-1..6)`), radius (`var(--radius-sm/md/lg/pill)`), shadow (`var(--shadow-sm/md/lg)`), motion (`var(--transition)`). This is what makes dark/light mode work — light mode only re-declares variables.
  - The primary brand colour is `var(--primary-color)`, **not** `--accent-color` (that token doesn't exist; only `--accent-glow` does).
- Every new/changed component must read correctly in **both** themes. If it only looks right in dark mode, you hardcoded something.
- Class naming follows a loose BEM-ish pattern: `.admin-card`, `.admin-card-header`, `.admin-card-body`. Match the block-element prefix of the file you're editing rather than inventing a new vocabulary.
- Icons: Bootstrap Icons (`<i class="bi bi-...">`), used sparingly. No emoji in the UI.

## JS (`static/js/`)

- Vanilla JS only — no framework (no Vue/React/Alpine). Keep it that way; interactivity is mostly server-rendered HTML/JSON via fetch.
- Only `script.js` (general) and `dashboard_reports.js` (feature-specific) exist today. New feature-specific JS gets its own file named after the feature, not appended to `script.js`.

## Template tags/filters (`stock_take/templatetags/custom_filters.py`)

Check this file before writing a new filter — `format_date_str`, `date_for_input`, `get_item`, `price_2_4` already exist. Only filters are used (no custom tags); keep new additions as filters unless there's a real need for a tag.

## Management commands (`stock_take/management/commands/`)

snake_case, descriptive, verb-led names (`backfill_invoice_price_history.py`, `cleanup_xero_payment_duplicates.py`). Used for backfills, cleanup, and test tooling (`test_summary.py`) — not part of normal request flow.

## Testing (`stock_take/tests.py`, `docs/testing.md`)

- `TestCase` (Django built-in), not pytest.
- Test classes: `{ModelOrFeature}Tests`, e.g. `CustomerModelTests`, `DesktopMachineViewTests`.
- Test methods: `test_{scenario}`, e.g. `test_create_desktop_machine_with_metrics`.
- Use module-level `_create_*` helpers for fixtures (`_create_user()`, `_create_order()`) rather than repeating setup inline.
- Coverage is currently thin (~82 tests for a ~50k-line app) — when adding non-trivial logic, add a test in the same module/PR rather than relying on manual QA only.

## Don't

- Don't add `.env`, `*.db`, logs, or anything under `virtual_environment/`/`staticfiles/`/`cache/` to git — already gitignored, keep it that way.
- Don't add per-view permission checks — that's the middleware's job.
- Don't introduce a new frontend framework, ORM pattern, or base model class without flagging it as a deliberate architectural change first.
- Don't keep appending to `views.py` or `purchase_order_views.py`.
- Don't use an excessive ammount of icons/emojis.
