"""
Tool registry for dssat_agent — discovered by the chat orchestrator.

Each entry describes one tool the LLM can call. The chat agent's
`_build_tool_registry()` imports this dict generically (no hardcoded
references to DSSAT), so adding or removing tools here is the only
change needed to expose/hide them.

Keys in each entry:
    description     — one-paragraph tool description; becomes the JSON
                      schema description the LLM reads.
    schema_builder  — zero-arg callable returning the JSON schema (dict)
                      for the tool's parameters. Called per request so
                      dynamic enums stay fresh.
    func            — callable(args_dict, user=None) -> envelope dict.
                      Must return a `status` field; see
                      `chat.schemas.envelope` for the contract.
    owner_agent     — app label ('dssat_agent').

This module has no import-time dependency on Django or Pydantic validators
that hit the ORM — the schema builders handle that lazily.
"""

from __future__ import annotations

from typing import Any, Callable, Dict


def _run_experiment_schema_builder() -> Dict[str, Any]:
    from earthrise_agents_base.schemas import build_tool_schema
    from data_agent.schemas import build_dataset_enum
    from dssat_agent.schemas import SingleExperiment, build_crop_enum

    return build_tool_schema(
        SingleExperiment,
        enum_providers={
            "crop": build_crop_enum,
            "weather_dataset": build_dataset_enum,
        },
    )


def _run_experiment_func(args: Dict[str, Any], user: Any = None) -> Dict[str, Any]:
    from dssat_agent.services import run_experiment_tool

    return run_experiment_tool(args, user=user)


def _run_experiment_description() -> str:
    from dssat_agent.services import RUN_EXPERIMENT_TOOL_DESCRIPTION

    return RUN_EXPERIMENT_TOOL_DESCRIPTION


def _query_experiment_schema_builder() -> Dict[str, Any]:
    from earthrise_agents_base.schemas import build_tool_schema
    from dssat_agent.schemas import QueryExperimentInput

    return build_tool_schema(QueryExperimentInput)


def _query_experiment_func(args: Dict[str, Any], user: Any = None) -> Dict[str, Any]:
    from dssat_agent.services import query_experiment_tool

    return query_experiment_tool(args, user=user)


def _query_experiment_description() -> str:
    from dssat_agent.services import QUERY_EXPERIMENT_TOOL_DESCRIPTION

    return QUERY_EXPERIMENT_TOOL_DESCRIPTION


def _experiment_wizard_step_schema_builder() -> Dict[str, Any]:
    from earthrise_agents_base.schemas import build_tool_schema
    from dssat_agent.schemas import ExperimentWizardInput, build_crop_enum

    return build_tool_schema(
        ExperimentWizardInput,
        enum_providers={"crop": build_crop_enum},
    )


def _experiment_wizard_step_func(args: Dict[str, Any], user: Any = None) -> Dict[str, Any]:
    from dssat_agent.services import experiment_wizard_step_tool

    return experiment_wizard_step_tool(args, user=user)


def _experiment_wizard_step_description() -> str:
    from dssat_agent.services import EXPERIMENT_WIZARD_STEP_TOOL_DESCRIPTION

    return EXPERIMENT_WIZARD_STEP_TOOL_DESCRIPTION


TOOL_REGISTRY: Dict[str, Dict[str, Any]] = {
    # run_experiment is listed FIRST — when the user gave full inputs
    # (crop + location + planting date), the fast path should be the
    # obvious choice. llama3.1 biases toward earlier-declared tools when
    # their descriptions overlap.
    "run_experiment": {
        "description": _run_experiment_description,
        "schema_builder": _run_experiment_schema_builder,
        "func": _run_experiment_func,
        "owner_agent": "dssat_agent",
    },
    "experiment_wizard_step": {
        "description": _experiment_wizard_step_description,
        "schema_builder": _experiment_wizard_step_schema_builder,
        "func": _experiment_wizard_step_func,
        "owner_agent": "dssat_agent",
    },
    "query_experiment": {
        "description": _query_experiment_description,
        "schema_builder": _query_experiment_schema_builder,
        "func": _query_experiment_func,
        "owner_agent": "dssat_agent",
    },
}
