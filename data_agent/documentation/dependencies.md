# Dependencies

`data_agent` is a **clean leaf sub-agent**: it depends only on
the framework (`earthrise_agents_base`, 6 import sites) and on
PostGIS — no other sub-agent or product.

It does **not** import `accounts`. It *contributes* a `roles.yaml`
that `accounts` discovers (see below), and its explorer pages
rely on the global `accounts.middleware.LoginRequiredMiddleware`
that is installed project-wide — but neither of those is a Python
import, so no code dependency on `accounts` exists.

Verified first-party import counts (production code, excluding
tests/migrations):

| Target | Import sites |
|---|---|
| `earthrise_agents_base` | 6 |
| `accounts` | 0 |
| `dssat_agent` / `knowledge_agent` | 0 |

## Rule

Sub-agents never import from a product shell. Product shells decide which sub-agents to
mount; sub-agents must remain reusable across products. Reverse
imports would break that boundary and are rejected in review.

## What `data_agent` imports

| Depends on | How | Purpose |
|---|---|---|
| `earthrise_agents_base.a2a` | Python import | `SkillTable`, `AgentCard`, `build_urlpatterns`, `register_agent` — the framework's A2A primitives. |
| `django` + `django.contrib.gis` | Python import | ORM, migrations, spatial querysets. |
| `psycopg2` | Python import | Raw PostGIS queries where the ORM lacks raster support. |
| `celery` | Python import | Async ETL jobs. |
| `geopandas`, `requests`, `shapely` | Python import | ETL pipeline internals. |

## What depends on `data_agent`

| Consumer | How | Uses |
|---|---|---|
| `dssat_agent` | Python import at `AppConfig.ready` → `post_migrate` | Calls `data_agent.services.register_raster_source(...)` and `register_vector_source(...)` for every entry in `dssat_agent/data/sources.yaml`. Also queries raster time-series via `query_raster_data(...)` when building DSSAT weather inputs, and samples reference rasters (soil-code, planting-date) via `sample_raster_at_point(...)`. |
| `dssat_agent` (remote mode) | HTTP A2A | When `AGENT_CLIENTS['data_agent']['mode'] == 'remote'`, `dssat_agent` calls `list_raster_sources`, `register_raster_sources`, `list_vector_sources`, `register_vector_sources`, `load_unloaded_vectors` over JSON-RPC instead of Python. |
| Product shell (`dssat_chat_agent`) | Indirect — never imports `data_agent` directly | The chat orchestrator only sees `data_agent` through the tool registry and the framework's A2A client. |

## Data-source ownership

`data_agent` is **source-agnostic**. It knows how to persist and
query rasters/vectors, but it does not ship any specific source
definition. Product shells own that data. The flow is:

```
dssat_agent/data/sources.yaml
        │
        ▼
dssat_agent/apps.py::_do_register()   (post_migrate handler)
        │
        │ AGENT_CLIENTS['data_agent']['mode']
        │
        ├── embedded → data_agent.services.register_raster_source(...)
        │               data_agent.services.register_vector_source(...)
        │               data_agent.services.load_unloaded_vectors()
        │
        └── remote   → HTTP JSON-RPC to /data_agent/
                      (same skill names via A2A)
```

`data_agent`'s codebase contains no reference to any specific
upstream (no `nasa_power.py`, no `chirps.py`, etc.). The ETL
pipeline is driven purely by the `dataset_information` dict
persisted on each `RasterDataset` row.

## Role contribution (not an import)

`data_agent` ships a `roles.yaml` that the `accounts` app's role
discovery reads at startup to register any roles/permissions the
data explorer needs. This is a *data* contribution consumed **by**
`accounts` — the arrow points from `accounts` to the file on disk,
not from `data_agent` into `accounts`. Likewise, `data_agent`
explorer views are protected by the project-wide
`accounts.middleware.LoginRequiredMiddleware`; that is global
middleware config, not a Python import. Both keep `data_agent`
free of any code dependency on `accounts`.

## Shared database

There is no separate database. `data_agent` shares the project
`DATABASES['default']` connection with every other sub-agent, but
isolates its tables under the `DATAAGENT_SCHEMA` schema
(default `dataagent`). Sister apps that read `data_agent` tables
directly (rare — prefer the service API) must qualify by schema.

No cross-app database foreign keys exist. Every FK on
`data_agent` models is within-app or to
`settings.AUTH_USER_MODEL`. This is what keeps the DB layer
extraction-clean.

## Extraction readiness

**READY.** `data_agent` depends only on the framework, imports no
sibling sub-agent and no plugin, and holds no cross-app FKs. It
can be lifted into a stand-alone service as-is; consumers already
reach it either through its Python service API (embedded mode) or
over A2A JSON-RPC (remote mode).

## Dependency diagram

```
                                 ┌──────────────────────┐
                                 │ product shell        │
                                 │ (e.g. dssat_agent)   │
                                 └────────────┬─────────┘
                                              │ register_*, query_*
                                              ▼
    ┌──────────────────────────┐        ┌─────────────────────┐
    │ earthrise_agents_base    │◀──────▶│      data_agent     │
    │   .a2a primitives        │  uses  │  (this sub-agent)   │
    └──────────────────────────┘        └────────────┬────────┘
                                                     │
                                          psycopg2 + Django ORM
                                                     ▼
                                        ┌────────────────────────┐
                                        │ Postgres + PostGIS     │
                                        │ schema: dataagent      │
                                        └────────────────────────┘
```
