"""
SkillTable + module-level agent registry.

Each sub-agent app instantiates one SkillTable, decorates its service
functions as skills, builds an AgentCard, and calls ``register_agent``
in its ``AppConfig.ready()``. The generic A2A views then dispatch
incoming JSON-RPC calls via ``get_agent(name).skills.dispatch(...)``.

Thread-safe: registration happens once at Django startup, dispatch is
read-only.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional, Tuple

from pydantic import BaseModel

from .card import AgentCard, SkillDefinition

logger = logging.getLogger(__name__)


class SkillTable:
    """Decorator-based skill registration for one agent.

    Usage::

        skills = SkillTable()

        @skills.skill(
            id="fetch_data",
            name="Fetch Data",
            description="Download weather data for a region.",
        )
        def fetch_data(params: dict) -> dict:
            ...

    The decorator returns the underlying function unchanged so it can
    still be called directly (for embedded / in-process use).
    """

    def __init__(self) -> None:
        self._entries: Dict[str, Dict[str, Any]] = {}

    def skill(
        self,
        id: str,
        *,
        name: str,
        description: str,
        input_schema: Optional[Dict[str, Any]] = None,
        output_schema: Optional[Dict[str, Any]] = None,
        tags: Optional[List[str]] = None,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Register ``fn`` as the handler for skill ``id``."""

        def _decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
            if id in self._entries:
                raise ValueError(
                    f"SkillTable already contains skill {id!r}; refusing to overwrite."
                )
            self._entries[id] = {
                "definition": SkillDefinition(
                    id=id,
                    name=name,
                    description=description,
                    input_schema=input_schema,
                    output_schema=output_schema,
                    tags=list(tags or []),
                ),
                "handler": fn,
            }
            return fn

        return _decorator

    def dispatch(self, skill_id: str, params: Dict[str, Any]) -> Any:
        """Execute the handler for ``skill_id`` with ``params``."""
        entry = self._entries.get(skill_id)
        if entry is None:
            raise KeyError(f"Unknown skill: {skill_id}")
        return entry["handler"](params)

    def has(self, skill_id: str) -> bool:
        return skill_id in self._entries

    def known_skills(self) -> List[str]:
        return sorted(self._entries.keys())

    def skill_definitions(self) -> List[SkillDefinition]:
        return [e["definition"] for e in self._entries.values()]


# ---------------------------------------------------------------------------
# Module-level agent registry
# ---------------------------------------------------------------------------

# Registry: agent_name → (card, skills). Populated at AppConfig.ready().
_REGISTRY: Dict[str, Tuple[AgentCard, SkillTable]] = {}


def register_agent(*, name: str, card: AgentCard, skills: SkillTable) -> None:
    """Register a sub-agent under ``name``. Idempotent.

    Called from AppConfig.ready() or module top-level. Re-registration
    with the same (card, skills) is silent — a warning only fires when
    the card differs, which usually indicates a real bug (two apps
    trying to claim the same agent name with different capabilities).
    """
    existing = _REGISTRY.get(name)
    if existing is not None:
        prev_card, prev_skills = existing
        if prev_card == card and prev_skills is skills:
            return  # Truly identical re-registration; silent.
        logger.warning(
            "Agent %r re-registered with a different card or skill table — "
            "overwriting previous registration.",
            name,
        )
    _REGISTRY[name] = (card, skills)


def get_agent(name: str) -> Tuple[AgentCard, SkillTable]:
    """Fetch an agent's (card, skills) tuple. Raises KeyError if unknown."""
    if name not in _REGISTRY:
        raise KeyError(f"No agent registered under name={name!r}")
    return _REGISTRY[name]


def is_registered(name: str) -> bool:
    return name in _REGISTRY


def iterate_agents():
    """Yield ``(name, card, skills)`` for every registered agent."""
    for name, (card, skills) in sorted(_REGISTRY.items()):
        yield name, card, skills
