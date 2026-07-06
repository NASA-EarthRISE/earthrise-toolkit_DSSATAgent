"""
Public service API for the data_agent.

Provides clean Python-callable functions that can be imported directly
by other Django apps (consuming sub-agents) without going through A2A HTTP.

Sections:
- Raster source registration & queries
- Vector source registration & queries
- Admin boundary spatial queries
"""

import json
import logging
from typing import Dict, List, Optional

import psycopg2
from django.conf import settings

logger = logging.getLogger(__name__)

_prefix_map_cache = None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _resolve_schema_placeholders(obj):
    """Recursively replace '${SCHEMA}' with the actual dataagent schema name."""
    schema = _schema()
    if isinstance(obj, str):
        return obj.replace('${SCHEMA}', schema)
    elif isinstance(obj, dict):
        return {k: _resolve_schema_placeholders(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_resolve_schema_placeholders(item) for item in obj]
    return obj


def _get_prefix_map() -> Dict[str, str]:
    """
    Build a mapping of source name -> PostGIS table prefix.
    Cached after first call. Includes both single sources and combined sources.
    """
    global _prefix_map_cache
    if _prefix_map_cache is not None:
        return _prefix_map_cache

    from data_agent.models import RasterDataset

    prefix_map = {}

    for ds in RasterDataset.objects.all():
        info = ds.dataset_information or {}
        subroutines = info.get('subroutines', {})
        prefix = subroutines.get('load_to_postgis', {}).get('table_prefix', '')
        if prefix:
            prefix_map[prefix] = prefix
            name_lower = ds.dataset_name.lower().replace(' ', '_')
            prefix_map[name_lower] = prefix
            # Also map dataset_subtype so the frontend can use the source ID
            # directly (it sends dataset_subtype, not table_prefix).
            if ds.dataset_subtype:
                prefix_map[ds.dataset_subtype] = prefix

    _prefix_map_cache = prefix_map
    return prefix_map


def invalidate_prefix_cache():
    """Clear the cached prefix map (call after adding new data sources)."""
    global _prefix_map_cache
    _prefix_map_cache = None


def _get_connection():
    """Raw psycopg2 connection using Django DATABASES['default'] settings."""
    from earthrise_agents_base.db import get_raw_connection
    return get_raw_connection()


def _schema():
    return getattr(settings, 'DATAAGENT_SCHEMA', 'dataagent')


# ---------------------------------------------------------------------------
# Raster source registration
# ---------------------------------------------------------------------------

def list_raster_sources() -> Dict:
    """
    List all registered raster data sources (legacy shape).

    Returns:
        dict with 'single_sources' and 'combined_sources' lists.

    Note: prefer ``list_all_data_sources()`` for new code — it returns a
    unified shape that includes reference rasters too.
    """
    from data_agent.handlers import handle_list_sources
    return handle_list_sources()


def list_all_data_sources() -> Dict:
    """
    List every registered data source in a unified shape: time-series (qualified)
    sources, combined sources, and static rasters.

    Each entry includes ``id``, ``name``, ``kind`` (qualified|combined|static),
    ``storage_mode``, ``variables``, and optional ``coverage_extent``.

    This is the canonical listing for the generic data explorer and the map
    UI's source dropdown.
    """
    from data_agent.handlers import handle_list_all_data_sources
    return handle_list_all_data_sources()


def register_raster_source(config: Dict) -> Dict:
    """
    Register a single raster data source if it doesn't already exist.

    Args:
        config: dict with keys matching RasterDataset fields
                (dataset_name, dataset_subtype, dataset_information, etc.)

    Returns:
        dict with 'name', 'created' (bool).
    """
    from data_agent.models import RasterDataset

    name = config.get('dataset_subtype', config.get('name', ''))

    # Resolve ${SCHEMA} placeholders in dataset_information
    ds_info = config.get('dataset_information', {})
    ds_info = _resolve_schema_placeholders(ds_info)

    # Hoist top-level config fields into dataset_information so the listing
    # API has a single place to read them. Existing in-info values win.
    ds_info = dict(ds_info)
    if config.get('variables') and not ds_info.get('variables'):
        ds_info['variables'] = list(config['variables'])
    if config.get('variable_display_names') and not ds_info.get('variable_display_names'):
        ds_info['variable_display_names'] = dict(config['variable_display_names'])
    for key in ('available_from', 'available_to',
                'backfill_start', 'backfill_end'):
        if config.get(key) is not None and ds_info.get(key) is None:
            ds_info[key] = config[key]

    defaults = {
        'dataset_name': config.get('dataset_name', name),
        'dataset_subtype': config.get('dataset_subtype', name),
        'tds_product_name': config.get('tds_product_name', 'UNKNOWN_PRODUCT_NAME'),
        'tds_region': config.get('tds_region', 'UNKNOWN_REGION'),
        'tds_spatial_resolution': config.get('tds_spatial_resolution', 'UNKNOWN_SPATIAL_RESOLUTION'),
        'tds_temporal_resolution': config.get('tds_temporal_resolution', 'UNKNOWN_TEMPORAL_RESOLUTION'),
        'number': config.get('number', 0),
        'data_category': config.get('data_category', 'observational'),
        'data_type': config.get('data_type', 'raster'),
        'dataset_information': ds_info,
        'is_pipeline_enabled': True,
    }

    existing = RasterDataset.objects.filter(dataset_subtype=defaults['dataset_subtype']).first()
    if existing:
        # Update mutable fields if they changed
        update_fields = []
        if existing.dataset_information != ds_info:
            existing.dataset_information = ds_info
            update_fields.append('dataset_information')
        new_name = config.get('dataset_name', name)
        if new_name and existing.dataset_name != new_name:
            existing.dataset_name = new_name
            update_fields.append('dataset_name')
        if update_fields:
            existing.save(update_fields=update_fields)
        invalidate_prefix_cache()
        return {'name': name, 'created': False}

    RasterDataset.objects.create(**defaults)
    invalidate_prefix_cache()
    return {'name': name, 'created': True}


def register_raster_sources(configs: List[Dict]) -> List[Dict]:
    """Register multiple raster sources. Returns list of results."""
    return [register_raster_source(c) for c in configs]


# ---------------------------------------------------------------------------
# Raster data queries
# ---------------------------------------------------------------------------

def check_availability(
    source: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    schema: Optional[str] = None,
) -> Dict:
    """Check what data dates exist in PostGIS for a source."""
    from data_agent.handlers import handle_check_availability

    params = {'source': source}
    if start_date:
        params['start_date'] = start_date
    if end_date:
        params['end_date'] = end_date
    if schema:
        params['schema'] = schema

    return handle_check_availability(params, _get_prefix_map())


def query_raster_data(
    source: str,
    variables: Optional[List[str]] = None,
    lat: Optional[float] = None,
    lon: Optional[float] = None,
    bbox: Optional[Dict] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    schema: Optional[str] = None,
) -> Dict:
    """Query stored raster time-series data from PostGIS."""
    from data_agent.handlers import handle_query_time_series

    params = {'source': source}
    if variables:
        params['variables'] = variables
    if lat is not None:
        params['lat'] = lat
    if lon is not None:
        params['lon'] = lon
    if bbox:
        params['bbox'] = bbox
    if start_date:
        params['start_date'] = start_date
    if end_date:
        params['end_date'] = end_date
    if schema:
        params['schema'] = schema

    return handle_query_time_series(params, _get_prefix_map())


# ---------------------------------------------------------------------------
# Generic raster point sampling (for non-time-series reference rasters)
# ---------------------------------------------------------------------------

def sample_raster_at_point(
    name: str,
    lat: float,
    lon: float,
    bands: Optional[List[int]] = None,
    no_data_value: Optional[float] = None,
) -> Dict[int, Optional[float]]:
    """
    Sample one or more bands of a registered raster at a single (lat, lon).

    Generic point query for any raster registered in the
    ``reference_lookup`` convention. Knows nothing about what the values mean —
    callers translate raw pixel values into domain
    semantics. Uses PostGIS ``ST_Value(rast, band, point)`` which is point
    sampling (categorical-safe; no bilinear interpolation).

    Parameters
    ----------
    name : str
        The ``RasterDataset.dataset_subtype`` of a registered raster (e.g.
        ``soil_lookup_v1``).
    lat, lon : float
        WGS84 coordinates of the sample point.
    bands : list[int] | None
        1-indexed band numbers. ``None`` returns all bands present.
    no_data_value : float | None
        Optional sentinel value to convert to ``None`` after the SQL query
        returns. Use this when the GeoTIFF NoData was not propagated into
        PostGIS as a true NULL.

    Returns
    -------
    dict
        ``{band_num: pixel_value_or_None, ...}``. ``None`` indicates either
        out-of-extent, NULL pixel, or matched ``no_data_value``.

    Note: embedded mode (direct Python import) is the only consumer today; to
    run data_agent as a standalone remote service, expose this through
    data_agent/a2a/handlers.py.
    """
    from data_agent.models import RasterDataset

    try:
        ds = RasterDataset.objects.get(dataset_subtype=name)
    except RasterDataset.DoesNotExist:
        raise ValueError(
            f"Raster '{name}' is not registered. Known reference rasters can "
            f"be listed via RasterDataset.objects.all()."
        )

    info = ds.dataset_information or {}
    table_name = (
        info.get('subroutines', {}).get('reference_lookup', {}).get('table_name')
    )
    if not table_name:
        raise ValueError(
            f"Raster '{name}' has no subroutines.reference_lookup.table_name "
            f"in dataset_information; this point-query API is only for "
            f"reference rasters loaded under the reference_lookup convention."
        )

    if bands is None:
        # Default to all bands declared in metadata; fall back to band 1
        band_meanings = info.get('band_meanings') or {}
        if band_meanings:
            bands = sorted(int(b) for b in band_meanings.keys())
        else:
            bands = [1]

    result: Dict[int, Optional[float]] = {b: None for b in bands}

    conn = _get_connection()
    try:
        with conn.cursor() as cur:
            for band in bands:
                cur.execute(
                    f"""
                    SELECT ST_Value(rast, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326), false)
                    FROM {table_name}
                    WHERE ST_Intersects(rast, ST_SetSRID(ST_MakePoint(%s, %s), 4326))
                    LIMIT 1
                    """,
                    [band, lon, lat, lon, lat],
                )
                row = cur.fetchone()
                if row is None:
                    continue
                value = row[0]
                if value is None:
                    continue
                if no_data_value is not None and value == no_data_value:
                    continue
                result[band] = value
    finally:
        conn.close()

    return result


def get_raster_coverage(name: str) -> Optional[Dict]:
    """
    Return the registered ``coverage_extent`` metadata dict for a raster.

    Returns ``None`` if the raster doesn't exist or has no coverage metadata.
    Coverage shapes supported: ``{type: 'global'}``, ``{type: 'bbox', min_lat,
    max_lat, min_lon, max_lon}``, ``{type: 'country', iso3}``.
    """
    from data_agent.models import RasterDataset

    try:
        ds = RasterDataset.objects.get(dataset_subtype=name)
    except RasterDataset.DoesNotExist:
        return None

    info = ds.dataset_information or {}
    return info.get('coverage_extent')


# ---------------------------------------------------------------------------
# Categorical raster legend (code + label lookup for pixel values)
# ---------------------------------------------------------------------------

_legend_cache: Dict[str, Dict[int, Dict[str, str]]] = {}
_legend_cache_hash: Dict[str, Optional[str]] = {}


def load_raster_legend(raster_name: str, legend_path: str, legend_cfg: Dict) -> int:
    """
    Bulk-load a CSV legend into CategoricalRasterLegend for a raster.

    Replaces all existing rows for this raster_name (idempotent).
    Returns the number of rows loaded.
    """
    import csv
    from data_agent.models import CategoricalRasterLegend

    legend_type = (legend_cfg.get('type') or 'csv').lower()
    if legend_type != 'csv':
        logger.warning("Legend type '%s' not yet supported (only 'csv')", legend_type)
        return 0

    value_col = legend_cfg.get('value_column', 'band_value')
    key_col = legend_cfg.get('key_column', 'soil_code')
    label_col = legend_cfg.get('label_column', 'description')

    CategoricalRasterLegend.objects.filter(raster_dataset_name=raster_name).delete()

    rows = []
    with open(legend_path, 'r', newline='') as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                band_value = int(row[value_col])
            except (KeyError, ValueError, TypeError):
                continue
            code = (row.get(key_col) or '').strip()
            label_text = (row.get(label_col) or '').strip()
            if not code or code.upper() == 'NODATA':
                continue
            rows.append(CategoricalRasterLegend(
                raster_dataset_name=raster_name,
                band_value=band_value,
                code=code[:20],
                label=label_text,
            ))

    if rows:
        CategoricalRasterLegend.objects.bulk_create(rows, batch_size=2000)

    # Invalidate the in-process cache
    _legend_cache.pop(raster_name, None)
    _legend_cache_hash.pop(raster_name, None)

    return len(rows)


def get_raster_legend(raster_name: str) -> Dict[int, Dict[str, str]]:
    """
    Return the cached legend dict for a categorical raster, loading from
    the DB on first access.

    Returns ``{band_value: {'code': '...', 'label': '...'}, ...}``.
    """
    from data_agent.models import CategoricalRasterLegend, RasterDataset

    # Check if the cache needs invalidating (legend_hash changed)
    try:
        ds = RasterDataset.objects.get(dataset_subtype=raster_name)
        current_hash = (ds.dataset_information or {}).get('legend_hash')
    except RasterDataset.DoesNotExist:
        current_hash = None

    cached_hash = _legend_cache_hash.get(raster_name)
    if cached_hash != current_hash or raster_name not in _legend_cache:
        rows = CategoricalRasterLegend.objects.filter(
            raster_dataset_name=raster_name
        ).values('band_value', 'code', 'label')
        legend = {
            int(r['band_value']): {
                'code': r['code'] or '',
                'label': r['label'] or '',
            }
            for r in rows
        }
        _legend_cache[raster_name] = legend
        _legend_cache_hash[raster_name] = current_hash

    return _legend_cache[raster_name]


def lookup_legend_label(raster_name: str, band_value) -> Optional[str]:
    """
    Resolve a single pixel value to its human-readable label string.

    Returns ``"CODE — Description"`` or ``None`` if not found.
    """
    if band_value is None:
        return None
    try:
        bv = int(band_value)
    except (TypeError, ValueError):
        return None
    legend = get_raster_legend(raster_name)
    entry = legend.get(bv)
    if not entry:
        return None
    code = entry.get('code', '')
    label = entry.get('label', '')
    if code and label:
        return f"{code} — {label}"
    return code or label or None


def is_point_in_coverage(name: str, lat: float, lon: float) -> bool:
    """
    Check whether a (lat, lon) falls inside the registered coverage extent of
    a raster. Returns ``True`` for global rasters, ``False`` if the raster has
    no coverage metadata or the coordinates are outside the declared shape.
    """
    coverage = get_raster_coverage(name)
    if not coverage:
        return False

    coverage_type = coverage.get('type', 'bbox')

    if coverage_type == 'global':
        return True

    if coverage_type == 'bbox':
        return (
            coverage.get('min_lat', -90) <= lat <= coverage.get('max_lat', 90)
            and coverage.get('min_lon', -180) <= lon <= coverage.get('max_lon', 180)
        )

    if coverage_type == 'country':
        # MVP: hardcoded US bbox for SSURGO. A future enhancement would
        # delegate to the admin boundary table at services.py for a true
        # geometry intersection.
        iso3 = coverage.get('iso3', '').upper()
        if iso3 == 'USA':
            return 18.0 <= lat <= 72.0 and -180.0 <= lon <= -65.0
        return False

    return False


def fetch_data(
    source: str,
    bbox: Dict,
    start_date: str,
    end_date: str,
    schema: Optional[str] = None,
) -> Dict:
    """Trigger a data fetch via Celery task."""
    from data_agent.handlers import handle_fetch_data

    params = {
        'source': source,
        'bbox': bbox,
        'start_date': start_date,
        'end_date': end_date,
    }
    if schema:
        params['schema'] = schema

    return handle_fetch_data(params, _get_prefix_map())


def fetch_data_sync(
    source: str,
    bbox: Dict,
    start_date: str,
    end_date: str,
    schema: Optional[str] = None,
) -> Dict:
    """
    Fetch data synchronously (runs ETL pipeline directly, no Celery).

    Use this when calling from within a Celery task or when you need
    the data immediately. For fire-and-forget fetches, use fetch_data().
    """
    from data_agent.models import RasterDataset, DataFetchJob
    from data_agent.etl.etl_pipeline import ETL_Pipeline
    import logging as _logging

    schema = schema or _schema()
    prefix_map = _get_prefix_map()
    source_prefix = prefix_map.get(source, source)

    # Find the dataset
    ds = None
    for d in RasterDataset.objects.all():
        info = d.dataset_information or {}
        p = info.get('subroutines', {}).get('load_to_postgis', {}).get('table_prefix', '')
        if p == source_prefix:
            ds = d
            break
    if not ds:
        return {'error': f'No RasterDataset found for source: {source}'}

    # Create job record for tracking
    job = DataFetchJob.objects.create(
        raster_dataset=ds,
        schema_name=schema,
        bbox_west=bbox['west'],
        bbox_south=bbox['south'],
        bbox_east=bbox['east'],
        bbox_north=bbox['north'],
        start_date=start_date,
        end_date=end_date,
        status='downloading',
    )

    # Run pipeline directly (synchronous)
    class _Stdout:
        def write(self, msg): logger.info(msg.rstrip())
        def flush(self): pass

    class _Style:
        def SUCCESS(self, msg): return msg
        def WARNING(self, msg): return msg
        def ERROR(self, msg): return msg

    try:
        from datetime import datetime as _dt, date as _date
        if isinstance(start_date, str):
            start_date = _dt.strptime(start_date, '%Y-%m-%d').date()
        if isinstance(end_date, str):
            end_date = _dt.strptime(end_date, '%Y-%m-%d').date()

        pipeline = ETL_Pipeline(
            ds, _Stdout(), _Style(),
            start_date, end_date,
            bbox=bbox,
        )
        pipeline.execute_etl_pipeline()

        job.status = 'completed'
        job.save(update_fields=['status', 'updated_at'])

        return {
            'status': 'completed',
            'source': source,
            'job_id': str(job.id),
        }
    except Exception as e:
        logger.error("fetch_data_sync failed: %s", e)
        job.status = 'failed'
        job.error_message = str(e)[:1000]
        job.save(update_fields=['status', 'error_message', 'updated_at'])
        return {
            'status': 'failed',
            'error': str(e),
            'job_id': str(job.id),
        }


def check_remote(
    source: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    lat: Optional[float] = None,
    lon: Optional[float] = None,
    bbox: Optional[Dict] = None,
) -> Dict:
    """Probe upstream URLs to check if data is available on remote servers."""
    from data_agent.handlers import handle_check_remote

    params = {'source': source}
    if start_date:
        params['start_date'] = start_date
    if end_date:
        params['end_date'] = end_date
    if lat is not None:
        params['lat'] = lat
    if lon is not None:
        params['lon'] = lon
    if bbox:
        params['bbox'] = bbox

    return handle_check_remote(params)


# ---------------------------------------------------------------------------
# Vector source registration
# ---------------------------------------------------------------------------

def list_vector_sources() -> List[Dict]:
    """List all registered vector data sources."""
    from data_agent.models import VectorDataset

    return [
        {
            'name': v.name,
            'label': v.label,
            'level': v.level,
            'source_url': v.source_url,
            'table_name': v.table_name,
            'is_loaded': v.is_loaded,
        }
        for v in VectorDataset.objects.all()
    ]


def register_vector_source(config: Dict) -> Dict:
    """
    Register a vector data source if it doesn't already exist.

    Args:
        config: dict with name, label, level, source_url, table_name.

    Returns:
        dict with 'name', 'created' (bool).
    """
    from data_agent.models import VectorDataset

    name = config['name']
    new_metadata = config.get('metadata', {})

    try:
        existing = VectorDataset.objects.get(name=name)
        created = False
        # Update fields
        existing.label = config.get('label', name)
        existing.level = config.get('level')
        existing.source_url = config.get('source_url', '')
        existing.schema_name = config.get('schema_name', _schema())
        existing.table_name = config.get('table_name', 'admin')
        # Reset is_loaded if metadata changed
        if existing.metadata != new_metadata:
            existing.metadata = new_metadata
            existing.is_loaded = False
        existing.save()
    except VectorDataset.DoesNotExist:
        created = True
        VectorDataset.objects.create(
            name=name,
            label=config.get('label', name),
            level=config.get('level'),
            source_url=config.get('source_url', ''),
            schema_name=config.get('schema_name', _schema()),
            table_name=config.get('table_name', 'admin'),
            metadata=new_metadata,
        )

    return {'name': name, 'created': created}


def register_vector_sources(configs: List[Dict]) -> List[Dict]:
    """Register multiple vector sources. Returns list of results."""
    return [register_vector_source(c) for c in configs]


def mark_vector_loaded(name: str):
    """Mark a vector dataset as loaded."""
    from data_agent.models import VectorDataset
    VectorDataset.objects.filter(name=name).update(is_loaded=True)


def load_vector_source(name: str) -> Dict:
    """
    Download and load a vector dataset into PostGIS based on its config.

    Reads the VectorDataset record, downloads the source file,
    parses it using metadata instructions, and inserts into the target table.

    Args:
        name: VectorDataset name.

    Returns:
        dict with 'name', 'rows_inserted'.
    """
    from data_agent.models import VectorDataset
    import tempfile
    import os

    import geopandas as gpd
    import requests as http_requests

    ds = VectorDataset.objects.get(name=name)
    if ds.is_loaded:
        return {'name': name, 'rows_inserted': 0, 'skipped': True}

    meta = ds.metadata or {}
    schema = ds.schema_name or _schema()
    table = ds.table_name or 'vector_data'

    # Ensure schema and table exist
    _ensure_vector_table(schema, table, meta)

    # Download source file
    source_url = ds.source_url
    if not source_url:
        raise ValueError(f"No source_url for vector dataset '{name}'")

    tmp = tempfile.NamedTemporaryFile(suffix='.gpkg', delete=False)
    try:
        logger.info("Downloading vector data from %s", source_url)
        resp = http_requests.get(source_url, stream=True, timeout=120)
        resp.raise_for_status()
        for chunk in resp.iter_content(chunk_size=8192):
            tmp.write(chunk)
        tmp.close()

        # Load using metadata instructions
        rows = _load_vector_file(tmp.name, schema, table, meta)
        ds.is_loaded = True
        ds.save(update_fields=['is_loaded', 'updated_at'])
        logger.info("Loaded %d rows for vector dataset '%s'", rows, name)
        return {'name': name, 'rows_inserted': rows}
    finally:
        os.unlink(tmp.name)


def load_unloaded_vectors() -> List[str]:
    """
    Load all unloaded vector datasets.

    Groups by source_url to avoid downloading the same file multiple times.
    Returns list of loaded dataset names.
    """
    from data_agent.models import VectorDataset
    import tempfile
    import os

    import geopandas as gpd
    import requests as http_requests

    unloaded = list(VectorDataset.objects.filter(is_loaded=False))
    if not unloaded:
        return []

    # Group by source_url
    by_url = {}
    for v in unloaded:
        by_url.setdefault(v.source_url, []).append(v)

    loaded_names = []
    for source_url, datasets in by_url.items():
        if not source_url:
            logger.warning("Skipping vector datasets with no source_url: %s",
                          ', '.join(d.name for d in datasets))
            continue

        # Download once
        tmp = tempfile.NamedTemporaryFile(suffix='.gpkg', delete=False)
        try:
            logger.info("Downloading vector data from %s", source_url)
            resp = http_requests.get(source_url, stream=True, timeout=120)
            resp.raise_for_status()
            for chunk in resp.iter_content(chunk_size=8192):
                tmp.write(chunk)
            tmp.close()

            # Load each dataset from the same file
            for ds in datasets:
                try:
                    meta = ds.metadata or {}
                    schema = ds.schema_name or _schema()
                    table = ds.table_name or 'vector_data'
                    _ensure_vector_table(schema, table, meta)
                    rows = _load_vector_file(tmp.name, schema, table, meta)
                    ds.is_loaded = True
                    ds.save(update_fields=['is_loaded', 'updated_at'])
                    loaded_names.append(ds.name)
                    logger.info("Loaded %d rows for '%s'", rows, ds.name)
                except Exception as e:
                    logger.error("Failed to load '%s': %s", ds.name, e)
        except Exception as e:
            logger.error("Failed to download %s: %s", source_url, e)
        finally:
            os.unlink(tmp.name)

    return loaded_names


def _ensure_vector_table(schema: str, table: str, meta: Dict):
    """Create the schema and vector table if they don't exist. Add missing columns."""
    con = _get_connection()
    try:
        cur = con.cursor()
        try:
            cur.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
            con.commit()
        except Exception:
            con.rollback()

        geom_type = meta.get('geometry_type', 'MultiPolygon')
        srid = meta.get('srid', 4326)

        # Build required columns
        required_cols = {}
        for col_name in meta.get('columns', {}):
            required_cols[col_name] = 'TEXT'
        if 'admin_level' in meta:
            required_cols['admin_level'] = 'INTEGER'

        # Check if table exists
        cur.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_schema = %s AND table_name = %s
        """, (schema, table))
        existing_cols = {row[0] for row in cur.fetchall()}

        if not existing_cols:
            # Table doesn't exist — create it
            col_defs = ["id SERIAL PRIMARY KEY"]
            for col_name, col_type in required_cols.items():
                col_defs.append(f"{col_name} {col_type}")
            col_defs.append(f"geom GEOMETRY({geom_type}, {srid})")
            col_sql = ", ".join(col_defs)
            cur.execute(f"CREATE TABLE {schema}.{table} ({col_sql})")
        else:
            # Table exists — add any missing columns
            for col_name, col_type in required_cols.items():
                if col_name not in existing_cols:
                    cur.execute(f"ALTER TABLE {schema}.{table} ADD COLUMN {col_name} {col_type}")

        cur.execute(f"""
            CREATE INDEX IF NOT EXISTS idx_{table}_geom
            ON {schema}.{table} USING GIST (geom)
        """)
        if 'admin_level' in meta:
            cur.execute(f"""
                CREATE INDEX IF NOT EXISTS idx_{table}_level
                ON {schema}.{table} (admin_level)
            """)
        con.commit()
        cur.close()
    finally:
        con.close()


def _load_vector_file(file_path: str, schema: str, table: str, meta: Dict) -> int:
    """
    Generic vector file loader. Reads a layer from the file using metadata
    instructions and inserts rows into PostGIS.

    Returns number of rows inserted.
    """
    import geopandas as gpd
    from shapely.geometry import MultiPolygon as ShapelyMultiPolygon

    # Find the right layer
    layer_names = meta.get('layer_names', [])
    gdf = None
    for layer_name in layer_names:
        try:
            gdf = gpd.read_file(file_path, layer=layer_name)
            break
        except Exception:
            continue

    # Fallback: try listing layers and matching by pattern
    if gdf is None:
        try:
            layers = gpd.list_layers(file_path)
            admin_level = meta.get('admin_level')
            if admin_level is not None:
                pattern = f'_{admin_level}'
                for lname in layers['name']:
                    if pattern in str(lname):
                        gdf = gpd.read_file(file_path, layer=lname)
                        break
        except Exception:
            pass

    if gdf is None or len(gdf) == 0:
        logger.warning("No data found for layers %s in %s", layer_names, file_path)
        return 0

    # Build rows from column mappings
    columns_map = meta.get('columns', {})
    admin_level = meta.get('admin_level')
    srid = meta.get('srid', 4326)

    rows = []
    for _, row in gdf.iterrows():
        geom = row.geometry
        if geom is None:
            continue
        if geom.geom_type == 'Polygon':
            geom = ShapelyMultiPolygon([geom])

        col_values = []
        for col_name, source_cols in columns_map.items():
            val = None
            for src in source_cols:
                val = row.get(src)
                if val is not None:
                    break
            col_values.append(val)

        rows.append((*col_values, admin_level, geom.wkt))

    if not rows:
        return 0

    # Insert
    col_names = list(columns_map.keys())
    if admin_level is not None:
        col_names.append('admin_level')
    col_names.append('geom')

    placeholders = ["%s"] * (len(col_names) - 1)
    placeholders.append(f"ST_Multi(ST_SetSRID(ST_GeomFromText(%s), {srid}))")

    sql = f"""
        INSERT INTO {schema}.{table} ({', '.join(col_names)})
        VALUES ({', '.join(placeholders)})
    """

    con = _get_connection()
    try:
        cur = con.cursor()
        cur.executemany(sql, rows)
        con.commit()
        count = len(rows)
        cur.close()
        return count
    finally:
        con.close()


def verify_schema():
    """Verify the dataagent schema exists in PostGIS, create if missing."""
    schema = _schema()
    con = _get_connection()
    try:
        cur = con.cursor()
        try:
            cur.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
            con.commit()
        except Exception:
            con.rollback()
        cur.close()
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Admin boundary spatial queries
# ---------------------------------------------------------------------------

def list_admin_units(level='admin1', parent=None) -> Dict:
    """List distinct admin unit names at a given level."""
    schema = _schema()
    col_map = {'country': 'country', 'admin1': 'admin1', 'admin2': 'admin2'}
    col = col_map.get(level, 'admin1')

    con = _get_connection()
    try:
        cur = con.cursor()
        if parent and level != 'country':
            parent_col = 'country' if level == 'admin1' else 'admin1'
            query = f"SELECT DISTINCT {col} FROM {schema}.admin WHERE {parent_col} = %s ORDER BY {col}"
            cur.execute(query, (parent,))
        else:
            query = f"SELECT DISTINCT {col} FROM {schema}.admin ORDER BY {col}"
            cur.execute(query)
        rows = cur.fetchall()
        cur.close()
        return {"level": level, "parent": parent, "units": [r[0] for r in rows if r[0]]}
    finally:
        con.close()


def stream_admin_boundaries_ndjson(level='admin1', parent=None):
    """
    Yield admin boundary features one per line (NDJSON format) for streaming.

    Each yielded string is a JSON-encoded GeoJSON Feature followed by a newline.
    Geometries are simplified server-side via ST_Simplify to reduce vertex count
    and prevent browser crashes on complex boundaries.
    """
    schema = _schema()
    level_map = {'country': 0, 'admin1': 1, 'admin2': 2}
    admin_level = level_map.get(level, 1)

    # Simplification tolerance in degrees — higher = fewer vertices.
    # 0.001 ≈ ~100m at the equator; good balance of speed vs visual quality.
    # Country-level uses more aggressive simplification since the geometries
    # are enormous (2M+ vertices).
    tolerance = {0: 0.01, 1: 0.005, 2: 0.001}.get(admin_level, 0.001)

    con = _get_connection()
    try:
        cur = con.cursor(name='admin_stream')  # server-side cursor for streaming
        if parent:
            parent_col = 'country' if admin_level == 1 else 'admin1'
            query = f"""
                SELECT country, admin1, admin2, admin_level,
                       ST_AsGeoJSON(ST_Simplify(geom, {tolerance}))
                FROM {schema}.admin
                WHERE admin_level = %s AND {parent_col} = %s
                ORDER BY COALESCE(admin2, admin1, country)
            """
            cur.execute(query, (admin_level, parent))
        else:
            query = f"""
                SELECT country, admin1, admin2, admin_level,
                       ST_AsGeoJSON(ST_Simplify(geom, {tolerance}))
                FROM {schema}.admin
                WHERE admin_level = %s
                ORDER BY COALESCE(admin2, admin1, country)
            """
            cur.execute(query, (admin_level,))

        for row in cur:
            country, admin1, admin2, alevel, geom_json = row
            if not geom_json:
                continue
            feature = {
                "type": "Feature",
                "properties": {
                    "country": country,
                    "admin1": admin1,
                    "admin2": admin2,
                    "admin_level": alevel,
                },
                "geometry": json.loads(geom_json),
            }
            yield json.dumps(feature) + '\n'

        cur.close()
    finally:
        con.close()


def get_admin_boundaries_geojson(level='admin1', parent=None) -> Dict:
    """Return admin boundaries at a given level as a GeoJSON FeatureCollection."""
    schema = _schema()
    level_map = {'country': 0, 'admin1': 1, 'admin2': 2}
    admin_level = level_map.get(level, 1)
    tolerance = {0: 0.01, 1: 0.005, 2: 0.001}.get(admin_level, 0.001)

    con = _get_connection()
    try:
        cur = con.cursor()
        if parent:
            parent_col = 'country' if admin_level == 1 else 'admin1'
            query = f"""
                SELECT country, admin1, admin2, admin_level,
                       ST_AsGeoJSON(ST_Simplify(geom, {tolerance}))
                FROM {schema}.admin
                WHERE admin_level = %s AND {parent_col} = %s
                ORDER BY COALESCE(admin2, admin1, country)
            """
            cur.execute(query, (admin_level, parent))
        else:
            query = f"""
                SELECT country, admin1, admin2, admin_level,
                       ST_AsGeoJSON(ST_Simplify(geom, {tolerance}))
                FROM {schema}.admin
                WHERE admin_level = %s
                ORDER BY COALESCE(admin2, admin1, country)
            """
            cur.execute(query, (admin_level,))

        features = []
        for row in cur.fetchall():
            country, admin1, admin2, alevel, geom_json = row
            if not geom_json:
                continue
            features.append({
                "type": "Feature",
                "properties": {
                    "country": country,
                    "admin1": admin1,
                    "admin2": admin2,
                    "admin_level": alevel,
                },
                "geometry": json.loads(geom_json),
            })
        cur.close()
        return {"type": "FeatureCollection", "features": features}
    finally:
        con.close()


def get_admin_boundary(admin_name, level='admin1') -> Dict:
    """Get the PostGIS geometry for an admin boundary as GeoJSON."""
    schema = _schema()
    col_map = {'country': 'country', 'admin1': 'admin1', 'admin2': 'admin2'}
    col = col_map.get(level, 'admin1')

    con = _get_connection()
    try:
        cur = con.cursor()
        query = f"""
            SELECT ST_AsGeoJSON(ST_Union(geom))
            FROM {schema}.admin
            WHERE {col} = %s
        """
        cur.execute(query, (admin_name,))
        row = cur.fetchone()
        cur.close()
        if not row or not row[0]:
            raise ValueError(f"Admin boundary not found: {admin_name} (level={level})")
        return json.loads(row[0])
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Backward-compatible aliases (for A2A dispatch using old names)
# ---------------------------------------------------------------------------

