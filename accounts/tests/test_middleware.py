"""
Tests for LoginRequiredMiddleware.

The middleware is the app's site-wide auth gate: every request that is
not to an explicitly exempted prefix must be authenticated. These tests
drive the middleware directly with RequestFactory so they need no DB and
no live views — request.user is the only thing the middleware inspects.
The exempt prefixes are read from the module (not hardcoded here) so the
assertions stay honest if the list changes.
"""

from types import SimpleNamespace

import pytest
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory

from accounts.middleware import LoginRequiredMiddleware, _EXEMPT_PREFIXES


SENTINEL = object()


def _build_middleware():
    """Middleware whose get_response returns a recognizable sentinel,
    so 'passed through' vs 'intercepted' is unambiguous."""
    return LoginRequiredMiddleware(lambda request: SENTINEL)


@pytest.fixture
def rf():
    return RequestFactory()


def test_unauthenticated_protected_path_redirects_to_login(rf):
    mw = _build_middleware()
    request = rf.get("/some/protected/page/")
    request.user = AnonymousUser()

    response = mw(request)

    assert response is not SENTINEL
    assert response.status_code == 302
    # redirect_to_login points at LOGIN_URL and preserves ?next=.
    assert "/accounts/login/" in response.url
    assert "next=" in response.url


def test_authenticated_request_passes_through(rf):
    mw = _build_middleware()
    request = rf.get("/some/protected/page/")
    # The middleware only checks request.user.is_authenticated.
    request.user = SimpleNamespace(is_authenticated=True)

    assert mw(request) is SENTINEL


@pytest.mark.parametrize("prefix", _EXEMPT_PREFIXES)
def test_exempt_prefixes_allow_anonymous(rf, prefix):
    mw = _build_middleware()
    request = rf.get(prefix)
    request.user = AnonymousUser()

    # Exempt paths never redirect, even for anonymous users.
    assert mw(request) is SENTINEL


def test_login_register_and_password_reset_are_exempt():
    # Guard the security-critical exemptions explicitly: an anonymous user
    # must always be able to reach these to authenticate / recover access.
    for required in ("/accounts/login/", "/accounts/register/",
                     "/accounts/password-reset/"):
        assert any(required.startswith(p) or p == required
                   for p in _EXEMPT_PREFIXES), f"{required} should be exempt"


def test_exempt_match_is_prefix_based_not_exact(rf):
    # A deeper path under an exempt prefix (e.g. the password-reset
    # confirm link) must also be allowed through for anonymous users.
    mw = _build_middleware()
    request = rf.get("/accounts/password-reset/MQ/set-token/")
    request.user = AnonymousUser()

    assert mw(request) is SENTINEL
