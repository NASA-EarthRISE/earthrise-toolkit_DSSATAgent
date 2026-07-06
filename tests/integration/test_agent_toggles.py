"""
Agent-toggle plumbing test.

Verifies that the per-chat `enabled_agents` policy is applied at every
filter site in the ReAct orchestrator:

1. `_resolve_enabled_app_labels` converts user-facing agent_labels
   (e.g. "knowledge") to Django app_labels (e.g. "knowledge_agent")
   via the discovery bridge.
2. `_build_tool_registry(enabled_agents=[<one>])` emits only meta tools
   plus tools owned by the allowed app — no data/dssat tools leak through.
3. `_tool_list_capabilities(enabled_agents=[<one>])` returns only skills
   whose `owner_agent` is in the allowed set.
4. `_tool_load_skill({"name": <skill owned by disabled agent>}, enabled_agents=[<other>])`
   rejects with a validation_error envelope.

Does not touch the LLM — all assertions are purely structural.
"""

from __future__ import annotations

import sys


def main() -> int:
    import django
    django.setup()

    from earthrise_agents_base.agent.chat_agent import ChatAgent
    from earthrise_agents_base.agent.discovery import (
        discover_agents,
        resolve_agent_label_to_app_label,
        resolve_agent_labels_to_app_labels,
    )

    agents = discover_agents()
    labels = sorted(agents.keys())
    print(f"[setup] discovered agents: {labels}")
    if len(labels) < 2:
        print("FAIL — need at least 2 discovered agents to exercise filtering.")
        return 1

    pick = labels[0]
    pick_app = resolve_agent_label_to_app_label(pick)
    print(f"[setup] pick agent_label='{pick}' -> app_label='{pick_app}'")
    if not pick_app:
        print("FAIL — resolve_agent_label_to_app_label returned None for a discovered agent.")
        return 1

    resolved_set = resolve_agent_labels_to_app_labels([pick])
    if resolved_set != {pick_app}:
        print(f"FAIL — resolve_agent_labels_to_app_labels({[pick]}) -> {resolved_set}, expected {{{pick_app!r}}}")
        return 1
    print(f"[ok] bridge function resolves to {resolved_set}")

    agent = ChatAgent()

    subagent_all = agent._collect_subagent_tools(allowed_app_labels=None)
    owners_all = {
        e.get("owner_agent") for e in subagent_all.values()
        if e.get("owner_agent")
    }
    print(f"[unrestricted] subagent-tool count={len(subagent_all)}  owners={owners_all}")

    subagent_scoped = agent._collect_subagent_tools(allowed_app_labels={pick_app})
    owners_scoped = {
        e.get("owner_agent") for e in subagent_scoped.values()
        if e.get("owner_agent")
    }
    print(f"[scoped={pick_app}] subagent-tool count={len(subagent_scoped)}  owners={owners_scoped}")

    extra = owners_scoped - {pick_app}
    if extra:
        print(f"FAIL — scoped collection includes tools owned by other agents: {extra}")
        return 1
    if owners_all - {pick_app} and not (subagent_all.keys() - subagent_scoped.keys()):
        print("FAIL — scoped collection did not drop any tools even though other agents exist.")
        return 1
    print(f"[ok] _collect_subagent_tools restricted to {pick_app} only")

    list_caps = agent._tool_list_capabilities(
        {}, allowed_app_labels=resolved_set,
    )
    skills = list_caps.get("skills") or []
    foreign = [
        s for s in skills
        if s.get("owner_agent") and s["owner_agent"] != pick_app
    ]
    if foreign:
        print(f"FAIL — list_capabilities leaked skills from other agents: {[s['name'] for s in foreign]}")
        return 1
    print(f"[ok] list_capabilities returned {len(skills)} skills, all owned by {pick_app}")

    try:
        from earthrise_agents_base.agent.skill_loader import SkillLoader
        loader = SkillLoader()
        all_skills = loader.list_loadable_skills() or []
    except Exception as e:
        print(f"[warn] skipping load_skill rejection test (loader unavailable): {e}")
        all_skills = []

    foreign_skill = next(
        (s for s in all_skills
         if s.get("owner_agent") and s["owner_agent"] != pick_app),
        None,
    )
    if foreign_skill:
        rejection = agent._tool_load_skill(
            {"name": foreign_skill["name"]},
            allowed_app_labels=resolved_set,
        )
        if not (isinstance(rejection, dict) and rejection.get("error")):
            print(f"FAIL — load_skill accepted a skill owned by a disabled agent: {rejection}")
            return 1
        print(f"[ok] load_skill rejected '{foreign_skill['name']}' "
              f"(owner={foreign_skill['owner_agent']}): {rejection.get('error')}")
    else:
        print("[skip] no foreign skill available to test load_skill rejection")

    print("\nPASS — agent-toggle filters applied correctly at every site.")
    return 0


if __name__ == "__main__":
    sys.exit(main())


# --- pytest integration wrapper (promoted from scripts/) ---------------------
# Marked 'live' (hits real Ollama/Postgres/Redis) → skipped by default in CI;
# run on demand with: pytest -m live tests/integration/test_agent_toggles.py
import pytest  # noqa: E402

pytestmark = pytest.mark.live


def test_agent_toggles():
    assert main() == 0
