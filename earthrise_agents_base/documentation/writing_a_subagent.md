# Writing a sub-agent

This guide walks through building a new sub-agent capability module
from scratch, using the framework's `SkillTable` + `AgentCard` +
generic A2A views. It's the canonical reference — every sub-agent
in this project follows this pattern.

**Prereqs:** the sub-agent is a self-contained Django app that
delivers one domain capability (retrieval, simulation, weather
fetching, etc.). It does **not** implement chat logic or product
branding. Those live in `earthrise_agents_base` (framework) and in a
product shell app respectively.

## 1. Create the Django app

```
manage.py startapp foo_agent
```

Layout after scaffolding:

```
foo_agent/
├── __init__.py
├── admin.py
├── apps.py
├── migrations/
├── models.py
├── tests/
└── views.py       # optional — only if this sub-agent has explorer pages
```

## 2. Declare it in `INSTALLED_APPS`

Add it to the project's `settings.py` **after** the product shell and
`earthrise_agents_base`:

```python
INSTALLED_APPS = [
    ...,
    "acme_chat_agent",         # product shell — first
    "earthrise_agents_base",   # framework
    "foo_agent",               # sub-agent capability modules
    "bar_agent",
    ...,
]
```

## 3. Configure the `AppConfig`

The framework's URL discovery reads `AppConfig` attributes to mount
each sub-agent's endpoints. Set:

```python
# foo_agent/apps.py
from django.apps import AppConfig

class FooAgentConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "foo_agent"
    verbose_name = "Foo Agent"

    # Discovery metadata (read by dssat_chat_project/urls.py + discovery.py)
    # CONVENTION: agent_label MUST equal the app label (discovery.py standardizes
    # on this, and AGENT_CLIENTS / get_client key on agent_label). Use underscores,
    # not hyphens — the A2A URL mounts at /<a2a_prefix>/ and the real agents all use
    # the underscore app label (e.g. data_agent, dssat_agent, knowledge_agent).
    agent_label = "foo_agent"               # == app label; keys AGENT_CLIENTS/get_client
    display_name = "Foo"                    # human-readable name
    url_prefix = "foo"                      # sub-agent explorer URL prefix
    url_module = "foo_agent.urls"           # if the sub-agent has explorer pages
    a2a_url_module = "foo_agent.a2a"        # single-file A2A module (see step 5)
    a2a_prefix = "foo_agent"                # public A2A URL prefix → mounts at /foo_agent/
    skills_dir = "skills"                   # SKILL.md content dir (optional)
    nav_items = []
    home_cards = []
```

The `a2a_url_module` value points at a **single Python module**, not
a package — the file `foo_agent/a2a.py` (not `foo_agent/a2a/`). This
is the collapsed pattern established in PR 3.3.

## 4. Define your service functions

Every skill is backed by one pure Python function that takes a
`params` dict and returns a JSON-serializable result:

```python
# foo_agent/services.py
def do_the_thing(params: dict) -> dict:
    """Business logic — no HTTP, no Django ORM assumptions beyond
    what your models provide, no LLM calls."""
    query = params.get("query", "")
    # ... implementation ...
    return {"results": [...]}
```

## 5. Register skills + AgentCard in a single `a2a.py`

This is where the framework's `SkillTable`, `AgentCard`, and
`build_urlpatterns` come together:

```python
# foo_agent/a2a.py
"""foo_agent A2A surface — skills + agent card + URL patterns."""

from typing import Any, Dict

from earthrise_agents_base.a2a import (
    AgentCard,
    SkillTable,
    build_urlpatterns,
    register_agent,
)

skills = SkillTable()


@skills.skill(
    id="do_the_thing",
    name="Do The Thing",
    description="One-line description that ends up in the LLM tool prompt.",
)
def _do_the_thing(params: Dict[str, Any]) -> Any:
    from .services import do_the_thing
    return do_the_thing(params)


AGENT_CARD = AgentCard(
    name="FooAgent",
    description="Explains what this sub-agent does.",
    version="1.0.0",
    skills=skills.skill_definitions(),
    capabilities={},
)


register_agent(name="foo_agent", card=AGENT_CARD, skills=skills)


# URL patterns — mounted at whatever prefix the project urlconf chooses.
app_name = "foo_agent_a2a"
urlpatterns = build_urlpatterns(agent_name="foo_agent")
```

That's ~40 lines. The framework handles JSON-RPC 2.0 envelope framing,
skill dispatch, error codes, and NaN sanitization. You don't touch
any of it.

## 6. Test the contract

Every sub-agent should ship two baseline tests:

```python
# foo_agent/tests/test_a2a_contract.py
import json
import pytest
from django.test import Client

AGENT_CARD_URL = "/foo_agent/.well-known/agent-card.json"
RPC_URL = "/foo_agent/"


@pytest.fixture
def client():
    return Client()


@pytest.mark.a2a_contract
def test_agent_card_returns_200(client):
    response = client.get(AGENT_CARD_URL)
    assert response.status_code == 200


@pytest.mark.a2a_contract
def test_advertises_expected_skills(client):
    response = client.get(AGENT_CARD_URL)
    card = response.json()
    skill_ids = {s["id"] for s in card["skills"]}
    assert "do_the_thing" in skill_ids


@pytest.mark.a2a_contract
def test_rpc_dispatches_skill(client):
    response = client.post(
        RPC_URL,
        data=json.dumps({
            "jsonrpc": "2.0",
            "id": "t1",
            "method": "message/send",
            "params": {"message": {"parts": [
                {"type": "data", "data": {"skill": "do_the_thing", "params": {}}},
            ]}},
        }),
        content_type="application/json",
    )
    assert response.status_code == 200
    body = response.json()
    assert "result" in body
```

Add the sub-agent's tests dir to `pytest.ini`'s `testpaths`.

## 7. Add SKILL.md content packs (optional)

Some products drive the chat LLM with domain-specific prompt packs.
Add markdown files under `foo_agent/skills/`:

```
foo_agent/skills/
├── foo-help/SKILL.md
├── foo-capabilities/SKILL.md
└── foo-results/SKILL.md
```

The `SkillLoader` in `earthrise_agents_base/agent/skill_loader.py`
picks these up at chat startup. See
[testing_guide.md](testing_guide.md#skill-content-packs) for the
format.

## 8. Common pitfalls

- **Do not import from any other sub-agent.** Sub-agents are
  independent capability modules. If `foo_agent` needs data from
  `bar_agent`, use the A2A protocol or a shared service.
- **Do not import from a product shell.** The product decides which
  sub-agents to ship; sub-agents must not know which product they
  live inside.
- **Do not hardcode source names, tenant ids, or product vocabulary
  in your handler code.** Product-specific config comes from the
  product shell app or from settings.
- **Skill decorator ids must be stable.** External A2A clients call
  by id; renaming a skill is a breaking change.

## What next

- [`writing_a_product_shell.md`](writing_a_product_shell.md) — build
  a product that assembles sub-agents into a deployable chat product.
- [`api_reference.md`](api_reference.md) — the exact signature for
  every framework primitive you'll use.
- [`testing_guide.md`](testing_guide.md) — fixtures, mock strategies,
  contract testing patterns.
