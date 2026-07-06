"""
Generic A2A endpoints — one implementation, parameterized by agent name.

Every sub-agent app mounts three URL patterns using the framework's
class-based views, passing ``agent_name`` via ``as_view(...)``. The
views look up the registered ``(AgentCard, SkillTable)`` pair from
``registry`` and serve/dispatch accordingly.

Response envelope is JSON-RPC 2.0 message/send with an ``artifacts``
result shape — matches the A2A protocol spec and emits the byte-level
envelope every sub-agent's A2A endpoint returns.
"""

from __future__ import annotations

import json
import logging
import math
from typing import Any, List

from django.http import HttpResponse, JsonResponse
from django.urls import path
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from .registry import get_agent, is_registered

logger = logging.getLogger(__name__)


def _sanitize_nans(obj: Any) -> Any:
    """Recursively replace float NaN/Inf with None for JSON safety.

    LangChain / xarray occasionally produce NaN/Inf in result payloads
    that break naive JSON encoding, so sanitize before serializing.
    """
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    if isinstance(obj, dict):
        return {k: _sanitize_nans(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize_nans(v) for v in obj]
    return obj


def _rpc_error(rpc_id: Any, code: int, message: str) -> JsonResponse:
    return JsonResponse({
        "jsonrpc": "2.0",
        "id": rpc_id,
        "error": {"code": code, "message": message},
    })


class _AgentBoundView(View):
    """Base class for views bound to a specific agent name via ``as_view()``.

    Django's ``as_view(agent_name="data")`` sets the attribute on the
    view class instance so ``self.agent_name`` is accessible in ``get()``
    / ``post()``.
    """

    agent_name: str = ""


class AgentCardView(_AgentBoundView):
    """GET /.well-known/agent-card.json — serve the registered AgentCard."""

    def get(self, request):
        try:
            card, _ = get_agent(self.agent_name)
        except KeyError:
            return JsonResponse(
                {"error": f"Agent {self.agent_name!r} not registered"},
                status=404,
            )
        # Serialize with legacy-compatible skill entry shape (id + name
        # + description as top-level keys, matching what the old
        # bespoke AGENT_CARD dicts emitted).
        payload = card.to_json_dict()
        # The A2A card ``url`` is cosmetic/self-descriptive — nothing
        # routes by it (routing is via settings.AGENT_CLIENTS). Sub-agents
        # no longer hardcode it, so stamp it at serve time from the request
        # (the URL the caller actually reached). Stamp on the serialized
        # dict copy — never mutate the registered card object.
        if not payload.get("url"):
            payload["url"] = request.build_absolute_uri("/")
        return JsonResponse(payload)


class HealthView(_AgentBoundView):
    """GET /health — trivial liveness probe."""

    def get(self, request):
        if is_registered(self.agent_name):
            return JsonResponse({"status": "ok"})
        return JsonResponse(
            {"status": "error", "error": f"Agent {self.agent_name!r} not registered"},
            status=503,
        )


@method_decorator(csrf_exempt, name="dispatch")
class RPCView(_AgentBoundView):
    """POST / — JSON-RPC 2.0 ``message/send`` skill dispatch.

    Body shape (input)::

        {
          "jsonrpc": "2.0",
          "id": <rpc_id>,
          "method": "message/send",
          "params": {
            "message": {
              "parts": [
                {"type": "data", "data": {"skill": "<id>", "params": {...}}}
              ]
            }
          }
        }

    Response shape (success)::

        {
          "jsonrpc": "2.0",
          "id": <rpc_id>,
          "result": {"artifacts": [{"parts": [{"type": "data", "data": <result>}]}]}
        }

    Error codes match JSON-RPC 2.0:
        -32700 parse error, -32601 method-or-skill not found,
        -32602 invalid params, -32603 internal error.
    """

    def post(self, request):
        try:
            body = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            return _rpc_error(None, -32700, "Parse error")

        rpc_id = body.get("id")
        method = body.get("method")

        if method != "message/send":
            return _rpc_error(rpc_id, -32601, f"Method not found: {method}")

        params = body.get("params", {})
        message = params.get("message", {})
        parts = message.get("parts", [])

        skill_name = None
        skill_params = {}
        for part in parts:
            if part.get("type") == "data":
                data = part.get("data", {})
                skill_name = data.get("skill")
                skill_params = data.get("params", {})
                break

        if not skill_name:
            return _rpc_error(rpc_id, -32602, "No skill specified in message")

        try:
            _, skills = get_agent(self.agent_name)
        except KeyError:
            return _rpc_error(
                rpc_id, -32603, f"Agent {self.agent_name!r} not registered",
            )

        if not skills.has(skill_name):
            return _rpc_error(rpc_id, -32601, f"Unknown skill: {skill_name}")

        try:
            result = skills.dispatch(skill_name, skill_params)
        except Exception as e:
            logger.exception(
                "Error handling skill %s on agent %s",
                skill_name, self.agent_name,
            )
            return _rpc_error(rpc_id, -32603, str(e))

        # Preserve legacy behavior: if a handler returns a dict with an
        # ``error`` key, surface it as an -32602 invalid-params error.
        if isinstance(result, dict) and "error" in result:
            return _rpc_error(rpc_id, -32602, result["error"])

        response_body = {
            "jsonrpc": "2.0",
            "id": rpc_id,
            "result": {
                "artifacts": [
                    {"parts": [{"type": "data", "data": result}]}
                ]
            },
        }
        content = json.dumps(_sanitize_nans(response_body), default=str)
        return HttpResponse(content, content_type="application/json")


def build_urlpatterns(agent_name: str) -> List:
    """Return the three A2A URL patterns for an agent, bound to its name.

    Sub-agents call this from their ``a2a/urls.py``::

        from earthrise_agents_base.a2a.views import build_urlpatterns
        urlpatterns = build_urlpatterns(agent_name="data")

    Mounts the three standard A2A URL patterns (agent card, health, RPC
    dispatch) for the named agent.
    """
    return [
        path(
            ".well-known/agent-card.json",
            AgentCardView.as_view(agent_name=agent_name),
            name="agent_card",
        ),
        path(
            "health",
            HealthView.as_view(agent_name=agent_name),
            name="health",
        ),
        path(
            "",
            RPCView.as_view(agent_name=agent_name),
            name="rpc",
        ),
    ]
