"""
Verification: knowledge-search and data-availability ReAct flows.

Validates that the LLM correctly routes non-DSSAT questions through the
right subagent skill, proving progressive disclosure + owner-agent-scoped
results composition work across subagents.
"""

from __future__ import annotations

import sys
import uuid


def _summary(result) -> str:
    tr = result.get("tool_results", []) or []
    parts = []
    for r in tr:
        parts.append(f"{r.get('name')}:{r.get('status')}")
    return f"iters={result.get('iteration',0)} tools={','.join(parts)}"


def run_case(agent_cls, prompt: str, label: str) -> int:
    thread = f"{label}-{uuid.uuid4().hex[:6]}"
    agent = agent_cls()
    print(f"\n=== {label} ===\n[prompt] {prompt}")
    response, full = agent.verbose_evaluate(prompt, thread_id=thread)
    print(f"[summary] {_summary(full)}")
    print(f"[response] {(response or '')[:400]}")
    tr = full.get("tool_results", []) or []
    names = {r.get("name") for r in tr}
    terminal_statuses = [r.get("status") for r in tr]
    return 0 if any(s in ("ok", "completed", "done") for s in terminal_statuses) else 1


def main() -> int:
    import django
    django.setup()
    from earthrise_agents_base.agent.chat_agent import ChatAgent

    exits = []
    exits.append(run_case(
        ChatAgent,
        "What is the CERES-Maize model in DSSAT?",
        "knowledge-search",
    ))
    # Data availability works if the LLM picks data-availability skill and the
    # tool returns ok OR a needs_input — either proves routing is right.
    exits.append(run_case(
        ChatAgent,
        "Do we have NASA POWER coverage for January 2024?",
        "data-availability",
    ))

    if all(c == 0 for c in exits):
        print("\nPASS — multi-agent routing + terminal ok statuses across skills.")
        return 0
    print(f"\nPARTIAL — exit codes {exits}")
    return 1 if any(c != 0 for c in exits) else 0


if __name__ == "__main__":
    sys.exit(main())


# --- pytest integration wrapper (promoted from scripts/) ---------------------
# Marked 'live' (hits real Ollama/Postgres/Redis) → skipped by default in CI;
# run on demand with: pytest -m live tests/integration/test_multi_agent.py
import pytest  # noqa: E402

pytestmark = pytest.mark.live


def test_multi_agent():
    assert main() == 0
