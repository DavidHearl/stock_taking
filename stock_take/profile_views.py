import json
from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from django.contrib.auth import update_session_auth_hash


@login_required
def user_profile(request):
    """Display the user profile page."""
    profile = getattr(request.user, 'profile', None)
    context = {
        'profile_user': request.user,
        'profile': profile,
    }
    return render(request, 'stock_take/profile.html', context)


@login_required
@require_http_methods(["POST"])
def user_profile_save(request):
    """Save user profile changes via AJAX."""
    try:
        data = json.loads(request.body)
        user = request.user

        editable_fields = ['first_name', 'last_name', 'email']
        for field in editable_fields:
            if field in data:
                setattr(user, field, data[field].strip())

        user.save()

        return JsonResponse({'success': True})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=400)


@login_required
@require_http_methods(["POST"])
def user_change_password(request):
    """Change the user's password via AJAX."""
    try:
        data = json.loads(request.body)
        current_password = data.get('current_password', '')
        new_password = data.get('new_password', '')
        confirm_password = data.get('confirm_password', '')

        if not request.user.check_password(current_password):
            return JsonResponse({'success': False, 'error': 'Current password is incorrect.'}, status=400)

        if len(new_password) < 8:
            return JsonResponse({'success': False, 'error': 'New password must be at least 8 characters.'}, status=400)

        if new_password != confirm_password:
            return JsonResponse({'success': False, 'error': 'New passwords do not match.'}, status=400)

        request.user.set_password(new_password)
        request.user.save()
        update_session_auth_hash(request, request.user)

        return JsonResponse({'success': True})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=400)
