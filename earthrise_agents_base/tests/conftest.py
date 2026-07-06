"""
Shared fixtures for chat agent baseline tests.

These fixtures provide a live safety net for the chat-agent baseline
tests. Every fixture here mocks external I/O
(Ollama, Redis, sub-agent HTTP calls) so tests run without any
infrastructure — just Python + Django + SQLite fallback.

Key mocking strategy:
- OllamaLLM and ChatOllama are patched at module import time on
  earthrise_agents_base.agent.chat_agent so ChatAgent.__init__ doesn't try to reach a
  real Ollama server. Individual tests then inject scripted responses
  onto agent.chat_llm.bind_tools(...).invoke.
- The LangGraph checkpointer falls back to MemorySaver when no
  Postgres connection is available (already the existing behavior);
  tests do not touch the checkpointer directly.
- Sub-agent HTTP clients (EmbeddedClient / RemoteClient in
  chat.agent.clients) are patched per-test when needed via the
  `mock_subagent_dispatch` fixture.
"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock

# Imported lazily-tolerant so the pure-filesystem lint (test_static_hygiene.py)
# in this same directory can be collected without the LLM stack installed. The
# fixtures below that use AIMessage require langchain anyway.
try:
    from langchain_core.messages import AIMessage
except ImportError:  # pragma: no cover - only when langchain isn't installed
    AIMessage = None


@pytest.fixture(autouse=True)
def _isolate_ollama(monkeypatch):
    """Prevent every test from reaching a live Ollama.

    Autouse so a test that forgets to opt in still doesn't hang on
    network I/O. Tests that want to script LLM behavior use the
    `chat_agent` fixture below and configure agent.chat_llm directly.
    """
    from unittest.mock import MagicMock as _MM

    monkeypatch.setattr("earthrise_agents_base.agent.chat_agent.OllamaLLM", _MM)
    monkeypatch.setattr("earthrise_agents_base.agent.chat_agent.ChatOllama", _MM)


@pytest.fixture
def chat_agent(monkeypatch):
    """Fresh ChatAgent with mocked LLMs and a fresh in-process memory.

    Does NOT set up a Django test DB — none of the chat-agent tests
    should reach the ORM. If a test needs DB access, mark it
    ``@pytest.mark.django_db`` and it will use the pytest-django DB
    fixture (SQLite fallback per chat_agent/settings.py:92).
    """
    from earthrise_agents_base.agent.chat_agent import ChatAgent

    agent = ChatAgent()
    # Replace the auto-mocked LLMs with explicit MagicMocks so tests
    # can configure return values naturally.
    agent.llm = MagicMock(name="OllamaLLM")
    agent.chat_llm = MagicMock(name="ChatOllama")
    return agent


@pytest.fixture
def scripted_llm():
    """Factory that scripts an LLM to return AIMessages on successive calls.

    Usage:
        def test_something(chat_agent, scripted_llm):
            scripted_llm(
                chat_agent.chat_llm,
                responses=[
                    AIMessage(content="", tool_calls=[
                        {"name": "fetch_data", "args": {...}, "id": "tc1"},
                    ]),
                    AIMessage(content="Final answer."),
                ],
            )
    """

    def _script(chat_llm: MagicMock, *, responses: list[AIMessage]) -> MagicMock:
        llm_with_tools = MagicMock(name="chat_llm_with_tools")
        llm_with_tools.invoke.side_effect = list(responses)
        chat_llm.bind_tools.return_value = llm_with_tools
        return llm_with_tools

    return _script


@pytest.fixture
def mock_subagent_dispatch(monkeypatch):
    """Intercept every call the chat_agent would make to a sub-agent.

    Records (agent_name, skill_name, params) tuples so tests can assert
    what the chat agent tried to invoke without needing sub-agent apps
    to actually execute.

    Usage:
        def test_route(chat_agent, mock_subagent_dispatch):
            ...
            calls = mock_subagent_dispatch.calls
            assert calls[0].agent == "data"
            assert calls[0].skill == "fetch_data"
    """
    from types import SimpleNamespace

    calls: list[SimpleNamespace] = []

    def _fake_execute_task(agent_name: str, skill: str, params: dict, **kwargs):
        calls.append(SimpleNamespace(agent=agent_name, skill=skill, params=params, kwargs=kwargs))
        return {"status": "ok", "result": {"stub": True}}

    # subagent_executor.execute_task is the seam the tool_executor node
    # dispatches through. Patching it here catches every sub-agent
    # invocation regardless of the underlying client (embedded vs remote).
    monkeypatch.setattr(
        "earthrise_agents_base.agent.subagent_executor.execute_task",
        _fake_execute_task,
        raising=False,
    )
    return SimpleNamespace(calls=calls)


@pytest.fixture
def canonical_state_factory():
    """Build a minimal ChatAgentState suitable for exercising a node.

    Callers override fields as needed:
        state = canonical_state_factory(user_query="What is DSSAT?")
    """
    from earthrise_agents_base.agent.chat_agent import ChatAgentState

    def _build(**overrides) -> ChatAgentState:
        defaults = {
            "user_query": "test query",
            "messages": [],
            "messages_lc": [],
            "params": {},
            "intent": None,
            "confidence": 0.0,
            "current_extraction": {},
            "pending_clarification": [],
            "turn_count": 0,
            "response": "",
            "wizard_params": {},
            "chat_id": "test-thread",
            "user_id": None,
            "iteration": 0,
            "pending_tool_context": {},
            "active_skills": [],
            "loaded_skill_bodies": {},
            "enabled_agents": ["dssat", "data", "knowledge"],
            "tool_results": [],
        }
        defaults.update(overrides)
        return ChatAgentState(**defaults)

    return _build
