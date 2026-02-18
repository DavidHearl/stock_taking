"""
Context processors for role-based access control.
Adds user permissions and navigation visibility to every template context.
"""

from .models import PAGE_SECTIONS, PAGE_CHOICES, Ticket
from .permissions import get_user_permissions


def user_permissions(request):
    """
    Makes user permissions available in all templates.
    Also provides current location and available locations for the top navbar.
    """
    if not request.user.is_authenticated:
        return {
            'role_perms': {},
            'user_role': None,
            'is_role_admin': False,
            'nav_sections': [],
            'current_location': '',
            'available_locations': [],
        }

    perms = get_user_permissions(request.user)
    profile = getattr(request.user, 'profile', None)
    role = profile.role if profile else None
    is_admin = request.user.is_superuser or (role and role.name == 'admin')

    # Build filtered nav sections for the template
    nav_sections = []
    for section_name, pages in PAGE_SECTIONS:
        visible_pages = []
        for codename, label in pages:
            page_perms = perms.get(codename, {})
            if page_perms.get('can_view', False):
                visible_pages.append({
                    'codename': codename,
                    'label': label,
                    'can_create': page_perms.get('can_create', False),
                    'can_edit': page_perms.get('can_edit', False),
                    'can_delete': page_perms.get('can_delete', False),
                })
        if visible_pages:
            nav_sections.append({
                'name': section_name,
                'pages': visible_pages,
            })

    # Location: current selection from profile, available from DB
    current_location = profile.selected_location if profile else ''
    try:
        from .models import Customer
        available_locations = list(
            Customer.objects.exclude(location__isnull=True).exclude(location='')
            .values_list('location', flat=True).distinct().order_by('location')
        )
    except Exception:
        available_locations = []

    return {
        'role_perms': perms,
        'user_role': role.name if role else None,
        'user_role_display': role.get_name_display() if role else 'No Role',
        'is_role_admin': is_admin,
        'nav_sections': nav_sections,
        'current_location': current_location,
        'available_locations': available_locations,
        # Ticket counts for nav badges
        'open_ticket_count': Ticket.objects.filter(status__in=['open', 'in_progress']).count(),
        'unread_ticket_count': Ticket.objects.filter(read_by_admin=False).exclude(status='closed').count() if is_admin else 0,
        # Impersonation context
        'is_impersonating': getattr(request, 'is_impersonating', False),
        'real_user': getattr(request, 'real_user', request.user),
    }
