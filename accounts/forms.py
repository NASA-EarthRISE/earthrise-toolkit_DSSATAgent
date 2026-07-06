from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import UserCreationForm

from accounts.role_discovery import get_available_roles

User = get_user_model()


class RegistrationForm(UserCreationForm):
    """Registration form with profile fields and role selection."""

    email = forms.EmailField(required=True)
    first_name = forms.CharField(max_length=150, required=True)
    last_name = forms.CharField(max_length=150, required=True)
    organization = forms.CharField(max_length=200, required=False)
    reason = forms.CharField(
        widget=forms.Textarea(attrs={'rows': 3}),
        required=False,
        help_text="Why are you requesting access?",
    )
    requested_roles = forms.MultipleChoiceField(
        required=False,
        widget=forms.CheckboxSelectMultiple,
        help_text="Select the roles you need",
    )

    class Meta:
        model = User
        fields = ['username', 'email', 'first_name', 'last_name', 'password1', 'password2']

    # Roles hidden from the public registration form. Admin-level roles
    # (granted by existing admins after approval) aren't self-selectable.
    # Derived generically — the global roles plus any per-agent "*_admin"
    # role — so no specific agent names are hardcoded here.
    _HIDDEN_GLOBAL_ROLES = {'administrator', 'developer'}

    @classmethod
    def _is_hidden_role(cls, name):
        return name in cls._HIDDEN_GLOBAL_ROLES or name.endswith('_admin')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Populate role choices from discovered roles (exclude admin-only)
        roles = get_available_roles()
        self.fields['requested_roles'].choices = [
            (r['name'], f"{r['display_name']} — {r['description']}")
            for r in roles
            if not self._is_hidden_role(r['name'])
        ]

    def clean_email(self):
        email = self.cleaned_data['email']
        if User.objects.filter(email=email).exists():
            raise forms.ValidationError("An account with this email already exists.")
        return email


class ProfileForm(forms.Form):
    """Profile editing form."""

    first_name = forms.CharField(max_length=150, required=True)
    last_name = forms.CharField(max_length=150, required=True)
    email = forms.EmailField(required=True)
    organization = forms.CharField(max_length=200, required=False)
    title = forms.CharField(max_length=100, required=False, label="Job title")
    phone = forms.CharField(max_length=30, required=False)
    country = forms.CharField(max_length=100, required=False)
    bio = forms.CharField(widget=forms.Textarea(attrs={'rows': 3}), required=False)
    areas_of_interest = forms.CharField(
        widget=forms.Textarea(attrs={'rows': 2}),
        required=False,
        help_text="Topics, regions, specializations (e.g., areas or domains you work in)",
    )
    preferred_language = forms.CharField(max_length=10, required=False)
