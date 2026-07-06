"""
LoginRequiredMiddleware — redirect unauthenticated requests to login.

Placed in MIDDLEWARE after AuthenticationMiddleware so request.user
is available.  Exempts login, registration, password-reset, Django
admin, and inter-agent A2A endpoints.
"""

import os

from django.contrib.auth.views import redirect_to_login


# Paths that do NOT require authentication
_EXEMPT_PREFIXES = [
    '/accounts/login/',
    '/accounts/register/',
    '/accounts/password-reset/',
    '/admin/',
    # A2A machine-to-machine endpoints
    '/data_agent/',
    '/knowledge_agent/',
]

# Build list once, respecting SUBPATH
_subpath = os.environ.get('SUBPATH', '')
if _subpath:
    _EXEMPT_PREFIXES = [f'/{_subpath}{p}' for p in _EXEMPT_PREFIXES] + _EXEMPT_PREFIXES


class LoginRequiredMiddleware:
    """Require authentication for all views except explicit exemptions."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not request.user.is_authenticated:
            path = request.path
            if not any(path.startswith(prefix) for prefix in _EXEMPT_PREFIXES):
                # redirect_to_login resolves settings.LOGIN_URL via
                # resolve_url(), so it handles both URL names like
                # 'accounts:login' and absolute paths uniformly, and
                # appends ?next= correctly.
                return redirect_to_login(path)

        return self.get_response(request)
