"""
Context processors for role-based access control.
Adds user permissions and navigation visibility to every template context.
"""

from .models import PAGE_SECTIONS, PAGE_CHOICES
from .permissions import get_user_permissions


def user_permissions(request):
    """
    Makes user permissions available in all templates.

    Template usage:
        {% if perms.orders.can_view %}...{% endif %}
        {% if perms.orders.can_edit %}...{% endif %}

    Also provides:
        - user_role: the role name string (or None)
        - is_role_admin: True if user has admin role
        - nav_sections: navigation sections filtered by permissions
    """
    if not request.user.is_authenticated:
        return {
            'role_perms': {},
            'user_role': None,
            'is_role_admin': False,
            'nav_sections': [],
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

    return {
        'role_perms': perms,
        'user_role': role.name if role else None,
        'user_role_display': role.get_name_display() if role else 'No Role',
        'is_role_admin': is_admin,
        'nav_sections': nav_sections,
    }
