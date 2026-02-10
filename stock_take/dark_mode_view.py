from django.contrib.auth.decorators import login_required
from django.http import JsonResponse


@login_required
def toggle_dark_mode(request):
    """Toggle dark mode for the current user"""
    if request.method == 'POST':
        # Get or create the user profile
        profile, created = request.user.profile.__class__.objects.get_or_create(user=request.user)
        # Toggle dark mode
        profile.dark_mode = not profile.dark_mode
        profile.save()
        
        return JsonResponse({
            'success': True,
            'dark_mode': profile.dark_mode
        })
    
    return JsonResponse({'success': False, 'error': 'POST required'}, status=400)
