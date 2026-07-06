"""
Baseline tests for the intake node + its downstream router.

The intake node is ``_intake_node`` and its router
``_route_from_intake``; react is the only routing path (there are no
``orchestration_mode`` / ``use_intent_routing`` flags).

Locks the invariants:

  1. Every user turn syncs the message into ConversationMemory before
     anything else runs. Losing this breaks conversation continuity.
  2. The intake node returns TASK intent + full confidence and does
     no LLM classification. The ReAct loop, not intake, decides what
     to do with the turn.
  3. ``_route_from_intake`` short-circuits to respond when intake
     precomputed missing params to ask about; otherwise every query
     enters the react loop.
"""

from __future__ import annotations

import pytest


class TestIntakeNode:
    """The intake node — memory sync + default routing state."""

    def test_syncs_user_query_into_memory(self, chat_agent, canonical_state_factory):
        state = canonical_state_factory(user_query="What is DSSAT?")

        chat_agent._intake_node(state)

        # Memory stores messages as dicts {"role": "user"|"assistant",
        # "content": "..."}. Assert on the dict shape.
        messages = chat_agent.memory.messages
        user_contents = [
            m.get("content", "") if isinstance(m, dict) else getattr(m, "content", "")
            for m in messages
        ]
        assert "What is DSSAT?" in user_contents, (
            f"Expected user message in memory after intake; got {user_contents}"
        )

    def test_default_intent_is_task(self, chat_agent, canonical_state_factory):
        """The whole point of the intake node: the ReAct loop, not
        intake, decides what to do. Intake short-circuits to TASK +
        full confidence and lets the loop take over."""
        from earthrise_agents_base.agent.chat_agent import ChatIntent

        state = canonical_state_factory(user_query="anything")

        result = chat_agent._intake_node(state)

        assert result["intent"] == ChatIntent.TASK
        assert result["confidence"] == 1.0


class TestRouteFromIntake:
    """The routing decision after intake."""

    def test_pending_clarification_short_circuits_to_respond(
        self, chat_agent, canonical_state_factory,
    ):
        """When intake pre-computed missing params to ask the user
        about, we skip the loop and let respond formulate the question."""
        state = canonical_state_factory(pending_clarification=["planting_date"])

        assert chat_agent._route_from_intake(state) == "respond"

    def test_default_routes_into_the_loop(self, chat_agent, canonical_state_factory):
        state = canonical_state_factory()
        assert chat_agent._route_from_intake(state) == "react_loop"
