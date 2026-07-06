"""
Knowledge agent configuration views — per-user preferences.

Single form page: preferred retrieval strategy. `system_strategy` is the
per-tenant default (`KnowledgeTenant.default_strategy`); `user_strategy`
is a per-(tenant, user) override stored in `KnowledgeConfig`.

NOTE on auth: these views call `request.user` directly and
`@login_required` guards both endpoints. If a downstream deployment ever
needs to expose these to anonymous users (e.g. public read-only docs),
strip the decorator and ensure the AnonymousUser case is handled in
`_read_user_strategy()` — Django can't FK an unsaved AnonymousUser, so
`KnowledgeConfig.objects.update_or_create(user=AnonymousUser(), ...)`
will raise. (Q-new-4: admin/user permission tiers happen elsewhere.)
"""

import json
import logging

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import ensure_csrf_cookie

from knowledge_agent.models import KnowledgeConfig, KnowledgeTenant
from knowledge_agent.store import _tenant_str

logger = logging.getLogger(__name__)


def _tenant_for_request(request) -> str:
    """Resolve the tenant for an HTTP request.

    Single-tenant deployments use DEFAULT_TENANT_ID. A multi-tenant
    deployment can read this from request.user.tenant_id,
    an X-Tenant-ID header, or a subdomain — that's an integration point
    the host project owns.
    """
    return _tenant_str(getattr(request, "knowledge_tenant_id", None))


@method_decorator(ensure_csrf_cookie, name='dispatch')
@method_decorator(login_required, name='dispatch')
class KnowledgePreferencesView(View):
    """Per-user Knowledge agent preferences page (authenticated users only)."""

    def get(self, request):
        tenant_id = _tenant_for_request(request)
        user = request.user

        system_strategy = _read_tenant_default(tenant_id)
        user_strategy = _read_user_strategy(tenant_id, user)

        try:
            from knowledge_agent.services import list_strategies
            strategies = list_strategies().get('strategies', [])
        except Exception:
            logger.exception("Failed to load strategies")
            strategies = []

        return render(request, 'knowledge_agent/explorer/preferences.html', {
            'strategies': strategies,
            'system_strategy': system_strategy,
            'user_strategy': user_strategy,
            'effective_strategy': user_strategy or system_strategy,
        })


def _read_tenant_default(tenant_id: str) -> str:
    """Return the tenant's default strategy, falling back to 'hybrid' if
    the tenant somehow isn't registered."""
    try:
        return KnowledgeTenant.objects.get(id=tenant_id).default_strategy
    except KnowledgeTenant.DoesNotExist:
        return 'hybrid'


def _read_user_strategy(tenant_id: str, user) -> str | None:
    """Read the per-(tenant, user) preferred_strategy override, or None."""
    try:
        row = KnowledgeConfig.objects.get(
            tenant_id=tenant_id, user=user, key='preferred_strategy',
        )
        return row.value
    except KnowledgeConfig.DoesNotExist:
        return None


@method_decorator(ensure_csrf_cookie, name='dispatch')
@method_decorator(login_required, name='dispatch')
class KnowledgePreferencesAPI(View):
    """Save or reset the per-(tenant, user) preferred retrieval strategy."""

    def post(self, request):
        tenant_id = _tenant_for_request(request)

        try:
            data = json.loads(request.body) if request.body else {}
        except json.JSONDecodeError:
            return JsonResponse({'error': 'Invalid JSON'}, status=400)

        strategy = data.get('preferred_strategy')

        if not strategy:
            # Empty value = reset to tenant default (delete the override row).
            KnowledgeConfig.objects.filter(
                tenant_id=tenant_id,
                user=request.user,
                key='preferred_strategy',
            ).delete()
            return JsonResponse({'success': True, 'reset': True})

        # The tenant row must exist for the FK to resolve; if it doesn't,
        # surface a clean error rather than a 500.
        if not KnowledgeTenant.objects.filter(id=tenant_id).exists():
            return JsonResponse(
                {'error': f"Tenant {tenant_id!r} is not registered."},
                status=400,
            )

        KnowledgeConfig.objects.update_or_create(
            tenant_id=tenant_id,
            user=request.user,
            key='preferred_strategy',
            defaults={'value': strategy},
        )
        return JsonResponse({'success': True, 'strategy': strategy})
