from allauth.account.adapter import DefaultAccountAdapter
from django.forms import ValidationError


class SliderobesAccountAdapter(DefaultAccountAdapter):
    """Custom allauth adapter that restricts signup to approved email domains
    and auto-generates usernames as firstname.lastname from the email."""

    ALLOWED_DOMAINS = ['sliderobes.com', 'sliderobes.co.uk']

    def clean_email(self, email):
        email = super().clean_email(email)
        domain = email.rsplit('@', 1)[-1].lower()
        if domain not in self.ALLOWED_DOMAINS:
            raise ValidationError(
                'Unable to register with this email address. '
                'Please contact your administrator for access.'
            )
        return email

    def generate_unique_username(self, txts, regex=None):
        """Generate username as firstname.lastname from the email address."""
        # txts is a list; the email is typically in there
        from allauth.account.utils import filter_users_by_username

        email = None
        for t in txts:
            if t and '@' in str(t):
                email = str(t).strip().lower()
                break

        if email:
            local_part = email.split('@')[0]  # e.g. 'david.hearl'
            # Normalise: replace common separators with dots, strip non-alphanum/dot
            base = local_part.replace('_', '.').replace('-', '.')
            base = ''.join(c for c in base if c.isalnum() or c == '.')
            base = base.strip('.')
        else:
            base = 'user'

        # Ensure uniqueness
        username = base
        i = 1
        while filter_users_by_username(username).exists():
            username = f"{base}.{i}"
            i += 1
        return username

    def save_user(self, request, user, form, commit=True):
        """Set first_name and last_name from the generated username."""
        user = super().save_user(request, user, form, commit=False)
        # Derive first/last name from username (firstname.lastname)
        parts = user.username.split('.')
        if len(parts) >= 2:
            user.first_name = parts[0].capitalize()
            user.last_name = '.'.join(parts[1:]).capitalize()
        else:
            user.first_name = parts[0].capitalize()
        if commit:
            user.save()
        return user
