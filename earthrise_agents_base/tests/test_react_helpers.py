"""
Baseline tests for the ReAct routing + tool-call parsing helpers.

These helpers — ``_route_after_react`` and
``_try_parse_tool_call_from_content`` — live in a framework subpackage.
The tests lock their observable contract regardless of where the code
physically lives.

Deliberately narrow: no LangGraph, no LLM, no Django DB. Just the two
helpers exercised directly.
"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage


# ---------------------------------------------------------------------------
# _route_after_react
# ---------------------------------------------------------------------------

class TestRouteAfterReact:
    """After the LLM runs: pending tool calls → tool_executor; else → respond."""

    def test_empty_state_routes_to_respond(self, chat_agent, canonical_state_factory):
        state = canonical_state_factory(messages_lc=[])
        assert chat_agent._route_after_react(state) == "respond"

    def test_no_tool_calls_routes_to_respond(self, chat_agent, canonical_state_factory):
        state = canonical_state_factory(
            messages_lc=[AIMessage(content="Just answering directly, no tools.")],
            iteration=1,
        )
        assert chat_agent._route_after_react(state) == "respond"

    def test_tool_calls_route_to_executor(self, chat_agent, canonical_state_factory):
        state = canonical_state_factory(
            messages_lc=[
                AIMessage(
                    content="",
                    tool_calls=[{"name": "fetch_data", "args": {}, "id": "tc1"}],
                ),
            ],
            iteration=1,
        )
        assert chat_agent._route_after_react(state) == "tool_executor"

    def test_max_iteration_cap_drops_to_respond_even_with_tool_calls(
        self, chat_agent, canonical_state_factory,
    ):
        """Guardrail: once we hit react_max_iterations, we stop looping
        regardless of what the LLM wants. This is the invariant that
        prevents runaway ReAct sessions."""
        state = canonical_state_factory(
            messages_lc=[
                AIMessage(
                    content="",
                    tool_calls=[{"name": "fetch_data", "args": {}, "id": "tc99"}],
                ),
            ],
            iteration=chat_agent.react_max_iterations,
        )
        assert chat_agent._route_after_react(state) == "respond"

    def test_multiple_tool_calls_still_route_to_executor(
        self, chat_agent, canonical_state_factory,
    ):
        state = canonical_state_factory(
            messages_lc=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {"name": "fetch_data", "args": {}, "id": "tc1"},
                        {"name": "check_availability", "args": {}, "id": "tc2"},
                    ],
                ),
            ],
            iteration=2,
        )
        assert chat_agent._route_after_react(state) == "tool_executor"


# ---------------------------------------------------------------------------
# _try_parse_tool_call_from_content
# ---------------------------------------------------------------------------

class TestTryParseToolCallFromContent:
    """Fallback parser: extracts a tool call the LLM emitted as prose JSON
    instead of via the structured tool_calls channel. Non-negotiable
    behavior — llama3.1 does this frequently, and losing this parser
    silently drops turns."""

    KNOWN = {"fetch_data", "load_skill", "list_capabilities"}

    def test_empty_content_returns_none(self, chat_agent):
        assert chat_agent._try_parse_tool_call_from_content(
            "", self.KNOWN, iteration=0,
        ) is None

    def test_plain_prose_returns_none(self, chat_agent):
        assert chat_agent._try_parse_tool_call_from_content(
            "Here is my answer.", self.KNOWN, iteration=0,
        ) is None

    def test_parameters_shape(self, chat_agent):
        content = '{"name": "fetch_data", "parameters": {"source": "chirps"}}'
        result = chat_agent._try_parse_tool_call_from_content(
            content, self.KNOWN, iteration=0,
        )
        assert result is not None
        assert result["name"] == "fetch_data"
        assert result["args"] == {"source": "chirps"}

    def test_arguments_shape(self, chat_agent):
        content = '{"name": "fetch_data", "arguments": {"source": "chirps"}}'
        result = chat_agent._try_parse_tool_call_from_content(
            content, self.KNOWN, iteration=0,
        )
        assert result is not None
        assert result["name"] == "fetch_data"
        assert result["args"] == {"source": "chirps"}

    def test_args_shape(self, chat_agent):
        content = '{"name": "fetch_data", "args": {"source": "chirps"}}'
        result = chat_agent._try_parse_tool_call_from_content(
            content, self.KNOWN, iteration=0,
        )
        assert result is not None
        assert result["name"] == "fetch_data"
        assert result["args"] == {"source": "chirps"}

    def test_code_fenced_json_is_stripped(self, chat_agent):
        content = '```json\n{"name": "fetch_data", "args": {"source": "chirps"}}\n```'
        result = chat_agent._try_parse_tool_call_from_content(
            content, self.KNOWN, iteration=0,
        )
        assert result is not None
        assert result["name"] == "fetch_data"

    def test_prose_prefix_before_json_still_parses(self, chat_agent):
        content = 'Sure! Here is the call: {"name": "fetch_data", "args": {"source": "chirps"}}'
        result = chat_agent._try_parse_tool_call_from_content(
            content, self.KNOWN, iteration=0,
        )
        assert result is not None
        assert result["name"] == "fetch_data"

    def test_recovers_from_missing_closing_brace(self, chat_agent):
        """llama3.1 sometimes truncates the closing brace; the parser
        retries with one appended."""
        content = '{"name": "fetch_data", "args": {"source": "chirps"}'
        result = chat_agent._try_parse_tool_call_from_content(
            content, self.KNOWN, iteration=0,
        )
        assert result is not None
        assert result["name"] == "fetch_data"
