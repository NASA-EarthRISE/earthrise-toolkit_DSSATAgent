# Dependencies

`knowledge_agent` is a **clean leaf sub-agent**. It depends only
on `earthrise_agents_base` (3 import sites) and its own storage
layer. It does not depend on `data_agent`, `dssat_agent`,
`accounts`, or any product shell.

It does **not** import `accounts`. Like every sub-agent it may
contribute a `roles.yaml` that `accounts` discovers, and its
pages sit behind the project-wide
`accounts.middleware.LoginRequiredMiddleware` — but neither is a
Python import, so there is no code dependency on `accounts`.

Verified first-party import counts (production code, excluding
tests/migrations):

| Target | Import sites |
|---|---|
| `earthrise_agents_base` | 3 |
| `accounts` | 0 |
| `data_agent` / `dssat_agent` | 0 |

## Rule

Sub-agents never import from a product shell. Tenant + source registration is a
product-shell concern — see [`registration.md`](registration.md)
for the three-way registration API, all of which is called
**into** `knowledge_agent` from the outside.

## What `knowledge_agent` imports

| Depends on | How | Purpose |
|---|---|---|
| `earthrise_agents_base.a2a` | Python import | `SkillTable`, `build_agent_card`, `build_urlpatterns`, `register_agent` — the framework's A2A primitives (`a2a.py` imports the `build_agent_card` factory, not the `AgentCard` class). |
| `django` + `django.contrib.postgres` | Python import | ORM, tsvector, pg_trgm. |
| `pgvector.django` | Python import | HNSW vector index + `CosineDistance` lookup used by `VectorStore`. |
| `pydantic` | Python import | `DomainConfig` schema for `register_tenant`. |
| `ollama` (or configured backend) | HTTP | Embedding + LLM calls via `get_embedding_backend()` / `get_llm_backend()`. |
| `celery` | Python import | Ingestion pipeline fan-out. |

## What depends on `knowledge_agent`

| Consumer | How | Uses |
|---|---|---|
| Product shell (`dssat_chat_agent`) | Python import at startup | `dssat_chat_agent/apps.py` calls `knowledge_agent.registry.register_tenant(...)` and `register_source(...)` to install its single tenant. Chat views call `knowledge_agent.services.retrieve(...)` / `retrieve_batch(...)` at request time. |
| Product shell (remote deployment) | HTTP A2A | Same skill set (`retrieve_knowledge`, `list_strategies`, `check_readiness`) dispatched over JSON-RPC to `/knowledge_agent/`. |
| Management commands (`ingest_knowledge`, `check_knowledge`) | Python import | Operator entry points; use `registry` + `ingestion` directly. |

`dssat_agent` used to trigger knowledge registration; that was
moved to the chat app in PR 2. `dssat_agent` no longer imports or
calls into `knowledge_agent`.

## Shared database

Same project `DATABASES['default']`. Knowledge tables live under
the `KNOWLEDGE_SCHEMA` schema (default `knowledge`). Every row on
every table carries a `tenant_id` FK to `KnowledgeTenant` — see
[`architecture.md`](architecture.md#three-data-model-layers).

Required Postgres extensions:

- `vector` (pgvector, HNSW index)
- `pg_trgm` (trigram indexes for BM25 fallback)

Both are enabled by the initial migration.

## No cross-sub-agent coupling

`knowledge_agent` is intentionally single-purpose and free of
sibling references. No import touches `data_agent` or
`dssat_agent`. Registered sources are plain file paths on disk;
document content flows in through `dssat_chat_agent/data/documents/<tenant>/`
which the product shell owns. This keeps `knowledge_agent` deployable
as a stand-alone service.

## Database isolation

No cross-app database foreign keys. Every FK lives within
`knowledge_agent` (rows carry a `tenant_id` FK to
`KnowledgeTenant`) or points to `settings.AUTH_USER_MODEL`. There
is no FK into another app's tables, which is what makes the DB
layer extraction-clean.

## Extraction readiness

**READY.** `knowledge_agent` depends only on the framework,
imports no sibling sub-agent and no plugin, and holds no
cross-app FKs. It can be deployed as a stand-alone A2A service
unchanged; product shells install tenants + sources from the
outside via the registration API.

## Dependency diagram

```
     ┌──────────────────────────┐
     │ product shell            │
     │ (dssat_chat_agent)       │
     └────────────┬─────────────┘
                  │  register_tenant / register_source (Python)
                  │  services.retrieve (Python or A2A)
                  ▼
     ┌─────────────────────────────────────────┐
     │           knowledge_agent               │
     │  registry / services / strategies /     │
     │  backends / store / ingestion           │
     └────────────┬────────────────────────────┘
                  │ Django ORM + pgvector
                  ▼
     ┌────────────────────────────┐
     │ Postgres + pgvector        │
     │ schema: knowledge          │
     └────────────────────────────┘
                  ▲
                  │ HTTP
     ┌────────────┴───────────────┐
     │   Ollama (or alt backend)  │
     │  embedding + LLM calls     │
     └────────────────────────────┘
```

## Related docs

- [`architecture.md`](architecture.md) — component layer diagram.
- [`registration.md`](registration.md) — the three registration
  paths product shells use to install tenants + sources.
- [`backends.md`](backends.md) — the pluggable embedding + LLM
  backend interfaces.
- [`../../earthrise_agents_base/documentation/writing_a_subagent.md`](../../earthrise_agents_base/documentation/writing_a_subagent.md)
  — why sub-agents stay leaf-shaped.
