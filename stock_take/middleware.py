"""
Role-based access control middleware, user impersonation, and activity logging.

Automatically checks page permissions based on the resolved URL name.
This is applied globally so no individual view decorators are needed.
Admin users and superusers bypass all checks.
"""

import logging

from django.contrib.auth.models import User
from django.http import JsonResponse
from django.shortcuts import render, redirect
from django.urls import resolve, Resolver404
from .permissions import URL_TO_PAGE
from .models import PAGE_CHOICES


# ─── Impersonation Middleware ─────────────────────────────────────
class ImpersonationMiddleware:
    """
    If the session contains ``_impersonate_user_id``, swap
    ``request.user`` to that user so every downstream view, context
    processor and permission check sees the impersonated identity.

    The *real* admin user is stored as ``request.real_user`` so we can
    always get back to it (e.g. to show the impersonation banner or to
    stop impersonation).
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.real_user = request.user            # always available
        request.is_impersonating = False

        impersonated_id = request.session.get('_impersonate_user_id')
        if impersonated_id and request.user.is_authenticated:
            try:
                impersonated_user = User.objects.get(pk=impersonated_id)
                request.real_user = request.user    # the original admin
                request.user = impersonated_user    # swap to target
                request.is_impersonating = True
            except User.DoesNotExist:
                # Stale session key – clear it
                del request.session['_impersonate_user_id']

        return self.get_response(request)


# URLs that should always be accessible (auth, admin panel, etc.)
EXEMPT_URL_NAMES = {
    'account_login',
    'account_logout',
    'account_signup',
    'password_reset',
    'password_reset_done',
    'password_reset_confirm',
    'password_reset_complete',
    'toggle_dark_mode',
    'global_search',
    'search_customers',
    'search_orders_api',
    'search_remedials_api',
    'search_stock_items',
}

# URL prefixes that should bypass permission checks
EXEMPT_URL_PREFIXES = (
    '/admin/',
    '/accounts/',
    '/admin-panel/',  # Admin panel pages have their own staff_required check
    '/__debug__/',
)


class RolePermissionMiddleware:
    """
    Middleware that enforces role-based page permissions.

    For each request:
    1. Skip if user is not authenticated (handled by login_required)
    2. Skip if URL is exempt (auth pages, admin, etc.)
    3. Resolve the URL name and look up the page_codename
    4. Check if the user's role has view permission for that page
    5. For write operations (POST/PUT/DELETE), check the appropriate CRUD permission
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Only check authenticated users
        if not request.user.is_authenticated:
            return self.get_response(request)

        # Superusers bypass everything
        if request.user.is_superuser:
            return self.get_response(request)

        # Check exempt prefixes
        path = request.path
        for prefix in EXEMPT_URL_PREFIXES:
            if path.startswith(prefix):
                return self.get_response(request)

        # Resolve the URL name
        try:
            match = resolve(path)
            url_name = match.url_name
        except Resolver404:
            return self.get_response(request)

        # Skip exempt URLs
        if url_name in EXEMPT_URL_NAMES:
            return self.get_response(request)

        # Look up the page codename for this URL
        page_codename = URL_TO_PAGE.get(url_name)

        # If this URL isn't mapped, allow access (it's an unmapped utility endpoint)
        if page_codename is None:
            return self.get_response(request)

        # Get user profile
        profile = getattr(request.user, 'profile', None)

        # No profile or no role = no access (except dashboard)
        if not profile or not profile.role:
            # Users without a role can only see dashboard
            if page_codename == 'dashboard':
                return self.get_response(request)
            return self._deny_access(request, page_codename, 'view')

        # Admin role passes all checks
        if profile.role.name == 'admin':
            return self.get_response(request)

        # Always let dashboard through — the view handles role-based redirects
        if page_codename == 'dashboard':
            return self.get_response(request)

        # Determine required action based on HTTP method
        method = request.method
        if method == 'GET':
            action = 'view'
        elif method == 'POST':
            # POST could be create or edit; check both - allow if either is permitted
            if profile.has_page_permission(page_codename, 'create') or \
               profile.has_page_permission(page_codename, 'edit'):
                return self.get_response(request)
            action = 'create'  # For the error message
        elif method in ('PUT', 'PATCH'):
            action = 'edit'
        elif method == 'DELETE':
            action = 'delete'
        else:
            action = 'view'

        # Check permission
        if profile.has_page_permission(page_codename, action):
            return self.get_response(request)

        return self._deny_access(request, page_codename, action)

    def _deny_access(self, request, page_codename, action):
        """Return a 403 response."""
        page_name = dict(PAGE_CHOICES).get(page_codename, page_codename)

        # AJAX requests get JSON
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or \
           request.content_type == 'application/json':
            return JsonResponse(
                {'error': f'You do not have {action} permission for {page_name}.'},
                status=403
            )

        return render(request, 'stock_take/403.html', {
            'page_name': page_name,
            'action': action,
        }, status=403)


# ─── Activity Logging Middleware ──────────────────────────────────

logger = logging.getLogger(__name__)

# URL name prefixes/patterns to skip logging (read-only AJAX, polling, static)
_SKIP_URL_NAMES = {
    'toggle_dark_mode',
    'global_search',
    'search_customers',
    'search_orders_api',
    'search_remedials_api',
    'search_stock_items',
    'get_week_fit_appointments',
    'get_week_fitter_timesheets',
}

# URL path prefixes to skip
_SKIP_PREFIXES = (
    '/admin/',
    '/accounts/',
    '/__debug__/',
    '/static/',
    '/media/',
)


class ActivityLoggingMiddleware:
    """
    Automatically log every data-mutating request (POST, PUT, PATCH, DELETE)
    to the ActivityLog table.

    Skips requests where a view already called ``log_activity()`` (detected via
    the ``request._activity_logged`` flag), and skips read-only / utility
    endpoints listed in _SKIP_URL_NAMES / _SKIP_PREFIXES.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        # Only log authenticated users
        if not getattr(request, 'user', None) or not request.user.is_authenticated:
            return response

        # ── Log error responses (4xx / 5xx) ──
        if response.status_code >= 400:
            # Only log mutating methods or server errors
            if request.method in ('POST', 'PUT', 'PATCH', 'DELETE') or response.status_code >= 500:
                error_message = ''
                exception_type = ''
                exception_value = ''
                exception_location = ''
                try:
                    content_type = response.get('Content-Type', '')
                    if 'application/json' in content_type:
                        import json
                        body = json.loads(response.content.decode('utf-8', errors='replace'))
                        error_message = body.get('error', '') or body.get('message', '') or body.get('detail', '')
                    if not error_message:
                        # Try to get Django messages framework errors
                        from django.contrib.messages import get_messages
                        msgs = [str(m) for m in get_messages(request) if m.level >= 40]  # ERROR level
                        error_message = '; '.join(msgs)
                    # For 500 errors in DEBUG, extract exception details from the HTML response
                    if response.status_code >= 500 and 'text/html' in content_type:
                        try:
                            html = response.content.decode('utf-8', errors='replace')
                            import re
                            # Extract exception type
                            m = re.search(r'Exception Type:\s*</td>\s*<td[^>]*>\s*([^<]+)', html)
                            if m:
                                exception_type = m.group(1).strip()
                            # Extract exception value
                            m = re.search(r'Exception Value:\s*</td>\s*<td[^>]*>\s*([^<]+)', html)
                            if m:
                                exception_value = m.group(1).strip()
                            # Extract exception location
                            m = re.search(r'Exception Location:\s*</td>\s*<td[^>]*>\s*([^<]+)', html)
                            if m:
                                exception_location = m.group(1).strip()
                            # Use exception info as error_message if we didn't get one
                            if not error_message and exception_type:
                                error_message = f"{exception_type}: {exception_value}"
                        except Exception:
                            pass
                except Exception:
                    pass

                path = request.path
                try:
                    match = resolve(path)
                    func_name = getattr(match.func, '__name__', match.url_name or path)
                except Exception:
                    func_name = path

                description = f"{response.status_code} error on {func_name.replace('_', ' ').title()}"
                if error_message:
                    description += f": {error_message}"

                try:
                    from .models import ActivityLog
                    ActivityLog.objects.create(
                        user=request.user,
                        event_type='error',
                        description=description,
                        extra_data={
                            'path': path,
                            'method': request.method,
                            'status_code': response.status_code,
                            'error_message': error_message,
                            'exception_type': exception_type,
                            'exception_value': exception_value,
                            'exception_location': exception_location,
                        },
                    )
                except Exception:
                    logger.exception('ActivityLoggingMiddleware failed to write error log')

            return response

        # Only log mutating methods for non-error responses
        if request.method not in ('POST', 'PUT', 'PATCH', 'DELETE'):
            return response

        # Skip if a view already logged a specific event
        if getattr(request, '_activity_logged', False):
            return response

        # Skip exempt paths
        path = request.path
        for prefix in _SKIP_PREFIXES:
            if path.startswith(prefix):
                return response

        # Resolve URL name
        try:
            match = resolve(path)
            url_name = match.url_name or ''
            view_func = match.func
        except Resolver404:
            return response

        # Skip exempt URL names
        if url_name in _SKIP_URL_NAMES:
            return response

        # Build a human-readable description from the view function name
        func_name = getattr(view_func, '__name__', url_name)
        description = func_name.replace('_', ' ').title()

        # Include the path for extra context
        extra_data = {'path': path, 'method': request.method}

        try:
            from .models import ActivityLog
            ActivityLog.objects.create(
                user=request.user,
                event_type='page_action',
                description=description,
                extra_data=extra_data,
            )
        except Exception:
            logger.exception('ActivityLoggingMiddleware failed to write log')

        return response
