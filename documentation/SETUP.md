# EarthRISEAgents — Setup & Initialization Guide

This guide covers bringing a fresh EarthRISEAgents deployment from an empty
database to a fully seeded, ingestion-ready state. It doubles as the
**initialization-order reference**: the canonical sequence of migrations,
seeders, and ingestion steps, and which of them run automatically.

All commands assume you are in the repository root (`EarthRISEAgents/`). For the
Docker quick start see the root [`README.md`](../README.md); this document
explains what those steps do and how to run them manually.

---

## 1. Architecture recap

EarthRISEAgents is a **single Django project** (`dssat_chat_project`) composed of
sibling apps — the generic platform (`earthrise_agents_base`), the DSSAT product
shell (`dssat_chat_agent`), the sub-agents (`dssat_agent`, `data_agent`,
`knowledge_agent`), and `accounts`. There is **one database**, **one migration
graph**, and **one `manage.py`**. The sub-agents are not separate services in the
default (embedded) mode — they run in-process and are wired by app discovery. See
`earthrise_agents_base/documentation/` for the framework model.

## 2. Prerequisites

| Dependency | Purpose | Notes |
|---|---|---|
| PostgreSQL + PostGIS 3.4 | Shared database for all apps | PG **15** in dev/compose (`postgres/Dockerfile` → `postgis/postgis:15-3.4`), PG **16** in K8s (`Deployment/K8S/deployment.yml` → `postgis:16-3.4`). Extensions: `postgis`, `postgis_raster`, `postgis_topology`, `vector`, `pg_trgm`, `fuzzystrmatch` (see `postgres/init-extensions.sh`) |
| Redis 7 | Celery broker/result backend | Queues per app (see `settings.CELERY_TASK_ROUTES`) |
| Ollama | LLM inference + embeddings | `ollama pull llama3.1 && ollama pull nomic-embed-text` |
| CDS API key | ERA5 / AgERA5 weather downloads (optional) | [Register at Copernicus CDS](https://cds.climate.copernicus.eu/user/register) |

Configuration is via `config.env` (copy from `config.env.dev.example`). Key
variables are documented in
[`earthrise_agents_base/documentation/settings_reference.md`](../earthrise_agents_base/documentation/settings_reference.md).

## 3. Initialization order (the important part)

`manage.py migrate` fires each app's `post_migrate` handler (declared in its
`AppConfig.ready()`). These handlers perform only **lightweight registration** —
they do **not** populate the heavy reference libraries. A fresh `migrate` therefore
leaves a bootable but **empty** deployment; the actual data is populated by the
`initialize_application` command (see below).

| App | `post_migrate` action (registration only) |
|---|---|
| `accounts` | Syncs roles/permissions/groups from every app's `roles.yaml` (`sync_roles`) |
| `dssat_agent` | Registers DSSAT **data sources** with `data_agent` (from `data/sources.yaml`). Does **not** seed soils/crops/cultivars/crop-models or load rasters — see `dssat_agent/apps.py::_do_register`. |
| `data_agent` | Verifies the `dataagent` PostGIS schema exists |
| `knowledge_agent` | Loads registered tenant config |
| `dssat_chat_agent` | Registers the DSSAT knowledge tenant + document sources from `data/tenants.yaml` |

The heavy population runs in one idempotent command owned by the product shell,
`initialize_application` (`dssat_chat_agent/management/commands/`), which chains the
individual seeders in dependency order:

```
sync_roles → seed_soils → seed_crop_files → seed_cultivars → seed_crop_models
           → seed_config → setup_data_sources → load_reference_rasters
           → fetch_all_time_series → ingest_documents
```

The explicit `seed_*` / `setup_data_sources` / `load_reference_rasters` management
commands remain available and are **idempotent** (`update_or_create`), so you can
re-run any of them individually after changing reference data without a full
re-migrate. `initialize_application` accepts `--skip-weather` (the ETL needs network
+ `CDS_API_KEY`) and `--skip-documents` (needs the out-of-band corpus); each step is
wrapped so one failure logs and the run continues.

**Canonical fresh-start sequence:**

```bash
# 1. Apply migrations — runs the post_migrate REGISTRATION handlers above.
python manage.py migrate

# 2. Create an admin user.
python manage.py createsuperuser

# 3. Populate ALL reference data (idempotent). This is the step that seeds
#    soils, crops, cultivars, crop models, system config, and loads the
#    reference rasters — and (unless skipped) runs the weather ETL and corpus
#    ingest. Skip the external-dependency steps as needed:
python manage.py initialize_application --skip-weather --skip-documents

# 4. Ingest the knowledge corpus (see Section 4 — requires the downloaded corpus).
#    (Omit --skip-documents in step 3 to do this as part of initialize_application.)
python manage.py ingest_documents
```

Everything except the external-data steps (weather ETL, corpus ingest) is populated
by `initialize_application`. A plain `migrate` alone does **not** produce a working
DB.

> **Why seeders live in each app** — they are Django management commands, which
> Django only discovers under `<app>/management/commands/`. Keeping them there
> (rather than a central `initialization/` folder) is both the framework
> requirement and the right seam for future per-app extraction: each app ships
> and owns its own seed data. `tenants.yaml` likewise stays with its owning app
> (`dssat_chat_agent/data/tenants.yaml`), mirroring `dssat_agent/data/sources.yaml`.

## 4. Knowledge corpus (manual — external data)

The RAG ingestion corpus (~285 MB of DSSAT papers, model references, and user
guides) is **not** in the repo. The repo tracks only the empty
`dssat_chat_agent/data/documents/` directory (via `.gitkeep`).

1. **Download** the corpus from the public Google Drive folder:
   <https://drive.google.com/drive/folders/1mXfmTZgfhc6xjZn9o7JZoDmgE3WlNTXX?usp=drive_link>
2. **Place** it under `dssat_chat_agent/data/documents/` (the default path the app
   resolves; `docker-compose.yml` bind-mounts this folder in dev). For production
   on K8s, put it on the PVC and point `KNOWLEDGE_DOCUMENTS_DIR` at it — see
   [`Deployment/K8S/README.md`](../Deployment/K8S/README.md).
3. **Ingest**:

   ```bash
   python manage.py ingest_documents
   ```

   Ingestion parses the PDFs, chunks and embeds them (via `nomic-embed-text`), and
   populates the `knowledge` schema. Advanced retrieval strategies
   (contextual, graphrag, raptor, ontology) make an LLM call per chunk and can
   take significant time; see
   [`knowledge_agent/documentation/ingestion.md`](../knowledge_agent/documentation/ingestion.md)
   for the strategy/table breakdown and controls.

## 5. Weather data (optional)

`data_agent` fetches gridded weather on demand when the DSSAT wizard or a chat
request needs it — no bulk preload is required for a working install. To
pre-fetch a date range:

```bash
python manage.py start_etl_pipeline --start_date 2024-01-01 --end_date 2024-01-31
```

ERA5 / AgERA5 require a valid `CDS_API_KEY`; NASA POWER and CHIRPS/CHIRTS need no
auth. See [`data_agent/documentation/`](../data_agent/documentation/) for the
dataset catalog and registration model.

## 6. Docker

The `web` container's entrypoint (`scripts/entrypoint.sh`) runs
`migrate --noinput` (which triggers the post_migrate **registration** handlers in
Section 3) and `collectstatic` on every boot, then runs a best-effort time-series
backfill (`fetch_all_time_series`). It does **not** run the reference-data seeders,
so you still run `createsuperuser` and `initialize_application` yourself:

```bash
docker compose up -d --build
docker compose exec web python manage.py createsuperuser
docker compose exec web python manage.py initialize_application   # seeds soils/crops/cultivars/models/rasters
docker compose exec web python manage.py ingest_documents         # after placing the corpus
```

(`initialize_application` will also run the weather ETL and corpus ingest unless you
pass `--skip-weather` / `--skip-documents`.)

App: <http://localhost:8100>

## 7. Verify

```bash
python manage.py check
python manage.py showmigrations         # all applied
# In the app: log in, start a chat, confirm agent tool traces render.
```
