"""
Shared fixtures for the accounts app test suite.

Every DB-touching test relies on pytest-django's transactional test DB,
which runs the ``post_migrate`` signal on setup — so the global groups
(``administrator``/``developer``) and every agent role from the various
``roles.yaml`` files already exist before a test body runs. The helpers
here only build the per-test fixtures (pending registrations, users).

No network / Ollama / external services are touched: the registration
flow is pure Django (ORM + template render + locmem email backend that
pytest-django installs via ``setup_test_environment``).
"""

import uuid

import pytest
from django.contrib.auth import get_user_model

from accounts.models import RegistrationRequest, UserProfile

User = get_user_model()


# A password that survives Django's default validators
# (min length, not-all-numeric, not too common, not user-similar).
VALID_PASSWORD = "Zephyr!Marmot42"


def registration_form_data(**overrides):
    """Return a valid POST payload for the public registration form."""
    data = {
        "username": "alice",
        "email": "alice@example.com",
        "first_name": "Alice",
        "last_name": "Applegate",
        "password1": VALID_PASSWORD,
        "password2": VALID_PASSWORD,
        "organization": "ACME Research",
        "reason": "I need access to run simulations.",
    }
    data.update(overrides)
    return data


@pytest.fixture
def make_pending_registration(db):
    """Factory that mirrors what ``RegisterView.post`` persists: an
    inactive User + UserProfile + a pending RegistrationRequest.

    Used by the approve/reject tests so they can exercise the admin
    review APIs without going through the public form first.
    """

    def _make(username="bob", email="bob@example.com", roles=None):
        user = User.objects.create_user(
            username=username,
            email=email,
            password="unused-inactive-pw-123",
            first_name="Bob",
            last_name="Baxter",
            is_active=False,
        )
        UserProfile.objects.create(user=user, organization="Org")
        reg = RegistrationRequest.objects.create(
            user=user,
            organization="Org",
            reason="Please let me in",
            requested_roles=roles or [],
        )
        return reg

    return _make


@pytest.fixture
def superuser_with_email(db):
    """An active superuser that owns an email address, so the
    'notify admins of a new registration' path has a recipient.
    """
    return User.objects.create_superuser(
        username="root",
        email="root@example.com",
        password="rootpw-Zephyr!42",
    )
