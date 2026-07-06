# EarthRISEAgents Modularization Plan

**Goal:** Keep EarthRISEAgents composable so that the generic platform and each
sub-agent can eventually be deployed independently and recombined to build a
variety of chatbot-enabled spatial services — not just DSSAT crop simulation.

**Current status:** The platform/product/sub-agent split is **done and shipped**
as a single Django project (the "monolith"). This document describes that shipped
architecture, assesses how ready each app is to be pulled into its own
repository, and records the remaining decoupling work. **The current release
target is monolith publishing** — the extraction steps in Section 4 are future
work, not blockers for publishing.

> This file supersedes the earlier plan that proposed a `chat/` core with a
> `plugins/dssat/` adapter registry and standalone `ChatAgent`/`DataAgent`
> service trees. That design was abandoned; the shipped architecture below (a
> generic platform app + a thin product shell + discovery-wired sub-agent apps)
> is what actually exists.

---

## 1. Shipped architecture

```
dssat_chat_project/     Django project package (settings, urls, celery, asgi/wsgi).
                        DSSAT-domain root — MAY carry domain defaults.
earthrise_agents_base/  Generic PLATFORM app: chat orchestrator, agent discovery
                        spine, A2A registry, shared tool schemas, base UI (shell
                        templates + NASA Horizon design system), generic skills.
                        DOMAIN-FREE.
dssat_chat_agent/       DSSAT PRODUCT SHELL (thin): branding template overrides +
                        data/tenants.yaml (the DSSAT knowledge tenant/ontology).
accounts/               Auth, roles/permissions (roles.yaml discovery), profiles.
dssat_agent/            Sub-agent: DSSAT crop simulation (vendors the GPLv3
                        DSSATTools fork).
data_agent/             Sub-agent: weather/raster data provider (PostGIS, ETL).
knowledge_agent/        Sub-agent: RAG over documentation (pgvector, 12 strategies).
```

**The discovery/registration spine (why this is already modular).** Sub-agents are
plug-and-play: an app declares metadata on its Django `AppConfig` (`agent_label`,
`url_module`, `a2a_url_module`, `skills_dir`, `nav_items`, `home_cards`,
`plots_module`) and the framework wires everything automatically —
`earthrise_agents_base/agent/discovery.py::discover_agents()` scans installed
apps; tools (`tools.py::TOOL_REGISTRY`), skills (`skills/*/SKILL.md`), URLs, nav,
home cards, management items, roles (`roles.yaml`), and A2A endpoints are all
discovered, never hardcoded. **The framework contains zero hardcoded sub-agent
names.** See `earthrise_agents_base/documentation/writing_a_subagent.md`.

**Domain lives in config/product-shell, not the platform.** The generic apps
carry no DSSAT knowledge; the DSSAT tenant/ontology is declared in
`dssat_chat_agent/data/tenants.yaml`, branding via template overrides
(`earthrise_agents_base/documentation/branding_and_overrides.md`). Domain audit
verdict: `earthrise_agents_base`, `data_agent`, `knowledge_agent`, and `accounts`
are domain-free; only `dssat_chat_project` (the DSSAT-domain root) and the DSSAT
apps carry DSSAT vocabulary.

---

## 2. The single biggest asset: no cross-app database coupling

There are **no cross-app foreign keys anywhere**. Every FK is within-app or points
at `settings.AUTH_USER_MODEL`. Each app's tables are self-contained (schema
isolation per agent via `DATAAGENT_SCHEMA` / `SIMULATION_SCHEMA` /
`KNOWLEDGE_SCHEMA`). This means extraction is a **Python-import problem, not a
database problem** — no schema surgery or data migration is required to split an
app out. The work in Section 4 is entirely in the import graph.

---

## 3. Extraction-readiness assessment

Verified first-party production import graph (source → target = import sites,
excluding tests/migrations):

| Source ↓ / Target → | base | accounts | data_agent | knowledge_agent | dssat_agent | shell |
|---|---|---|---|---|---|---|
| **earthrise_agents_base** | — | — | — | — | — | — |
| **accounts** | 1 ✅ | — | — | — | — | — |
| **data_agent** | 6 ✅ | — | — | — | — | — |
| **knowledge_agent** | 3 ✅ | — | — | — | — | — |
| **dssat_agent** | 23 ✅ | — | 35 ❌ | — | — | — |
| **dssat_chat_agent** | (compositional) | — | — | 1 ✅ | — | — |

✅ expected · ❌ cross-sub-agent (blocks independent extraction). No circular
dependencies, and **no plugin→plugin edges except `dssat_agent → data_agent`**.
The framework now imports nothing from any plugin. Each app's per-app
`documentation/dependencies.md` carries the detailed breakdown.

| App | Verdict | Blocker |
|---|---|---|
| `data_agent` | **READY** | Imports only the framework. |
| `knowledge_agent` | **READY** | Imports only the framework. |
| `accounts` | **READY** | Imports only the framework (§4.2 done). |
| `dssat_chat_agent` (shell) | **READY** (pure leaf; compositional deps only). | — |
| `earthrise_agents_base` | **READY** | Plugin-free framework (§4.2 done). |
| `dssat_agent` | **NOT READY** | 35-site hard dependency on `data_agent` (§4.1). |

---

## 4. Extraction roadmap (future work — not required for monolith publishing)

### 4.1 Decouple `dssat_agent` from `data_agent` (the dominant blocker)

`dssat_agent` directly imports `data_agent` at 35 sites (e.g. `data_agent.services`
in `weather_resolver.py`, `services/insitu_lookup.py`, `services/workflow.py`,
`services/api.py`, `services/monte_carlo_service.py`; `data_agent.models.RasterDataset`
in `management/commands/setup_data_sources.py` and `startup/reference_rasters.py`;
`data_agent.schemas.location.LocationInput` in `schemas/experiment.py` and
`schemas/wizard.py`). These bypass the A2A abstraction that already exists
(`earthrise_agents_base.agent.clients.get_client('data')`, already used by
`dssat_agent/apps.py::_register_remote`).

**Plan:**
1. Route all runtime data access through `get_client('data')` A2A skill calls
   instead of importing `data_agent.services`. Add the needed skills to
   `data_agent`'s `SkillTable` where missing (`sample_raster_at_point`,
   `is_point_in_coverage`, `get_raster_legend`; `resolve_location`/`query_data`
   already exist).
2. Move the shared `LocationInput` schema into `earthrise_agents_base.schemas` so
   both agents depend on the framework, not each other.
3. Decide how `dssat_agent` explorer map views get raster data cross-repo (remote
   tile URLs rather than `data_agent.services` imports).

Until then, the direct imports are acceptable **for the monolith** (both apps ship
together). This is tracked as extraction Blocker C.

### 4.2 Make `accounts` a plugin — resolve the framework→accounts edge — DONE

Decision: **`accounts` is a plugin, not platform core.**

**Done (2026-07):** The generic authz primitives (`is_admin`, `is_agent_admin`,
`AdminRequiredMixin`, `AgentAdminMixin`, `AgentPermissionMixin`) were moved from
`accounts/mixins.py` into `earthrise_agents_base/mixins.py`. All importers now
point at the framework (`accounts/views.py`, `dssat_agent/explorer/*` ×5, and the
framework's own `views.py`/`views_management.py`/`context_processors.py`).
Result: **`earthrise_agents_base` imports nothing from any plugin**, and
`accounts` now depends only on the framework — it is a clean plugin. `accounts`
retains its real substance (registration/approval workflow, auth flows + their
templates, the `roles.yaml` discovery/sync engine, `LoginRequiredMiddleware`,
user-administration UI). Those mixins were only a thin authz convenience that had
leaked into the plugin layer.

### 4.3 Package the framework + shared schemas as a versioned dependency

`earthrise_agents_base` is the one intended shared dependency: every sub-agent
imports `earthrise_agents_base.schemas.build_tool_schema` /
`validation_error_to_react` and the A2A helpers (`AgentCard`, `SkillTable`,
`register_agent`, `build_urlpatterns`). When apps are split into separate repos,
publish `earthrise_agents_base` (plus the shared schemas + A2A surface) as a
real versioned, installable package so each extracted agent pins a framework
version instead of vendoring it. Until extraction, in-repo imports are fine.

### 4.4 Extraction seam (envelope boundary)

`dssat_agent` reaches into framework conversation internals
(`earthrise_agents_base.models.Chat`/`Message`,
`agent.chat_agent.ConversationMemory`) at a few sites. This is acceptable
sub-agent→framework coupling for the monolith. When splitting repos, formalize a
documented public API (or an A2A/envelope boundary) so `dssat_agent` can run
without importing the orchestrator's models directly.

---

## 5. Deployment scenarios the architecture already supports

- **DSSAT crop simulation (current):** `dssat_chat_agent` shell + `dssat_agent` +
  `data_agent` + `knowledge_agent` (dssat tenant).
- **A different domain (e.g. flood, air quality):** ship a new thin product shell
  (branding + `tenants.yaml`) and a new domain sub-agent that declares the same
  `AppConfig` metadata; `data_agent` and `knowledge_agent` are reused as-is
  (knowledge via a different tenant/domain config). No platform changes.
- **Embedded vs remote sub-agents:** `settings.AGENT_CLIENTS[label]['mode']`
  switches each sub-agent between in-process (`embedded`) and A2A HTTP (`remote`)
  per-agent. The ReAct tool-dispatch path (`subagent_executor.execute_task`) routes
  through `get_client()`, which honors that mode. *Note: `embedded` is the default
  and best-exercised mode; the `remote` (A2A HTTP) client path exists but is lightly
  tested. The earlier plan-execute orchestration mode has been removed — the graph
  is now pure ReAct.*

---

## 6. Design principles (retained)

1. **Configuration over code.** Domain specifics live in YAML/JSON/env, not Python
   logic. The generic apps stay domain-free.
2. **Discovery, not registration tables.** Apps self-declare `AppConfig` metadata;
   the framework discovers. No app-name is hardcoded in the platform.
3. **Schema isolation per agent.** Each sub-agent owns its Postgres schema; no
   cross-app foreign keys.
4. **A2A protocol is domain-agnostic.** JSON-RPC 2.0 `message/send`; embedded or
   remote is a config switch.
5. **Each app carries its own seeders and roles.** Management commands live under
   `<app>/management/commands/` and roles under `<app>/roles.yaml` — the right
   seam for per-app extraction.
