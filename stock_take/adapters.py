from allauth.account.adapter import DefaultAccountAdapter
from django.forms import ValidationError


class SliderobesAccountAdapter(DefaultAccountAdapter):
    """Custom allauth adapter that restricts signup to approved email domains."""

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
