# Configuration reference

Every setting or environment variable the `data_agent` reads at
runtime. Set these in the host project's Django settings module, or via the
`config.env` file
consumed by Docker Compose. All values have safe defaults; only
override what you need.

## Django settings

### `DATAAGENT_SCHEMA`

- **Default:** `"dataagent"`
- **Purpose:** PostgreSQL schema that holds every raster time-series
  table, the `admin` vector table, and reference rasters.
  Auto-created on `post_migrate` by
  `data_agent.services.verify_schema()`.
- **Read from env:** `DATAAGENT_SCHEMA`
- **Consumers:** `data_agent.services._schema()`,
  `data_agent.handlers.handle_check_availability`,
  `handle_query_time_series`, `data_agent.tile_handler`.

### `AGENT_CLIENTS['data_agent']`

- **Default:** `{'mode': 'embedded', 'url': 'http://localhost:5000'}`
- **Purpose:** Shared framework setting that tells sister apps how
  to talk to `data_agent`. `mode='embedded'` means call the
  Python services directly; `mode='remote'` means dispatch over
  A2A HTTP. (The settings-dict key is the full app label
  `data_agent`, matching `settings.py`; `dssat_agent/apps.py::_do_register`
  reads `AGENT_CLIENTS.get('data_agent', {})`.)
- **Read from env:**
  - `DATA_AGENT_MODE` — `embedded` (default) or `remote`
  - `DATA_AGENT_URL` — base URL when `mode='remote'`
- **Consumers:** Any product shell that wires up data-source
  registration. `dssat_agent/apps.py::_do_register()` reads this
  to decide between `_register_embedded()` (Python import) and
  `_register_remote()` (A2A HTTP client).

### `DATABASES['default']`

Standard Django `DATABASES` mapping. `data_agent` reads
`HOST / PORT / NAME / USER / PASSWORD` from
`settings.DATABASES['default']` when it opens a raw `psycopg2`
connection for PostGIS work (see
`data_agent.services._get_connection`).

### Celery keys

Inherits the project-wide `CELERY_BROKER_URL`,
`CELERY_RESULT_BACKEND`, and `CELERY_TASK_ROUTES`. The host
project's routing config sends `data_agent.tasks.*` to the
`data_agent` queue.

## ETL environment variables

Used only by the ETL pipeline (`data_agent/etl/etl_pipeline.py`)
when writing to PostGIS.

| Variable | Default | Purpose |
|---|---|---|
| `ETL_DEBUG_LEVEL` | `DEBUG` | Log level for the pipeline. |
| `DBHOST` | `localhost` | Postgres host (fallback if `TARGET_DB_HOST` unset). |
| `DBNAME` | `dssatserv` | Database name. |
| `DBUSER` | `postgres` | Database user. |
| `PASSWORD` | `` (empty) | Database password. |
| `PORT` | `5432` | Database port. |

Prefer setting `DATABASES['default']` — the ETL pipeline uses
these only as a fallback when a `target_db` block is not passed
in from the caller.

## No hardcoded source names

`data_agent` intentionally holds **no** environment variables
that name a data source (no `NASA_POWER_URL`, no `CHIRPS_ROOT`,
etc.). Every source, including its upstream URL template, variable
list, and query limits, is registered at startup via
`register_raster_source(...)` or `register_vector_source(...)`.
See [`registration.md`](registration.md) and the product shell's
`sources.yaml` for concrete examples.

## Related framework docs

- [`earthrise_agents_base/documentation/settings_reference.md`](../../earthrise_agents_base/documentation/settings_reference.md)
  — framework-wide settings (Celery routing, `AGENT_CLIENTS`,
  agent discovery).
- [`earthrise_agents_base/documentation/writing_a_subagent.md`](../../earthrise_agents_base/documentation/writing_a_subagent.md)
  — how a sub-agent's `AppConfig` participates in URL and A2A
  discovery.
