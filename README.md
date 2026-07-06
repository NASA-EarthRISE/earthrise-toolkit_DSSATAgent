# EarthRISEAgents

A multi-agent, LLM-driven platform for domain research assistants. A generic
chat/orchestration **platform** hosts pluggable **sub-agents** (data,
knowledge, simulation) behind a single conversational UI, and a thin **product
shell** brands and configures it for a specific domain — here, **DSSAT** crop
simulation.

The agents run locally against [Ollama](https://ollama.com/) (no external LLM
API required) with PostGIS + pgvector for spatial data and retrieval.

## Architecture

```
dssat_chat_project/     Django project package (settings, urls, celery, asgi/wsgi)
earthrise_agents_base/  Generic PLATFORM: chat orchestrator, agent discovery,
                        A2A registry, shared tool schemas, base UI (shell
                        templates + design system), generic skills. Domain-free.
dssat_chat_agent/       DSSAT PRODUCT SHELL (thin): branding overrides +
                        data/tenants.yaml (the DSSAT knowledge tenant/ontology).
accounts/               Auth, roles/permissions (roles.yaml discovery), profiles.
dssat_agent/            Sub-agent: DSSAT crop simulation (bundles the DSSATTools fork).
data_agent/             Sub-agent: weather/raster data provider (PostGIS, ETL).
knowledge_agent/        Sub-agent: RAG over documentation (pgvector, 12 strategies).
```

**Key ideas**

- **Discovery/registration spine.** Sub-agents are plug-and-play: an app
  declares metadata on its `AppConfig` (`agent_label`, `url_module`,
  `a2a_url_module`, `skills_dir`, `nav_items`, `home_cards`) and is wired
  automatically — tools (`tools.py::TOOL_REGISTRY`), skills (`skills/*/SKILL.md`),
  URLs, nav, and roles are all discovered. See
  `earthrise_agents_base/documentation/writing_a_subagent.md`.
- **Domain lives in the product shell / config, not the platform.** The generic
  apps carry no DSSAT knowledge; the DSSAT tenant/ontology is declared in
  `dssat_chat_agent/data/tenants.yaml`, and branding via template overrides
  (see `earthrise_agents_base/documentation/branding_and_overrides.md`).
- **Embedded or remote agents.** `AGENT_CLIENTS` in settings runs each
  sub-agent in-process (`embedded`) or over A2A HTTP (`remote`). The ReAct
  tool-dispatch path (`earthrise_agents_base/agent/subagent_executor.py::execute_task`)
  routes every sub-agent call through `get_client()`, which honors that per-agent
  `mode`. *Note: `embedded` is the default and best-exercised mode; the `remote`
  (A2A HTTP) client path exists but is lightly tested. (The earlier plan-execute
  orchestration mode has been removed — the graph is now pure ReAct.)*

## Requirements

- Docker + Docker Compose
- An Ollama instance (local host by default) with the models in `config.env`
  (`MODEL_NAME`, `EMBEDDING_MODEL`)
- System libraries (handled inside the images): GDAL, eccodes, libpq, and a
  Fortran runtime for the DSSAT binary.

## Quick start (development)

```bash
# 1. Configure
cp config.env.dev.example config.env      # then edit values

# 2. Pull an LLM + embedding model in your Ollama
ollama pull llama3.1 && ollama pull nomic-embed-text

# 3. Build & run (dev image bakes Python deps in)
docker compose up -d --build

# 4. Create a superuser
docker compose exec web python manage.py createsuperuser

# 5. Initialize the application — populate all data (idempotent, re-runnable):
#    roles, soils, crops, cultivars, crop models, system config, the reference
#    rasters (loaded into PostGIS), and the weather time-series ETL.
#    Add --skip-weather (the ETL needs CDS_API_KEY + network) and/or
#    --skip-documents (RAG ingestion needs the out-of-band corpus) to skip the
#    external-dependency steps.
docker compose exec web python manage.py initialize_application
```
App: http://localhost:8100

> **Iterating:** rebuild the image to pick up code changes
> (`docker compose up -d --build`).

## Deployment modes (two Dockerfiles)

This repo has **two** build strategies — pick deliberately:

- **`Dockerfile.dev`** (used by `docker-compose.yml`) — bakes Python
  dependencies into the image and compiles/installs the local DSSATTools fork.
  Self-contained; best for local dev.
- **`Dockerfile`** (production/K8s) — does **not** bake Python packages. A
  `venv-builder` initContainer runs `scripts/build_venv.sh` to install
  `requirements.txt` + the local DSSATTools fork into a venv on a shared PVC
  that all pods mount. Knowledge documents also live on the PVC
  (`KNOWLEDGE_DOCUMENTS_DIR`). K8s manifests are in `Deployment/K8S/`.

Running the production `Dockerfile` directly (without the venv-builder flow)
will fail with import errors — use `Dockerfile.dev` for a self-contained image.

## Configuration

Copy an example to `config.env` (never commit a real one):

- `config.env.dev.example` — development (DEBUG on, console email, local Ollama)
- `config.env.prod.example` — production (DEBUG **unset**, real hosts/secrets)

Security note: `SECRET_KEY` is **required** in production (the app refuses to
start without it when `DEBUG` is off), and transport-security settings
(`SECURE_*`, secure cookies, HSTS) auto-enable whenever `DEBUG` is unset — so
production must run behind an HTTPS-terminating proxy and must **not** set
`DEBUG`.

## External data (not in the repo)

To keep the repo small, the RAG ingestion corpus (~285 MB of DSSAT papers,
model references, and user guides) is distributed out-of-band and git-ignored.
The repo tracks only the empty `dssat_chat_agent/data/documents/` directory (via
a `.gitkeep`) so the app's default resolve/mount path exists.

**Download the corpus** from the public Google Drive folder:
<https://drive.google.com/drive/folders/1mXfmTZgfhc6xjZn9o7JZoDmgE3WlNTXX?usp=drive_link>

Then place it and ingest:

- `dssat_chat_agent/data/documents/` — the corpus. It is
  **`.dockerignore`d — never baked into the image** — and provided at runtime:
  - **Dev:** `docker-compose.yml` bind-mounts the host folder
    `./dssat_chat_agent/data/documents` into the container at the same path (the
    default the app resolves), so just unpack the downloaded corpus there — no
    env override needed.
  - **Prod (K8s):** place the corpus on the PVC and point
    `KNOWLEDGE_DOCUMENTS_DIR` at it — see [`Deployment/K8S/README.md`](Deployment/K8S/README.md).
  - Then ingest: `python manage.py ingest_documents`.

Runtime reference rasters (`dssat_agent/data/reference_rasters/`) **are** in the
repo.

## The DSSATTools fork

`dssat_agent/DSSATTools/` is a **locally modified fork** of
[Py_DSSATTools](https://github.com/daquinterop/Py_DSSATTools), vendored as
source (it ships a precompiled Linux DSSAT binary in `package_data`; it is not
recompiled at build time). It is **GPLv3**, which is why this repository is
licensed GPLv3 (see LICENSE).

## Testing

```bash
docker compose exec web pytest            # config in pytest.ini
```
`pytest.ini` sets `DJANGO_SETTINGS_MODULE=dssat_chat_project.settings` and skips
`live`/`slow`-marked tests by default.

## Documentation

- Framework guides: `earthrise_agents_base/documentation/` (writing sub-agents
  and product shells, template/branding conventions, settings, testing).
- DSSAT specifics: `dssat_agent/documentation/`.
- Validation notes: `evaluations/`.

## License

GPLv3 — see [LICENSE](LICENSE).
