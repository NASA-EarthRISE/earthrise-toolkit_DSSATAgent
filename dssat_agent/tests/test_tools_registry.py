"""
Baseline tests for dssat_agent's embedded tool exposure surface.

dssat_agent does not currently expose an A2A HTTP endpoint — it's
embedded-only. That means the analog of an "A2A contract" is the
``TOOL_REGISTRY`` dict in dssat_agent/tools.py, which the chat agent
discovers via the generic ``_build_tool_registry_for_state`` helper.

The skill names in ``TOOL_REGISTRY`` and their input schemas are a
contract the chat agent binds against — it needs ``run_experiment``
(etc.) as callable tools with a stable input shape. This test locks
the current shape.

Focused set:
  1. TOOL_REGISTRY exposes the three documented tools.
  2. Each entry has the required keys (description, schema_builder,
     func, owner_agent).
  3. Each schema builder returns a valid JSON-Schema-shaped dict.
  4. All tools declare owner_agent="dssat_agent" for the enabled_agents
     filter to work.
"""

from __future__ import annotations

import pytest


EXPECTED_TOOLS = {"run_experiment", "experiment_wizard_step", "query_experiment"}


@pytest.mark.a2a_contract
class TestToolRegistry:

    def test_registry_exposes_expected_tools(self):
        from dssat_agent.tools import TOOL_REGISTRY
        assert EXPECTED_TOOLS.issubset(set(TOOL_REGISTRY.keys())), (
            f"Missing tools: {EXPECTED_TOOLS - set(TOOL_REGISTRY.keys())}"
        )

    @pytest.mark.parametrize("tool_name", sorted(EXPECTED_TOOLS))
    def test_entry_has_required_keys(self, tool_name):
        from dssat_agent.tools import TOOL_REGISTRY
        entry = TOOL_REGISTRY[tool_name]
        assert "description" in entry
        assert "schema_builder" in entry
        assert "func" in entry
        assert "owner_agent" in entry

    @pytest.mark.parametrize("tool_name", sorted(EXPECTED_TOOLS))
    def test_owner_agent_is_dssat_agent(self, tool_name):
        """The enabled_agents filter in the chat tool registry keys off
        owner_agent. If this drifts, chat can silently drop dssat tools."""
        from dssat_agent.tools import TOOL_REGISTRY
        assert TOOL_REGISTRY[tool_name]["owner_agent"] == "dssat_agent"

    @pytest.mark.django_db
    @pytest.mark.parametrize("tool_name", sorted(EXPECTED_TOOLS))
    def test_schema_builder_returns_dict(self, tool_name):
        """The schema builder must return a dict — anything else fails
        when llama3.1 tries to bind the tool via LangChain.

        Marked django_db because run_experiment's builder injects
        dynamic enums (cultivars, crops) from the database.
        """
        from dssat_agent.tools import TOOL_REGISTRY
        schema = TOOL_REGISTRY[tool_name]["schema_builder"]()
        assert isinstance(schema, dict)

    @pytest.mark.parametrize("tool_name", sorted(EXPECTED_TOOLS))
    def test_description_is_nonempty_string(self, tool_name):
        """LangChain uses the description to help the LLM select
        tools. Empty descriptions cause silent selection failures."""
        from dssat_agent.tools import TOOL_REGISTRY
        desc = TOOL_REGISTRY[tool_name]["description"]()
        assert isinstance(desc, str)
        assert desc.strip(), "Tool description must not be empty"
