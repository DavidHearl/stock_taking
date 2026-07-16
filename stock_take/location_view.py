import json
import logging

from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.http import JsonResponse
from django.views.decorators.http import require_POST

logger = logging.getLogger(__name__)


def _available_locations():
    """The known location names (same source/cache as the top-nav picker)."""
    locations = cache.get('nav_available_locations')
    if locations is None:
        from .models import Customer
        locations = list(
            Customer.objects.exclude(location__isnull=True).exclude(location='')
            .values_list('location', flat=True).distinct().order_by('location')
        )
        cache.set('nav_available_locations', locations, 300)
    return locations


@login_required
@require_POST
def set_location(request):
    """Set the user's selected location filter (one or more), stored on their profile.

    Accepts JSON ``{"locations": ["Belfast", "Dublin"]}`` (preferred) or the legacy
    single ``{"location": "Belfast"}`` form. An empty list means "all locations".
    Unknown location names are ignored so only real branches are stored.
    """
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, AttributeError, TypeError):
        data = None

    if isinstance(data, dict) and 'locations' in data:
        raw = data.get('locations') or []
        if not isinstance(raw, list):
            raw = [raw]
    elif isinstance(data, dict):
        raw = [data.get('location', '')]
    else:
        raw = request.POST.getlist('locations') or [request.POST.get('location', '')]

    # Only keep known locations, de-duplicated, in the canonical available order.
    requested = {str(loc).strip().lower() for loc in raw if str(loc).strip()}
    available = _available_locations()
    selected = [loc for loc in available if loc.strip().lower() in requested]

    profile = request.user.profile
    profile.selected_location = ','.join(selected)
    profile.save(update_fields=['selected_location'])

    return JsonResponse({'success': True, 'locations': selected})
