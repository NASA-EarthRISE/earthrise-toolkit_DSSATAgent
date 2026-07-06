# Testing guide

Framework testing patterns, fixtures, and mock strategies. Every test
in this project follows these conventions.

## Test infrastructure

- `pytest` + `pytest-django` (installed in `requirements.txt`)
- Root `pytest.ini` declares `DJANGO_SETTINGS_MODULE` and testpaths
- Tests live in each app's `tests/` directory
- Custom markers: `a2a_contract`, `chat_routing`, `integration`,
  `live`, `slow`
- Default `addopts` excludes `live` and `slow` — CI runs the fast
  suite; live tests hit real Ollama/Postgres/Redis

## Where tests live

| App | Test scope |
|---|---|
| `earthrise_agents_base/tests/` | ReAct loop, intake node, tool schema, envelope — framework mechanics only |
| `dssat_chat_agent/tests/` | Tenant registration, template override, product-level assembly |
| `data_agent/tests/` | A2A contract, sub-agent integration, ETL unit tests |
| `knowledge_agent/tests/` | A2A contract, retrieval strategies |
| `dssat_agent/tests/` | Tool registry, wizard submit, DSSAT-specific integration |

## Fixtures (framework)

Provided by `earthrise_agents_base/tests/conftest.py`:

### `_isolate_ollama` (autouse)

Patches `OllamaLLM` and `ChatOllama` on the `chat_agent` module so
no test can accidentally reach a live Ollama server. Fires
automatically — you don't opt in.

### `chat_agent`

Fresh `ChatAgent` instance with `self.llm` and `self.chat_llm`
replaced by `MagicMock`. Configure the mocks per-test:

```python
def test_something(chat_agent):
    chat_agent.chat_llm.bind_tools.return_value.invoke.return_value = AIMessage(
        content="", tool_calls=[{"name": "foo", "args": {}, "id": "tc1"}],
    )
    # ... exercise chat_agent methods ...
```

### `scripted_llm`

Factory for scripting a sequence of `AIMessage` responses:

```python
def test_react_iterations(chat_agent, scripted_llm, canonical_state_factory):
    scripted_llm(
        chat_agent.chat_llm,
        responses=[
            AIMessage(content="", tool_calls=[
                {"name": "fetch_data", "args": {"source": "chirps"}, "id": "tc1"},
            ]),
            AIMessage(content="Final answer."),
        ],
    )
    state = canonical_state_factory(user_query="Get data")
    chat_agent._react_node(state)
    # first invoke consumed the first response
```

### `mock_subagent_dispatch`

Intercepts `earthrise_agents_base.agent.subagent_executor.execute_task`. Every
sub-agent invocation is recorded so tests can assert what was called:

```python
def test_routes_to_data(chat_agent, mock_subagent_dispatch):
    # ... trigger a tool call ...
    calls = mock_subagent_dispatch.calls
    assert calls[0].agent == "data"
    assert calls[0].skill == "fetch_data"
```

### `canonical_state_factory`

Build a minimal `ChatAgentState` with sensible defaults:

```python
def test_route(chat_agent, canonical_state_factory):
    state = canonical_state_factory(user_query="test", iteration=3)
    assert chat_agent._route_after_react(state) == "respond"
```

## Test patterns

### A2A contract tests

Every sub-agent ships this shape. Snapshot the endpoint behavior;
PR 3.3 uses these as the safety net when consolidating A2A code.

```python
import json
import pytest
from django.test import Client


@pytest.fixture
def client():
    return Client()


@pytest.mark.a2a_contract
class TestAgentCard:
    def test_returns_200(self, client):
        response = client.get("/foo_agent/.well-known/agent-card.json")
        assert response.status_code == 200

    def test_advertises_expected_skills(self, client):
        card = client.get("/foo_agent/.well-known/agent-card.json").json()
        skill_ids = {s["id"] for s in card["skills"]}
        assert {"do_the_thing", "other_skill"}.issubset(skill_ids)


@pytest.mark.a2a_contract
class TestRPCErrors:
    def test_malformed_body(self, client):
        response = client.post(
            "/foo_agent/", data="not-json", content_type="application/json",
        )
        assert response.json()["error"]["code"] == -32700
```

Full reference: `data_agent/tests/test_a2a_contract.py`.

### ReAct loop unit tests

Test loop mechanics with a scripted LLM binding — no live Ollama, no
DB:

```python
def test_max_iter_cap(chat_agent, canonical_state_factory):
    state = canonical_state_factory(
        messages_lc=[
            AIMessage(content="", tool_calls=[{"name": "x", "args": {}, "id": "1"}]),
        ],
        iteration=chat_agent.react_max_iterations,
    )
    assert chat_agent._route_after_react(state) == "respond"
```

### Chat routing tests

Assert canonical prompts route to the expected sub-agent. These
tests live in **each sub-agent's** tests directory — the sub-agent
owns "when the chat invokes me correctly, I answer":

```python
# foo_agent/tests/test_chat_integration.py
def test_foo_query_reaches_this_agent(chat_agent, mock_subagent_dispatch,
                                       scripted_llm):
    scripted_llm(chat_agent.chat_llm, responses=[
        AIMessage(content="", tool_calls=[
            {"name": "do_the_thing", "args": {"query": "foo"}, "id": "tc1"},
        ]),
        AIMessage(content="Done."),
    ])
    chat_agent.evaluate("Do the foo thing please")
    calls = mock_subagent_dispatch.calls
    assert any(c.skill == "do_the_thing" for c in calls)
```

If `foo_agent` isn't in `INSTALLED_APPS` for a given deployment, this
test doesn't run (pytest only collects tests it finds).

### Template override tests

Product shells verify their branding wins template resolution:

```python
@pytest.mark.django_db
def test_shell_base_resolves_to_product_variant():
    from django.template.loader import get_template
    template = get_template("shell/base.html")
    origin = str(template.origin.name)
    assert "acme_chat_agent" in origin
```

Full reference: `dssat_chat_agent/tests/test_registration.py`.

## Skill content packs

If a sub-agent ships prompt content under `<agent>/skills/`, add a
smoke test that verifies each `SKILL.md` parses:

```python
def test_skill_packs_load():
    from earthrise_agents_base.agent.skill_loader import SkillLoader
    loader = SkillLoader("foo_agent/skills")
    for skill_name in ("foo-help", "foo-capabilities"):
        assert loader.get_skill_body(skill_name), (
            f"SKILL.md for {skill_name} is empty or unreadable"
        )
```

## Common pitfalls

- **Skipping `pytest-django` init.** The root `pytest.ini` declares
  `DJANGO_SETTINGS_MODULE`. If you invoke pytest with a different
  working directory or override the config file, Django settings
  won't load and every test that touches the ORM will error.
- **Reaching Ollama in tests.** The `_isolate_ollama` autouse fixture
  guards this. If you disable it accidentally (e.g. via
  `autouse=False`), you'll hang on network calls.
- **Missing `@pytest.mark.django_db`** for tests that touch the ORM.
  pytest-django raises `RuntimeError: Database access not allowed`.
- **Order-dependent test coupling.** Sub-agent tests should be
  self-contained — don't rely on tests from other apps running first
  to set up state.

## Running the suite

```bash
# Full baseline (no live services)
docker exec earthrise-web pytest -m "not live and not slow"

# One sub-agent
docker exec earthrise-web pytest foo_agent/tests/

# One marker
docker exec earthrise-web pytest -m a2a_contract

# One test
docker exec earthrise-web pytest foo_agent/tests/test_a2a_contract.py::TestAgentCard::test_returns_200 -v
```

## What next

- Every sub-agent's `test_a2a_contract.py` is a reference
  implementation — copy and adapt
- `earthrise_agents_base/tests/test_react_helpers.py` shows the
  mocked-LLM patterns
- `dssat_chat_agent/tests/test_registration.py` shows the
  template-override pattern
