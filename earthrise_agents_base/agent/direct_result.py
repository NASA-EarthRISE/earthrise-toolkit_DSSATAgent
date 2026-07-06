"""
Write a subagent service result envelope directly to a chat Message.

Use this when a subagent executes a workflow with already-structured inputs
(wizard submissions, form posts, API-triggered runs) and there is no reason
to route through the ChatAgent's LLM loop. The helper is the one place that
knows how an envelope maps onto Chat state: response text, artifacts,
conversation_context, SSE publish, Message status.

Expected envelope shape (same contract the ReAct tool path produces):

    {
        "status": "completed" | "error" | "failed" | "missing_data"
                  | "validation_error" | "cancelled" | "needs_input",
        "response_text": Optional[str],     # subagent-provided narrative
        "response_footer": Optional[str],   # markdown link / trailing note
        "artifacts": Optional[List[Dict]],  # charts, tables, maps
        "key_results": Optional[Dict],      # scalar summary for fallback text
        "conversation_context": Optional[Dict],  # merged into memory context_store
        "error": Optional[str],             # for error/failed/missing_data
        "issues": Optional[List[Dict]],     # for validation_error
        "prompt": Optional[str],            # for needs_input
        ...
    }

A future subagent that wants the same wizard-style direct-execution pattern
writes its own Celery task, calls its service, and hands the resulting
envelope to `write_envelope_to_message`. No chat-agent plumbing required.
"""

from __future__ import annotations

import json
import logging
from typing import Dict, Optional

logger = logging.getLogger(__name__)


_TERMINAL_OK_STATUSES = {"completed", "ok", "done"}
_ERROR_STATUSES = {"error", "failed", "missing_data"}


def write_envelope_to_message(
    chat_id: str,
    message_id: str,
    envelope: Dict,
) -> None:
    """
    Persist `envelope` as the assistant reply on `message_id` in `chat_id`.

    Merges `envelope['conversation_context']` into the Chat's stored memory
    so follow-up turns (through either the ChatAgent loop or another direct
    submission) see the same context the ReAct path would have stored.

    Publishes an SSE event on channel ``chat:<message_id>`` so the open chat
    view transitions from "processing" to the rendered reply without a
    page reload.
    """
    from django.utils import timezone
    from earthrise_agents_base.models import Chat, Message

    try:
        chat = Chat.objects.get(id=chat_id)
        message = Message.objects.get(id=message_id)
    except (Chat.DoesNotExist, Message.DoesNotExist) as e:
        logger.error("direct_result: chat/message not found: %s", e)
        return

    status = (envelope.get("status") or "").lower()
    response_text, is_error = _render_envelope(envelope, status)
    artifacts = envelope.get("artifacts") or []

    _merge_conversation_context(chat, envelope.get("conversation_context"))
    _append_assistant_message_to_memory(chat, response_text)

    message.content = response_text
    message.artifacts = artifacts
    message.status = "failed" if is_error else "completed"
    message.current_node = None
    message.save(
        update_fields=["content", "artifacts", "status", "current_node"],
    )

    chat.is_processing = False
    chat.updated_at = timezone.now()
    chat.save(update_fields=["is_processing", "updated_at", "agent_memory"])

    _publish(
        message_id,
        {
            "type": "error" if is_error else "done",
            "content": response_text,
            "artifacts": artifacts,
            **({"message": response_text} if is_error else {}),
        },
    )


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _render_envelope(envelope: Dict, status: str) -> tuple[str, bool]:
    """Return (response_text, is_error) for an envelope."""
    if status in _ERROR_STATUSES:
        return _render_error(envelope), True

    if status == "validation_error":
        return _render_validation_error(envelope), True

    if status == "needs_input":
        prompt = envelope.get("prompt") or "I need a bit more information."
        return prompt, False

    if status == "cancelled":
        return envelope.get("message") or "Cancelled.", False

    # Success path (completed/ok/done, or unknown-but-not-error).
    # Preference order:
    #   1. ``envelope['response_text']`` (LLM-synthesized or pre-built).
    #   2. ``_render_success_fallback`` — generic key-result bullet dump.
    response_text = envelope.get("response_text")
    if not response_text:
        response_text = _render_success_fallback(envelope)

    footer = (envelope.get("response_footer") or "").strip()
    if footer and footer not in response_text:
        response_text = f"{response_text}{footer}"

    return response_text, False


def synthesize_envelope_narrative(
    envelope: Dict,
    *,
    user_query: str = "",
    agent_label: str,
) -> Optional[str]:
    """LLM-synthesize a human narrative from a result envelope.

    Used by direct-submission paths (e.g. wizard-driven crop runs) that
    bypass the ChatAgent ReAct loop but still want the same kind of
    natural-language reply the LLM produces inside the loop. Returns
    ``None`` when synthesis can't run (LLM unavailable, prompt missing,
    no key_results) so the caller can fall back to whatever
    ``envelope['response_text']`` or the deterministic fallback already
    has.

    The LLM context mirrors ``ChatAgent._synthesize_results``: ``status``
    + ``error`` from the envelope, plus ``key_results`` flattened to
    bullet lines. The agent-specific formatting guidelines (e.g. a
    product-provided ``foo-results`` skill) are composed into the
    prompt via the same ``chat-results`` template ChatAgent uses.

    Best-effort: any exception falls through to ``None`` so a slow or
    down LLM never blocks the wizard reply path.
    """
    key_results = envelope.get("key_results") or {}
    if not key_results and not envelope.get("response_text"):
        return None

    try:
        from earthrise_agents_base.agent.skill_loader import SkillLoader
        from langchain_ollama import OllamaLLM
        import os
        # Reuse the same skills directory layout ChatAgent uses so the
        # composed ``foo-results`` body is available.
        skills_dir = os.environ.get(
            'SKILLS_DIR',
            os.path.join(os.path.dirname(__file__), '..', 'skills'),
        )
        skills = SkillLoader(skills_dir)
        try:
            from earthrise_agents_base.agent.discovery import get_all_skills_dirs
            for extra_dir in get_all_skills_dirs():
                if os.path.isdir(extra_dir) and extra_dir != skills_dir:
                    extra = SkillLoader(extra_dir)
                    skills.merge(extra)
        except Exception:
            pass

        prompt_template = skills.get_prompt("chat-results", section="Results")
        if not prompt_template:
            return None

        # Build readable result lines, the same way _synthesize_results
        # does. Hide private keys (``_``-prefixed) from the LLM unless
        # they're the ``_insights`` text (which is meant for it).
        lines = []
        for label, value in key_results.items():
            if label.startswith("_") and label != "_insights":
                continue
            if isinstance(value, dict):
                for sub_key, sub_val in value.items():
                    if isinstance(sub_val, dict) and 'avg' in sub_val:
                        avg = sub_val['avg']
                        severity = (
                            'severe' if avg > 0.50
                            else 'moderate' if avg > 0.20
                            else 'mild'
                        )
                        lines.append(
                            f"- {sub_key.replace('_', ' ').title()}: "
                            f"{severity} (avg {avg:.2f})"
                        )
                    else:
                        lines.append(f"- {sub_key}: {sub_val}")
            else:
                lines.append(f"- {label}: {value}")
        result_block = "\n".join(lines) if lines else "(no key results)"

        # Compose agent-specific formatting guidance (e.g. foo-results).
        agent_guide = ""
        try:
            if hasattr(skills, "get_composed_content_for_agents"):
                agent_guide = skills.get_composed_content_for_agents(
                    "results", agents=[agent_label], section="Results",
                ) or ""
            if not agent_guide and hasattr(skills, "get_composed_content"):
                agent_guide = skills.get_composed_content(
                    "results", section="Results",
                ) or ""
        except Exception:
            pass

        prompt = prompt_template.format(
            user_query=user_query or "(direct submission — see parameters below)",
            params="(see experiment detail page)",
            results=f"### {agent_label}\n```\n{result_block}\n```",
            agent_results=agent_guide
            or "Present the results as a short, clear narrative.",
        )

        ollama_url = os.environ.get('OLLAMA_URL', 'http://localhost:11434')
        model_name = os.environ.get('LLM_MODEL_NAME', 'qwen2.5:7b')
        llm = OllamaLLM(
            base_url=ollama_url, model=model_name,
            num_predict=1024, num_ctx=16384, temperature=0.2,
        )
        text = llm.invoke(prompt).strip().strip('"\'')
        return text or None
    except Exception as e:
        logger.info(
            "synthesize_envelope_narrative failed (%s); falling back",
            e,
        )
        return None


def _render_success_fallback(envelope: Dict) -> str:
    """Build deterministic narrative from key_results when the subagent
    didn't supply its own `response_text`. Used for wizard/form submissions
    where we intentionally skip the LLM synthesis step."""
    key_results = envelope.get("key_results") or {}

    # Drop private/internal keys the subagent attached for UI use.
    visible = {
        k: v for k, v in key_results.items()
        if not k.startswith("_") and k not in {"experiment_type", "stress"}
    }

    if not visible:
        return "Run completed successfully."

    lines = ["Run completed successfully.", "", "**Key results:**"]
    for label, value in visible.items():
        lines.append(f"- **{label}:** {_format_scalar(value)}")
    return "\n".join(lines)


def _render_error(envelope: Dict) -> str:
    error = (
        envelope.get("error")
        or envelope.get("message")
        or "The run could not be completed."
    )
    return str(error)


def _render_validation_error(envelope: Dict) -> str:
    issues = envelope.get("issues") or []
    if not issues:
        return envelope.get("error") or "Some inputs need attention."
    lines = [
        f"- {i.get('field', '?')}: {i.get('message', '')}".rstrip()
        for i in issues
    ]
    body = "I couldn't run that yet — a few inputs need attention:\n\n" + "\n".join(lines)
    hint = envelope.get("hint")
    if hint:
        body += f"\n\n{hint}"
    return body


def _format_scalar(value) -> str:
    if isinstance(value, float):
        return f"{value:,.2f}"
    if isinstance(value, int):
        return f"{value:,}"
    return str(value)


# ---------------------------------------------------------------------------
# Memory + SSE
# ---------------------------------------------------------------------------

def _merge_conversation_context(chat, context: Optional[Dict]) -> None:
    """Merge a subagent's `conversation_context` into the chat's stored memory
    context_store so follow-up turns (ChatAgent or otherwise) can reference
    experiment_id, batch_id, etc."""
    if not context:
        return
    mem = chat.agent_memory or {}
    store = dict(mem.get("context_store") or {})
    store.update(context)
    mem["context_store"] = store
    chat.agent_memory = mem


def _append_assistant_message_to_memory(chat, content: str) -> None:
    """Record the assistant reply in the chat's persisted message history so
    future turns see it as context."""
    mem = chat.agent_memory or {}
    messages = list(mem.get("messages") or [])
    messages.append({"role": "assistant", "content": content})
    mem["messages"] = messages
    chat.agent_memory = mem


def _publish(message_id: str, payload: Dict) -> None:
    from django.conf import settings
    import redis as _redis
    try:
        r = _redis.Redis.from_url(settings.CELERY_BROKER_URL)
        r.publish(f"earthrise_agents_base:{message_id}", json.dumps(payload))
    except Exception as e:
        logger.warning("direct_result: SSE publish failed for %s: %s", message_id, e)
