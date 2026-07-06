"""
Shared schema primitives for the chat orchestrator.

Contains cross-agent helpers that are generic over tool schemas:
- `envelope` — Pydantic models for the ok/needs_input/validation_error/error/cancelled
  envelope shapes that every tool returns.
- `errors` — validation_error_to_react(): ValidationError -> react envelope dict.
- `tool_schema` — build_tool_schema(): generates a JSON schema from a Pydantic
  model with per-field enum injection for dynamic catalogs.

Agent-specific input schemas live inside their owning sub-agent's own
``schemas/`` submodule (e.g. ``<agent_name>/schemas/``). The framework
never imports sub-agent-specific types.
"""

from earthrise_agents_base.schemas.envelope import (
    OkEnvelope,
    NeedsInputEnvelope,
    ValidationErrorEnvelope,
    ErrorEnvelope,
    CancelledEnvelope,
    ValidationIssue,
)
from earthrise_agents_base.schemas.errors import validation_error_to_react
from earthrise_agents_base.schemas.tool_schema import build_tool_schema, inject_enum_into_schema

__all__ = [
    "OkEnvelope",
    "NeedsInputEnvelope",
    "ValidationErrorEnvelope",
    "ErrorEnvelope",
    "CancelledEnvelope",
    "ValidationIssue",
    "validation_error_to_react",
    "build_tool_schema",
    "inject_enum_into_schema",
]
