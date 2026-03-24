from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.models import User
from django.http import JsonResponse, StreamingHttpResponse
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import csrf_exempt
from django.contrib import messages
from .models import Role, PagePermission, PAGE_SECTIONS, PAGE_CHOICES, SyncLog, ActivityLog
import subprocess
import threading
import time
import os
import signal


def staff_required(view_func):
    """Decorator that requires the *real* user (not impersonated) to be staff."""
    def wrapper(request, *args, **kwargs):
        real_user = getattr(request, 'real_user', request.user)
        if not real_user.is_authenticated or not real_user.is_staff:
            from django.contrib.auth.views import redirect_to_login
            return redirect_to_login(request.get_full_path())
        return view_func(request, *args, **kwargs)
    wrapper.__name__ = view_func.__name__
    wrapper.__doc__ = view_func.__doc__
    return wrapper


def _real_staff_required(view_func):
    """Requires the *real* user (not impersonated) to be staff."""
    def wrapper(request, *args, **kwargs):
        real_user = getattr(request, 'real_user', request.user)
        if not real_user.is_authenticated or not real_user.is_staff:
            return redirect('dashboard')
        return view_func(request, *args, **kwargs)
    return wrapper


# ── Impersonation ─────────────────────────────────────────────────
@_real_staff_required
@require_POST
def impersonate_start(request, user_id):
    """Begin impersonating another user. Staff only."""
    target = get_object_or_404(User, pk=user_id)
    real_user = getattr(request, 'real_user', request.user)

    if target == real_user:
        messages.info(request, "You cannot impersonate yourself.")
        return redirect('admin_users')

    request.session['_impersonate_user_id'] = target.pk
    messages.success(
        request,
        f"Now impersonating {target.get_full_name() or target.username}."
    )
    return redirect('dashboard')


@login_required
def impersonate_stop(request):
    """Stop impersonating and return to the real admin account."""
    if '_impersonate_user_id' in request.session:
        del request.session['_impersonate_user_id']
        messages.success(request, "Impersonation ended.")
    return redirect('dashboard')


@staff_required
def admin_users(request):
    """Admin users management page."""
    roles = Role.objects.all()

    if request.method == 'POST':
        user_id = request.POST.get('user_id')
        role_id = request.POST.get('role_id')
        action = request.POST.get('action')

        if action == 'assign_role':
            try:
                target_user = User.objects.get(id=user_id)
                profile = target_user.profile
                if role_id:
                    role = Role.objects.get(id=role_id)
                    profile.role = role
                else:
                    profile.role = None
                profile.save()
                messages.success(request, f"Role updated for {target_user.username}")
            except (User.DoesNotExist, Role.DoesNotExist):
                messages.error(request, "Invalid user or role")
        return redirect('admin_users')

    users = User.objects.all().select_related('profile', 'profile__role').order_by('username')

    # Group users by role
    from collections import OrderedDict
    role_groups = OrderedDict()
    # Add groups for each defined role in order
    for role in roles.order_by('name'):
        role_groups[role.id] = {
            'role': role,
            'users': [],
        }
    # Add a group for users with no role
    role_groups[None] = {
        'role': None,
        'users': [],
    }
    for u in users:
        role_id = u.profile.role_id if hasattr(u, 'profile') and u.profile else None
        if role_id in role_groups:
            role_groups[role_id]['users'].append(u)
        else:
            role_groups[None]['users'].append(u)

    context = {
        'users': users,
        'roles': roles,
        'role_groups': list(role_groups.values()),
    }
    return render(request, 'stock_take/admin_users.html', context)


@staff_required
def admin_templates(request):
    """Admin templates management page."""
    context = {}
    return render(request, 'stock_take/admin_templates.html', context)


@staff_required
def admin_roles(request):
    """Admin roles management page - list all roles."""
    roles = Role.objects.prefetch_related('page_permissions', 'users').all()

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'create_defaults':
            _create_default_roles()
            messages.success(request, "Default roles created successfully")
            return redirect('admin_roles')

    context = {
        'roles': roles,
        'page_sections': PAGE_SECTIONS,
    }
    return render(request, 'stock_take/admin_roles.html', context)


@staff_required
def admin_role_edit(request, role_id):
    """Edit a specific role's page permissions."""
    role = get_object_or_404(Role, id=role_id)

    if request.method == 'POST':
        # Update description
        role.description = request.POST.get('description', '')
        role.save()

        # Update page permissions
        for codename, label in PAGE_CHOICES:
            perm, created = PagePermission.objects.get_or_create(
                role=role,
                page_codename=codename,
            )
            perm.can_view = request.POST.get(f'{codename}_view') == 'on'
            perm.can_create = request.POST.get(f'{codename}_create') == 'on'
            perm.can_edit = request.POST.get(f'{codename}_edit') == 'on'
            perm.can_delete = request.POST.get(f'{codename}_delete') == 'on'
            perm.save()

        messages.success(request, f"Permissions updated for {role.get_name_display()}")
        return redirect('admin_roles')

    # Build permission data for template
    existing_perms = {p.page_codename: p for p in role.page_permissions.all()}
    sections_data = []
    for section_name, pages in PAGE_SECTIONS:
        pages_data = []
        for codename, label in pages:
            perm = existing_perms.get(codename)
            pages_data.append({
                'codename': codename,
                'label': label,
                'can_view': perm.can_view if perm else False,
                'can_create': perm.can_create if perm else False,
                'can_edit': perm.can_edit if perm else False,
                'can_delete': perm.can_delete if perm else False,
            })
        sections_data.append({
            'name': section_name,
            'pages': pages_data,
        })

    context = {
        'role': role,
        'sections_data': sections_data,
    }
    return render(request, 'stock_take/admin_role_edit.html', context)


@staff_required
@require_POST
def admin_role_toggle_all(request, role_id):
    """Toggle all permissions for a role (AJAX endpoint)."""
    role = get_object_or_404(Role, id=role_id)
    enable = request.POST.get('enable') == 'true'
    permission_type = request.POST.get('type', 'all')  # 'view', 'create', 'edit', 'delete', or 'all'

    for codename, label in PAGE_CHOICES:
        perm, created = PagePermission.objects.get_or_create(
            role=role,
            page_codename=codename,
        )
        if permission_type == 'all':
            perm.can_view = enable
            perm.can_create = enable
            perm.can_edit = enable
            perm.can_delete = enable
        else:
            setattr(perm, f'can_{permission_type}', enable)
        perm.save()

    return JsonResponse({'success': True})


def _create_default_roles():
    """Create the 5 default roles with appropriate permissions."""
    for role_name, role_label in Role.ROLE_CHOICES:
        role, created = Role.objects.get_or_create(
            name=role_name,
            defaults={'description': f'{role_label} role'}
        )

        if role_name == 'admin':
            # Admin gets everything
            for codename, label in PAGE_CHOICES:
                PagePermission.objects.get_or_create(
                    role=role,
                    page_codename=codename,
                    defaults={
                        'can_view': True,
                        'can_create': True,
                        'can_edit': True,
                        'can_delete': True,
                    }
                )
        else:
            # Everyone else gets view-only on dashboard and tickets by default
            for codename, label in PAGE_CHOICES:
                defaults = {
                    'can_view': codename in ('dashboard', 'tickets'),
                    'can_create': codename == 'tickets',
                    'can_edit': False,
                    'can_delete': False,
                }
                PagePermission.objects.get_or_create(
                    role=role,
                    page_codename=codename,
                    defaults=defaults,
                )


@staff_required
def admin_settings(request):
    """Admin settings page."""
    context = {}
    return render(request, 'stock_take/admin_settings.html', context)


@staff_required
def admin_design_rules(request):
    """Design system reference — live preview of all UI components."""
    return render(request, 'stock_take/admin_design_rules.html')


# ── Registry of all API / sync scripts ────────────────────────────────
# Each entry may optionally carry a 'log_name' key that matches the
# SyncLog.script_name written by that script.  If omitted, no run history
# is shown for that entry.
SCRIPT_GROUPS = [
    {
        'group': 'Anthill CRM',
        'icon': 'bi-people-fill',
        'scripts': [
            {
                'log_name': 'sync_anthill_customers_management',
                'label': 'Full Customer Sync',
                'bullets': [
                    'Pulls all ~275k customer records from Anthill CRM and creates/updates local Customer records.',
                    'Smart-skip: customers already in the database are ignored entirely — no API call, no DB write.',
                    'Only genuinely new customers trigger detail queries, making repeat runs fast.',
                    'Use --force to re-fetch every customer regardless.',
                ],
                'file': 'stock_take/management/commands/sync_anthill_customers.py',
                'schedule': 'Manual',
                'commands': [
                    {'cmd': 'python manage.py sync_anthill_customers', 'note': 'Sync new customers only (skips already-synced)'},
                    {'cmd': 'python manage.py sync_anthill_customers --force', 'note': 'Re-sync ALL customers, including already-synced'},
                    {'cmd': 'python manage.py sync_anthill_customers --dry-run', 'note': 'Preview without saving'},
                    {'cmd': 'python manage.py sync_anthill_customers --limit 100', 'note': 'Limit to first 100 customers (for testing)'},
                    {'cmd': 'python manage.py sync_anthill_customers --skip-details', 'note': 'Skip fetching full customer details (faster)'},
                ],
            },
            {
                'log_name': 'sync_recent_customers',
                'label': 'Recent Customer Sync',
                'bullets': [
                    'Two-pass sync, runs automatically at 08:00 & 12:00 UTC.',
                    'Pass 1: scans Anthill for customers created in the last 7 days, saves each as Customer or Lead.',
                    'Pass 2: re-checks every Lead with an Anthill ID for a qualifying sale and promotes them to Customer.',
                    'Catches leads created months ago that have only recently received a sale in Anthill.',
                    'Use --skip-upgrade to run Pass 1 only.',
                ],
                'file': 'stock_take/management/commands/sync_recent_customers.py',
                'schedule': 'Automated — daily at 08:00 & 12:00 (Docker scheduler)',
                'commands': [
                    {'cmd': 'python manage.py sync_recent_customers', 'note': 'Scan 7 days + upgrade all leads (default)'},
                    {'cmd': 'python manage.py sync_recent_customers --dry-run', 'note': 'Preview without saving'},
                    {'cmd': 'python manage.py sync_recent_customers --skip-upgrade', 'note': 'Scan only — skip lead upgrade pass'},
                    {'cmd': 'python manage.py sync_recent_customers --days 14', 'note': 'Extend scan window to 14 days'},
                ],
            },
            {
                'log_name': 'sync_anthill_customers',
                'label': 'Standalone: Two-Phase Customer & Sales Import',
                'bullets': [
                    'Legacy standalone script at the project root (not a Django management command).',
                    'Phase 1: syncs sale activities for customers already in the database.',
                    'Phase 2: imports any new customers/leads from Anthill.',
                    'Writes a SyncLog entry on completion.',
                ],
                'file': 'sync_anthill_customers.py',
                'schedule': 'Manual',
                'commands': [
                    {'cmd': 'python sync_anthill_customers.py', 'note': 'Full sync (both phases)'},
                    {'cmd': 'python sync_anthill_customers.py --sales-only', 'note': 'Only sync sales for existing customers'},
                    {'cmd': 'python sync_anthill_customers.py --skip-sales', 'note': 'Only import new customers'},
                    {'cmd': 'python sync_anthill_customers.py --days 365', 'note': 'Import last 365 days only'},
                    {'cmd': 'python sync_anthill_customers.py --dry-run', 'note': 'Preview without saving'},
                ],
            },
            {
                'log_name': 'sync_anthill_workflow',
                'label': 'Standalone: Workflow Status Refresh',
                'bullets': [
                    'Refreshes status, category, and activity_type on existing AnthillSale records.',
                    'Fetches the latest activity data from Anthill CRM.',
                    'Groups requests by customer to minimise API calls.',
                    'Writes a SyncLog entry on completion.',
                ],
                'file': 'sync_anthill_workflow.py',
                'schedule': 'Manual',
                'commands': [
                    {'cmd': 'python sync_anthill_workflow.py', 'note': 'Refresh all existing sale records'},
                    {'cmd': 'python sync_anthill_workflow.py --dry-run', 'note': 'Report changes without writing to DB'},
                    {'cmd': 'python sync_anthill_workflow.py --days 180', 'note': 'Only refresh sales active within last 180 days'},
                ],
            },
            {
                'log_name': 'sync_anthill_fit_dates',
                'label': 'Sync Installation (Fit) Dates from Anthill',
                'bullets': [
                    'Parses the "Fit From Date" custom field (DD/MM/YYYY text) into AnthillSale.fit_date (a proper date field).',
                    'The Anthill SOAP API does not expose appointments — fit dates come from this custom field.',
                    'Run after sync_anthill_workflow to ensure all fit dates are populated.',
                    'sync_anthill_workflow also parses fit dates automatically on each run.',
                ],
                'file': 'stock_take/management/commands/sync_anthill_fit_dates.py',
                'schedule': 'Manual / after sync_anthill_workflow',
                'commands': [
                    {'cmd': 'python manage.py sync_anthill_fit_dates --dry-run', 'note': 'Preview changes without saving'},
                    {'cmd': 'python manage.py sync_anthill_fit_dates', 'note': 'Parse all fit_from_date values into fit_date'},
                    {'cmd': 'python manage.py sync_anthill_fit_dates --missing-only', 'note': 'Only sales with no fit_date yet (fastest)'},
                    {'cmd': 'python manage.py sync_anthill_fit_dates --days 365', 'note': 'Only sales from the last 12 months'},
                    {'cmd': 'python manage.py sync_anthill_fit_dates --sale-id 419324', 'note': 'Single sale (for testing)'},
                ],
            },
            {
                'log_name': None,
                'label': 'Backfill Sale Fit Dates (from Orders — one-off)',
                'bullets': [
                    'One-off command: copied confirmed fit dates from linked Order records into AnthillSale.fit_date.',
                    '117 records updated on 2026-03-09.',
                    'Superseded by sync_anthill_fit_dates, which parses the Fit From Date custom field directly.',
                ],
                'file': 'stock_take/management/commands/backfill_sale_fit_dates.py',
                'schedule': 'One-off (already run — 117 records updated)',
                'commands': [
                    {'cmd': 'python manage.py backfill_sale_fit_dates --dry-run', 'note': 'Preview without saving'},
                    {'cmd': 'python manage.py backfill_sale_fit_dates', 'note': 'Copy fit dates from linked Orders'},
                    {'cmd': 'python manage.py backfill_sale_fit_dates --force', 'note': 'Overwrite existing fit dates too'},
                ],
            },
            {
                'log_name': None,
                'label': 'Link Sales ↔ Orders & Sync Fit Dates',
                'bullets': [
                    'Links AnthillSale records to their matching Order (by sale_number = anthill_activity_id).',
                    'Syncs fit_date bidirectionally: Order → Sale and Sale → Order.',
                    'Safe to re-run at any time — only updates where links or dates are missing.',
                ],
                'file': 'stock_take/management/commands/link_sales_orders.py',
                'schedule': 'Manual / after syncing sales or orders',
                'commands': [
                    {'cmd': 'python manage.py link_sales_orders --dry-run', 'note': 'Preview changes'},
                    {'cmd': 'python manage.py link_sales_orders', 'note': 'Link sales to orders and sync fit dates'},
                ],
            },
            {
                'log_name': 'sync_anthill_payments',
                'label': 'Payment History Sync  ⚠️ API limitation',
                'bullets': [
                    '⚠️ NOT AVAILABLE — the Anthill SOAP API has no payment read endpoint.',
                    'Only write-only methods exist: AddPayment / AddPaymentByUserId.',
                    'Payment history visible in the Anthill UI cannot be fetched via the API.',
                    'The model and command are retained as placeholders for a future API version.',
                ],
                'file': 'stock_take/management/commands/sync_anthill_payments.py',
                'schedule': 'N/A — API does not support reading payments',
                'commands': [
                    {'cmd': 'python manage.py sync_anthill_payments', 'note': '⚠️ Will exit immediately with API limitation notice'},
                ],
            },
            {
                'log_name': 'upgrade_leads',
                'label': 'Upgrade Leads → Customers',
                'bullets': [
                    'Re-checks every Lead with an Anthill Customer ID for a qualifying (non-cancelled) sale.',
                    'Promotes matching leads: creates a Customer record, saves sale activities, links existing AnthillSale records.',
                    'Marks the Lead as converted after promotion.',
                    'Safe to run repeatedly — already-converted leads are skipped.',
                ],
                'file': 'stock_take/management/commands/upgrade_leads.py',
                'schedule': 'Manual / as needed',
                'commands': [
                    {'cmd': 'python manage.py upgrade_leads --dry-run', 'note': 'Preview which leads would be promoted'},
                    {'cmd': 'python manage.py upgrade_leads', 'note': 'Promote all qualifying leads to Customer'},
                    {'cmd': 'python manage.py upgrade_leads --limit 10', 'note': 'Process first 10 matches only (for testing)'},
                ],
            },
        ],
    },
    {
        'group': 'Xero',
        'icon': 'bi-receipt-cutoff',
        'scripts': [
            {
                'log_name': 'sync_xero_customers',
                'label': 'Sync Customers to Xero',
                'bullets': [
                    'Fetches all contacts from Xero and matches them to local Customer records by name.',
                    'Stores the Xero Contact ID on each matched customer.',
                    'Prerequisite: connect to Xero via /xero/status/ first.',
                ],
                'file': 'stock_take/management/commands/sync_xero_customers.py',
                'schedule': 'Manual',
                'commands': [
                    {'cmd': 'python manage.py sync_xero_customers', 'note': 'Match and store Xero Contact IDs'},
                    {'cmd': 'python manage.py sync_xero_customers --dry-run', 'note': 'Preview without saving'},
                ],
            },
            {
                'log_name': 'sync_xero_invoices',
                'label': 'Sync Invoices from Xero',
                'bullets': [
                    'Fetches invoices for every customer with a Xero Contact ID.',
                    'Creates/updates local Invoice records with payment status.',
                    'Prerequisites: Xero connected (/xero/status/) and sync_xero_customers run first.',
                ],
                'file': 'stock_take/management/commands/sync_xero_invoices.py',
                'schedule': 'Manual',
                'commands': [
                    {'cmd': 'python manage.py sync_xero_invoices', 'note': 'Sync invoices for all customers with a Xero ID'},
                    {'cmd': 'python manage.py sync_xero_invoices --dry-run', 'note': 'Preview without saving'},
                    {'cmd': 'python manage.py sync_xero_invoices --customer 123', 'note': 'Single customer (by database PK)'},
                ],
            },
            {
                'log_name': 'sync_xero_sale_payments',
                'label': 'Sync Sale Payments from Xero',
                'bullets': [
                    'Matches Anthill sale contract numbers (e.g. BFS-SD-412885) against Xero invoice Reference fields.',
                    'Stores individual payments as AnthillPayment records.',
                    'Read-only — never writes to or modifies any Xero data.',
                    'Prerequisites: Xero connected (/xero/status/) and sales have contract numbers (run sync_anthill_workflow first).',
                ],
                'file': 'stock_take/management/commands/sync_xero_sale_payments.py',
                'schedule': 'Automated — daily at 10:00 (Docker scheduler)',
                'commands': [
                    {'cmd': 'python manage.py sync_xero_sale_payments', 'note': 'Sync all Category 3 sales with a contract number'},
                    {'cmd': 'python manage.py sync_xero_sale_payments --days 90', 'note': 'Only sales active within last 90 days'},
                    {'cmd': 'python manage.py sync_xero_sale_payments --sale-id 417437', 'note': 'Single sale by Anthill activity ID'},
                    {'cmd': 'python manage.py sync_xero_sale_payments --dry-run', 'note': 'Preview without saving to database'},
                    {'cmd': 'python manage.py sync_xero_sale_payments --no-name-check', 'note': 'Match by reference only (skip contact name cross-check)'},
                ],
            },
            {
                'log_name': 'mark_historic_sales_paid',
                'label': 'Mark Historic Sales as Paid',
                'bullets': [
                    'Marks all existing AnthillSale records as paid_in_full=True.',
                    'Establishes a clean baseline for the outstanding balance dashboard card.',
                    'Run once after setting up Xero payment sync.',
                    'Use --location to restrict to a single branch.',
                ],
                'file': 'stock_take/management/commands/mark_historic_sales_paid.py',
                'schedule': 'One-time',
                'commands': [
                    {'cmd': 'python manage.py mark_historic_sales_paid --dry-run', 'note': 'Preview how many records would be updated'},
                    {'cmd': 'python manage.py mark_historic_sales_paid', 'note': 'Mark all unpaid sales as paid'},
                    {'cmd': 'python manage.py mark_historic_sales_paid --location Belfast', 'note': 'Only Belfast sales'},
                ],
            },
        ],
    },
    {
        'group': 'Google Maps',
        'icon': 'bi-map',
        'scripts': [
            {
                'log_name': 'check_maps_usage',
                'label': 'Check Maps API Usage',
                'bullets': [
                    'Reports current Google Maps API usage against free-tier limits.',
                    'Limits: 10,000 Dynamic Map loads/month and 10,000 Geocoding requests/month.',
                    'Alerts when usage exceeds the configured threshold.',
                ],
                'file': 'stock_take/management/commands/check_maps_usage.py',
                'schedule': 'Manual',
                'commands': [
                    {'cmd': 'python manage.py check_maps_usage', 'note': 'Check current usage'},
                    {'cmd': 'python manage.py check_maps_usage --days 60', 'note': 'Check last 60 days'},
                    {'cmd': 'python manage.py check_maps_usage --alert-threshold 50', 'note': 'Alert at 50% of free tier'},
                ],
            },
        ],
    },
    {
        'group': 'Maintenance',
        'icon': 'bi-wrench-adjustable',
        'scripts': [
            {
                'log_name': 'cleanup_duplicate_schedules',
                'label': 'Clean Up Duplicate Schedules',
                'bullets': [
                    'Removes duplicate auto-generated stock take schedules that accumulate over time.',
                    'Safe to run at any time.',
                ],
                'file': 'stock_take/management/commands/cleanup_duplicate_schedules.py',
                'schedule': 'Manual / as needed',
                'commands': [
                    {'cmd': 'python manage.py cleanup_duplicate_schedules', 'note': 'Remove duplicates'},
                    {'cmd': 'python manage.py cleanup_duplicate_schedules --dry-run', 'note': 'Preview without deleting'},
                ],
            },
            {
                'log_name': 'cleanup_xero_payment_duplicates',
                'label': 'Clean Up Xero Payment Duplicates',
                'bullets': [
                    'Removes duplicate Xero payment records caused by overpayments being recorded with the full amount on every sale for the same customer.',
                    'Pass 1: Removes old-format plain-UUID records where new UUID_InvoiceID records exist.',
                    'Pass 2: Detects cross-sale duplication where the same payment appears on multiple sales.',
                    'Pass 3: Removes payments that push a sale\'s total paid above its sale value.',
                    'Always preview with --dry-run first.',
                ],
                'file': 'stock_take/management/commands/cleanup_xero_payment_duplicates.py',
                'schedule': 'Manual / as needed',
                'commands': [
                    {'cmd': 'python manage.py cleanup_xero_payment_duplicates --dry-run', 'note': 'Preview without deleting'},
                    {'cmd': 'python manage.py cleanup_xero_payment_duplicates', 'note': 'Remove duplicates'},
                ],
            },
            {
                'log_name': 'set_legacy_completion_dates',
                'label': 'Set Legacy Completion Dates',
                'bullets': [
                    'Backfills completion dates on older completed stock take schedule records.',
                    'Covers records that pre-date the addition of the completion date field.',
                    'One-off migration helper — safe to re-run.',
                ],
                'file': 'stock_take/management/commands/set_legacy_completion_dates.py',
                'schedule': 'One-off',
                'commands': [
                    {'cmd': 'python manage.py set_legacy_completion_dates', 'note': 'Set completion dates'},
                    {'cmd': 'python manage.py set_legacy_completion_dates --days-ago 35 --dry-run', 'note': 'Preview with custom offset'},
                ],
            },
        ],
    },
    {
        'group': 'Tests',
        'icon': 'bi-check2-circle',
        'scripts': [
            {
                'log_name': None,
                'label': 'Run Unit Tests (stock_take app)',
                'bullets': [
                    'Runs the full Django test suite for the stock_take application (currently 77 tests).',
                    'Covers models, RBAC, forms, dashboard helpers, views, stock history, payments, and order workflow.',
                    'Uses --keepdb by default to reuse the test database and speed up repeated runs.',
                    'Exit code 0 = all pass; failures are shown inline.',
                ],
                'file': 'stock_take/tests.py',
                'schedule': 'Manual / before deployments',
                'commands': [
                    {'cmd': 'python manage.py test stock_take --keepdb', 'note': 'Run all tests (reuse test DB)'},
                    {'cmd': 'python manage.py test stock_take', 'note': 'Run all tests (recreate test DB from scratch)'},
                    {'cmd': 'python manage.py test stock_take --keepdb --verbosity=2', 'note': 'Verbose output — shows each test name'},
                ],
            },
        ],
    },
]


def _fmt_age(ran_at):
    """Return a single-unit age string (e.g. '3 days ago') for a datetime."""
    if ran_at is None:
        return None
    from django.utils import timezone
    delta = timezone.now() - ran_at
    total_seconds = int(delta.total_seconds())
    if total_seconds < 60:
        return 'just now'
    minutes = total_seconds // 60
    hours   = total_seconds // 3600
    days    = total_seconds // 86400
    months  = days // 30
    years   = days // 365
    if minutes < 60:
        return f"{minutes} min ago"
    elif hours < 24:
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    elif days < 90:
        return f"{days} day{'s' if days != 1 else ''} ago"
    elif months < 12:
        return f"{months} month{'s' if months != 1 else ''} ago"
    else:
        return f"{years} year{'s' if years != 1 else ''} ago"


@staff_required
def admin_api(request):
    """Admin API scripts page — shows script registry and recent run logs."""
    # Enrich each script entry with SyncLog data
    groups = []
    for group in SCRIPT_GROUPS:
        enriched_scripts = []
        for entry in group['scripts']:
            log_name = entry.get('log_name')
            last_log = SyncLog.objects.filter(script_name=log_name).order_by('-ran_at').first() if log_name else None
            recent_logs = SyncLog.objects.filter(script_name=log_name).order_by('-ran_at')[:5] if log_name else []

            # Derive trigger type and schedule detail from the schedule string
            schedule = entry.get('schedule', '')
            if 'Automated' in schedule:
                trigger = 'automated'
                schedule_detail = schedule.replace('Automated — ', '').replace('Automated', '').strip(' —')
            elif 'One-off' in schedule:
                trigger = 'oneoff'
                import re as _re
                m = _re.search(r'\((.+)\)', schedule)
                schedule_detail = m.group(1) if m else ''
            elif 'One-time' in schedule:
                trigger = 'oneoff'
                schedule_detail = ''
            elif 'N/A' in schedule:
                trigger = 'na'
                schedule_detail = schedule.replace('N/A — ', '').replace('N/A', '').strip(' —')
            else:
                trigger = 'manual'
                schedule_detail = schedule.split(' / ', 1)[1] if ' / ' in schedule else ''

            enriched_scripts.append({
                **entry,
                'last_log': last_log,
                'recent_logs': recent_logs,
                'trigger': trigger,
                'schedule_detail': schedule_detail,
                'last_run_display': _fmt_age(last_log.ran_at) if last_log else None,
            })
        groups.append({
            'group': group['group'],
            'icon': group['icon'],
            'scripts': enriched_scripts,
        })

    context = {
        'groups': groups,
    }
    return render(request, 'stock_take/admin_api.html', context)


# ── Script runner ─────────────────────────────────────────────────
# In-memory registry of running processes.  Keyed by a unique run_id.
# Each value is a dict: {process, output_lines, started_at, cmd, status, pid}
# This lives in the Django process so scripts persist across page navigations.
_running_scripts = {}
_script_lock = threading.Lock()

# Whitelist of allowed commands (prefixes) to prevent arbitrary execution
ALLOWED_CMD_PREFIXES = set()
for _grp in SCRIPT_GROUPS:
    for _scr in _grp['scripts']:
        for _c in _scr.get('commands', []):
            ALLOWED_CMD_PREFIXES.add(_c['cmd'])


def _reader_thread(proc, run_id):
    """Background thread that reads stdout/stderr and appends to output buffer."""
    try:
        for line in iter(proc.stdout.readline, ''):
            with _script_lock:
                entry = _running_scripts.get(run_id)
                if entry:
                    entry['output_lines'].append(line)
    except Exception:
        pass
    finally:
        proc.stdout.close()
        proc.wait()
        with _script_lock:
            entry = _running_scripts.get(run_id)
            if entry:
                entry['status'] = 'finished'
                entry['exit_code'] = proc.returncode


@staff_required
@require_POST
def run_script(request):
    """Start a whitelisted script as a subprocess.  Returns a run_id for
    streaming output and cancellation."""
    import json
    body = json.loads(request.body)
    cmd = body.get('cmd', '').strip()

    # Validate against whitelist
    if cmd not in ALLOWED_CMD_PREFIXES:
        return JsonResponse({'error': 'Command not allowed'}, status=403)

    # Only allow one instance of the same command at a time
    with _script_lock:
        for rid, entry in _running_scripts.items():
            if entry['cmd'] == cmd and entry['status'] == 'running':
                return JsonResponse({
                    'error': 'This script is already running',
                    'run_id': rid,
                }, status=409)

    # Generate a unique run id
    import uuid
    run_id = uuid.uuid4().hex[:12]

    # Replace 'python' with the venv python or sys.executable
    import sys
    parts = cmd.split()
    if parts[0] == 'python':
        parts[0] = sys.executable

    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    try:
        proc = subprocess.Popen(
            parts,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            cwd=base_dir,
            env={**os.environ, 'PYTHONUNBUFFERED': '1'},
        )
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)

    with _script_lock:
        _running_scripts[run_id] = {
            'process': proc,
            'output_lines': [],
            'started_at': time.time(),
            'cmd': cmd,
            'status': 'running',
            'exit_code': None,
            'pid': proc.pid,
        }

    # Start reader thread
    t = threading.Thread(target=_reader_thread, args=(proc, run_id), daemon=True)
    t.start()

    return JsonResponse({'run_id': run_id, 'pid': proc.pid})


@staff_required
def script_output(request, run_id):
    """SSE endpoint that streams script output lines.  Stays open until the
    script finishes or the client disconnects."""
    import json as _json

    def event_stream():
        cursor = 0
        while True:
            with _script_lock:
                entry = _running_scripts.get(run_id)
                if not entry:
                    yield f"data: {_json.dumps({'type': 'error', 'message': 'Unknown run_id'})}\n\n"
                    return

                new_lines = entry['output_lines'][cursor:]
                cursor += len(new_lines)
                status = entry['status']
                exit_code = entry['exit_code']

            for line in new_lines:
                yield f"data: {_json.dumps({'type': 'output', 'line': line})}\n\n"

            if status == 'finished':
                yield f"data: {_json.dumps({'type': 'finished', 'exit_code': exit_code})}\n\n"
                return

            time.sleep(0.3)

    response = StreamingHttpResponse(event_stream(), content_type='text/event-stream')
    response['Cache-Control'] = 'no-cache'
    response['X-Accel-Buffering'] = 'no'
    return response


@staff_required
@require_POST
def cancel_script(request, run_id):
    """Kill a running script."""
    with _script_lock:
        entry = _running_scripts.get(run_id)
        if not entry:
            return JsonResponse({'error': 'Unknown run_id'}, status=404)
        if entry['status'] != 'running':
            return JsonResponse({'status': entry['status']})

    proc = entry['process']
    try:
        proc.terminate()
        # Give it a moment, then force-kill
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)

    with _script_lock:
        entry['status'] = 'cancelled'
        entry['exit_code'] = -1

    return JsonResponse({'status': 'cancelled'})


@staff_required
def running_scripts_status(request):
    """Return status of all running/recent scripts so the page can reconnect
    after navigation."""
    with _script_lock:
        result = {}
        for rid, entry in _running_scripts.items():
            result[rid] = {
                'cmd': entry['cmd'],
                'status': entry['status'],
                'exit_code': entry['exit_code'],
                'started_at': entry['started_at'],
                'pid': entry['pid'],
                'output_line_count': len(entry['output_lines']),
            }
    return JsonResponse(result)


@staff_required
def admin_activity_log(request):
    """Admin page showing the user activity log."""
    from django.contrib.auth.models import User as DjangoUser

    # Filters
    event_type   = request.GET.get('event_type', '')
    user_id      = request.GET.get('user_id', '')
    search       = request.GET.get('q', '').strip()

    logs = ActivityLog.objects.select_related('user', 'order')

    if event_type:
        logs = logs.filter(event_type=event_type)
    if user_id:
        logs = logs.filter(user_id=user_id)
    if search:
        logs = logs.filter(description__icontains=search)

    # Paginate – 50 per page
    from django.core.paginator import Paginator
    paginator  = Paginator(logs, 50)
    page_num   = request.GET.get('page', 1)
    page_obj   = paginator.get_page(page_num)

    users          = DjangoUser.objects.filter(activity_logs__isnull=False).distinct().order_by('first_name', 'last_name')
    event_choices  = ActivityLog.EVENT_CHOICES

    # Include any event_types stored in the DB that aren't in EVENT_CHOICES
    defined_keys = {k for k, _ in event_choices}
    extra_types = (
        ActivityLog.objects.exclude(event_type__in=defined_keys)
        .values_list('event_type', flat=True)
        .distinct()
        .order_by('event_type')
    )
    all_choices = list(event_choices) + [(t, t.replace('_', ' ').title()) for t in extra_types]

    # Recent error logs — only unresolved
    error_logs = (
        ActivityLog.objects
        .filter(event_type='error', resolved=False)
        .select_related('user', 'order')
        .order_by('-timestamp')[:50]
    )

    return render(request, 'stock_take/admin_activity_log.html', {
        'page_obj':      page_obj,
        'users':         users,
        'event_choices': all_choices,
        'filter_event':  event_type,
        'filter_user':   user_id,
        'filter_q':      search,
        'total_count':   logs.count(),
        'error_logs':    error_logs,
    })


@login_required
def resolve_error_log(request, pk):
    """Mark an error log entry as resolved."""
    from django.http import JsonResponse
    from django.utils import timezone as tz

    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required'}, status=405)

    entry = get_object_or_404(ActivityLog, pk=pk, event_type='error')
    entry.resolved = True
    entry.resolved_at = tz.now()
    entry.resolved_by = request.user
    entry.save(update_fields=['resolved', 'resolved_at', 'resolved_by'])
    return JsonResponse({'success': True})


@login_required
def error_log_history(request):
    """Return resolved error logs as JSON for the history modal."""
    from django.http import JsonResponse

    entries = (
        ActivityLog.objects
        .filter(event_type='error', resolved=True)
        .select_related('user', 'resolved_by')
        .order_by('-timestamp')[:200]
    )

    data = []
    for e in entries:
        data.append({
            'id': e.pk,
            'timestamp': e.timestamp.strftime('%d %b %Y %H:%M'),
            'user': e.user.get_full_name() or e.user.username if e.user else 'System',
            'status_code': e.extra_data.get('status_code', ''),
            'description': e.description,
            'path': e.extra_data.get('path', ''),
            'exception_type': e.extra_data.get('exception_type', ''),
            'exception_value': e.extra_data.get('exception_value', ''),
            'exception_location': e.extra_data.get('exception_location', ''),
            'resolved_at': e.resolved_at.strftime('%d %b %Y %H:%M') if e.resolved_at else '',
            'resolved_by': (
                e.resolved_by.get_full_name() or e.resolved_by.username
            ) if e.resolved_by else '',
        })

    return JsonResponse({'entries': data})