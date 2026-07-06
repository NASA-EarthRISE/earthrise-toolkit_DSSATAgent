# dssat_agent — developer documentation

Crop-simulation sub-agent wrapping the DSSAT (Decision Support
System for Agrotechnology Transfer) modelling framework. Exposes
three chat tools plus explorer pages for browsing crops, soils,
fields, treatments, and experiment history.

This sub-agent exposes its capabilities two ways: **chat tools**
registered via `dssat_agent.tools.TOOL_REGISTRY` (`run_experiment`,
`experiment_wizard_step`, `query_experiment`) that the chat orchestrator
discovers generically, **and** a full **A2A skill surface** built in
`dssat_agent/a2a.py` (~20 skills — `list_crops`, `list_cultivars`,
`run_simulation`, `run_ensemble`, `run_monte_carlo`, soil-profile CRUD, file
exports, …), registered via `register_agent(name="dssat_agent")` and mounted at
`/dssat_agent/` (agent card `DSSATAgent - DSSAT Crop Modeling`, v1.0.0). See
[`skills.md`](skills.md) for the chat-tool contract and `dssat_agent/a2a.py`
for the A2A `SkillTable`.

## Quick links

| Topic | File |
|---|---|
| Settings + env vars this app reads | [`configuration.md`](configuration.md) |
| Apps this depends on / that depend on this | [`dependencies.md`](dependencies.md) |
| Chat tool registry (id, params, output) | [`skills.md`](skills.md) |

## What this sub-agent does

- Runs DSSAT simulations end-to-end: assembles weather, soil,
  cultivar, and management inputs; writes a FileX; invokes the
  DSSAT binary; captures inputs/outputs; and persists them as
  `StoredExperiment` rows.
- Ships a guided wizard state machine that collects the missing
  inputs for a run one field at a time (`needs_input` envelopes).
- Handles follow-up queries against a completed experiment:
  variable extraction, time-series charts, stress summaries, and
  full summary output.
- Registers the raster/vector data sources it needs at startup
  by calling into `data_agent`. Definitions come from
  `dssat_agent/data/sources.yaml` — the same file drives
  registration whether `data_agent` is embedded or remote.
- Provides explorer pages for browsing DSSAT domain objects:
  crops, cultivars, soil profiles, code lookups.

## What this sub-agent does not do

- It does not implement chat, LLM prompting, or product branding
  (that lives in `earthrise_agents_base` + the product shell).
- It does not import from any product shell app.
- It does not talk to weather APIs directly — every remote data
  fetch is delegated to `data_agent`.

## Tools — one-line index

| Tool id | Description |
|---|---|
| `run_experiment` | Fast path — run a DSSAT simulation given crop, location, and planting date. |
| `experiment_wizard_step` | Guided wizard that asks for missing inputs before running. |
| `query_experiment` | Follow-up query (variables, chart, stress, summary) against a completed experiment. |

See [`skills.md`](skills.md) for the full I/O contract for each tool.

## Minimum deployment requirements

- PostgreSQL 12+ with PostGIS. The `SIMULATION_SCHEMA` schema
  (default `simulation`) is auto-created via migrations.
- Redis reachable at `CELERY_BROKER_URL` for the `dssat_agent`
  worker queue.
- A Celery worker subscribed to the `dssat_agent` queue.
- A working DSSAT binary bundled under
  `dssat_agent/DSSATTools/DSSATTools/bin/` (already vendored).
- `data_agent` available in embedded or remote mode — required
  for weather data ingestion at simulation time.

## Bootstrapping

Run from the host Django project that includes `dssat_agent` (substitute
your own project module for `<host_project>`):

```bash
python manage.py migrate dssat_agent
celery -A <host_project> worker -Q dssat_agent -l info
```

At `post_migrate` time `dssat_agent` acquires a Postgres advisory
lock and performs **lightweight source registration only**
(`dssat_agent/apps.py::_do_register`):

1. Reads `dssat_agent/data/sources.yaml`.
2. Registers each raster + vector source with `data_agent`
   (embedded or remote, depending on `AGENT_CLIENTS['data_agent']['mode']`).

It does **not** seed `StoredSoilProfile` / `StoredCropFile` rows or load the
reference rasters — that heavy population runs separately via the product
shell's `initialize_application` command (which chains `seed_soils`,
`seed_crop_files`, `seed_cultivars`, `seed_crop_models`, `setup_data_sources`,
and `load_reference_rasters`). A plain `migrate` leaves the DSSAT reference
libraries empty. See the root `README.md` "Initialize the application" step and
`documentation/SETUP.md`.

Registration is idempotent — edits to `sources.yaml` propagate on
the next process restart.
