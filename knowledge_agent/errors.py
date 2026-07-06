"""
Two-tier error formatting for the knowledge agent.

Per Q9 design: when a retrieval call fails (strategy unavailable, ontology
not built, embedding backend down), admins should see actionable error
detail (which tenant, which pipeline to run, the underlying exception),
while regular users should see a safe minimal message that doesn't leak
infrastructure context.

`admin` is defined as `user.is_staff or user.is_superuser` per the
Q-new-4 confirmation. Anonymous and unauthenticated users always get the
non-admin formatter.
"""

from __future__ import annotations

from typing import Any, Dict, Optional


_USER_FALLBACK_MESSAGE = (
    "This retrieval strategy is currently unavailable. Please try again "
    "or pick a different strategy."
)


def is_admin(user: Any) -> bool:
    """Return True for the staff/superuser tier, False otherwise.

    AnonymousUser, None, or any user without `is_staff`/`is_superuser`
    set to True falls into the regular-user tier.
    """
    if user is None:
        return False
    if not getattr(user, "is_authenticated", False):
        return False
    return bool(
        getattr(user, "is_staff", False) or getattr(user, "is_superuser", False)
    )


def format_strategy_error(
    *,
    user: Any,
    error_message: str,
    admin_detail: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Shape an error envelope appropriate to the requester's role.

    Always returns a `{status, error}` dict. For admins, additionally
    includes a `details` object so they can act on the failure (which
    tenant, which pipeline to run, the underlying exception).
    """
    if is_admin(user):
        envelope: Dict[str, Any] = {
            "status": "error",
            "error": error_message,
        }
        if admin_detail:
            envelope["details"] = admin_detail
        return envelope

    return {
        "status": "error",
        "error": _USER_FALLBACK_MESSAGE,
    }
