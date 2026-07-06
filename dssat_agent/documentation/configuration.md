# Configuration reference

Every setting or environment variable the `dssat_agent` reads at
runtime. All values have safe defaults; only override what you
need.

## Django settings

### `SIMULATION_SCHEMA`

- **Default:** `"simulation"`
- **Purpose:** PostgreSQL schema that holds DSSAT-owned tables
  (`StoredExperiment`, `StoredSoilProfile`, `StoredCropFile`, etc.).
- **Read from env:** `SIMULATION_SCHEMA`
- **Set by:** the host project's settings.

### `DATAAGENT_SCHEMA`

- **Default:** `"dataagent"`
- **Purpose:** Read by `dssat_agent.services.spatial_data_service`
  when it opens direct PostGIS connections to sample rasters or
  admin boundaries owned by `data_agent`. Value must match what
  `data_agent` was configured with — the same env var covers both.
- **Read from env:** `DATAAGENT_SCHEMA`

### `AGENT_CLIENTS['data_agent']`

- **Default:** `{'mode': 'embedded', 'url': 'http://localhost:5000'}`
- **Purpose:** Tells `dssat_agent` how to reach `data_agent`.
  `mode='embedded'` → direct Python imports of
  `data_agent.services`. `mode='remote'` → JSON-RPC over the `url`.
  The settings-dict key is the full app label `data_agent` (matching
  `settings.py`); `dssat_agent/apps.py::_do_register` reads
  `AGENT_CLIENTS.get('data_agent', {})`.
- **Read from env:**
  - `DATA_AGENT_MODE` — `embedded` (default) or `remote`
  - `DATA_AGENT_URL` — base URL when `mode='remote'`
- **Consumer:** `dssat_agent/apps.py::_do_register()` at
  `post_migrate`, and `dssat_agent/services/spatial_data_service.py`
  for all runtime data queries.

### `DATABASES['default']`

Standard Django `DATABASES` mapping. Used for both the ORM and
the raw `psycopg2` connections that `spatial_data_service` opens
for PostGIS work.

### Celery keys

Inherits the project-wide `CELERY_BROKER_URL`,
`CELERY_RESULT_BACKEND`, and `CELERY_TASK_ROUTES`. Routing sends
`dssat_agent.tasks.*` to the `dssat_agent` queue
(see the host project's settings).

## Environment variables

### `SUBPATH`

- **Default:** empty
- **Purpose:** Optional URL prefix under which the whole Django
  site is mounted (used behind reverse proxies). Read by
  `dssat_agent/explorer/views_api.py` and
  `dssat_agent/services/draft_submit.py` when building
  absolute-URL links back to the site.

### `TARGET_DB_HOST` / `TARGET_DB_PORT` / `TARGET_DB_NAME` / `TARGET_DB_USER` / `TARGET_DB_PASS`

Optional overrides used by the `setup_data_sources` management
command when pointing at an external Postgres. Empty by default —
the command falls back to `DATABASES['default']`.

## Location of source-registration config

`dssat_agent` ships its own YAML file that describes every data
source it needs `data_agent` to register on its behalf:

- **Path:** `dssat_agent/data/sources.yaml`
- **Loaded by:** `dssat_agent/apps.py::_do_register()` on
  `post_migrate`.
- **Structure:**

```yaml
raster_sources:
  single:
    - dataset_name: NASA POWER Daily
      dataset_subtype: nasa_power_daily
      # ... full config passed to data_agent.services.register_raster_source
vector_sources:
  - name: gadm_admin1
    label: GADM Admin Level 1
    # ... full config passed to data_agent.services.register_vector_source
reference_rasters:
  # in-house reference TIFFs registered via
  # dssat_agent.startup.reference_rasters
```

The YAML file is the sole source of truth for source names —
edits propagate to `data_agent` on the next restart.

## DSSAT binary location

`dssat_agent` calls the DSSAT executable from
`dssat_agent/DSSATTools/DSSATTools/bin/` (vendored — no env var
needed). Rebuild the container image after any binary swap; the
running container is not patched in place.

## Related framework docs

- [`earthrise_agents_base/documentation/settings_reference.md`](../../earthrise_agents_base/documentation/settings_reference.md)
  — framework-wide settings (Celery routing, `AGENT_CLIENTS`,
  agent discovery, product-shell branding).
- [`../data_agent/documentation/configuration.md`](../../data_agent/documentation/configuration.md)
  — `data_agent`'s config surface (shares `DATAAGENT_SCHEMA` and
  the `AGENT_CLIENTS['data_agent']` block).
