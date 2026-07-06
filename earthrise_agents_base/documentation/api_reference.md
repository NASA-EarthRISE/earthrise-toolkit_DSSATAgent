# API reference

Public API for building sub-agents and product shells against the
framework. Every symbol here is stable across minor version bumps.

## `earthrise_agents_base.a2a`

### `AgentCard`

Pydantic model — the A2A discovery document served at
`.well-known/agent-card.json`.

```python
from earthrise_agents_base.a2a import AgentCard

card = AgentCard(
    name="FooAgent",
    description="What Foo does.",
    version="1.0.0",
    url=None,                              # optional public URL
    skills=skills.skill_definitions(),     # populated from SkillTable
    capabilities={"streaming": False},     # free-form dict
)
```

Serializes to the A2A protocol JSON shape via `card.to_json_dict()`. In practice
sub-agents build the card with the `build_agent_card(...)` factory (see
`dssat_agent/a2a.py`, `knowledge_agent/a2a.py`) rather than constructing
`AgentCard` directly.

### `SkillDefinition`

Pydantic model for one skill entry in the agent card.

```python
SkillDefinition(
    id="do_the_thing",              # required — stable identifier for external A2A callers
    name="Do The Thing",            # human-readable
    description="What it does.",
    input_schema=None,              # optional JSON Schema for the input dict
    output_schema=None,
    tags=[],
)
```

Usually constructed indirectly via `SkillTable.skill(...)` — you
don't build one by hand.

### `SkillTable`

Decorator-based skill registration for one sub-agent.

```python
skills = SkillTable()

@skills.skill(
    id="do_the_thing",
    name="Do The Thing",
    description="One-line description shown to the LLM.",
    input_schema=None,          # optional
    output_schema=None,         # optional
    tags=["read-only"],         # optional
)
def _handler(params: dict) -> dict:
    ...
    return result
```

The decorator returns the wrapped function unchanged (callable
directly for embedded / in-process use).

Public methods:

- `skills.dispatch(skill_id, params)` — execute the handler; raises
  `KeyError` if unknown
- `skills.has(skill_id)` — bool
- `skills.known_skills()` — sorted list of registered ids
- `skills.skill_definitions()` — list of `SkillDefinition` for the
  agent card

### `register_agent(*, name, card, skills)`

Register a sub-agent with the module-level registry. Called from
`AppConfig.ready()` or a sub-agent module's top-level. Idempotent —
re-registration with the same (card, skills) is silent.

```python
register_agent(name="foo", card=AGENT_CARD, skills=skills)
```

### `get_agent(name) -> (AgentCard, SkillTable)`

Fetch a registered agent's card + skills. Raises `KeyError` if the
name isn't registered. Used by the framework's generic A2A views;
you rarely call this directly.

### `iterate_agents()`

Yield `(name, card, skills)` for every registered agent — used by
the chat agent's tool registry builder.

### `build_urlpatterns(agent_name: str) -> list`

Return the three A2A URL patterns for an agent, bound to its name:

- `.well-known/agent-card.json` → `AgentCardView`
- `health` → `HealthView`
- `""` (empty path, POST) → `RPCView`

```python
# sub-agent's a2a.py
urlpatterns = build_urlpatterns(agent_name="foo")
```

## `earthrise_agents_base.agent`

### `ChatAgent`

The primary orchestrator class. It is instantiated directly
(`ChatAgent(memory=...)`) by `earthrise_agents_base/agent/tasks.py` and
`chat_agent.py::create_chat_agent` — there is **no** product-subclass /
`chat_agent_class` / `allowed_skills` mechanism in the current codebase. The tool
registry is built generically from all discovered agents' skills; scoping which
sub-agents participate in a turn is done per call via `enabled_agents`, not by a
product subclass.

```python
from earthrise_agents_base.agent.chat_agent import ChatAgent, create_chat_agent

agent = create_chat_agent(memory=memory)          # or ChatAgent(memory=memory)
```

Public methods:

- `agent.evaluate(message, *, thread_id="default", enabled_agents=None) -> str`
  — main entry point. Runs one turn end-to-end and returns the assistant's
  response as a string. `enabled_agents` scopes which sub-agents participate.
- `agent.verbose_evaluate(...)` — same, but returns `(response, state_dict)`
  for debugging.

Construction:

- `memory` — `ConversationMemory` instance (passed at `__init__`)
- The ReAct iteration cap comes from the `REACT_MAX_ITERATIONS` env var (via
  `load_config`), not a settings dict.

### `ChatAgentState`

Dataclass holding one turn's state. Sub-agent developers don't touch
this; product developers only touch it in tests via
`canonical_state_factory` (see the testing guide).

### `Memory` protocol

Any object implementing:

- `add_user_message(content: str) -> None`
- `add_assistant_message(content: str) -> None`
- `get_context_string() -> str`
- `messages: list` (dicts or objects with `.content`)

The framework ships `ConversationMemory` as the default. Products
that need extra fields (wizard state, turn tags) subclass it.

## `earthrise_agents_base.schemas.tool_schema`

### `build_tool_schema(model_cls, *, enum_providers=None, inline_refs=True)`

Convert a Pydantic model class into an OpenAI-tool-schema dict. The first
argument is `model_cls`; `enum_providers` and `inline_refs` are keyword-only.
Handles llama3.1's `$ref`-inlining requirement automatically.

```python
from earthrise_agents_base.schemas.tool_schema import build_tool_schema

class RunFooInput(BaseModel):
    crop: str
    location: str

schema = build_tool_schema(RunFooInput)
```

Optional `enum_providers` — dict of `field_path` → callable — lets
you inject enum choices from a live DB catalog at bind time (used by
the DSSAT wizard for cultivar/soil-profile dropdowns).

### `inject_enum_into_schema(schema, field_path, values)`

In-place mutation. Rarely called directly.

## Envelope & progress

There is no top-level `earthrise_agents_base.envelope` module; these symbols live
in the `agent` package and `schemas`:

### `synthesize_envelope_narrative(...)` — `earthrise_agents_base.agent.direct_result`

LLM-driven turn-summary generator. Composes envelope status + key
results + skill-specific formatting into a human-readable response.
Used by the respond node; sub-agents don't call this. (Envelope Pydantic
models live in `earthrise_agents_base.schemas.envelope`.)

### `set_progress_context(...)`, `publish_progress(...)` — `earthrise_agents_base.agent.progress`

Contextvar-based SSE progress publisher. Sub-agents that emit
progress during long-running skills call `publish_progress(...)`;
the framework handles the transport.

## Deprecated / removed (PR 3.1)

The following were removed and MUST NOT be referenced:

- `StructuredExtractor` — deleted with the legacy plan/execute path
- `orchestration_mode` setting — deleted; react is the only mode
- `use_intent_routing` setting — deleted with the extractor
- `_orchestrator_node` method name — renamed to `_intake_node`
- `_route_from_orchestrator_react` — renamed to `_route_from_intake`
- `_respond_node_react` — renamed to `_respond_node`
