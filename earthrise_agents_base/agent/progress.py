"""
Generic tool-progress streaming for the chat SSE pipeline.

Tools (e.g. `run_experiment`, `knowledge_retrieve`) often have multiple
internal stages — building the experiment, fetching weather, running the
simulation, formatting results. Without per-stage events the user sees
"Running tasks..." for thirty seconds and assumes the pipeline is hung.

Instead of threading the chat `message_id` through every tool signature,
we stash it in a contextvar at the top of `_tool_executor_node` and let
tools call `publish_progress(...)` from anywhere in their call stack.

Event shape on Redis pub/sub (channel `chat:<message_id>`):

    {
        "type": "tool_progress",
        "tool": "<tool_name>" | None,
        "stage": "<machine-readable-key>",
        "title": "<short heading>",
        "description": "<optional one-liner>",
        "iteration": <int> | None,
        ...extra
    }

The chat.js SSE consumer renders this as a thinking-trace entry,
alongside the existing `tool_call` / `tool_result` / `thinking` events.

Calling `publish_progress` outside a chat pipeline (e.g. from a CLI
script) is a no-op — the contextvar is unset, nothing is published.
"""

from __future__ import annotations

import contextvars
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


# Per-task context: which chat message is the active publish target.
# Set by `_tool_executor_node` before dispatching tool calls; tools read
# it implicitly via `publish_progress`.
_message_id_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "chat_progress_message_id", default=None,
)
_tool_name_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "chat_progress_tool_name", default=None,
)
_iteration_var: contextvars.ContextVar[Optional[int]] = contextvars.ContextVar(
    "chat_progress_iteration", default=None,
)


def set_progress_context(
    *,
    message_id: Optional[str],
    tool_name: Optional[str] = None,
    iteration: Optional[int] = None,
) -> None:
    """Stash the publish target for the current tool dispatch. Call once
    at the top of each tool invocation; the orchestrator's tool executor
    handles this for ReAct-driven tools.
    """
    _message_id_var.set(message_id)
    _tool_name_var.set(tool_name)
    _iteration_var.set(iteration)


def clear_progress_context() -> None:
    """Reset to the no-op state. Pair with `set_progress_context` around
    the boundary where the tool finishes.
    """
    _message_id_var.set(None)
    _tool_name_var.set(None)
    _iteration_var.set(None)


def publish_progress(
    stage: str,
    title: str = "",
    description: str = "",
    **extra: Any,
) -> None:
    """Emit a `tool_progress` SSE event for the active message. No-op when
    no message is in scope.

    `stage` is a short machine-readable key (e.g. "build_experiment") the
    UI can use to group / animate / dedupe consecutive events.
    `title` and `description` are user-visible.
    """
    message_id = _message_id_var.get()
    if not message_id:
        return
    payload = {
        "type": "tool_progress",
        "tool": _tool_name_var.get(),
        "stage": stage,
        "title": title or stage.replace("_", " ").title(),
        "description": description,
        "iteration": _iteration_var.get(),
        **extra,
    }
    try:
        from earthrise_agents_base.tasks import _publish_chat_event
        _publish_chat_event(message_id, payload)
    except Exception as e:
        logger.debug("[progress] publish failed: %s", e)
