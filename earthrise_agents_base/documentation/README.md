# earthrise_agents_base

The **framework** app. Reusable infrastructure for building agent-based
chat products against sub-agent capability modules. Contains **no**
product-specific (DSSAT / knowledge / data) knowledge.

## What it provides

- `agent/` — `ChatAgent`, `ConversationMemory`, LangGraph state machine
  (`intake → react_loop ⇄ tool_executor → respond`)
- `react/` (via `agent/chat_agent.py`) — the ReAct loop with tool_call
  parsing fallbacks (`_route_after_react`, `_try_parse_tool_call_from_content`)
- `envelope/` (via `agent/direct_result.py`, `agent/progress.py`) —
  response envelope rendering + SSE progress publishing
- `schemas/tool_schema.py` — Pydantic → OpenAI-tool-schema builder with
  dynamic enum injection and llama3.1 `$ref`-inlining compat
- `agent/clients.py`, `agent/discovery.py` — generic sub-agent HTTP
  clients + agent discovery
- `agent/subagent_executor.py` — dispatcher that routes tool calls to
  sub-agents
- `templates/shell/_base_skeleton.html` — the actual base page template
  (nav, chrome, design tokens)
- `templates/shell/base.html` — default shell entry point extending the
  skeleton (product apps override this — see `template_conventions.md`)
- `models.py` — `Chat`, `Message`, `MessageFeedback` (persistence layer;
  used by any product built on this framework)

## What it does NOT provide

- No DSSAT vocabulary
- No knowledge tenant registration
- No product branding (design tokens neutral, no logo)
- No hardcoded skill allowlists

Those all live in a **product shell** app (e.g. `dssat_chat_agent/`)
that wires this framework to sub-agents. See
[`writing_a_product_shell.md`](writing_a_product_shell.md).

## App layout

```
earthrise_agents_base/
├── apps.py                          # AppConfig — no product wiring
├── models.py                        # Chat, Message, MessageFeedback
├── agent/                           # ChatAgent orchestrator + LangGraph nodes + ReAct
│   ├── chat_agent.py                # ChatAgent (instantiated directly; no subclass hook)
│   ├── clients.py, discovery.py     # sub-agent HTTP + discovery
│   ├── direct_result.py             # envelope renderers
│   ├── progress.py                  # SSE progress publisher
│   ├── skill_loader.py              # composable skill discovery
│   └── subagent_executor.py         # tool_executor dispatch
├── schemas/tool_schema.py           # Pydantic → tool schema builder
├── templates/
│   ├── shell/                       # cross-app design system entry
│   │   ├── _base_skeleton.html      # actual skeleton (skeleton pattern)
│   │   └── base.html                # default entry — extends skeleton
│   └── earthrise_agents_base/       # framework's own product pages
│       ├── chat.html, home.html, feedback_management.html
│       └── partials/nav.html, feedback_widget.html
├── static/earthrise_agents_base/
│   ├── css/tokens.css               # generic design tokens (:root custom properties)
│   ├── css/chat.css                 # chat styles (consumes tokens via var(--…))
│   └── js/                          # generic chat page JS
├── tests/
│   ├── conftest.py                  # framework fixtures (mocked LLMs)
│   ├── test_react_helpers.py        # loop mechanics
│   └── test_intake_node.py          # intake node contract
├── urls.py                          # framework URLs (chat page, feedback)
├── tasks.py                         # Celery task for chat processing
└── documentation/                   # you are here
```

## Inheritance for other apps

**Every sub-agent app imports from earthrise_agents_base**:
- The `AgentCard` + `SkillTable` primitives (post-PR-3.3)
- `build_tool_schema` for tool descriptions
- Optional: envelope helpers, progress sink, if the sub-agent emits SSE

**Every product shell app imports from earthrise_agents_base**:
- Templates: extend/override `shell/base.html` for branded shell inheritance
- (The shell does **not** subclass `ChatAgent` — there is no `allowed_skills`
  hook; `ChatAgent` is instantiated directly by the framework. The reference
  shell `dssat_chat_agent` only registers its knowledge tenant and overrides
  templates.)

**Nothing** in earthrise_agents_base imports from:
- Product shell apps (`dssat_chat_agent`, etc.)
- Sub-agent apps (`dssat_agent`, `data_agent`, `knowledge_agent`)

Enforced by CI:
```
! grep -rn "^from dssat_chat_agent\|^from dssat_agent\|^from data_agent\|^from knowledge_agent" earthrise_agents_base/
```

## Related docs

- [`template_conventions.md`](template_conventions.md) — the `shell/base.html` override pattern
- [`branding_and_overrides.md`](branding_and_overrides.md) — rebranding: blocks, design tokens, product name
- [`writing_a_subagent.md`](writing_a_subagent.md) — building a new sub-agent
- [`writing_a_product_shell.md`](writing_a_product_shell.md) — building a new product like `dssat_chat_agent`
- [`settings_reference.md`](settings_reference.md) — every framework-read setting
- [`branding_and_overrides.md`](branding_and_overrides.md) — theming & product overrides
- [`horizon-design-system.md`](horizon-design-system.md) — the NASA Horizon design tokens
