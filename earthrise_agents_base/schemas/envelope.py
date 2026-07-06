"""
Tool-result envelope shapes.

Every tool invoked by the ReAct loop returns one of these envelope shapes.
The `_tool_executor_node` inspects `status` to decide whether to post a
ToolMessage, pause the graph via `interrupt()`, retry on validation,
or terminate the flow.
"""

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class ValidationIssue(BaseModel):
    """One field-level problem surfaced to the LLM for iterative retry."""

    field: str = Field(..., description=(
        "Dotted path to the offending field (e.g. 'planting.method' or 'crop')."
    ))
    message: str = Field(..., description="Short human-readable description.")
    received: Optional[Any] = Field(None, description=(
        "The value that triggered the failure, if available."
    ))
    valid_values: Optional[List[str]] = Field(None, description=(
        "Allowed values for this field (truncated to ~50 entries when large)."
    ))
    suggestions: Optional[List[str]] = Field(None, description=(
        "Closest matches to `received` via fuzzy match, if applicable."
    ))


class OkEnvelope(BaseModel):
    """Successful tool result. Subclassed per-tool with a `payload` attached."""

    status: Literal["ok"] = "ok"
    payload: Dict[str, Any] = Field(default_factory=dict, description=(
        "Tool-specific result body. Shape depends on the tool — see its docstring."
    ))


class NeedsInputEnvelope(BaseModel):
    """
    Tool is pausing to ask the user for additional information.

    The ReAct executor translates this into a LangGraph `interrupt()`.
    On resume via `Command(resume=user_reply)`, the executor merges
    `user_reply` into `known` and invokes `next_tool` (falling back to
    the originating tool).
    """

    status: Literal["needs_input"] = "needs_input"
    prompt: str = Field(..., description=(
        "Plain-English question to pose to the user. May include "
        "newline-separated sub-questions for grouped inputs."
    ))
    schema_: Dict[str, Any] = Field(
        default_factory=dict,
        alias="schema",
        description=(
            "JSON schema describing the shape of the expected reply. "
            "The frontend may render a typed form from this; otherwise "
            "the user replies in free text and the next tool invocation "
            "validates."
        ),
    )
    known: Dict[str, Any] = Field(default_factory=dict, description=(
        "Partial state gathered so far. Passed back into the tool on resume "
        "so multi-turn state is preserved without requiring the LLM to "
        "remember it."
    ))
    next_tool: Optional[str] = Field(None, description=(
        "Which tool to invoke when the user replies. Defaults to the tool "
        "that originated this envelope. Used when a tool wants to hand off "
        "to a bundled helper (e.g., first call to `run_experiment` routes "
        "resume to `experiment_wizard_step`)."
    ))

    model_config = {"populate_by_name": True}


class ValidationErrorEnvelope(BaseModel):
    """
    Parameter validation failed. The LLM may retry with corrected args.

    After 2 retries with the same `(field, message)` on the same tool,
    the executor converts the third failure to `NeedsInputEnvelope` that
    escalates to the user.
    """

    status: Literal["validation_error"] = "validation_error"
    error: str = Field(..., description="One-line summary.")
    issues: List[ValidationIssue] = Field(default_factory=list)
    hint: Optional[str] = Field(None, description=(
        "Optional next-step guidance to the LLM (e.g., 'call "
        "experiment_wizard_step instead')."
    ))


class ErrorEnvelope(BaseModel):
    """Subagent or infrastructure error. Not a validation problem."""

    status: Literal["error"] = "error"
    error: str
    details: Optional[Dict[str, Any]] = None


class CancelledEnvelope(BaseModel):
    """User abandoned a multi-turn flow (e.g., pressed 'Abandon wizard')."""

    status: Literal["cancelled"] = "cancelled"
    reason: Optional[str] = None
