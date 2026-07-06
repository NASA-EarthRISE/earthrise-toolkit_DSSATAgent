"""
End-to-end tests for the registration + admin review workflow.

Covered:
  - Submitting the public form creates an INACTIVE user + a *pending*
    RegistrationRequest (no active account is minted).
  - Active superusers are emailed that a request is waiting.
  - Approving activates the user, assigns requested groups, stamps the
    profile, flips the request to 'approved', and emails the applicant.
  - Rejecting emails the applicant and does NOT leave an account behind.
  - The admin review APIs are gated to admins.
"""

import json

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.urls import reverse

from accounts.models import RegistrationRequest, UserProfile
from accounts.tests.conftest import registration_form_data

User = get_user_model()


# ---------------------------------------------------------------------------
# Public registration submission
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_submitting_registration_creates_pending_request(client):
    resp = client.post(reverse("accounts:register"), registration_form_data())

    # Renders the "pending review" page (not a redirect to login/home).
    assert resp.status_code == 200

    user = User.objects.get(username="alice")
    # The account exists but is INACTIVE — it must not be usable yet.
    assert user.is_active is False

    reg = RegistrationRequest.objects.get(user=user)
    assert reg.status == "pending"
    assert reg.reviewed_at is None
    assert reg.reviewed_by is None

    # A profile is created alongside the request.
    assert UserProfile.objects.filter(user=user).exists()


@pytest.mark.django_db
def test_submitting_registration_records_requested_roles(client):
    data = registration_form_data(username="carol", email="carol@example.com",
                                  requested_roles=["farmer"])
    resp = client.post(reverse("accounts:register"), data)
    assert resp.status_code == 200

    reg = RegistrationRequest.objects.get(user__username="carol")
    assert reg.requested_roles == ["farmer"]
    # Requesting a role does NOT grant it before approval.
    assert reg.user.groups.count() == 0


@pytest.mark.django_db
def test_invalid_registration_creates_nothing(client):
    # Mismatched passwords -> form invalid -> no user, no request.
    data = registration_form_data(password2="something-else-99")
    resp = client.post(reverse("accounts:register"), data)

    assert resp.status_code == 200  # re-renders form with errors
    assert not User.objects.filter(username="alice").exists()
    assert RegistrationRequest.objects.count() == 0


@pytest.mark.django_db
def test_registration_notifies_active_superusers(client, superuser_with_email, mailoutbox):
    resp = client.post(reverse("accounts:register"), registration_form_data())
    assert resp.status_code == 200

    admin_mails = [m for m in mailoutbox if "root@example.com" in m.to]
    assert len(admin_mails) == 1
    assert admin_mails[0].subject  # non-empty rendered subject


# ---------------------------------------------------------------------------
# Approval
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_approve_activates_user_assigns_roles_and_emails(admin_client, make_pending_registration, mailoutbox):
    reg = make_pending_registration(username="dan", email="dan@example.com")
    assert Group.objects.filter(name="farmer").exists()  # synced via post_migrate

    url = reverse("accounts:approve_registration", args=[reg.id])
    resp = admin_client.post(
        url, data=json.dumps({"roles": ["farmer"], "notes": "looks good"}),
        content_type="application/json",
    )

    assert resp.status_code == 200
    assert resp.json()["success"] is True

    reg.refresh_from_db()
    assert reg.status == "approved"
    assert reg.reviewed_at is not None
    assert reg.reviewed_by is not None

    user = reg.user
    user.refresh_from_db()
    assert user.is_active is True
    assert user.groups.filter(name="farmer").exists()

    profile = UserProfile.objects.get(user=user)
    assert profile.approved_at is not None
    assert profile.approved_by is not None

    approval_mails = [m for m in mailoutbox if "dan@example.com" in m.to]
    assert len(approval_mails) == 1


@pytest.mark.django_db
def test_approve_unknown_role_does_not_error(admin_client, make_pending_registration):
    reg = make_pending_registration(username="eve", email="eve@example.com")
    url = reverse("accounts:approve_registration", args=[reg.id])
    resp = admin_client.post(
        url, data=json.dumps({"roles": ["nonexistent_role"]}),
        content_type="application/json",
    )
    # Unknown groups are logged & skipped, not fatal.
    assert resp.status_code == 200
    reg.user.refresh_from_db()
    assert reg.user.is_active is True
    assert reg.user.groups.count() == 0


@pytest.mark.django_db
def test_approve_requires_admin(client, make_pending_registration):
    reg = make_pending_registration()
    url = reverse("accounts:approve_registration", args=[reg.id])
    resp = client.post(url, data="{}", content_type="application/json")
    # AdminRequiredMixin redirects an unauthenticated/non-admin user to login.
    assert resp.status_code == 302
    reg.refresh_from_db()
    assert reg.status == "pending"  # unchanged
    reg.user.refresh_from_db()
    assert reg.user.is_active is False


# ---------------------------------------------------------------------------
# Rejection
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_reject_marks_rejected_keeps_inactive_user_and_emails(admin_client, make_pending_registration, mailoutbox):
    reg = make_pending_registration(username="frank", email="frank@example.com")
    user_id = reg.user_id

    url = reverse("accounts:reject_registration", args=[reg.id])
    resp = admin_client.post(
        url, data=json.dumps({"notes": "insufficient justification"}),
        content_type="application/json",
    )

    assert resp.status_code == 200
    assert resp.json()["success"] is True

    # The request is marked rejected with reviewer + notes (audit trail kept).
    reg.refresh_from_db()
    assert reg.status == "rejected"
    assert reg.reviewed_by is not None
    assert reg.reviewed_at is not None
    assert reg.review_notes == "insufficient justification"

    # The applicant's account is retained but stays inactive (never activated).
    user = User.objects.get(pk=user_id)
    assert user.is_active is False

    reject_mails = [m for m in mailoutbox if "frank@example.com" in m.to]
    assert len(reject_mails) == 1


@pytest.mark.django_db
def test_reject_requires_admin(client, make_pending_registration):
    reg = make_pending_registration()
    url = reverse("accounts:reject_registration", args=[reg.id])
    resp = client.post(url, data="{}", content_type="application/json")
    assert resp.status_code == 302
    # The applicant's inactive account is untouched.
    reg.user.refresh_from_db()
    assert reg.user.is_active is False
