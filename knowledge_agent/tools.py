"""
Tool registry for knowledge_agent — discovered generically by the chat
orchestrator.

Registry entries expose:
  - description: callable that returns the LLM-facing tool description
  - schema_builder: callable that returns the JSON Schema for the tool's
    Pydantic input model
  - func: callable that takes (args_dict, *, user=...) and returns the
    ReAct envelope dict (status/error/results/...)
  - owner_agent: app label of the owning agent, used by the orchestrator
    for routing
"""

from __future__ import annotations

from typing import Any, Dict


def _knowledge_retrieve_schema_builder() -> Dict[str, Any]:
    from earthrise_agents_base.schemas import build_tool_schema
    from knowledge_agent.schemas import RetrieveInput

    return build_tool_schema(RetrieveInput)


def _knowledge_retrieve_func(args: Dict[str, Any], user: Any = None) -> Dict[str, Any]:
    from knowledge_agent.tool_wrapper import knowledge_retrieve_tool

    return knowledge_retrieve_tool(args, user=user)


def _knowledge_retrieve_description() -> str:
    from knowledge_agent.tool_wrapper import KNOWLEDGE_RETRIEVE_TOOL_DESCRIPTION

    return KNOWLEDGE_RETRIEVE_TOOL_DESCRIPTION


TOOL_REGISTRY: Dict[str, Dict[str, Any]] = {
    "knowledge_retrieve": {
        "description": _knowledge_retrieve_description,
        "schema_builder": _knowledge_retrieve_schema_builder,
        "func": _knowledge_retrieve_func,
        "owner_agent": "knowledge_agent",
    },
}
