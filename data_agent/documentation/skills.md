# A2A skills

Every skill exposed at `/data_agent/` via JSON-RPC 2.0. Ids come
from `data_agent.a2a.skills` (the `SkillTable`); implementations
live in `data_agent.handlers`. Each skill is also callable
directly as a Python function via `data_agent.services`.

## Agent card

- **Name:** `DataAgent - Weather Data Service`
- **Version:** `1.1.0`
- **URL contract:** `/data_agent/`
- **Card:** `GET /data_agent/.well-known/agent-card.json`
- **RPC:** `POST /data_agent/`
- **Capabilities:** `{"streaming": false, "tiles": true}`

## Skill dispatch envelope

Every RPC call looks like this:

```json
POST /data_agent/
{
  "jsonrpc": "2.0",
  "id": "t1",
  "method": "message/send",
  "params": {"message": {"parts": [
    {"type": "data", "data": {"skill": "<skill_id>", "params": {...}}}
  ]}}
}
```

The framework handles envelope framing, error codes, and NaN
sanitization (see
[`earthrise_agents_base/documentation/api_reference.md`](../../earthrise_agents_base/documentation/api_reference.md)).

---

## `list_sources` — List Data Sources

Returns every registered raster data source, with its variable
list, storage prefix, and enable flag.

**Input params:** *(none)*

**Output:**

```json
{
  "sources": [
    {
      "name": "<display name>",
      "prefix": "<postgis table prefix>",
      "dataset_type": "time_series" | "static",
      "resolution": "0.25deg",
      "variables": ["tmax", "tmin", "rain", "srad"],
      "enabled": true,
      "data_category": "observational" | "forecast",
      "data_type": "raster"
    }
  ]
}
```

**Example call:**

```python
from data_agent.services import list_raster_sources
list_raster_sources()
```

---

## `check_availability` — Check Data Availability

Reports how many dates are loaded in PostGIS for a source, and
the first/last covered date. Optionally scoped to a date window.

**Input params:**

| Name | Required | Type | Notes |
|---|---|---|---|
| `source` | yes | str | Source id (`dataset_subtype`) or table prefix. |
| `start_date` | no | ISO date | Inclusive lower bound. |
| `end_date` | no | ISO date | Inclusive upper bound. |
| `schema` | no | str | Override for `DATAAGENT_SCHEMA`. |

**Output:**

```json
{
  "source": "nasa_power_daily",
  "prefix": "nasa_power",
  "availability": {
    "tmax": {"count": 730, "first_date": "2023-01-01", "last_date": "2024-12-31"},
    "tmin": {"count": 730, "first_date": "2023-01-01", "last_date": "2024-12-31"},
    "rain": {"count": 730, "first_date": "2023-01-01", "last_date": "2024-12-31"},
    "srad": {"count": 730, "first_date": "2023-01-01", "last_date": "2024-12-31"}
  }
}
```

Errors: `{"error": "Unknown source: <name>"}` if the source id
does not resolve through the prefix map.

**Example call:**

```python
from data_agent.services import check_availability
check_availability("nasa_power_daily", start_date="2024-01-01", end_date="2024-06-30")
```

---

## `query_time_series` — Query Time-Series Data

Returns per-date variable values from PostGIS. Supports point
sampling (`lat` + `lon`), bbox averaging (`bbox`), and optional
forecast ensemble filtering (`ens`).

**Input params:**

| Name | Required | Type | Notes |
|---|---|---|---|
| `source` | yes | str | Source id or table prefix. |
| `variables` | no | list[str] | Default `["tmax", "tmin", "rain", "srad"]`. |
| `lat`, `lon` | one of | float | For point queries. |
| `bbox` | one of | `{west, south, east, north}` | For bbox queries. |
| `start_date`, `end_date` | no | ISO date | Optional date window. |
| `ens` | no | int | Ensemble member index (forecast sources only). |
| `schema` | no | str | Override for `DATAAGENT_SCHEMA`. |

**Output:**

```json
{
  "records": [{"date": "2024-01-01", "tmax": 12.4, "tmin": 3.1, "rain": 0.0, "srad": 15.2}],
  "count": 1,
  "variables": ["tmax", "tmin", "rain", "srad"]
}
```

**Example call:**

```python
from data_agent.services import query_raster_data
query_raster_data(
    source="nasa_power_daily",
    lat=34.1, lon=-86.8,
    start_date="2024-03-01", end_date="2024-06-30",
)
```

---

## `fetch_data` — Fetch Data

Enqueues a Celery ETL job that downloads a source from its
upstream API, transforms it via the configured pipeline, and
loads it into PostGIS. Returns immediately with a job id; poll
the `DataFetchJob` model or the source's `check_availability`
skill to see when data lands.

**Input params:**

| Name | Required | Type | Notes |
|---|---|---|---|
| `source` | yes | str | Source id or table prefix. |
| `bbox` | yes | `{west, south, east, north}` | Area of interest. |
| `start_date` | yes | ISO date | Inclusive. |
| `end_date` | yes | ISO date | Inclusive. |
| `schema` | no | str | Override for `DATAAGENT_SCHEMA`. |

**Output:**

```json
{
  "source": "nasa_power_daily",
  "schema": "dataagent",
  "job_id": "<uuid>",
  "celery_task_id": "<celery id>"
}
```

**Example call:**

```python
from data_agent.services import fetch_data
fetch_data(
    source="nasa_power_daily",
    bbox={"west": -87, "south": 33, "east": -86, "north": 35},
    start_date="2024-01-01", end_date="2024-01-31",
)
```

For synchronous fetches (from inside a Celery task or when the
caller needs data on return), use
`data_agent.services.fetch_data_sync(...)` — same signature,
runs the ETL pipeline in-process.

---

## `check_remote` — Check Remote Availability

Probes the upstream URLs of a source (HEAD or short GET, depending
on the source's declared `fetch_config.format`) to report how
many dates in a range are actually reachable. Handles per-date
URL formats, CDS/STAC extent queries, NASA POWER regional JSON,
and Open-Meteo point queries.

**Input params:**

| Name | Required | Type | Notes |
|---|---|---|---|
| `source` | yes | str | Source id or table prefix. |
| `start_date` | no | ISO date | Defaults to `2023-06-15`. |
| `end_date` | no | ISO date | If set, all dates in the range are probed. |
| `lat`, `lon` | no | float | Point context for APIs that need it. |
| `bbox` | no | `{west, south, east, north}` | Bbox context for regional APIs. |

**Output:**

```json
{
  "source": "nasa_power_daily",
  "checks": [
    {
      "source_name": "NASA POWER Daily",
      "prefix": "nasa_power",
      "format": "regional_json_api",
      "reachable": true,
      "url_checked": "https://...",
      "note": "",
      "days_available": 31,
      "days_requested": 31,
      "first_date": "2024-01-01",
      "last_date": "2024-01-31"
    }
  ]
}
```

**Example call:**

```python
from data_agent.services import check_remote
check_remote(
    source="nasa_power_daily",
    start_date="2024-01-01", end_date="2024-01-31",
    lat=34.1, lon=-86.8,
)
```

---

## Bespoke tile endpoint (not an A2A skill)

In addition to the framework-generated A2A routes, `data_agent`
mounts one extra URL:

```
GET /data_agent/tiles/<source>/<variable>/<date>/<z>/<x>/<y>.png
```

Served by `data_agent.views.TileView`. Returns a PNG raster tile
for the requested XYZ cell. This is intentionally not an A2A
skill — it's a Leaflet-compatible tile server for the map
explorer and is not JSON-RPC.

## Related docs

- [`registration.md`](registration.md) — the programmatic API for
  installing sources before any of these skills can respond.
- [`configuration.md`](configuration.md) — settings that affect
  where these skills read from.
- [`../../earthrise_agents_base/documentation/api_reference.md`](../../earthrise_agents_base/documentation/api_reference.md)
  — framework primitives (`SkillTable`, `register_agent`,
  `build_urlpatterns`) that produce this A2A surface.
