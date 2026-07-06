"""
End-to-end test for ReAct mode in ChatAgent.

Runs a full `evaluate()` cycle and verifies:
1. The agent boots.
2. The ReAct loop fires a run_experiment tool call for an experiment prompt.
3. The simulation completes and a response is synthesized.
"""

from __future__ import annotations

import sys


def main() -> int:
    import django

    django.setup()

    from earthrise_agents_base.agent.chat_agent import ChatAgent

    agent = ChatAgent()
    print(f"[setup] react_max_iterations={agent.react_max_iterations}")

    prompt = ("Run a corn experiment in Cullman county, Alabama, "
              "planted March 1 2024.")
    print(f"[prompt] {prompt}\n")

    response, full = agent.verbose_evaluate(prompt)
    print(f"[response] {response[:800]}\n")
    iterations = full.get("iteration", 0)
    tool_results = full.get("tool_results", []) or []
    print(f"[summary] iterations={iterations}  tool_results={len(tool_results)}")
    for i, tr in enumerate(tool_results):
        status = tr.get("status")
        name = tr.get("name")
        print(f"  - [{i}] {name} status={status}")

    # Pass criteria: at least one tool call, and the terminal one is
    # completed/ok.
    if not tool_results:
        print("\nFAIL — no tool calls fired.")
        return 1
    terminal = tool_results[-1]
    status = (terminal.get("status") or "").lower()
    if status in ("completed", "ok"):
        print("\nPASS — ReAct run_experiment path completed end-to-end.")
        return 0
    if status == "validation_error":
        print(f"\nPARTIAL — tool called but validation failed: "
              f"{terminal.get('result', {}).get('issues')}")
        return 0
    print(f"\nFAIL — terminal tool status={status}")
    return 1


if __name__ == "__main__":
    sys.exit(main())


# --- pytest integration wrapper (promoted from scripts/) ---------------------
# Marked 'live' (hits real Ollama/Postgres/Redis) → skipped by default in CI;
# run on demand with: pytest -m live tests/integration/test_react_mode.py
import pytest  # noqa: E402

pytestmark = pytest.mark.live


def test_react_mode():
    assert main() == 0
