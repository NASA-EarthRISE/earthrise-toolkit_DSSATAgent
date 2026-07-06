"""
AgentCard + SkillDefinition — the A2A protocol descriptor Pydantic models.

Every sub-agent exposes one AgentCard at ``.well-known/agent-card.json``.
Consumers (chat agent, external A2A clients) read the card to discover
what skills are available and their input schemas.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class SkillDefinition(BaseModel):
    """One executable skill exposed via A2A."""

    id: str = Field(..., description="Skill identifier — used in the /tasks/send message.")
    name: str = Field(..., description="Human-readable display name.")
    description: str = Field(..., description="One-line description of what the skill does.")
    input_schema: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Optional JSON Schema (OpenAI-tool-schema shape) for the input.",
    )
    output_schema: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Optional JSON Schema for the output shape.",
    )
    tags: List[str] = Field(default_factory=list)


class AgentCard(BaseModel):
    """A2A agent card served at ``.well-known/agent-card.json``.

    The shape matches the A2A protocol spec's discovery document.
    """

    name: str = Field(..., description="Display name of the agent.")
    description: str = Field(..., description="Short description of what the agent does.")
    version: str = Field(default="1.0.0")
    url: Optional[str] = Field(
        default=None,
        description="Public URL where this agent is reachable.",
    )
    skills: List[SkillDefinition] = Field(default_factory=list)
    capabilities: Dict[str, Any] = Field(
        default_factory=dict,
        description="Free-form dict for streaming, batch, etc. flags.",
    )

    def to_json_dict(self) -> Dict[str, Any]:
        """Serialize with ``by_alias=False`` and drop None values."""
        return self.model_dump(exclude_none=True)


def build_agent_card(
    *,
    name: str,
    description: str,
    skills: Any,
    version: str = "1.0.0",
    capabilities: Optional[Dict[str, Any]] = None,
) -> "AgentCard":
    """Construct an :class:`AgentCard` from a populated ``SkillTable``.

    Deliberately has **no** ``url`` parameter. The A2A card ``url`` is
    cosmetic/self-descriptive — nothing routes by it (routing is 100%
    via ``settings.AGENT_CLIENTS``) — so sub-agents must not hardcode it.
    It is stamped at serve time from the incoming request by
    ``AgentCardView`` so the served ``.well-known/agent-card.json`` always
    advertises the URL the caller actually reached.

    ``skills`` is any object exposing ``skill_definitions()`` (a
    ``SkillTable``); duck-typed to avoid a circular import.
    """
    return AgentCard(
        name=name,
        description=description,
        version=version,
        skills=skills.skill_definitions(),
        capabilities=capabilities or {},
    )
