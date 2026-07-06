"""
Tests for the accounts models: UserProfile and RegistrationRequest.

Notably documents that a profile is NOT auto-created by a signal — it is
created explicitly by the registration/approval code paths. The views use
``UserProfile.objects.get_or_create`` precisely because a bare User has no
profile.
"""

import uuid

import pytest
from django.contrib.auth import get_user_model

from accounts.models import RegistrationRequest, UserProfile

User = get_user_model()


@pytest.mark.django_db
def test_creating_user_does_not_auto_create_profile():
    user = User.objects.create_user(username="ned", password="pw-Zephyr!42")
    # No post_save signal wires a profile — it must be created explicitly.
    assert not UserProfile.objects.filter(user=user).exists()
    assert not hasattr(user, "profile") or _no_related_profile(user)


def _no_related_profile(user):
    try:
        _ = user.profile
        return False
    except UserProfile.DoesNotExist:
        return True


@pytest.mark.django_db
def test_profile_one_to_one_link():
    user = User.objects.create_user(username="olive", first_name="Olive",
                                    last_name="Ortiz", password="pw-Zephyr!42")
    profile = UserProfile.objects.create(user=user, organization="Field Co")

    user.refresh_from_db()
    assert user.profile == profile
    assert profile.user == user
    assert profile.organization == "Field Co"


@pytest.mark.django_db
def test_profile_str_uses_full_name():
    user = User.objects.create_user(username="pam", first_name="Pam",
                                    last_name="Poole", password="pw-Zephyr!42")
    profile = UserProfile.objects.create(user=user)
    assert "Pam Poole" in str(profile)


@pytest.mark.django_db
def test_registration_request_defaults():
    user = User.objects.create_user(username="quinn", email="quinn@example.com",
                                    password="pw-Zephyr!42", is_active=False)
    reg = RegistrationRequest.objects.create(user=user)

    assert isinstance(reg.id, uuid.UUID)      # UUID primary key
    assert isinstance(reg.token, uuid.UUID)   # email-verification token
    assert reg.status == "pending"            # default status
    assert reg.requested_roles == []          # JSON default list
    assert reg.reviewed_by is None
    assert reg.created_at is not None


@pytest.mark.django_db
def test_registration_request_str_includes_email_and_status():
    user = User.objects.create_user(username="rick", email="rick@example.com",
                                    password="pw-Zephyr!42", is_active=False)
    reg = RegistrationRequest.objects.create(user=user)
    text = str(reg)
    assert "rick@example.com" in text
    assert "pending" in text


@pytest.mark.django_db
def test_registration_request_tokens_are_unique():
    u1 = User.objects.create_user(username="sam", email="sam@example.com",
                                  password="pw-Zephyr!42", is_active=False)
    u2 = User.objects.create_user(username="tom", email="tom@example.com",
                                  password="pw-Zephyr!42", is_active=False)
    r1 = RegistrationRequest.objects.create(user=u1)
    r2 = RegistrationRequest.objects.create(user=u2)
    assert r1.token != r2.token
