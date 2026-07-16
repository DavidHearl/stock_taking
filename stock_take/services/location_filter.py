"""Helpers for the site-wide multi-location filter.

The user's location selection lives on ``UserProfile.selected_location`` as a
comma-separated list of one or more location names (blank = all locations).
These helpers turn that selection into query filters so every list/report
applies it the same "match ANY of the selected locations" way.
"""

from django.db.models import Q


def profile_locations(profile):
    """Return the list of locations selected on a profile (empty = all)."""
    if not profile:
        return []
    return profile.selected_location_list


def request_locations(request):
    """Return the selected locations for the request's user (empty = all)."""
    profile = getattr(getattr(request, 'user', None), 'profile', None)
    return profile_locations(profile)


def location_q(locations, *fields, lookup='iexact'):
    """Build a Q matching any of ``locations`` against any of ``fields``.

    Returns ``None`` when no locations are selected so callers can skip
    filtering entirely (which shows all locations).

    Example::

        q = location_q(locs, 'sale__location', 'location', lookup='icontains')
        if q:
            qs = qs.filter(q)
    """
    if not locations or not fields:
        return None
    combined = Q()
    for loc in locations:
        for field in fields:
            combined |= Q(**{f'{field}__{lookup}': loc})
    return combined
