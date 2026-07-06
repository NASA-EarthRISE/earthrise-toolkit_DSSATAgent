"""
Tool registry for data_agent — discovered generically by the chat
orchestrator. See a sub-agent's tools.py for the shared schema contract.
"""

from __future__ import annotations

from typing import Any, Dict


# ---------------------------------------------------------------------------
# Schema builders — invoked per-request so dynamic enums stay fresh.
# ---------------------------------------------------------------------------


def _query_data_schema_builder() -> Dict[str, Any]:
    from earthrise_agents_base.schemas import build_tool_schema
    from data_agent.schemas import QueryDataInput, build_dataset_enum

    return build_tool_schema(
        QueryDataInput,
        enum_providers={"source": build_dataset_enum},
    )


def _check_availability_schema_builder() -> Dict[str, Any]:
    from earthrise_agents_base.schemas import build_tool_schema
    from data_agent.schemas import CheckAvailabilityInput, build_dataset_enum

    return build_tool_schema(
        CheckAvailabilityInput,
        enum_providers={"source": build_dataset_enum},
    )


def _resolve_dataset_schema_builder() -> Dict[str, Any]:
    from earthrise_agents_base.schemas import build_tool_schema
    from data_agent.schemas import ResolveDatasetInput

    return build_tool_schema(ResolveDatasetInput)


def _resolve_location_schema_builder() -> Dict[str, Any]:
    from earthrise_agents_base.schemas import build_tool_schema
    from data_agent.schemas import ResolveLocationInput

    return build_tool_schema(ResolveLocationInput)


# ---------------------------------------------------------------------------
# Function wrappers
# ---------------------------------------------------------------------------


def _query_data_func(args: Dict[str, Any], user: Any = None) -> Dict[str, Any]:
    from data_agent.tool_wrapper import query_data_tool

    return query_data_tool(args, user=user)


def _check_availability_func(args: Dict[str, Any], user: Any = None) -> Dict[str, Any]:
    from data_agent.tool_wrapper import check_availability_tool

    return check_availability_tool(args, user=user)


def _resolve_dataset_func(args: Dict[str, Any], user: Any = None) -> Dict[str, Any]:
    from data_agent.tool_wrapper import resolve_dataset_tool

    return resolve_dataset_tool(args, user=user)


def _resolve_location_func(args: Dict[str, Any], user: Any = None) -> Dict[str, Any]:
    from data_agent.tool_wrapper import resolve_location_tool

    return resolve_location_tool(args, user=user)


# ---------------------------------------------------------------------------
# Descriptions (lazy — pulled from tool_wrapper constants)
# ---------------------------------------------------------------------------


def _desc(name: str):
    def _fn():
        from data_agent import tool_wrapper

        return getattr(tool_wrapper, name)
    return _fn


TOOL_REGISTRY: Dict[str, Dict[str, Any]] = {
    "query_data": {
        "description": _desc("QUERY_DATA_TOOL_DESCRIPTION"),
        "schema_builder": _query_data_schema_builder,
        "func": _query_data_func,
        "owner_agent": "data_agent",
    },
    "check_availability": {
        "description": _desc("CHECK_AVAILABILITY_TOOL_DESCRIPTION"),
        "schema_builder": _check_availability_schema_builder,
        "func": _check_availability_func,
        "owner_agent": "data_agent",
    },
    "resolve_dataset": {
        "description": _desc("RESOLVE_DATASET_TOOL_DESCRIPTION"),
        "schema_builder": _resolve_dataset_schema_builder,
        "func": _resolve_dataset_func,
        "owner_agent": "data_agent",
    },
    "resolve_location": {
        "description": _desc("RESOLVE_LOCATION_TOOL_DESCRIPTION"),
        "schema_builder": _resolve_location_schema_builder,
        "func": _resolve_location_func,
        "owner_agent": "data_agent",
    },
}
