from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.models import User
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.contrib import messages
from .models import Role, PagePermission, PAGE_SECTIONS, PAGE_CHOICES, SyncLog


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
                'description': (
                    'Pulls all customer records from Anthill CRM (~275k), fetches full details for each, '
                    'and creates/updates local Customer records. '
                    'Smart-skip: customers already in the database are skipped entirely — '
                    'no API call, no DB write. Only genuinely new customers trigger detail queries, '
                    'making repeat runs fast. Use --force to re-fetch every customer.'
                ),
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
                'description': (
                    'Syncs customers from Anthill CRM created within the last 7 days. '
                    'Customers with a WorkGuruClientID are saved as Customer; '
                    'all others are saved as Contact (Lead). '
                    'Runs automatically via the scheduler Docker service.'
                ),
                'file': 'stock_take/management/commands/sync_recent_customers.py',
                'schedule': 'Automated — daily at 08:00 & 12:00 (Docker scheduler)',
                'commands': [
                    {'cmd': 'python manage.py sync_recent_customers', 'note': 'Sync last 7 days (default)'},
                    {'cmd': 'python manage.py sync_recent_customers --days 14', 'note': 'Sync last 14 days'},
                    {'cmd': 'python manage.py sync_recent_customers --dry-run', 'note': 'Preview without saving'},
                ],
            },
            {
                'log_name': 'sync_anthill_customers',
                'label': 'Standalone: Two-Phase Customer & Sales Import',
                'description': (
                    'Legacy standalone script (project root). Phase 1 syncs sale activities for '
                    'customers already in the database. Phase 2 imports any new customers/leads. '
                    'Writes a SyncLog entry on completion.'
                ),
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
                'description': (
                    'Refreshes the status, category, and activity_type fields on existing AnthillSale '
                    'records by fetching the latest activity data from Anthill CRM. '
                    'Groups requests by customer to minimise API calls. '
                    'Writes a SyncLog entry on completion.'
                ),
                'file': 'sync_anthill_workflow.py',
                'schedule': 'Manual',
                'commands': [
                    {'cmd': 'python sync_anthill_workflow.py', 'note': 'Refresh all existing sale records'},
                    {'cmd': 'python sync_anthill_workflow.py --dry-run', 'note': 'Report changes without writing to DB'},
                    {'cmd': 'python sync_anthill_workflow.py --days 180', 'note': 'Only refresh sales active within last 180 days'},
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
                'description': (
                    'Fetches all contacts from Xero and matches them to local Customer records by name. '
                    'Stores the Xero Contact ID on each matched customer. '
                    'Prerequisite: connect to Xero via /xero/status/ first.'
                ),
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
                'description': (
                    'For every customer with a Xero Contact ID, fetches their invoices and '
                    'creates/updates local Invoice records with payment status. '
                    'Prerequisite: connect to Xero via /xero/status/ and run sync_xero_customers first.'
                ),
                'file': 'stock_take/management/commands/sync_xero_invoices.py',
                'schedule': 'Manual',
                'commands': [
                    {'cmd': 'python manage.py sync_xero_invoices', 'note': 'Sync invoices for all customers with a Xero ID'},
                    {'cmd': 'python manage.py sync_xero_invoices --dry-run', 'note': 'Preview without saving'},
                    {'cmd': 'python manage.py sync_xero_invoices --customer 123', 'note': 'Single customer (by database PK)'},
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
                'description': (
                    'Reports current Google Maps API usage against the free-tier limits '
                    '(10,000 loads/month for Dynamic Maps; 10,000 requests/month for Geocoding). '
                    'Alerts when usage exceeds the configured threshold.'
                ),
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
                'description': (
                    'Removes duplicate auto-generated stock take schedules that can accumulate '
                    'over time. Safe to run at any time.'
                ),
                'file': 'stock_take/management/commands/cleanup_duplicate_schedules.py',
                'schedule': 'Manual / as needed',
                'commands': [
                    {'cmd': 'python manage.py cleanup_duplicate_schedules', 'note': 'Remove duplicates'},
                    {'cmd': 'python manage.py cleanup_duplicate_schedules --dry-run', 'note': 'Preview without deleting'},
                ],
            },
            {
                'log_name': 'set_legacy_completion_dates',
                'label': 'Set Legacy Completion Dates',
                'description': (
                    'Backfills completion dates on older completed stock take schedule records '
                    'that pre-date the completion date field. One-off migration helper.'
                ),
                'file': 'stock_take/management/commands/set_legacy_completion_dates.py',
                'schedule': 'One-off',
                'commands': [
                    {'cmd': 'python manage.py set_legacy_completion_dates', 'note': 'Set completion dates'},
                    {'cmd': 'python manage.py set_legacy_completion_dates --days-ago 35 --dry-run', 'note': 'Preview with custom offset'},
                ],
            },
        ],
    },
]


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
            enriched_scripts.append({
                **entry,
                'last_log': last_log,
                'recent_logs': recent_logs,
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
