"""
Synchronous skill handlers for the DataAgent A2A service.

These are plain functions (no async, no event queues) that return dicts.
Called from asgi.py via sync_to_async.
"""

import datetime
import logging

import requests as http_requests
from django.conf import settings

logger = logging.getLogger(__name__)


def handle_list_sources():
    """List all available data sources (legacy simplified format)."""
    from data_agent.models import RasterDataset

    sources = []
    for ds in RasterDataset.objects.all():
        info = ds.dataset_information or {}
        subroutines = info.get('subroutines', {})
        prefix = subroutines.get('load_to_postgis', {}).get('table_prefix', 'unknown')
        conversions = subroutines.get('unit_conversions', {})
        variables = [c.get('target_name', k) for k, c in conversions.items()] if conversions else []

        if not variables:
            # Try variables dict (new format)
            var_dict = info.get('variables', {})
            if isinstance(var_dict, dict):
                variables = list(var_dict.keys())

        sources.append({
            'name': ds.dataset_name,
            'prefix': prefix,
            'dataset_type': ds.dataset_type,
            'resolution': ds.tds_spatial_resolution,
            'variables': variables,
            'enabled': ds.is_pipeline_enabled,
            'data_category': ds.data_category,
            'data_type': ds.data_type,
        })

    return {'sources': sources}


# ---------------------------------------------------------------------------
# Generic source listing (covers time-series, combined, and static rasters)
# ---------------------------------------------------------------------------

def _extract_variable_metadata(info):
    """
    Resolve a structured variable description from RasterDataset.dataset_information.

    Each RasterDataset has TWO related variable lists that should always
    cover each other:
      - **source_variables**: what the upstream API/file provides (the
        keys of subroutines.unit_conversions, e.g. T2M_MAX, PRECTOTCORR
        for NASA POWER).
      - **final_variables**: what the dataset emits after ETL (the
        target_name values of unit_conversions, e.g. tmax, rain).

    The mapping between them lives in subroutines.unit_conversions:
      {source_var: {operation: 'rename', target_name: <final_var>}}

    Final variables must be covered either by an extract step (direct
    source variable mapped via unit_conversions) or a transform step
    (combining variables via source_groups in granule_info).

    Returns a dict:
      {
        'source_variables': [...],          # upstream API names
        'final_variables':  [...],          # canonical names emitted post-ETL
        'mapping': [{source, final, operation}],
        'mapping_kind': 'declared' | 'unit_conversions' | 'band_meanings'
                       | 'granule_legacy' | 'unknown',
      }
    """
    # Precedence 1: explicit canonical declaration
    if isinstance(info.get('variables'), list) and info['variables']:
        finals = list(info['variables'])
        return {
            'source_variables': finals,  # assume direct identity if no other info
            'final_variables': finals,
            'mapping': [{'source': v, 'final': v, 'operation': 'identity'} for v in finals],
            'mapping_kind': 'declared',
        }

    # Precedence 2: time-series convention with unit_conversions
    subroutines = info.get('subroutines') or {}
    conversions = subroutines.get('unit_conversions') or {}
    if conversions:
        source_vars = list(conversions.keys())
        mapping = []
        finals = []
        for src, cfg in conversions.items():
            op = (cfg or {}).get('operation') or 'rename'
            tgt = (cfg or {}).get('target_name') or src
            mapping.append({'source': src, 'final': tgt, 'operation': op})
            if tgt not in finals:
                finals.append(tgt)
        return {
            'source_variables': source_vars,
            'final_variables': finals,
            'mapping': mapping,
            'mapping_kind': 'unit_conversions',
        }

    # Precedence 3: static raster with band_meanings
    band_meanings = info.get('band_meanings') or {}
    if band_meanings:
        finals = [str(m) for m in band_meanings.values()]
        return {
            # Static rasters are loaded directly from a file rather than
            # an API, so source vs final is the band number → band meaning
            # relationship.
            'source_variables': [f'band_{k}' for k in band_meanings.keys()],
            'final_variables': finals,
            'mapping': [
                {'source': f'band_{k}', 'final': str(v), 'operation': 'band_extract'}
                for k, v in band_meanings.items()
            ],
            'mapping_kind': 'band_meanings',
        }

    # Precedence 4 (legacy): granule_info.file_info_by_key
    granule = info.get('granule_info') or {}
    legacy = []
    for file_info in (granule.get('file_info_by_key') or {}).values():
        for var_key, var_info in (file_info.get('variables') or {}).items():
            orig = var_info.get('original_variable', var_key)
            if orig not in legacy:
                legacy.append(orig)
    if legacy:
        return {
            'source_variables': legacy,
            'final_variables': legacy,
            'mapping': [{'source': v, 'final': v, 'operation': 'legacy'} for v in legacy],
            'mapping_kind': 'granule_legacy',
        }

    return {
        'source_variables': [],
        'final_variables': [],
        'mapping': [],
        'mapping_kind': 'unknown',
    }


def _extract_variables(info):
    """Backward-compat shim returning only the final-variable list."""
    return _extract_variable_metadata(info)['final_variables']


def _classify_storage_mode(info):
    """
    Determine how a RasterDataset is stored in PostGIS.

    Returns one of:
      - ``per_variable_tables``: time-series convention. One table per variable,
        named ``{schema}.{table_prefix}_{variable}``. Single-band per table.
      - ``multi_band_table``: static raster convention. One table for the
        whole raster, named ``{schema}.{table_name}``. Multiple bands per row.
      - ``unknown``: dataset has no storage info (not yet loaded).
    """
    subroutines = (info or {}).get('subroutines') or {}
    if 'reference_lookup' in subroutines and subroutines['reference_lookup'].get('table_name'):
        return 'multi_band_table'
    if 'load_to_postgis' in subroutines and subroutines['load_to_postgis'].get('table_prefix'):
        return 'per_variable_tables'
    return 'unknown'


def _build_variable_display_names(info):
    """
    Build a {variable: display_name} dict from dataset_information.

    Reads from the canonical ``variables`` dict first, falls back to
    ``variable_display_names``, then to the raw variable key.
    """
    result = {}
    variables = info.get('variables', {})
    if isinstance(variables, dict):
        for var, meta in variables.items():
            if isinstance(meta, dict) and meta.get('display_name'):
                result[var] = meta['display_name']
    # Legacy fallback: variable_display_names dict
    legacy = info.get('variable_display_names') or {}
    for var, name in legacy.items():
        if var not in result:
            result[var] = name
    return result


def handle_list_all_data_sources():
    """
    Return every registered data source in a unified shape, regardless of
    whether it's a time-series source, a combined source, or a
    static raster.

    Each entry:
        {
          'id': str,                     # dataset_subtype (or combo name)
          'name': str,                   # human-readable
          'display_name': str,           # same as name (for frontend convenience)
          'kind': 'qualified' | 'combined' | 'static',
          'storage_mode': 'per_variable_tables' | 'multi_band_table' | 'unknown',
          'variables': [str, ...],
          'data_category': str,
          'data_type': str,
          'enabled': bool,
          'resolution': str | None,
          'coverage_extent': dict | None,  # for static rasters
          'metadata': dict,                # the raw dataset_information
        }
    """
    from data_agent.models import RasterDataset

    items = []

    for ds in RasterDataset.objects.all().order_by('dataset_name'):
        info = ds.dataset_information or {}
        storage = _classify_storage_mode(info)
        has_source_groups = bool(info.get('granule_info', {}).get('source_groups'))
        kind = 'static' if storage == 'multi_band_table' else ('combined' if has_source_groups else 'qualified')
        var_meta = _extract_variable_metadata(info)

        # Build display names from variables dict (no hardcoded defaults)
        merged_vdn = _build_variable_display_names(info)

        # Per-variable metadata from the new variables dict
        variables_meta = info.get('variables', {})
        if isinstance(variables_meta, list):
            variables_meta = {}  # legacy list format — ignore

        items.append({
            'id': ds.dataset_subtype or ds.dataset_name,
            'name': ds.dataset_name,
            'display_name': ds.dataset_name,
            'kind': kind,
            'storage_mode': storage,
            'dataset_type': ds.dataset_type or ('static' if storage == 'multi_band_table' else 'time_series'),
            'temporal_resolution': info.get('temporal_resolution'),
            'variables': var_meta['final_variables'],
            'variables_metadata': variables_meta,
            'source_variables': var_meta['source_variables'],
            'variable_display_names': merged_vdn,
            'variable_mapping': var_meta['mapping'],
            'variable_mapping_kind': var_meta['mapping_kind'],
            'data_category': ds.data_category,
            'data_type': ds.data_type,
            'enabled': ds.is_pipeline_enabled,
            'resolution': ds.tds_spatial_resolution or None,
            'coverage_extent': info.get('coverage_extent'),
            'metadata': info,
        })

    return {'sources': items}


def handle_check_availability(params, prefix_map):
    """Check what dates are loaded in PostGIS."""
    from data_agent.etl.etl_pipeline import ETL_Pipeline

    source = params.get('source')
    if not source:
        return {'error': "Missing 'source' parameter"}

    source_prefix = prefix_map.get(source, '')
    if not source_prefix:
        logger.warning("check_availability: unknown source '%s'", source)
        return {'error': f"Unknown source: {source}. Use 'list_sources' to see available sources."}

    schema = params.get('schema', settings.DATAAGENT_SCHEMA)
    logger.info(
        "check_availability: source='%s' prefix='%s' schema='%s'",
        source, source_prefix, schema,
    )

    start_date = params.get('start_date')
    end_date = params.get('end_date')

    if start_date and isinstance(start_date, str):
        start_date = datetime.date.fromisoformat(start_date)
    if end_date and isinstance(end_date, str):
        end_date = datetime.date.fromisoformat(end_date)

    pipeline = ETL_Pipeline(None, None, None, None, None)
    con = pipeline._get_postgis_connection()

    variables = ['tmax', 'tmin', 'rain', 'srad']
    availability = {}

    # Fast path: use MIN/MAX/COUNT on the first available variable table
    # instead of fetching all distinct dates for all 4 variables sequentially.
    # All variables for a source share the same date range (loaded together),
    # so querying one is sufficient and ~4x faster.
    cur = con.cursor()
    fast_done = False
    for var in variables:
        table = f'{source_prefix}_{var}'
        try:
            query = f'SELECT MIN(fdate), MAX(fdate), COUNT(DISTINCT fdate) FROM {schema}.{table}'
            conditions = []
            params_list = []
            if start_date:
                conditions.append('fdate >= %s')
                params_list.append(start_date)
            if end_date:
                conditions.append('fdate <= %s')
                params_list.append(end_date)
            if conditions:
                query += ' WHERE ' + ' AND '.join(conditions)

            cur.execute(query, params_list)
            row = cur.fetchone()
            if row and row[0] is not None:
                info = {
                    'count': row[2],
                    'first_date': str(row[0]),
                    'last_date': str(row[1]),
                }
                # Apply same result to all variables (they share dates)
                for v in variables:
                    availability[v] = info
                fast_done = True
                break
        except Exception:
            con.rollback()
            continue

    # Fallback: if fast path failed for all variables
    if not fast_done:
        for var in variables:
            availability[var] = {'count': 0, 'first_date': None, 'last_date': None}

    cur.close()
    con.close()

    return {'source': source, 'prefix': source_prefix, 'availability': availability}


def handle_query_time_series(params, prefix_map):
    """Query stored time-series data from PostGIS."""
    from data_agent.etl.etl_pipeline import ETL_Pipeline

    source = params.get('source')
    variables = params.get('variables', ['tmax', 'tmin', 'rain', 'srad'])
    lat = params.get('lat')
    lon = params.get('lon')
    bbox = params.get('bbox')
    start_date = params.get('start_date')
    end_date = params.get('end_date')
    ens = params.get('ens')

    if not source:
        return {'error': "Missing 'source' parameter"}

    source_prefix = prefix_map.get(source, '')
    if not source_prefix:
        return {'error': f"Unknown source: {source}"}

    schema = params.get('schema', settings.DATAAGENT_SCHEMA)

    if start_date and isinstance(start_date, str):
        start_date = datetime.date.fromisoformat(start_date)
    if end_date and isinstance(end_date, str):
        end_date = datetime.date.fromisoformat(end_date)

    bbox_tuple = None
    if bbox:
        bbox_tuple = (bbox['west'], bbox['south'], bbox['east'], bbox['north'])

    pipeline = ETL_Pipeline(None, None, None, None, None)
    con = pipeline._get_postgis_connection()

    try:
        # For forecast sources with ens column, filter by ensemble member
        if ens is not None:
            # Direct query with ens filter
            import numpy as np
            from pandas import DataFrame, Series

            cur = con.cursor()
            records_data = {}
            for var in variables:
                table = f'{source_prefix}_{var}'
                query = (
                    f"SELECT fdate, ST_value(ra.rast, pn.pt_geom) AS val "
                    f"FROM {schema}.{table} AS ra, "
                    f"(SELECT ST_SetSRID(ST_Point(%s, %s), 4326) AS pt_geom) AS pn "
                    f"WHERE ST_Within(pn.pt_geom, ST_Envelope(rast)) "
                    f"AND ens = %s"
                )
                query_params = [lon, lat, int(ens)]
                if start_date:
                    query += f" AND fdate >= %s"
                    query_params.append(start_date)
                if end_date:
                    query += f" AND fdate <= %s"
                    query_params.append(end_date)
                try:
                    cur.execute(query, query_params)
                    rows = cur.fetchall()
                    for row in rows:
                        fdate = row[0]
                        if fdate not in records_data:
                            records_data[fdate] = {'date': str(fdate)}
                        records_data[fdate][var] = float(row[1]) if row[1] is not None else None
                except Exception:
                    con.rollback()
            cur.close()
            records = sorted(records_data.values(), key=lambda r: r['date'])
            return {'records': records, 'count': len(records), 'variables': variables, 'ens': ens}

        df = pipeline.to_data_table(
            con, variables,
            start_date=start_date, end_date=end_date,
            lat=lat, lon=lon, bbox=bbox_tuple,
            source_prefix=source_prefix, schema=schema,
        )

        records = pipeline.to_json(df)
        return {'records': records, 'count': len(records), 'variables': variables}
    except ValueError as e:
        return {'error': str(e)}
    finally:
        con.close()


def handle_fetch_data(params, prefix_map):
    """Kick off a data fetch via Celery task."""
    from data_agent.models import RasterDataset, DataFetchJob
    from data_agent.tasks import fetch_data_task

    logger.info("handle_fetch_data called with params: %s", params)

    source = params.get('source')
    if not source:
        logger.warning("fetch_data: missing 'source' parameter")
        return {'error': "Missing 'source' parameter"}

    bbox = params.get('bbox')
    if not bbox:
        logger.warning("fetch_data: missing 'bbox' parameter")
        return {'error': "Missing 'bbox' parameter (west,south,east,north)"}

    start_date = params.get('start_date')
    end_date = params.get('end_date')
    if not start_date or not end_date:
        logger.warning("fetch_data: missing date parameters")
        return {'error': "Missing 'start_date' and/or 'end_date'"}

    schema = params.get('schema', 'dataagent')

    source_prefix = prefix_map.get(source, '')
    if not source_prefix:
        logger.warning("fetch_data: unknown source '%s'", source)
        return {'error': f"Unknown source: {source}"}

    # Find RasterDataset by dataset_subtype or table_prefix
    ds_info = RasterDataset.objects.filter(dataset_subtype=source).first()
    if not ds_info:
        for ds in RasterDataset.objects.all():
            info = ds.dataset_information or {}
            prefix = info.get('subroutines', {}).get(
                'load_to_postgis', {}
            ).get('table_prefix', '')
            if prefix == source_prefix:
                ds_info = ds
                break

    if not ds_info:
        logger.warning(
            "fetch_data: no RasterDataset with table_prefix='%s' for source '%s'",
            source_prefix, source,
        )
        return {'error': f"No RasterDataset found for source: {source}"}

    job = DataFetchJob.objects.create(
        raster_dataset=ds_info,
        schema_name=schema,
        bbox_west=bbox['west'],
        bbox_south=bbox['south'],
        bbox_east=bbox['east'],
        bbox_north=bbox['north'],
        start_date=start_date,
        end_date=end_date,
    )
    result = fetch_data_task.delay(str(job.id))
    job.celery_task_id = result.id
    job.save()
    logger.info(
        "fetch_data: created job %s for source '%s' (prefix='%s', schema='%s'), "
        "celery_task_id=%s",
        job.id, source, source_prefix, schema, result.id,
    )

    return {
        'source': source,
        'schema': schema,
        'job_id': str(job.id),
        'celery_task_id': result.id,
    }


def _parse_dates_cds_catalog(resp_json, start_date, end_date):
    """Extract temporal extent from a CDS/STAC catalog response and compute
    how many of the requested days fall within the source's available range."""
    extent = resp_json.get('extent', {})
    temporal = extent.get('temporal', {})
    intervals = temporal.get('interval', [])

    if not intervals:
        logger.warning("_parse_dates_cds_catalog: no temporal interval found. Keys: %s",
                        list(resp_json.keys()))
        return None, None, None

    interval = intervals[0]  # first (usually only) interval
    src_start_str = interval[0] if len(interval) > 0 else None
    src_end_str = interval[1] if len(interval) > 1 else None

    # Parse ISO timestamps (may have trailing Z or timezone)
    src_start = None
    src_end = None
    if src_start_str:
        src_start = datetime.date.fromisoformat(src_start_str[:10])
    if src_end_str:
        src_end = datetime.date.fromisoformat(src_end_str[:10])
    else:
        # null end = ongoing, use today
        src_end = datetime.date.today()

    logger.info(
        "_parse_dates_cds_catalog: source extent %s to %s, requested %s to %s",
        src_start, src_end, start_date, end_date,
    )

    # Compute overlap with requested range
    if not start_date or not end_date or not src_start:
        return str(src_start) if src_start else None, str(src_end), None

    overlap_start = max(start_date, src_start)
    overlap_end = min(end_date, src_end)
    if overlap_start > overlap_end:
        return str(src_start), str(src_end), 0

    days_available = (overlap_end - overlap_start).days + 1
    return str(src_start), str(src_end), days_available


def _parse_dates_regional_json_api(resp_json):
    """Extract available dates from a NASA POWER-style regional JSON response."""
    features = resp_json.get('features', [])
    if not features:
        props = resp_json.get('properties', {})
        if props:
            features = [resp_json]
    if not features:
        return []

    # Get date keys from the first feature's parameter data
    props = features[0].get('properties', {})
    parameter = props.get('parameter', {})
    for var_data in parameter.values():
        if isinstance(var_data, dict):
            dates = sorted(var_data.keys())
            logger.info("_parse_dates_regional_json_api: found %d date keys", len(dates))
            return dates
    return []


def _parse_dates_point_api_grid(resp_json):
    """Extract available dates from an Open-Meteo-style point JSON response."""
    daily = resp_json.get('daily', {})
    dates = daily.get('time', [])
    logger.info("_parse_dates_point_api_grid: found %d dates", len(dates))
    return dates


def _probe_all_dates(pipeline, ds_info, start_date, end_date, bbox=None, lat=None, lon=None):
    """HEAD-probe every date in the range for per-date-URL formats
    (file_download, grib2_bands).  Uses concurrent requests for speed."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from datetime import timedelta

    total_days = (end_date - start_date).days + 1
    all_dates = [start_date + timedelta(days=i) for i in range(total_days)]

    # Pre-build all URLs
    date_urls = []
    for d in all_dates:
        probe = pipeline.build_probe_url(ds_info, d, bbox=bbox, lat=lat, lon=lon)
        url = probe.get('url')
        if url:
            date_urls.append((d, url))

    if not date_urls:
        return 0, total_days, []

    logger.info("check_remote: probing %d date URLs concurrently", len(date_urls))

    def _head(item):
        d, url = item
        try:
            resp = http_requests.head(url, timeout=10, allow_redirects=True)
            if resp.status_code == 405:
                resp = http_requests.get(url, timeout=10, stream=True, allow_redirects=True)
                resp.close()
            return d, resp.status_code
        except http_requests.RequestException:
            return d, None

    found_dates = []
    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = {pool.submit(_head, item): item for item in date_urls}
        for future in as_completed(futures):
            d, status = future.result()
            if status is not None and status < 400:
                found_dates.append(str(d))

    found_dates.sort()
    logger.info(
        "check_remote: %d/%d dates reachable (range %s to %s)",
        len(found_dates), total_days, start_date, end_date,
    )
    return len(found_dates), total_days, found_dates


def _classify_error(status_code, body):
    """Parse an HTTP error response and return a user-friendly note.

    - 5xx / 401 / 403 → generic "Server error"
    - 400 with date-range info → extract and surface the allowed dates
    - Anything else → brief status summary
    """
    import json
    import re

    # Server-side or auth errors: don't expose internals
    if status_code >= 500 or status_code in (401, 403):
        return 'Server error'

    # Try to parse JSON body for a structured reason
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, TypeError):
        data = None

    if data:
        reason = data.get('reason', '') or data.get('message', '') or data.get('error', '')
        if isinstance(reason, str):
            # Look for "from YYYY-MM-DD to YYYY-MM-DD" pattern
            m = re.search(
                r'from\s+(\d{4}-\d{2}-\d{2})\s+to\s+(\d{4}-\d{2}-\d{2})',
                reason,
            )
            if m:
                return (
                    f"Requested dates are out of range. "
                    f"Available dates: {m.group(1)} to {m.group(2)}"
                )
            # Look for standalone date ranges like "YYYY-MM-DD - YYYY-MM-DD"
            m = re.search(
                r'(\d{4}-\d{2}-\d{2})\s*[-–]\s*(\d{4}-\d{2}-\d{2})',
                reason,
            )
            if m:
                return (
                    f"Requested dates are out of range. "
                    f"Available dates: {m.group(1)} to {m.group(2)}"
                )
            if reason:
                return reason

    return f'HTTP {status_code}'


def handle_check_remote(params):
    """Probe upstream URLs to check if a source's data is reachable,
    and report date availability at the requested location."""
    from data_agent.models import RasterDataset
    from data_agent.etl.etl_pipeline import ETL_Pipeline

    source = params.get('source')
    if not source:
        return {'error': "Missing 'source' parameter"}

    raw_start = params.get('start_date', '2023-06-15')
    raw_end = params.get('end_date')
    probe_date = datetime.date.fromisoformat(raw_start) if isinstance(raw_start, str) else raw_start
    end_date = datetime.date.fromisoformat(raw_end) if isinstance(raw_end, str) and raw_end else None

    lat = params.get('lat')
    lon = params.get('lon')
    bbox = params.get('bbox')

    logger.info(
        "check_remote: source='%s', start=%s, end=%s, lat=%s, lon=%s, bbox=%s",
        source, probe_date, end_date, lat, lon, bbox,
    )

    # Resolve source to dataset by dataset_subtype or table_prefix
    datasets = []
    ds = RasterDataset.objects.filter(dataset_subtype=source).first()
    if not ds:
        for candidate in RasterDataset.objects.all():
            info = candidate.dataset_information or {}
            prefix = info.get('subroutines', {}).get(
                'load_to_postgis', {}
            ).get('table_prefix', '')
            if prefix == source:
                ds = candidate
                break
    if ds:
        datasets = [ds]
        logger.info("check_remote: resolved '%s' to dataset '%s'", source, ds.dataset_name)

    if not datasets:
        logger.warning("check_remote: no datasets found for source '%s'", source)
        return {'error': f"No datasets found for source: {source}"}

    days_requested = (end_date - probe_date).days + 1 if end_date else None
    pipeline = ETL_Pipeline(None, None, None, None, None)
    checks = []

    for ds in datasets:
        ds_info = ds.dataset_information or {}
        prefix = ds_info.get('subroutines', {}).get(
            'load_to_postgis', {}
        ).get('table_prefix', 'unknown')
        fmt = ds_info.get('granule_info', {}).get('fetch_config', {}).get('format', 'unknown')

        logger.info(
            "check_remote: probing dataset '%s' (prefix='%s', format='%s')",
            ds.dataset_name, prefix, fmt,
        )

        check_result = {
            'source_name': ds.dataset_name,
            'prefix': prefix,
            'format': fmt,
            'reachable': False,
            'url_checked': '',
            'note': '',
            'days_available': None,
            'days_requested': days_requested,
            'first_date': None,
            'last_date': None,
        }

        # --- per-date-URL formats: HEAD-probe every date ---
        if fmt in ('file_download', 'grib2_bands') and end_date:
            found, total, found_dates = _probe_all_dates(
                pipeline, ds_info, probe_date, end_date,
                bbox=bbox, lat=lat, lon=lon,
            )
            check_result['reachable'] = found > 0
            check_result['days_available'] = found
            check_result['note'] = f'{found}/{total} dates reachable'
            if found_dates:
                check_result['first_date'] = found_dates[0]
                check_result['last_date'] = found_dates[-1]
            # For the URL display, show the first date's URL
            single_probe = pipeline.build_probe_url(ds_info, probe_date, bbox=bbox, lat=lat, lon=lon)
            check_result['url_checked'] = single_probe.get('url', '')
            checks.append(check_result)
            continue

        # --- API / other formats ---
        # Chunk by query_limits.max_days_per_request if configured
        fetch_cfg = ds_info.get('granule_info', {}).get('fetch_config', {})
        q_limits = fetch_cfg.get('query_limits', {})
        max_days = q_limits.get('max_days_per_request')

        date_chunks = []
        if max_days and end_date and (end_date - probe_date).days + 1 > max_days:
            chunk_start = probe_date
            while chunk_start <= end_date:
                chunk_end = min(chunk_start + datetime.timedelta(days=max_days - 1), end_date)
                date_chunks.append((chunk_start, chunk_end))
                chunk_start = chunk_end + datetime.timedelta(days=1)
            logger.info(
                "check_remote: splitting %d-day range into %d chunks of ≤%d days",
                (end_date - probe_date).days + 1, len(date_chunks), max_days,
            )
        else:
            date_chunks = [(probe_date, end_date)]

        # Probe each chunk and merge results
        all_dates_found = []
        any_reachable = False
        last_url = ''
        last_note = ''

        for chunk_start, chunk_end in date_chunks:
            probe = pipeline.build_probe_url(
                ds_info, chunk_start, end_date=chunk_end, bbox=bbox, lat=lat, lon=lon,
            )
            url = probe.get('url')
            last_url = url or last_url
            last_note = probe.get('note', '') or last_note

            if not url:
                continue

            try:
                method = probe.get('method', 'HEAD')
                logger.info("check_remote: sending %s request to %s (chunk %s to %s)",
                            method, url, chunk_start, chunk_end)
                if method == 'HEAD':
                    resp = http_requests.head(url, timeout=10, allow_redirects=True)
                    if resp.status_code == 405:
                        resp = http_requests.get(url, timeout=30, allow_redirects=True)
                else:
                    resp = http_requests.get(url, timeout=30, allow_redirects=True)

                if resp.status_code < 400:
                    any_reachable = True
                    probe_fmt = probe.get('format', '')
                    try:
                        if probe_fmt == 'regional_json_api':
                            dates = _parse_dates_regional_json_api(resp.json())
                            all_dates_found.extend(dates)
                        elif probe_fmt == 'point_api_grid':
                            dates = _parse_dates_point_api_grid(resp.json())
                            all_dates_found.extend(dates)
                        elif probe_fmt == 'xarray_slice/cds_api':
                            src_first, src_last, days_avail = _parse_dates_cds_catalog(
                                resp.json(), chunk_start, chunk_end,
                            )
                            if days_avail:
                                # Generate date list from extent overlap
                                d = chunk_start
                                while d <= chunk_end and days_avail > 0:
                                    all_dates_found.append(str(d))
                                    d += datetime.timedelta(days=1)
                                    days_avail -= 1
                    except Exception as e:
                        logger.warning("check_remote: failed to parse chunk response: %s", e)
                else:
                    body = resp.text[:300]
                    logger.warning("check_remote: chunk %s-%s returned HTTP %d: %s",
                                   chunk_start, chunk_end, resp.status_code, body)
                    last_note = _classify_error(resp.status_code, body)

            except http_requests.Timeout as e:
                last_note = f'Timeout: {e}'
                logger.warning("check_remote: timeout on chunk: %s", e)
            except http_requests.ConnectionError as e:
                last_note = f'Connection error: {e}'
                logger.warning("check_remote: connection error on chunk: %s", e)
            except http_requests.RequestException as e:
                last_note = str(e)
                logger.warning("check_remote: request error on chunk: %s", e)

        # Merge chunk results
        check_result['reachable'] = any_reachable
        check_result['url_checked'] = last_url
        check_result['note'] = last_note
        if all_dates_found:
            all_dates_found.sort()
            check_result['days_available'] = len(all_dates_found)
            check_result['first_date'] = all_dates_found[0]
            check_result['last_date'] = all_dates_found[-1]
        elif not any_reachable:
            check_result['note'] = last_note or 'Not reachable'

        checks.append(check_result)

    logger.info(
        "check_remote: finished — source='%s', %d checks, results=[%s]",
        source, len(checks),
        ', '.join(
            f"{c['source_name']}={'yes' if c['reachable'] else 'no'}"
            f"(days={c.get('days_available', '?')})"
            for c in checks
        ),
    )

    return {
        'source': source,
        'checks': checks,
    }
