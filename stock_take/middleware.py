"""
Role-based access control middleware.

Automatically checks page permissions based on the resolved URL name.
This is applied globally so no individual view decorators are needed.
Admin users and superusers bypass all checks.
"""

from django.http import JsonResponse
from django.shortcuts import render, redirect
from django.urls import resolve, Resolver404
from .permissions import URL_TO_PAGE
from .models import PAGE_CHOICES


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
