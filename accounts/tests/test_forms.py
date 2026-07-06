"""
Tests for the registration form.

RegistrationForm subclasses UserCreationForm and adds required identity
fields, a duplicate-email guard, and role choices sourced from role
discovery (with admin-level roles hidden from public self-selection).
"""

import pytest
from django.contrib.auth import get_user_model

from accounts.forms import RegistrationForm
from accounts.tests.conftest import VALID_PASSWORD

User = get_user_model()


def _base_data(**overrides):
    data = {
        "username": "newbie",
        "email": "newbie@example.com",
        "first_name": "New",
        "last_name": "Bee",
        "password1": VALID_PASSWORD,
        "password2": VALID_PASSWORD,
    }
    data.update(overrides)
    return data


@pytest.mark.django_db
def test_valid_form():
    form = RegistrationForm(data=_base_data())
    assert form.is_valid(), form.errors


@pytest.mark.django_db
@pytest.mark.parametrize("missing", ["email", "first_name", "last_name", "username"])
def test_required_fields_enforced(missing):
    data = _base_data()
    data.pop(missing)
    form = RegistrationForm(data=data)
    assert not form.is_valid()
    assert missing in form.errors


@pytest.mark.django_db
def test_duplicate_email_rejected():
    User.objects.create_user(username="existing", email="dupe@example.com",
                             password="pw-Zephyr!42")
    form = RegistrationForm(data=_base_data(email="dupe@example.com"))
    assert not form.is_valid()
    assert "email" in form.errors


@pytest.mark.django_db
def test_password_mismatch_rejected():
    form = RegistrationForm(data=_base_data(password2="Different!99xyz"))
    assert not form.is_valid()
    assert "password2" in form.errors


@pytest.mark.django_db
def test_admin_roles_hidden_from_public_choices():
    form = RegistrationForm()
    choice_names = {value for value, _label in form.fields["requested_roles"].choices}
    # Global admin roles and any *_admin agent role are not self-selectable.
    assert "administrator" not in choice_names
    assert "developer" not in choice_names
    assert not any(name.endswith("_admin") for name in choice_names)
    # A normal agent role IS offered.
    assert "farmer" in choice_names
