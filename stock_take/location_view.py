from django.contrib.auth.decorators import login_required
from django.http import JsonResponse


@login_required
def set_location(request):
    """Set the user's selected location, stored on their profile."""
    if request.method == 'POST':
        import json
        try:
            data = json.loads(request.body)
            location = data.get('location', '').strip()
        except (json.JSONDecodeError, AttributeError):
            location = request.POST.get('location', '').strip()

        profile = request.user.profile
        profile.selected_location = location
        profile.save(update_fields=['selected_location'])

        return JsonResponse({'success': True, 'location': location})

    return JsonResponse({'success': False, 'error': 'POST required'}, status=400)
