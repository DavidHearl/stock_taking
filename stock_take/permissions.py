"""
Role-based access control decorators and utilities.

Usage in views:
    @page_permission_required('orders')
    def ordering(request):
        ...

    @page_permission_required('orders', action='create')
    def create_order(request):
        ...
"""

from functools import wraps
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from .models import PAGE_SECTIONS, PAGE_CHOICES


# =============================================
# Map URL names → page codenames
# =============================================
# This maps Django URL names to the page_codename used in the permission system.
# If a view isn't listed here, use the @page_permission_required decorator explicitly.

URL_TO_PAGE = {
    # Dashboard
    'dashboard': 'dashboard',

    # Projects - Orders
    'ordering': 'orders',
    'load_order_details_ajax': 'orders',
    'load_order_indicators_ajax': 'orders',
    'search_orders': 'orders',
    'order_details': 'order_details',
    'update_customer_info': 'order_details',
    'update_sale_info': 'order_details',
    'update_order_type': 'order_details',
    'update_boards_po': 'order_details',
    'update_job_checkbox': 'order_details',
    'update_order_financial': 'order_details',
    'save_all_order_financials': 'order_details',
    'recalculate_order_financials': 'order_details',
    'generate_summary_document': 'order_details',

    # Projects - Customers
    'customers_list': 'customers',
    'customer_detail': 'customer_details',
    'customer_save': 'customer_details',
    'customer_delete': 'customer_details',
    'customers_bulk_delete': 'customers',

    # Projects - Remedials
    'remedials': 'remedials',

    # Accounting
    'invoices_list': 'invoices',
    'invoice_detail': 'invoices',
    'sync_invoices': 'invoices',

    # Purchase
    'purchase_orders_list': 'purchase_orders',
    'sync_purchase_orders': 'purchase_orders',
    'purchase_order_detail': 'purchase_order_details',
    'purchase_order_save': 'purchase_order_details',
    'purchase_order_receive': 'purchase_order_details',
    'suppliers_list': 'suppliers',
    'supplier_detail': 'supplier_details',
    'boards_summary': 'boards_summary',
    'os_doors_summary': 'os_doors_summary',
    'material_shortage': 'material_shortage',
    'raumplus_storage': 'raumplus_storage',

    # Products & Stock
    'stock_items_manager': 'products',
    'update_stock_items_batch': 'products',
    'add_product': 'products',
    'product_detail': 'product_details',
    'upload_product_image': 'product_details',
    'stock_list': 'stock_list',
    'import_csv': 'stock_list',
    'export_csv': 'stock_list',
    'update_item': 'stock_list',
    'schedule_list': 'stock_take',
    'schedule_create': 'stock_take',
    'schedule_edit': 'stock_take',
    'schedule_update_status': 'stock_take',
    'delete_schedule': 'stock_take',
    'stock_take_detail': 'stock_take',
    'completed_stock_takes': 'completed_stock_takes',
    'category_list': 'categories',
    'category_create': 'categories',
    'category_edit': 'categories',
    'category_delete': 'categories',
    'substitutions': 'substitutions',
    'delete_substitution': 'substitutions',
    'edit_substitution': 'substitutions',

    # Calendar
    'fit_board': 'fit_board',
    'add_fit_appointment': 'fit_board',
    'update_fit_status': 'fit_board',
    'delete_fit_appointment': 'fit_board',
    'move_fit_appointment': 'fit_board',
    'timesheets': 'timesheets',
    'workflow': 'workflow',

    # Tools
    'map': 'map',
    'generate_materials': 'generate_materials',
    'generate_pnx': 'generate_materials',
    'generate_csv': 'generate_materials',
    'check_database': 'database_check',

    # Reports
    'material_report': 'material_report',
    'costing_report': 'costing_report',
    'remedial_report': 'remedial_report',

    # Tickets
    'tickets_list': 'tickets',
    'ticket_detail': 'tickets',
    'ticket_update_status': 'tickets',
    'ticket_edit': 'tickets',
    'ticket_delete': 'tickets',

    # Claim Service
    'claim_service': 'claim_service',
    'claim_delete': 'claim_service',
    'claim_download_zip': 'claim_service',
}


def get_user_permissions(user):
    """Get a dict of all permissions for the user, keyed by page_codename."""
    perms = {}
    if not user.is_authenticated:
        return perms

    profile = getattr(user, 'profile', None)
    is_admin = user.is_superuser or (profile and profile.role and profile.role.name == 'admin')

    for codename, label in PAGE_CHOICES:
        if is_admin:
            perms[codename] = {
                'can_view': True,
                'can_create': True,
                'can_edit': True,
                'can_delete': True,
            }
        elif profile and profile.role:
            try:
                pp = profile.role.page_permissions.get(page_codename=codename)
                perms[codename] = {
                    'can_view': pp.can_view,
                    'can_create': pp.can_create,
                    'can_edit': pp.can_edit,
                    'can_delete': pp.can_delete,
                }
            except Exception:
                perms[codename] = {
                    'can_view': False,
                    'can_create': False,
                    'can_edit': False,
                    'can_delete': False,
                }
        else:
            perms[codename] = {
                'can_view': False,
                'can_create': False,
                'can_edit': False,
                'can_delete': False,
            }
    return perms


def page_permission_required(page_codename, action='view'):
    """
    Decorator to restrict access to a view based on role page permissions.

    Usage:
        @page_permission_required('orders')          # requires view permission
        @page_permission_required('orders', 'edit')   # requires edit permission
    """
    def decorator(view_func):
        @wraps(view_func)
        @login_required
        def wrapper(request, *args, **kwargs):
            profile = getattr(request.user, 'profile', None)

            # Superusers always pass
            if request.user.is_superuser:
                return view_func(request, *args, **kwargs)

            # Check role permission
            if profile and profile.has_page_permission(page_codename, action):
                return view_func(request, *args, **kwargs)

            # If AJAX, return JSON error
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return JsonResponse(
                    {'error': 'You do not have permission to access this page.'},
                    status=403
                )

            # Render a 403 page
            return render(request, 'stock_take/403.html', {
                'page_name': dict(PAGE_CHOICES).get(page_codename, page_codename),
                'action': action,
            }, status=403)

        return wrapper
    return decorator


def crud_permission_required(page_codename):
    """
    Decorator that checks CRUD permissions based on HTTP method.
    GET → view, POST with create indicators → create, PUT/PATCH → edit, DELETE → delete
    """
    def decorator(view_func):
        @wraps(view_func)
        @login_required
        def wrapper(request, *args, **kwargs):
            if request.user.is_superuser:
                return view_func(request, *args, **kwargs)

            method = request.method
            if method == 'GET':
                action = 'view'
            elif method == 'POST':
                action = 'create'
            elif method in ('PUT', 'PATCH'):
                action = 'edit'
            elif method == 'DELETE':
                action = 'delete'
            else:
                action = 'view'

            profile = getattr(request.user, 'profile', None)
            if profile and profile.has_page_permission(page_codename, action):
                return view_func(request, *args, **kwargs)

            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return JsonResponse(
                    {'error': f'You do not have {action} permission for this page.'},
                    status=403
                )

            return render(request, 'stock_take/403.html', {
                'page_name': dict(PAGE_CHOICES).get(page_codename, page_codename),
                'action': action,
            }, status=403)

        return wrapper
    return decorator
