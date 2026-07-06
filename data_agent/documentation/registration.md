# Source registration

`data_agent` ships with **no** built-in data sources. Every raster
or vector layer served by this app has to be installed by the
product shell at startup. Registration is idempotent — call the
API on every process start with the full desired-state list, and
it will upsert.

Two APIs cover the two data types:

- `register_raster_source(config)` — for gridded time-series and
  static reference rasters.
- `register_vector_source(config)` — for admin boundaries and
  other polygonal reference layers.

Both live in `data_agent.services` and are pure Python — call
them from a sister app's `AppConfig.ready()` (or, more safely,
from a `post_migrate` signal handler so DB writes happen after
migrations).

## Where the product shell wires this

The consuming agent owns its source definitions. Its
`apps.py::_do_register()` (called from `post_migrate`) reads
`dssat_agent/data/sources.yaml` and dispatches to either
`register_raster_source` (embedded mode) or the equivalent A2A
skill (remote mode). See
[`dependencies.md`](dependencies.md#data-source-ownership) for
the full picture.

## `register_raster_source(config: dict) -> dict`

Upserts a `RasterDataset` row.

**Config shape:**

```python
{
    # Identity
    "dataset_name": "NASA POWER Daily",           # human-readable
    "dataset_subtype": "nasa_power_daily",        # unique id, used in skill calls
    "data_category": "observational",             # "observational" | "forecast"
    "data_type": "raster",

    # THREDDS / catalog descriptors (kept for legacy compatibility;
    # not required for correctness)
    "tds_product_name": "power_daily_v9",
    "tds_region": "global",
    "tds_spatial_resolution": "0.5deg",
    "tds_temporal_resolution": "daily",
    "number": 1,

    # Everything else — pipeline config, variable metadata, upstream
    # URL templates — lives inside dataset_information. This dict is
    # the sole runtime source of truth; register_raster_source hoists
    # a few common top-level fields into it for backward compatibility.
    "dataset_information": {
        "coverage_extent": {"type": "global"},
        "subroutines": {
            "load_to_postgis": {"table_prefix": "nasa_power"},
            "unit_conversions": {
                "T2M_MAX":       {"operation": "rename", "target_name": "tmax"},
                "T2M_MIN":       {"operation": "rename", "target_name": "tmin"},
                "PRECTOTCORR":   {"operation": "rename", "target_name": "rain"},
                "ALLSKY_SFC_SW_DWN": {"operation": "rename", "target_name": "srad"},
            },
            "reference_lookup": None,           # set for static rasters instead
        },
        "granule_info": {
            "fetch_config": {
                "format": "regional_json_api",
                "url_template": "https://power.larc.nasa.gov/api/...",
                "query_limits": {"max_days_per_request": 365},
            },
        },
    },
}
```

The `${SCHEMA}` placeholder inside `dataset_information` is
resolved to `settings.DATAAGENT_SCHEMA` at registration time —
handy for embedded SQL templates.

**Return:**

```python
{"name": "nasa_power_daily", "created": True | False}
```

Existing rows are updated in place when `dataset_information` or
`dataset_name` differ from the stored value; the prefix cache is
invalidated on every call. Bulk form:
`register_raster_sources(configs: list[dict]) -> list[dict]`.

## `register_vector_source(config: dict) -> dict`

Upserts a `VectorDataset` row. Rows with `is_loaded=False` are
picked up by `load_unloaded_vectors()`, which downloads the file
and inserts into the target PostGIS table.

**Config shape:**

```python
{
    "name": "gadm_admin1",                        # unique id
    "label": "GADM Admin Level 1",                # human-readable
    "level": 1,                                    # optional integer level
    "source_url": "https://example.org/gadm.gpkg",
    "schema_name": "dataagent",                   # defaults to DATAAGENT_SCHEMA
    "table_name": "admin",
    "metadata": {
        "geometry_type": "MultiPolygon",
        "srid": 4326,
        "admin_level": 1,
        "layer_names": ["ADM_1", "gadm_1"],
        "columns": {
            "country": ["COUNTRY", "NAME_0"],
            "admin1":  ["NAME_1"],
            "admin2":  ["NAME_2"],
        },
    },
}
```

**Return:**

```python
{"name": "gadm_admin1", "created": True | False}
```

Bulk form: `register_vector_sources(configs: list[dict]) -> list[dict]`.

## Convenience helpers

| Function | Purpose |
|---|---|
| `list_raster_sources()` | Legacy shape listing (dict with `single_sources` + `combined_sources`). |
| `list_all_data_sources()` | Unified shape covering time-series, combined, and static rasters. Canonical for UI. |
| `list_vector_sources()` | Registered vector datasets with `is_loaded` state. |
| `mark_vector_loaded(name)` | Flip `is_loaded=True` without running the loader. |
| `load_vector_source(name)` | Download + insert a single unloaded vector dataset. |
| `load_unloaded_vectors()` | Load every `is_loaded=False` vector dataset, grouped by `source_url` to avoid re-downloads. |
| `invalidate_prefix_cache()` | Drop the in-memory `dataset_subtype → table_prefix` map after out-of-band DB edits. |
| `verify_schema()` | Ensure `DATAAGENT_SCHEMA` exists in Postgres. Called on `post_migrate`. |

## Static / reference rasters

Rasters loaded under the `subroutines.reference_lookup` convention
(single multi-band table, no time dimension) register the same
way — the loader picks up `reference_lookup.table_name` and
routes storage through `sample_raster_at_point(...)` rather than
the per-variable time-series pipeline. Categorical rasters can
attach a CSV legend via `load_raster_legend(...)` for pixel-value
→ code/label lookup.

## Example — full end-to-end (embedded mode)

```python
# some_product_shell/apps.py
from django.apps import AppConfig
from django.db.models.signals import post_migrate

class SomeProductConfig(AppConfig):
    name = "some_product"

    def ready(self):
        post_migrate.connect(_register_sources, sender=self)

def _register_sources(sender, **kwargs):
    from data_agent.services import (
        register_raster_source,
        register_vector_source,
        load_unloaded_vectors,
    )
    register_raster_source({...})                # see shape above
    register_vector_source({...})
    load_unloaded_vectors()                      # downloads any unloaded vectors
```

## Remote mode

When `AGENT_CLIENTS['data_agent']['mode'] == 'remote'`, the same
registration surface is available over A2A JSON-RPC as
`register_raster_sources` and `register_vector_sources`. The
product shell uses `earthrise_agents_base.agent.clients.get_client('data')`
and calls the skills by id. See `dssat_agent/apps.py::_register_remote()`
for a working example.
