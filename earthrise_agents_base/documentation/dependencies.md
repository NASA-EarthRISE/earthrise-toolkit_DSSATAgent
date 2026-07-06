# Dependencies

`earthrise_agents_base` is the **framework** app. In the target
architecture it must depend on **nothing** in this monorepo except
Django itself. Everything else — sub-agents and product shells —
depends on it, never the other way around.

The framework never imports a sub-agent (`data_agent`,
`dssat_agent`, `knowledge_agent`) or a product shell
(`dssat_chat_agent`). Sub-agents integrate with the framework via
**AppConfig metadata discovery** — so the framework discovers plugins by
convention, not by importing them. The metadata is read by several consumers, not
all by `discovery.py`:

- `earthrise_agents_base/agent/discovery.py::discover_agents()` reads
  `agent_label`, `skills_dir`, `nav_items`, `plots_module`, `agent_icon`,
  `agent_color`.
- `dssat_chat_project/urls.py` reads `url_module`, `a2a_url_module`, `url_prefix`,
  `a2a_prefix` to mount the routes.
- `earthrise_agents_base/context_processors.py` reads `home_cards` and
  `management_items` for the UI.

Verified first-party import counts (production code, excluding
tests/migrations):

| Target | Import sites | Status |
|---|---|---|
| `accounts` | 0 | clean |
| `data_agent` | 0 | clean |
| `dssat_agent` | 0 | clean |
| `knowledge_agent` | 0 | clean |
| `dssat_chat_agent` | 0 | clean |

The framework imports **nothing** from any plugin or sub-agent. It
now **owns** the generic authz mixins in
`earthrise_agents_base/mixins.py` (`is_admin`, `is_agent_admin`,
`AdminRequiredMixin`, `AgentAdminMixin`, `AgentPermissionMixin`),
which it uses via relative imports and which `accounts`, `dssat_agent`,
and the framework itself consume.

## What `earthrise_agents_base` imports

| Depends on | How | Purpose |
|---|---|---|
| `django` (+ contrib) | Python import | ORM, views, templates, URL routing. |
| `celery`, `pydantic`, LangGraph | Python import | Chat task runner, tool schemas, ReAct state machine. |

The framework imports no plugin or sub-agent. Its own
`context_processors.py`, `views.py`, and `views_management.py` reach
the generic authz helpers via a relative `from .mixins import` —
they live inside the framework, not in any plugin.

## The generic authz mixins (framework-owned)

`earthrise_agents_base/mixins.py` **owns** the generic authz
primitives — `is_admin`, `is_agent_admin`, `AdminRequiredMixin`,
`AgentAdminMixin`, `AgentPermissionMixin`. The framework consumes
them internally through relative imports:

- `context_processors.py` — `is_admin`
- `views.py` — `AdminRequiredMixin`
- `views_management.py` — `AgentAdminMixin`

Plugins and sub-agents that need these helpers now import them
*from the framework* (`accounts` and `dssat_agent` both do), so
every arrow points the correct way: plugin/sub-agent → framework.
There is no wrong-direction edge left here.

## Depended on by

| Consumer | How |
|---|---|
| `accounts` | 1 import — authz mixins from `earthrise_agents_base.mixins` (a correct plugin → framework edge). |
| `data_agent` | 6 imports — A2A primitives (`SkillTable`, `AgentCard`, `build_urlpatterns`, `register_agent`). |
| `dssat_agent` | 23 imports — schemas, A2A clients, agent primitives, authz mixins. |
| `knowledge_agent` | 3 imports — A2A primitives. |
| `dssat_chat_agent` | Compositional — subclasses `ChatAgent`, shadows `shell/base.html`; see its `dependencies.md`. |

Enforced by CI (see `README.md`):

```
! grep -rn "^from dssat_chat_agent\|^from dssat_agent\|^from data_agent\|^from knowledge_agent" earthrise_agents_base/
```

The framework imports no plugin, so there is no `accounts` edge
for the guard to exclude — every first-party dependency arrow
already points *into* the framework.

## Database isolation

No cross-app database foreign keys. `earthrise_agents_base` owns
the chat persistence layer (`Chat`, `Message`, `MessageFeedback`);
every FK is within-app or to `settings.AUTH_USER_MODEL`. No FK
points into a sub-agent's or plugin's tables.

## Dependency diagram

```
   ┌──────────────────────────────────────────────────────┐
   │  product shells   plugin      sub-agents               │
   │  (dssat_chat_     (accounts)  (data_/dssat_/           │
   │   agent)                       knowledge_agent)        │
   └──────────┬────────────┬──────────────┬────────────────┘
              │ import      │ import (1)   │ import (6 / 23 / 3)
              ▼             ▼              ▼
          ┌───────────────────────────────────────┐
          │        earthrise_agents_base          │
          │   (framework — imports nothing;       │
          │    owns the generic authz mixins)     │
          └───────────────────────────────────────┘
```

## Extraction readiness

**READY (plugin-free).** Nothing the framework needs sits above
it — it is the root of the import graph and imports no plugin or
sub-agent. The generic authz mixins now live in
`earthrise_agents_base/mixins.py`, so `accounts` is a clean plugin
that depends on the framework rather than the reverse. There is no
wrong-direction edge left to clear.

## Related docs

- [`README.md`](README.md) — what the framework provides / does not.
- [`writing_a_subagent.md`](writing_a_subagent.md) — the discovery
  contract sub-agents use to plug in without being imported.
- [`../../accounts/documentation/dependencies.md`](../../accounts/documentation/dependencies.md)
  — the plugin that consumes the framework's authz mixins.
