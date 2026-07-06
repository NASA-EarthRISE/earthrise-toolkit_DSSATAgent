"""
Explorer views for the data_agent — generic data source browser, map viewer,
and the API endpoints they need.

All data is fetched via direct service calls (no A2A HTTP). API endpoints
that wrap data_agent.services functions live here too — the data_agent owns
its own API surface rather than reaching across into another sub-agent.
"""

import json
import logging

from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from data_agent import services as data_services
from data_agent.tiles import generate_tile

logger = logging.getLogger(__name__)

def _all_source_prefixes(raw):
    """Return every registered source prefix (single + combined).

    data_agent lists all sources; it does not decide which are "complete"
    for any particular consumer — each source advertises its own variables
    and callers filter accordingly.
    """
    prefixes = [s['prefix'] for s in raw.get('single_sources', [])]
    prefixes += [c['prefix'] for c in raw.get('combined_sources', [])]
    return prefixes


class ExplorerIndexView(View):
    """Data explorer landing page."""

    def get(self, request):
        return render(request, 'data_agent/explorer/index.html')


class DataExplorerView(View):
    """
    Generic data sources explorer — inspect registered rasters, check
    availability, view source/final variable mappings.
    """

    def get(self, request):
        return render(request, 'data_agent/explorer/data_explorer.html', {})


# Alias so WeatherExplorerView imports resolve to the generic data explorer view.
WeatherExplorerView = DataExplorerView


class MapExplorerView(View):
    """Render the Leaflet map explorer page.

    The map JS loads sources async via /data/api/sources/, so this view
    passes no source context anymore.
    """

    def get(self, request):
        return render(request, 'data_agent/explorer/map.html', {})


@method_decorator(csrf_exempt, name='dispatch')
class TileView(View):
    """Generate a map tile directly from PostGIS."""

    def get(self, request, source, variable, date, z, x, y):
        try:
            png_bytes = generate_tile(source, variable, date, z, x, y)
            return HttpResponse(
                png_bytes,
                content_type='image/png',
                headers={'Cache-Control': 'public, max-age=3600'},
            )
        except Exception as e:
            logger.error("Tile generation error: %s", e)
            return HttpResponse(status=500)


@method_decorator(csrf_exempt, name='dispatch')
class AdminBoundariesAPI(View):
    """
    Return admin boundaries as GeoJSON FeatureCollection (default) or as
    NDJSON stream (one feature per line) when requested via
    ``Accept: application/x-ndjson`` or ``?stream=1``.
    """

    def get(self, request):
        from django.http import StreamingHttpResponse

        level = request.GET.get('level', 'admin1')
        parent = request.GET.get('parent')
        use_stream = (
            'application/x-ndjson' in request.headers.get('Accept', '')
            or request.GET.get('stream') == '1'
        )

        try:
            if use_stream:
                gen = data_services.stream_admin_boundaries_ndjson(
                    level=level, parent=parent
                )
                return StreamingHttpResponse(
                    gen,
                    content_type='application/x-ndjson',
                    headers={'X-Content-Type-Options': 'nosniff'},
                )
            else:
                geojson = data_services.get_admin_boundaries_geojson(
                    level=level, parent=parent
                )
                return JsonResponse(geojson, safe=False)
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=400)


@method_decorator(csrf_exempt, name='dispatch')
class MapSourceDatesAPI(View):
    """Return available date range for a source."""

    def get(self, request):
        source = request.GET.get('source', '')
        if not source:
            return JsonResponse({'error': 'Missing source parameter'}, status=400)
        try:
            result = data_services.check_availability(source=source)
            return JsonResponse({'success': True, 'result': result})
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=400)


# =============================================================================
# Generic data API endpoints
#
# These wrap data_agent.services functions so the data_agent owns its
# own API surface rather than the explorer pages reaching elsewhere for
# endpoints. Mounted under /api/data/*.
# =============================================================================


@method_decorator(csrf_exempt, name='dispatch')
class DataSourcesAPI(View):
    """
    GET /api/data/sources/

    List every registered data source in a unified shape: time-series sources,
    combined sources, and static rasters. Used by the generic data
    explorer and the source dropdown on the map UI.

    Every registered source is returned with its declared ``variables`` and
    kind (single / combined / static). This endpoint applies NO consumer
    filtering: each source already advertises the variables it provides, so
    callers select the sources whose variables satisfy their own
    requirements (data_agent is domain-agnostic and holds no such rules).
    """

    def get(self, request):
        try:
            result = data_services.list_all_data_sources()
            return JsonResponse({'success': True, **result})
        except Exception as e:
            logger.exception("DataSourcesAPI error")
            return JsonResponse({'error': str(e)}, status=500)


@method_decorator(csrf_exempt, name='dispatch')
class DataAvailabilityCheckAPI(View):
    """
    POST /api/data/check/

    Check availability of one or more registered data sources over a date
    range and spatial extent. Returns per-source coverage info.

    Used by the wizard's "Check Weather" button on the management step and
    by the data source explorer's "Check Availability" button.
    """

    @staticmethod
    def _summarize(raw, start_date=None, end_date=None):
        """Collapse per-variable availability into one row per source."""
        from datetime import date as date_type

        availability = raw.get('availability', {})
        first_dates, last_dates, counts = [], [], []
        for info in availability.values():
            count = info.get('count', 0)
            counts.append(count)
            if count > 0:
                if info.get('first_date'):
                    first_dates.append(info['first_date'])
                if info.get('last_date'):
                    last_dates.append(info['last_date'])

        days_available = max(counts) if counts else 0

        days_requested = None
        if start_date and end_date:
            try:
                sd = date_type.fromisoformat(start_date) if isinstance(start_date, str) else start_date
                ed = date_type.fromisoformat(end_date) if isinstance(end_date, str) else end_date
                days_requested = (ed - sd).days + 1
            except (ValueError, TypeError):
                pass

        result = {
            'source': raw.get('source', ''),
            'available': bool(first_dates),
            'first_date': min(first_dates) if first_dates else None,
            'last_date': max(last_dates) if last_dates else None,
            'days_available': days_available,
            'days_requested': days_requested,
        }

        if days_requested and days_available >= days_requested:
            result['coverage'] = 'all'
        elif days_available > 0:
            result['coverage'] = 'partial'
        else:
            result['coverage'] = 'none'

        return result

    def post(self, request):
        try:
            data = json.loads(request.body)
            source = data.get('source', '')
            start_date = data.get('start_date')
            end_date = data.get('end_date')

            if source == 'all':
                raw_sources = data_services.list_raster_sources()
                prefixes = _all_source_prefixes(raw_sources)

                results = []
                for prefix in prefixes:
                    try:
                        raw = data_services.check_availability(
                            source=prefix,
                            start_date=start_date,
                            end_date=end_date,
                        )
                        results.append(self._summarize(raw, start_date, end_date))
                    except Exception:
                        results.append({
                            'source': prefix,
                            'available': False,
                            'first_date': None,
                            'last_date': None,
                            'days_available': 0,
                            'days_requested': None,
                            'coverage': 'none',
                        })
            else:
                raw = data_services.check_availability(
                    source=source,
                    start_date=start_date,
                    end_date=end_date,
                )
                results = [self._summarize(raw, start_date, end_date)]

            return JsonResponse({'success': True, 'results': results})
        except (json.JSONDecodeError, Exception) as e:
            logger.exception("DataAvailabilityCheckAPI error")
            return JsonResponse({'error': str(e)}, status=400)


@method_decorator(csrf_exempt, name='dispatch')
class DataRemoteCheckAPI(View):
    """
    POST /api/data/check-remote/

    Probe an upstream API source (e.g. NASA POWER, CHIRPS) for data
    availability without loading anything into PostGIS. Useful when local
    data is incomplete and the user wants to confirm the upstream has the
    requested range before kicking off an ETL fetch.
    """

    def post(self, request):
        try:
            data = json.loads(request.body)
            source = data.get('source', '')
            if not source:
                return JsonResponse({'error': 'Missing source'}, status=400)

            kwargs = {'source': source}
            if data.get('start_date'):
                kwargs['start_date'] = data['start_date']
            if data.get('end_date'):
                kwargs['end_date'] = data['end_date']
            if data.get('lat') is not None:
                kwargs['lat'] = data['lat']
            if data.get('lon') is not None:
                kwargs['lon'] = data['lon']
            if data.get('bbox'):
                kwargs['bbox'] = data['bbox']

            result = data_services.check_remote(**kwargs)
            return JsonResponse({'success': True, 'result': result})
        except (json.JSONDecodeError, Exception) as e:
            logger.exception("DataRemoteCheckAPI error")
            return JsonResponse({'error': str(e)}, status=400)


@method_decorator(csrf_exempt, name='dispatch')
class DataQueryAPI(View):
    """
    POST /api/data/query/

    Query stored raster data from PostGIS for a given source, variable list,
    and bbox/point. Returns the actual pixel values.
    """

    def post(self, request):
        try:
            data = json.loads(request.body)
            source = data.get('source', '')
            if not source:
                return JsonResponse({'error': 'Missing source'}, status=400)

            kwargs = {'source': source}
            if data.get('variables'):
                kwargs['variables'] = data['variables']
            if data.get('lat') is not None:
                kwargs['lat'] = data['lat']
            if data.get('lon') is not None:
                kwargs['lon'] = data['lon']
            if data.get('bbox'):
                kwargs['bbox'] = data['bbox']
            if data.get('start_date'):
                kwargs['start_date'] = data['start_date']
            if data.get('end_date'):
                kwargs['end_date'] = data['end_date']

            result = data_services.query_raster_data(**kwargs)
            return JsonResponse({'success': True, 'result': result})
        except (json.JSONDecodeError, Exception) as e:
            logger.exception("DataQueryAPI error")
            return JsonResponse({'error': str(e)}, status=400)


@method_decorator(csrf_exempt, name='dispatch')
class DataDatesAPI(View):
    """
    GET /api/data/dates/?source=X&month=2024-06

    Returns available dates for a source within the requested month ±1 month
    buffer. For static rasters (no time dimension), returns
    ``{static: true}``. Used by the date calendar widget to highlight
    available dates and grey out unavailable ones.
    """

    def get(self, request):
        source = request.GET.get('source', '')
        month_str = request.GET.get('month', '')  # YYYY-MM

        if not source:
            return JsonResponse({'error': 'source is required'}, status=400)

        from data_agent.models import RasterDataset
        from data_agent.handlers import _classify_storage_mode, _extract_variables

        try:
            ds = RasterDataset.objects.get(dataset_subtype=source)
        except RasterDataset.DoesNotExist:
            return JsonResponse({'error': f'Unknown source: {source}'}, status=404)

        info = ds.dataset_information or {}
        storage = _classify_storage_mode(info)

        # Static rasters — no dates
        if storage == 'multi_band_table':
            return JsonResponse({'success': True, 'static': True, 'dates': []})

        # Parse month + compute buffer (±1 month)
        import datetime
        if month_str:
            try:
                year, mon = int(month_str[:4]), int(month_str[5:7])
            except (ValueError, IndexError):
                year, mon = datetime.date.today().year, datetime.date.today().month
        else:
            year, mon = datetime.date.today().year, datetime.date.today().month

        # Start of previous month
        if mon == 1:
            buf_start = datetime.date(year - 1, 12, 1)
        else:
            buf_start = datetime.date(year, mon - 1, 1)

        # End of next month
        if mon == 12:
            next_year, next_mon = year + 1, 1
        else:
            next_year, next_mon = year, mon + 1
        if next_mon == 12:
            buf_end = datetime.date(next_year, 12, 31)
        else:
            buf_end = datetime.date(next_year, next_mon + 1, 1) - datetime.timedelta(days=1)

        # Query available dates using the first variable's table
        variables = _extract_variables(info)
        if not variables:
            return JsonResponse({'success': True, 'static': False, 'dates': []})

        prefix = (info.get('subroutines') or {}).get('load_to_postgis', {}).get('table_prefix', '')
        if not prefix:
            prefix = source

        from data_agent.etl.etl_pipeline import ETL_Pipeline
        from django.conf import settings
        schema = getattr(settings, 'DATAAGENT_SCHEMA', 'dataagent')

        pipeline = ETL_Pipeline(None, None, None, None, None)
        con = pipeline._get_postgis_connection()
        try:
            dates = pipeline.check_availability(
                con, schema, prefix, variables[0],
                start_date=buf_start, end_date=buf_end,
            )
        except Exception:
            dates = []
        finally:
            con.close()

        return JsonResponse({
            'success': True,
            'static': False,
            'dates': [d.isoformat() for d in dates],
            'month': f'{year:04d}-{mon:02d}',
            'buffer_start': buf_start.isoformat(),
            'buffer_end': buf_end.isoformat(),
        })


@method_decorator(csrf_exempt, name='dispatch')
class DataPointQueryAPI(View):
    """
    POST /api/data/point-query/

    Query ALL (or selected) registered data sources at a single lat/lon.

    - **Time-series/combined** sources return time-series: per-variable arrays
      of ``{date, value}`` pairs over the requested date range.
    - **Static rasters** return per-band values with band meaning labels.
      Categorical rasters include the raw integer pixel value; client-side
      legend lookup (via a sub-agent's insitu APIs) can resolve to
      human-readable labels if needed.

    Input::

        {
          "lat": 32.6,
          "lon": -86.7,
          "sources": "all"  |  ["nasa_power_daily", "soil_lookup_v1"],
          "start_date": "2024-01-01",   // for time-series sources
          "end_date": "2024-12-31"
        }

    Output::

        {
          "location": {"lat": 32.6, "lon": -86.7},
          "time_series_results": [ ... ],
          "static_results": [ ... ]
        }
    """

    def post(self, request):
        try:
            data = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({'error': 'Invalid JSON'}, status=400)

        try:
            lat = float(data['lat'])
            lon = float(data['lon'])
        except (KeyError, ValueError, TypeError):
            return JsonResponse(
                {'error': 'lat and lon are required (floats)'},
                status=400,
            )

        source_filter = data.get('sources', 'all')
        variable_filter = data.get('variable')  # optional: query only this variable
        start_date = data.get('start_date')
        end_date = data.get('end_date')

        from data_agent.handlers import (
            handle_list_all_data_sources,
            _classify_storage_mode,
            _extract_variables,
        )

        all_sources = handle_list_all_data_sources().get('sources', [])

        # Filter sources if caller requested specific ones.
        if source_filter != 'all' and isinstance(source_filter, list):
            all_sources = [s for s in all_sources if s['id'] in source_filter]

        time_series_results = []
        static_results = []

        for src in all_sources:
            src_id = src['id']
            src_name = src['name']
            storage = src.get('storage_mode', 'unknown')
            variables = src.get('variables', [])
            metadata = src.get('metadata', {})

            if storage == 'per_variable_tables':
                # Time-series / combined source query.
                # If a specific variable was requested, only query that one.
                query_vars = [variable_filter] if variable_filter and variable_filter in variables else variables
                if not query_vars:
                    continue
                try:
                    result = data_services.query_raster_data(
                        source=src_id,
                        variables=query_vars,
                        lat=lat,
                        lon=lon,
                        start_date=start_date,
                        end_date=end_date,
                    )
                    records = result.get('records', [])
                    if not records:
                        continue

                    # Pivot from [{date, var, ...}] to
                    # {variable: [{date, value}]} for charting.
                    var_series = {}
                    for var in query_vars:
                        series = []
                        for rec in records:
                            val = rec.get(var)
                            if val is not None:
                                series.append({
                                    'date': rec.get('date'),
                                    'value': val,
                                })
                        if series:
                            var_series[var] = series

                    if var_series:
                        time_series_results.append({
                            'source_id': src_id,
                            'source_name': src_name,
                            'variables': var_series,
                        })
                except Exception as e:
                    logger.warning(
                        "Point query for time-series source %s failed: %s",
                        src_id, e,
                    )

            elif storage == 'multi_band_table':
                # Static raster point query.
                coverage = metadata.get('coverage_extent')
                if coverage:
                    from data_agent.services import is_point_in_coverage
                    if not is_point_in_coverage(src_id, lat, lon):
                        continue

                try:
                    band_meanings = metadata.get('band_meanings', {})

                    # If a specific variable was requested, only query that band
                    if variable_filter and band_meanings:
                        filtered_bm = {
                            k: v for k, v in band_meanings.items()
                            if v == variable_filter
                        }
                        if filtered_bm:
                            band_meanings = filtered_bm

                    bands_to_query = sorted(
                        int(b) for b in band_meanings.keys()
                    ) if band_meanings else [1]

                    values = data_services.sample_raster_at_point(
                        src_id, lat, lon,
                        bands=bands_to_query,
                        no_data_value=metadata.get('no_data_value'),
                    )

                    # Resolve legend labels for categorical rasters
                    is_categorical = metadata.get('value_type') == 'categorical'

                    bands = {}
                    for band_idx, meaning in (band_meanings or {}).items():
                        val = values.get(int(band_idx))
                        label = str(val) if val is not None else None
                        if is_categorical and val is not None:
                            resolved = data_services.lookup_legend_label(src_id, val)
                            if resolved:
                                label = resolved
                        bands[str(meaning)] = {
                            'band': int(band_idx),
                            'value': val,
                            'label': label,
                        }

                    if any(b['value'] is not None for b in bands.values()):
                        static_results.append({
                            'source_id': src_id,
                            'source_name': src_name,
                            'value_type': metadata.get('value_type', 'unknown'),
                            'bands': bands,
                        })
                except Exception as e:
                    logger.warning(
                        "Point query for static source %s failed: %s",
                        src_id, e,
                    )

        return JsonResponse({
            'success': True,
            'location': {'lat': lat, 'lon': lon},
            'time_series_results': time_series_results,
            'static_results': static_results,
        })


@method_decorator(csrf_exempt, name='dispatch')
class DataZonalStatsAPI(View):
    """
    POST /api/data/zonal-stats/

    Compute zonal statistics over a GeoJSON polygon for a data source.

    - For time-series (per_variable_tables): uses PostGIS ST_SummaryStats to
      compute mean/min/max/count over the intersection of the raster and
      the polygon for a given date.
    - For categorical static rasters: uses ST_ValueCount to return a
      frequency table of pixel values within the polygon.

    Input::
        {
          "source": "power",
          "variable": "tmax",       // for time-series; ignored for categorical
          "date": "2024-06-15",     // for time-series; ignored for static
          "geometry": { GeoJSON geometry (Polygon or MultiPolygon) },
          "aggregation": "mean"     // for time-series: mean|min|max
        }
    """

    def post(self, request):
        try:
            data = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({'error': 'Invalid JSON'}, status=400)

        source = data.get('source', '')
        variable = data.get('variable')
        date_str = data.get('date')
        geometry = data.get('geometry')
        aggregation = data.get('aggregation', 'mean')

        if not source or not geometry:
            return JsonResponse(
                {'error': 'source and geometry are required'},
                status=400,
            )

        from data_agent.models import RasterDataset
        from data_agent.handlers import _classify_storage_mode
        from data_agent.tile_handler import _resolve_tile_source
        from django.conf import settings
        import psycopg2

        schema = getattr(settings, 'DATAAGENT_SCHEMA', 'dataagent')
        geojson_str = json.dumps(geometry)

        # Resolve the source table
        resolved = _resolve_tile_source(source, variable or '1')
        if not resolved:
            return JsonResponse({'error': f'Unknown source: {source}'}, status=404)

        table = resolved['table']
        band = resolved['band']
        has_date = resolved['has_date']
        value_type = resolved.get('value_type', 'continuous')

        from earthrise_agents_base.db import get_raw_connection
        con = get_raw_connection()

        try:
            cur = con.cursor()

            if value_type == 'categorical':
                # Frequency table via ST_ValueCount
                if has_date:
                    query = f"""
                        SELECT (pvc).value, (pvc).count
                        FROM (
                            SELECT ST_ValueCount(
                                ST_Union(ST_Clip(rast, ST_GeomFromGeoJSON(%s))),
                                {band}, true
                            ) AS pvc
                            FROM {table}
                            WHERE fdate = %s
                              AND ST_Intersects(rast, ST_GeomFromGeoJSON(%s))
                        ) sub
                        ORDER BY (pvc).count DESC
                    """
                    cur.execute(query, [geojson_str, date_str, geojson_str])
                else:
                    query = f"""
                        SELECT (pvc).value, (pvc).count
                        FROM (
                            SELECT ST_ValueCount(
                                ST_Union(ST_Clip(rast, ST_GeomFromGeoJSON(%s))),
                                {band}, true
                            ) AS pvc
                            FROM {table}
                            WHERE ST_Intersects(rast, ST_GeomFromGeoJSON(%s))
                        ) sub
                        ORDER BY (pvc).count DESC
                    """
                    cur.execute(query, [geojson_str, geojson_str])

                rows = cur.fetchall()
                total = sum(r[1] for r in rows) or 1
                categories = []
                for r in rows:
                    if r[0] is None:
                        continue
                    entry = {
                        'value': r[0],
                        'count': r[1],
                        'percentage': round(r[1] / total * 100, 1),
                    }
                    # Resolve legend label for this category value
                    resolved = data_services.lookup_legend_label(source, r[0])
                    entry['label'] = resolved or str(r[0])
                    categories.append(entry)

                return JsonResponse({
                    'success': True,
                    'type': 'categorical',
                    'variable_type': 'categorical',
                    'source': source,
                    'categories': categories,
                    'total_pixels': total,
                })

            else:
                # Summary stats for continuous/time-series data
                if has_date:
                    query = f"""
                        SELECT (stats).count, (stats).sum, (stats).mean,
                               (stats).stddev, (stats).min, (stats).max
                        FROM (
                            SELECT ST_SummaryStats(
                                ST_Union(ST_Clip(rast, ST_GeomFromGeoJSON(%s))),
                                {band}, true
                            ) AS stats
                            FROM {table}
                            WHERE fdate = %s
                              AND ST_Intersects(rast, ST_GeomFromGeoJSON(%s))
                        ) sub
                    """
                    cur.execute(query, [geojson_str, date_str, geojson_str])
                else:
                    query = f"""
                        SELECT (stats).count, (stats).sum, (stats).mean,
                               (stats).stddev, (stats).min, (stats).max
                        FROM (
                            SELECT ST_SummaryStats(
                                ST_Union(ST_Clip(rast, ST_GeomFromGeoJSON(%s))),
                                {band}, true
                            ) AS stats
                            FROM {table}
                            WHERE ST_Intersects(rast, ST_GeomFromGeoJSON(%s))
                        ) sub
                    """
                    cur.execute(query, [geojson_str, geojson_str])

                row = cur.fetchone()
                if not row or row[0] is None:
                    return JsonResponse({
                        'success': True,
                        'type': 'continuous',
                        'variable_type': 'numeric',
                        'source': source,
                        'variable': variable,
                        'stats': None,
                        'message': 'No data within the selected area',
                    })

                stats = {
                    'count': row[0],
                    'sum': row[1],
                    'mean': round(row[2], 3) if row[2] else None,
                    'stddev': round(row[3], 3) if row[3] else None,
                    'min': round(row[4], 3) if row[4] else None,
                    'max': round(row[5], 3) if row[5] else None,
                }

                # Also compute value distribution for histogram / frequency table
                categories = []
                total = 0
                try:
                    if has_date:
                        vc_query = f"""
                            SELECT (pvc).value, (pvc).count
                            FROM (
                                SELECT ST_ValueCount(
                                    ST_Union(ST_Clip(rast, ST_GeomFromGeoJSON(%s))),
                                    {band}, true
                                ) AS pvc
                                FROM {table}
                                WHERE fdate = %s
                                  AND ST_Intersects(rast, ST_GeomFromGeoJSON(%s))
                            ) sub ORDER BY (pvc).value
                        """
                        cur.execute(vc_query, [geojson_str, date_str, geojson_str])
                    else:
                        vc_query = f"""
                            SELECT (pvc).value, (pvc).count
                            FROM (
                                SELECT ST_ValueCount(
                                    ST_Union(ST_Clip(rast, ST_GeomFromGeoJSON(%s))),
                                    {band}, true
                                ) AS pvc
                                FROM {table}
                                WHERE ST_Intersects(rast, ST_GeomFromGeoJSON(%s))
                            ) sub ORDER BY (pvc).value
                        """
                        cur.execute(vc_query, [geojson_str, geojson_str])
                    vc_rows = cur.fetchall()
                    total = sum(r[1] for r in vc_rows)
                    for r in vc_rows:
                        categories.append({
                            'value': r[0],
                            'count': r[1],
                            'percentage': round(r[1] / total * 100, 1) if total else 0,
                        })
                except Exception:
                    pass  # value distribution is optional; stats are primary

                resp = {
                    'success': True,
                    'type': 'continuous',
                    'variable_type': 'numeric',
                    'source': source,
                    'variable': variable,
                    'date': date_str,
                    'stats': stats,
                    'aggregation': aggregation,
                }
                if categories:
                    resp['categories'] = categories
                    resp['total_pixels'] = total
                return JsonResponse(resp)

        except Exception as e:
            logger.exception("DataZonalStatsAPI error")
            return JsonResponse({'error': str(e)}, status=500)
        finally:
            con.close()
