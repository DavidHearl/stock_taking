from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.models import User
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.contrib import messages
from .models import Role, PagePermission, PAGE_SECTIONS, PAGE_CHOICES


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
