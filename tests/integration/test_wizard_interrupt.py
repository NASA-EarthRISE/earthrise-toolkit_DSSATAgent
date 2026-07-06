"""
End-to-end test for the wizard interrupt flow.

Turn 1: vague prompt → LLM loads dssat-experiment-run → calls
        experiment_wizard_step with no args → tool returns needs_input →
        ReAct node fires `interrupt()` → graph pauses.
Turn 2: user supplies crop + location + planting_date → graph resumes →
        tool returns needs_input for shortcircuit fork → graph pauses again.
Turn 3: user replies "defaults" → graph resumes → wizard calls
        run_experiment → simulation completes → response synthesized.
"""

from __future__ import annotations

import sys
import uuid


def _print_snapshot(agent, thread_id: str, label: str) -> None:
    snapshot = agent.workflow.get_state({"configurable": {"thread_id": thread_id}})
    nxt = getattr(snapshot, "next", None) or ()
    print(f"[{label}] snapshot.next = {list(nxt)}")


def main() -> int:
    import django
    django.setup()

    from earthrise_agents_base.agent.chat_agent import ChatAgent

    thread_id = f"wizard-test-{uuid.uuid4().hex[:8]}"
    agent = ChatAgent()
    print(f"[setup] react_max_iterations={agent.react_max_iterations}  "
          f"thread_id={thread_id}\n")

    # ---------------------------------------------------------------
    # Turn 1: vague prompt → wizard asks for crop/location/date
    # ---------------------------------------------------------------
    print("=== Turn 1: vague prompt ===")
    prompt1 = "I want to run a crop experiment."
    print(f"[prompt] {prompt1}")
    response1, full1 = agent.verbose_evaluate(prompt1, thread_id=thread_id)
    print(f"[response] {(response1 or '')[:500]}\n")
    tr1 = full1.get("tool_results", []) or []
    print(f"[summary] iterations={full1.get('iteration', 0)}  tool_results={len(tr1)}")
    for tr in tr1:
        print(f"  - {tr.get('name')} status={tr.get('status')}")
    _print_snapshot(agent, thread_id, "turn1")

    snapshot = agent.workflow.get_state({"configurable": {"thread_id": thread_id}})
    if not snapshot.next:
        print("\nFAIL — expected graph to be paused at an interrupt after turn 1.")
        return 1
    print("  ✓ Graph is paused at an interrupt.\n")

    # ---------------------------------------------------------------
    # Turn 2: supply the core triple → wizard asks shortcircuit
    # ---------------------------------------------------------------
    print("=== Turn 2: supply crop + location + planting_date ===")
    prompt2 = "Corn in Cullman County, Alabama, planted March 1 2024."
    print(f"[prompt] {prompt2}")
    response2, full2 = agent.verbose_evaluate(prompt2, thread_id=thread_id)
    print(f"[response] {(response2 or '')[:500]}\n")
    tr2 = full2.get("tool_results", []) or []
    print(f"[summary] iterations={full2.get('iteration', 0)}  "
          f"tool_results={len(tr2)}")
    for tr in tr2:
        print(f"  - {tr.get('name')} status={tr.get('status')}")
    _print_snapshot(agent, thread_id, "turn2")

    snapshot = agent.workflow.get_state({"configurable": {"thread_id": thread_id}})
    if not snapshot.next:
        # It may have skipped the shortcircuit step and run directly — in
        # that case check for 'completed' instead.
        if any(t.get("status") == "completed" for t in tr2):
            print("  Note: wizard skipped shortcircuit step and ran "
                  "directly — acceptable.")
            print("\nPARTIAL PASS — interrupt → resume → simulation.")
            return 0
        print("\nFAIL — expected interrupt after turn 2 OR a completed "
              "run; got neither.")
        return 1
    print("  ✓ Graph is paused again (shortcircuit fork).\n")

    # ---------------------------------------------------------------
    # Turn 3: choose defaults → wizard runs experiment
    # ---------------------------------------------------------------
    print("=== Turn 3: choose shortcircuit=run with defaults ===")
    prompt3 = "Run with defaults."
    print(f"[prompt] {prompt3}")
    response3, full3 = agent.verbose_evaluate(prompt3, thread_id=thread_id)
    print(f"[response] {(response3 or '')[:600]}\n")
    tr3 = full3.get("tool_results", []) or []
    print(f"[summary] iterations={full3.get('iteration', 0)}  "
          f"tool_results={len(tr3)}")
    for tr in tr3:
        print(f"  - {tr.get('name')} status={tr.get('status')}")

    if any(t.get("status") == "completed" for t in tr3):
        print("\nPASS — wizard interrupt/resume cycle completed end-to-end.")
        return 0

    print("\nFAIL — no completed simulation after turn 3.")
    return 1


if __name__ == "__main__":
    sys.exit(main())


# --- pytest integration wrapper (promoted from scripts/) ---------------------
# Marked 'live' (hits real Ollama/Postgres/Redis) → skipped by default in CI;
# run on demand with: pytest -m live tests/integration/test_wizard_interrupt.py
import pytest  # noqa: E402

pytestmark = pytest.mark.live


def test_wizard_interrupt():
    assert main() == 0
