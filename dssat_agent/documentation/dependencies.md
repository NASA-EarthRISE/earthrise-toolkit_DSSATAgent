# Dependencies

`dssat_agent` depends on the framework (`earthrise_agents_base`)
and on `data_agent`. It does **not** depend on `knowledge_agent`
or `accounts`, and it does **not** import from any product shell.

Verified first-party import counts (production code, excluding
tests/migrations):

| Target | Import sites |
|---|---|
| `data_agent` | 35 |
| `earthrise_agents_base` | 23 |
| `knowledge_agent` | 0 |

`dssat_agent` is the **most heavily-coupled sub-agent** in this
repo. Its 35 direct imports of `data_agent`
(`data_agent.services`, `data_agent.models.RasterDataset`,
`data_agent.schemas.location.LocationInput`, …) reach straight
into that app's Python surface and **bypass the A2A
`get_client('data')` abstraction** the framework provides. Its 23
`earthrise_agents_base` imports include the authz mixins used on
its explorer views, which now come from
`earthrise_agents_base.mixins` rather than from `accounts`.

## Rule

Sub-agents never import from a product shell. The product shell decides
which sub-agents to ship; sub-agents must remain reusable across products.
Knowledge-agent registration used to live here — it was moved to
`chat/apps.py` (product shell) in PR 2 per the single-tenant
design. `dssat_agent` no longer imports or calls into
`knowledge_agent`.

## What `dssat_agent` imports

| Depends on | How | Purpose |
|---|---|---|
| `earthrise_agents_base.schemas` | Python import | `build_tool_schema` — turns Pydantic models into the JSON schema the chat LLM reads. |
| `earthrise_agents_base.agent.clients` | Python import | `get_client('data')` — A2A HTTP client when `AGENT_CLIENTS['data_agent']['mode'] == 'remote'`. |
| `data_agent.services` | Python import (embedded mode) | `register_raster_source`, `register_vector_source`, `load_unloaded_vectors`, plus runtime queries (`query_raster_data`, `sample_raster_at_point`, `get_admin_boundary`, `get_raster_legend`). |
| `data_agent.schemas` | Python import | `build_dataset_enum` — fills the `weather_dataset` enum in the `run_experiment` tool schema. |
| `data_agent.models` | Python import | Direct model access (e.g. `RasterDataset`) — bypasses the A2A boundary. |
| `data_agent` (remote mode) | HTTP A2A | Same skill set as embedded; dispatched over JSON-RPC. |
| `earthrise_agents_base.mixins` | Python import | `AdminRequiredMixin` / authz mixins on explorer views — now framework-owned (formerly imported from `accounts`). |
| `django`, `celery`, `pydantic` | Python import | Framework baseline. |
| `DSSATTools` (vendored) | Python import | Wraps the DSSAT binary in `dssat_agent/DSSATTools/`. |

## What depends on `dssat_agent`

| Consumer | How | Uses |
|---|---|---|
| Product shell (`dssat_chat_agent` / any DSSAT product) | Indirect — via the framework's generic tool registry | The chat orchestrator's `_build_tool_registry()` imports `dssat_agent.tools.TOOL_REGISTRY` and each entry's `func` / `schema_builder`. No hardcoded reference to the DSSAT name. |
| The host project's `urls.py` | Discovery (via `earthrise_agents_base.agent.discovery`) | Reads the `AppConfig` metadata (`url_prefix`, `url_module`) to mount explorer pages at `/dssat/`. |

## Shared database

Same project `DATABASES['default']`. `dssat_agent` isolates its
ORM tables under `SIMULATION_SCHEMA` (default `simulation`).
Runtime raster queries hit the `DATAAGENT_SCHEMA` schema (default
`dataagent`) via the `data_agent` service API — this is the only
sanctioned cross-schema access.

## Data-source ownership

`dssat_agent` **owns** the source list:
`dssat_agent/data/sources.yaml` is the single source of truth. On
`post_migrate` the app iterates that YAML and asks `data_agent`
to register each entry. This is the boundary that keeps
`data_agent` source-agnostic: names like `nasa_power_daily`,
`chirps`, etc. appear only in `sources.yaml` and never in
`data_agent`'s codebase.

## Dependency diagram

```
                                          ┌─────────────────────────┐
                                          │ product shell           │
                                          │ (dssat_chat_agent)      │
                                          └────────────┬────────────┘
                                                       │ discovers
                                             TOOL_REGISTRY (import)
                                                       ▼
     ┌─────────────────────────────┐         ┌─────────────────────┐
     │ earthrise_agents_base       │◀───────▶│    dssat_agent      │
     │  .schemas / .agent.clients  │  uses   │  (this sub-agent)   │
     └─────────────────────────────┘         └────────┬────────────┘
                                                      │
                                                      │ register_* + query_*
                                                      ▼
                                          ┌────────────────────────┐
                                          │      data_agent        │
                                          │  (embedded or A2A)     │
                                          └────────────┬───────────┘
                                                       │
                                            PostGIS: schema=dataagent
                                                       │
                                          ┌────────────▼───────────┐
                                          │ Postgres + PostGIS     │
                                          │ schema: simulation     │
                                          │        (dssat_agent)   │
                                          └────────────────────────┘
```

## No knowledge-agent link

`dssat_agent` deliberately has no relationship with
`knowledge_agent`. Retrieval-augmented answers are a product-shell
concern; the chat orchestrator invokes retrieval directly through
its own registry. `dssat_agent/apps.py` documents this in a
comment on the removed `_register_knowledge_sources` function.

## Database isolation

No cross-app database foreign keys. Every FK on `dssat_agent`
models is within-app or to `settings.AUTH_USER_MODEL`. The
cross-schema access to `dataagent` is via the `data_agent`
service API at query time — not a schema-level FK. This keeps the
DB layer extraction-clean even though the Python layer is not.

## Extraction readiness

**NOT READY.** `dssat_agent` is the main blocker for independent
extraction:

- 35 direct Python imports of `data_agent` internals
  (`services` / `models` / `schemas`) bypass the A2A
  `get_client('data')` abstraction. To extract, these must be
  routed through A2A (remote mode) so the two apps only speak
  over JSON-RPC.
- The 23 `earthrise_agents_base` imports are the expected
  sub-agent → framework edge and are fine — including the authz
  mixins, which now come from `earthrise_agents_base.mixins`
  (the former `accounts.mixins` coupling is gone).

Until the `data_agent` coupling (35) is broken, `dssat_agent`
cannot be lifted out on its own.
