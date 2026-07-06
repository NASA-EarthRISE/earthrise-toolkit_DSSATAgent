# data_agent — developer documentation

Source-agnostic weather/raster/vector data service. Downloads,
transforms, loads, and queries gridded time-series data and
supporting spatial layers for downstream analysis apps. Ships an
ETL pipeline, a tile server, and an A2A skill surface, with all
concrete data source definitions supplied at startup by whichever
product shell owns the deployment.

## Quick links

| Topic | File |
|---|---|
| Settings + env vars this app reads | [`configuration.md`](configuration.md) |
| Apps this depends on / that depend on this | [`dependencies.md`](dependencies.md) |
| A2A skill surface (id, params, output) | [`skills.md`](skills.md) |
| Programmatic source registration API | [`registration.md`](registration.md) |

## What this sub-agent does

- Persists gridded rasters (time-series and static/reference) into a
  dedicated PostGIS schema.
- Persists admin-boundary vectors and other polygonal reference layers.
- Fetches new data on demand via a Celery ETL pipeline that speaks
  multiple upstream formats (CDS/STAC, NASA POWER JSON, Open-Meteo,
  file download, GRIB2 bands).
- Answers point / bbox / time-range queries against the loaded
  raster tables through PostGIS spatial SQL.
- Serves PNG map tiles from PostGIS rasters.
- Exposes all of the above as JSON-RPC A2A skills at
  `/data_agent/` and as importable Python functions in
  `data_agent.services`.

## What this sub-agent does not do

- It does not know about NASA POWER, CHIRPS, MERRA-2, or any other
  named upstream. All sources are registered at runtime through
  `register_raster_source(...)` and `register_vector_source(...)`.
  The consuming agent (via its `AppConfig.ready` / `post_migrate`, driven
  by its own `sources.yaml`) is the sole owner of source definitions.
- It does not embed retrieval, chat, or simulation logic.
- It never imports from any product shell app.

## Skills — one-line index

| Skill id | Description |
|---|---|
| `list_sources` | List currently registered raster data sources. |
| `check_availability` | Report the loaded date coverage for a source in PostGIS. |
| `query_time_series` | Return time-series records for a source at a point / bbox. |
| `fetch_data` | Trigger a Celery ETL job for a source, bbox, and date range. |
| `check_remote` | Probe upstream URLs to see what dates are actually reachable. |

See [`skills.md`](skills.md) for the full I/O contract.

## Minimum deployment requirements

- PostgreSQL 12+ with PostGIS 3.x (raster + vector) and the
  `dataagent` schema (auto-created on `post_migrate`).
- Redis reachable at `CELERY_BROKER_URL` for the ETL queue
  (`data_agent` queue routing key).
- A Celery worker subscribed to the `data_agent` queue.

## Bootstrapping

Run from the host Django project that includes `data_agent` (substitute
your own project module for `<host_project>`):

```bash
python manage.py migrate data_agent
celery -A <host_project> worker -Q data_agent -l info
```

At `post_migrate` time the app verifies the `dataagent` schema
exists. The consuming agent also listens to `post_migrate` and calls
`register_raster_source(...)` for each source in its `sources.yaml`.
Sources are idempotent —
edits to `sources.yaml` propagate on the next restart.
