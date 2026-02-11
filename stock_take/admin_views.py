from django.shortcuts import render
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.models import User


def staff_required(view_func):
    """Decorator that requires the user to be staff."""
    decorated = user_passes_test(lambda u: u.is_staff)(view_func)
    return login_required(decorated)


@staff_required
def admin_users(request):
    """Admin users management page."""
    users = User.objects.all().order_by('username')
    context = {
        'users': users,
    }
    return render(request, 'stock_take/admin_users.html', context)


@staff_required
def admin_templates(request):
    """Admin templates management page."""
    context = {}
    return render(request, 'stock_take/admin_templates.html', context)


@staff_required
def admin_roles(request):
    """Admin roles management page."""
    context = {}
    return render(request, 'stock_take/admin_roles.html', context)


@staff_required
def admin_settings(request):
    """Admin settings page."""
    context = {}
    return render(request, 'stock_take/admin_settings.html', context)
