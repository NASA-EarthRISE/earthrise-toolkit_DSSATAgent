"""
earthrise_agents_base.a2a — generic A2A endpoints + registry.

Public API for sub-agent apps:
- ``AgentCard``, ``SkillDefinition`` — Pydantic models for the discovery card
- ``build_agent_card`` — construct an AgentCard from a SkillTable (no url;
  it is stamped at serve time from the request)
- ``SkillTable`` — decorator-based skill registration
- ``register_agent`` — called from AppConfig.ready()
- ``build_urlpatterns`` — 3-line helper for sub-agent urls.py

Public API for the framework's own consumers:
- ``get_agent(name)`` — lookup (card, skills) tuple
- ``iterate_agents()`` — enumerate every registered agent
"""

from .card import AgentCard, SkillDefinition, build_agent_card
from .registry import (
    SkillTable,
    register_agent,
    get_agent,
    is_registered,
    iterate_agents,
)
from .views import build_urlpatterns

__all__ = [
    "AgentCard",
    "SkillDefinition",
    "build_agent_card",
    "SkillTable",
    "register_agent",
    "get_agent",
    "is_registered",
    "iterate_agents",
    "build_urlpatterns",
]
