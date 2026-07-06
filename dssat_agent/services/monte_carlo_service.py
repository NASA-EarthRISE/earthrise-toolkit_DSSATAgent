"""
Monte Carlo simulation service.

Orchestrates spatial uncertainty analysis by sampling weather grid points,
building batch treatments, running DSSATBatch, and computing quantile-based
aggregation statistics.

Reuses builders from experiment_service.py and batch execution pattern
from ensemble_service.py.
"""

import logging
import math
import os
import random

from DSSATTools.batch import DSSATBatch
from DSSATTools.filex import Field

from .crop_service import CROP_CLASSES, get_crop_class
from .cultivar_service import resolve_cultivar
from .soil_service import profile_to_dssat, dict_to_dssat_soil
from .weather_service import build_weather_station, check_weather_coverage
from .validation import _parse_date
from .experiment_service import (
    _build_simulation_controls,
    _build_planting,
    _build_fertilizer,
    _build_irrigation,
    _build_harvest,
    _build_initial_conditions,
    _build_residue,
    _build_chemical,
    _build_tillage,
    _build_mow,
    _clean_summary,
    _safe_float,
    capture_batch_input_files,
)
from . import spatial_data_service

logger = logging.getLogger(__name__)


def run_monte_carlo(params, progress_callback=None):
    """
    Run a Monte Carlo spatial simulation.

    Parameters
    ----------
    params : dict
        Monte Carlo experiment parameters including spatial_mode,
        grid/circle/admin definitions, nens, weather_source, soil,
        crop, cultivar, planting, and management sections.
    progress_callback : callable, optional
        Called as ``progress_callback(stage, title, description)``.

    Returns
    -------
    dict
        Results with quantile statistics, per-treatment detail, and metadata.
    """

    def _progress(stage, title, description=''):
        if progress_callback:
            try:
                progress_callback(stage, title, description)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # 1. Parse parameters
    # ------------------------------------------------------------------
    from dssat_agent.services.config import get_config, resolve_user
    _user = resolve_user(params.get('user_id'))

    spatial_mode = params.get('spatial_mode', 'circle')
    nens = params.get('nens', 30)
    sampling_strategy = params.get('sampling_strategy', 'random')
    raw_weather_source = (
        params.get('weather_source')
        or get_config('default_weather_source', 'power', user=_user)
    )
    # Map external/UI labels (``nasa_power_daily``, ``nasa_power``) to the
    # internal raster-table prefix (``power``). Without this the per-point
    # query in ``spatial_data_service.get_weather_for_point`` builds
    # ``dataagent.nasa_power_daily_tmax`` and reports "All weather points
    # failed" because that table doesn't exist. ``prepare_weather`` does
    # the same mapping for single experiments via SOURCE_TO_PREFIX; MC
    # was bypassing it.
    from dssat_agent.services.workflow import SOURCE_TO_PREFIX
    weather_source = SOURCE_TO_PREFIX.get(raw_weather_source, raw_weather_source)

    crop_code = params.get('crop_code', '').upper()
    cultivar_code = params.get('cultivar_code', '')
    mc_dssat_model = params.get('dssat_model', '').upper() or None

    if not crop_code or not cultivar_code:
        return {"status": "error", "errors": ["crop_code and cultivar_code are required"]}

    try:
        get_crop_class(crop_code, mc_dssat_model)
    except KeyError:
        return {"status": "error", "errors": [f"Unsupported crop: {crop_code}"]}

    # ------------------------------------------------------------------
    # 2. Resolve sample point list
    #
    # Preferred path: the wizard's Step 2 lock hook materialised one Field
    # per sampled grid point and Step 3 attached per-field soil/elevation,
    # so the submit payload already carries the explicit point list under
    # ``fields``. Use it directly — that's the only way per-field choices
    # actually reach DSSAT.
    #
    # Legacy fallback (when ``fields`` is absent — e.g. a chat-driven path
    # that hasn't been migrated yet): regenerate the grid + sample down to
    # ``nens`` exactly like before. All points share params['soil_id'] /
    # params['elevation'] / params['weather_source'] in this branch.
    # ------------------------------------------------------------------
    fields_explicit = params.get('fields') or []
    fields_by_point = {}
    if fields_explicit:
        _progress('grid_generation', 'Loading Field Set',
                  f'{len(fields_explicit)} fields attached')
        sampled_points = []
        for f in fields_explicit:
            lat = f.get('latitude')
            lon = f.get('longitude')
            if lat is None or lon is None:
                continue
            pt = (float(lon), float(lat))
            sampled_points.append(pt)
            fields_by_point[pt] = f
        grid_points = sampled_points  # for metadata + the count log below
    else:
        _progress('grid_generation', 'Generating Grid Points',
                  f'Spatial mode: {spatial_mode}')
        try:
            grid_points = _generate_grid(params, spatial_mode, weather_source)
        except Exception as e:
            logger.exception("Grid generation failed")
            return {"status": "error", "errors": [f"Grid generation failed: {e}"]}
        if not grid_points:
            return {"status": "error", "errors": ["No grid points generated for the specified region"]}
        _progress('sampling', 'Sampling Weather Points',
                  f'{nens} samples from {len(grid_points)} grid points ({sampling_strategy})')
        sampled_points = _sample_points(grid_points, nens, sampling_strategy)

    if not sampled_points:
        return {"status": "error", "errors": ["No sample points to run"]}
    logger.info("MC: %d points available, %d sampled",
                len(grid_points), len(sampled_points))

    # ------------------------------------------------------------------
    # 3a. Pre-fetch weather for the whole region.
    # ------------------------------------------------------------------
    # ``run_full_simulation`` skips ``prepare_weather`` for MC because
    # the per-point query in step 5 below knows how to read PostGIS
    # rasters per lat/lon. But that read is a no-op when the source
    # hasn't been fetched + ingested for the points' bbox yet — which
    # is the common case for an MC region wider than any prior single
    # run's 0.5° bbox. Without this pre-fetch, every per-point query
    # returns 0 rows and we report "All weather points failed."
    sc = params.get('simulation_controls', {})
    sdate_obj = _parse_date(sc.get('sdate'))
    nyers_pre = sc.get('nyers', 1)
    if sdate_obj is not None:
        from datetime import date as _date, timedelta as _td
        end_obj = _date(sdate_obj.year + nyers_pre,
                        sdate_obj.month, sdate_obj.day) - _td(days=1)
        lons = [pt[0] for pt in sampled_points]
        lats = [pt[1] for pt in sampled_points]
        margin = 0.25
        bbox = {
            'west':  min(lons) - margin,
            'east':  max(lons) + margin,
            'south': min(lats) - margin,
            'north': max(lats) + margin,
        }
        try:
            from data_agent import services as _data_svc
            _progress('fetching_weather', 'Pre-fetching Weather',
                      f'Bbox covering {len(sampled_points)} points...')
            _data_svc.fetch_data_sync(
                source=raw_weather_source,  # use pre-mapped name
                bbox=bbox,
                start_date=sdate_obj.isoformat(),
                end_date=end_obj.isoformat(),
            )
        except Exception as e:
            logger.warning(
                "MC: weather pre-fetch failed (%s); proceeding to "
                "per-point queries which may also miss.", e,
            )

    # ------------------------------------------------------------------
    # 4. Build shared objects (soil, cultivar, sim controls, planting, etc.)
    # ------------------------------------------------------------------
    _progress('building', 'Building Experiment',
              'Constructing shared components...')

    try:
        # Soil. The new wizard path attaches one Field per sample point and
        # carries soil_id (or inline_soil) on each Field, so resolution is
        # per-point inside the loop below; the shared `soil` is only used
        # by the legacy fallback path. Build it lazily so a per-field path
        # without a top-level soil_id doesn't trip the validator.
        soil = None
        if not fields_by_point:
            if params.get('soil_id'):
                soil = profile_to_dssat(params['soil_id'])
            elif params.get('inline_soil'):
                soil = dict_to_dssat_soil(params['inline_soil'])
            else:
                return {"status": "error", "errors": ["soil_id or inline_soil is required"]}

        # Cultivar
        cultivar = resolve_cultivar(crop_code, cultivar_code, mc_dssat_model)

        # Simulation controls
        sim_controls = _build_simulation_controls(params)

        # Planting
        planting = _build_planting(params)

        # Optional management
        _mc_user_id = params.get('user_id')
        fertilizer = _build_fertilizer(params.get('fertilizer'), user_id=_mc_user_id)
        irrigation = _build_irrigation(params.get('irrigation'), user_id=_mc_user_id)
        harvest = _build_harvest(
            params.get('harvest'), user_id=_mc_user_id,
            planting_params=params.get('planting'),
        )
        initial_conditions = _build_initial_conditions(params.get('initial_conditions'))
        residue = _build_residue(params.get('residue'), user_id=_mc_user_id)
        chemical = _build_chemical(params.get('chemical'), user_id=_mc_user_id)
        tillage = _build_tillage(params.get('tillage'), user_id=_mc_user_id)
        mow = _build_mow(params.get('mow'), user_id=_mc_user_id)

    except Exception as e:
        logger.exception("MC: Failed to build shared components")
        return {"status": "error", "errors": [f"Build error: {e}"]}

    # ------------------------------------------------------------------
    # 5. Fetch weather and build per-treatment objects
    # ------------------------------------------------------------------
    _progress('fetching_weather', 'Fetching Weather Data',
              f'Querying rasters for {len(sampled_points)} points...')

    sc = params.get('simulation_controls', {})
    sdate = _parse_date(sc.get('sdate'))
    nyers = sc.get('nyers', 1)
    from datetime import date, timedelta
    end_date = date(sdate.year + nyers, sdate.month, sdate.day) - timedelta(days=1)

    treatment_objects = []
    treatment_meta = []
    warnings = []

    for idx, (lon, lat) in enumerate(sampled_points):
        try:
            # Per-point soil + elevation come off the explicit Field record
            # when present; otherwise fall back to the shared params.
            f_data = fields_by_point.get((lon, lat))
            if f_data:
                if f_data.get('soil_id'):
                    point_soil = profile_to_dssat(f_data['soil_id'])
                elif f_data.get('inline_soil'):
                    point_soil = dict_to_dssat_soil(f_data['inline_soil'])
                elif soil is not None:
                    point_soil = soil
                else:
                    warnings.append(
                        f"Field at ({lat}, {lon}) has no soil set, skipping")
                    continue
                point_elev = f_data.get('elevation', params.get('elevation'))
            else:
                point_soil = soil
                point_elev = params.get('elevation')

            # Per-point planting date: when the SPA's Step 4 lock pre-resolved
            # an Auto-mode planting date for this field, ``f_data['pdate']``
            # carries the absolute YYYY-MM-DD. Build a fresh Planting
            # instance with that date so each grid point gets its own row in
            # `*PLANTING DETAILS`. Without an override we reuse the shared
            # template planting (Fixed-Date semantics).
            point_planting = planting
            point_pdate = (f_data or {}).get('pdate')
            if point_pdate:
                from copy import deepcopy
                point_params = deepcopy(params)
                point_params.setdefault('planting', {})
                point_params['planting'] = {
                    **(point_params.get('planting') or {}),
                    'pdate': point_pdate,
                }
                point_params['planting_date'] = point_pdate
                try:
                    point_planting = _build_planting(point_params)
                except Exception as e:
                    warnings.append(
                        f"Failed to build per-field planting at ({lat}, {lon}): {e}")
                    point_planting = planting

            records = spatial_data_service.get_weather_for_point(
                weather_prefix=weather_source,
                lon=lon, lat=lat,
                start_date=sdate.isoformat(),
                end_date=end_date.isoformat(),
            )

            if not records:
                warnings.append(f"No weather data for point ({lat}, {lon}), skipping")
                continue

            weather_data = {
                "station": {
                    "lat": lat,
                    "lon": lon,
                    "elev": point_elev or 0,
                    "insi": weather_source[:4].upper(),
                },
                "records": records,
            }

            # Check coverage
            coverage = check_weather_coverage(weather_data, sdate, nyers)
            if not coverage.get('complete', False):
                warnings.append(
                    f"Incomplete weather for ({lat}, {lon}): "
                    f"{coverage.get('total_missing', 0)} days missing, skipping"
                )
                continue

            weather = build_weather_station(weather_data)

            field = Field(
                id_field=f'MC{idx:06d}',
                wsta=weather,
                id_soil=point_soil,
                xcrd=lon,
                ycrd=lat,
                elev=point_elev,
            )

            treatment_objects.append({
                'field': field,
                'cultivar': cultivar,
                'planting': point_planting,
                'sim_controls': sim_controls,
                'fertilizer': fertilizer,
                'irrigation': irrigation,
                'harvest': harvest,
                'initial_conditions': initial_conditions,
                'residue': residue,
                'chemical': chemical,
                'tillage': tillage,
                'mow': mow,
            })
            treatment_meta.append({
                'index': idx,
                'lat': lat,
                'lon': lon,
            })

        except Exception as e:
            logger.exception("MC: build treatment failed at (%s, %s)", lat, lon)
            warnings.append(f"Failed to build treatment for ({lat}, {lon}): {e}")
            continue

    if not treatment_objects:
        # Surface the per-point warnings so future failures are diagnosable
        # from celery logs without having to re-run with a debugger attached.
        for w in warnings:
            logger.error("MC point failure: %s", w)
        return {
            "status": "error",
            "errors": ["No valid treatments could be built. All weather points failed."],
            "warnings": warnings,
        }

    # ------------------------------------------------------------------
    # 6. Run DSSATBatch
    # ------------------------------------------------------------------
    _progress('running', 'Running DSSAT Batch',
              f'Executing {len(treatment_objects)} treatments...')

    batch = DSSATBatch()
    shared_inputs = {}
    output_files = {}
    try:
        for trt in treatment_objects:
            batch.add_treatment(
                field=trt['field'],
                cultivar=trt['cultivar'],
                planting=trt['planting'],
                simulation_controls=trt['sim_controls'],
                fertilizer=trt.get('fertilizer'),
                irrigation=trt.get('irrigation'),
                harvest=trt.get('harvest'),
                initial_conditions=trt.get('initial_conditions'),
                residue=trt.get('residue'),
                chemical=trt.get('chemical'),
                tillage=trt.get('tillage'),
                mow=trt.get('mow'),
            )

        results = batch.run(verbose=False)

        # Capture input + output files from batch.run_path before close()
        # removes the directory in the finally block. Inputs are read from
        # disk so they exactly match what DSSAT consumed. The .SPE and
        # GRSTAGE.CDE files are captured from their canonical source paths
        # via reference_crops (MC uses one shared cultivar across treatments).
        _ref_crops = [cultivar] if cultivar is not None else None
        shared_inputs = capture_batch_input_files(batch, reference_crops=_ref_crops)
        if hasattr(batch, 'output_files') and batch.output_files:
            for key, content in batch.output_files.items():
                if isinstance(content, str):
                    output_files[key] = content

    except Exception as e:
        logger.exception("MC: DSSAT batch execution failed")
        # Capture whatever DSSAT wrote before crashing — input + partial
        # output files. Without this the run dir is wiped in the finally
        # block and we can't see the actual FILEX/SOIL.SOL the parser
        # rejected.
        failure_inputs = {}
        try:
            _ref = [cultivar] if cultivar is not None else None
            failure_inputs = capture_batch_input_files(batch, reference_crops=_ref)
        except Exception:
            logger.warning("MC: failed to capture inputs after DSSAT failure",
                           exc_info=True)
        failure_outputs = {}
        try:
            run_path = getattr(batch, 'run_path', None)
            if run_path and os.path.isdir(run_path):
                for fname in os.listdir(run_path):
                    if fname.upper().endswith(('.OUT', '.LST')):
                        try:
                            with open(os.path.join(run_path, fname)) as fh:
                                failure_outputs[fname.rsplit('.', 1)[0]] = fh.read()
                        except Exception:
                            pass
        except Exception:
            logger.warning("MC: failed to capture outputs after DSSAT failure",
                           exc_info=True)
        return {
            "status": "failed",
            "errors": [str(e)],
            "warnings": warnings,
            "dssat_files": {
                "input": failure_inputs,
                "output": failure_outputs,
            },
        }
    finally:
        try:
            batch.close()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # 7. Enhanced aggregation
    # ------------------------------------------------------------------
    _progress('aggregating', 'Computing Statistics',
              'Calculating quantiles and summary statistics...')

    # Clean summaries before quantile aggregation so ``_compute_mc_quantiles``
    # sees the derived DAP fields (mat, flo, emergence_dap) instead of raw
    # YYYYDDD calendar dates from ``mdat``.
    cleaned_results = []
    for r in results:
        cleaned_results.append({
            **r,
            'summary': _clean_summary(r.get('summary', {})),
        })
    quantiles = _compute_mc_quantiles(cleaned_results)

    # Per-treatment detail
    treatments_detail = []
    for i, (result, meta) in enumerate(zip(cleaned_results, treatment_meta)):
        treatments_detail.append({
            'index': meta['index'],
            'lat': meta['lat'],
            'lon': meta['lon'],
            'summary': result['summary'],
            'dssat_files': {
                'input': dict(shared_inputs),
                'output': output_files,
            },
        })

    return {
        "status": "completed",
        "nens": nens,
        "spatial_mode": spatial_mode,
        "sampling_strategy": sampling_strategy,
        "grid_points_available": len(grid_points),
        "treatments_run": len(results),
        "quantiles": quantiles,
        "treatments": treatments_detail,
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# Grid generation dispatch
# ---------------------------------------------------------------------------

def _generate_grid(params, spatial_mode, weather_source):
    """Generate the list of (lon, lat) points for an MC run.

    Strategy is the primary axis:

      * ``systematic`` — deterministic lattice over the geometry at
        ``grid_spacing`` (degrees). Number of points falls out of the
        geometry; the user controls *density*. ``n_points`` is ignored.

      * ``random`` — uniform random sampling inside the geometry,
        producing exactly ``n_points`` samples. ``grid_spacing`` is
        ignored. (Implemented per-mode: rejection sampling inside the
        circle, plain uniform inside a bbox, PostGIS ``ST_GeneratePoints``
        inside an admin polygon.)

    Both strategies still feed downstream into the same DSSAT batch
    pipeline; the distinction lives entirely here.
    """
    if 'num_points' in params:
        logger.warning("num_points is deprecated; use n_points + sampling_strategy='random'")

    strategy = (params.get('sampling_strategy') or 'systematic').lower()
    grid_spacing = params.get('grid_spacing', 0.1)
    # `n_points` is the new canonical name for the random-strategy sample
    # count; `nens` is the legacy alias and `num_points` the older one.
    n_points = (params.get('n_points')
                or params.get('nens')
                or params.get('num_points')
                or 30)

    if spatial_mode == 'bbox':
        bbox = params.get('bbox', {})
        if strategy == 'random':
            return spatial_data_service.random_points_in_bbox(
                min_lat=bbox.get('min_lat', 0),
                max_lat=bbox.get('max_lat', 0),
                min_lon=bbox.get('min_lon', 0),
                max_lon=bbox.get('max_lon', 0),
                n_points=int(n_points),
            )
        return spatial_data_service.generate_grid_from_bbox(
            min_lat=bbox.get('min_lat', 0),
            max_lat=bbox.get('max_lat', 0),
            min_lon=bbox.get('min_lon', 0),
            max_lon=bbox.get('max_lon', 0),
            spacing_deg=grid_spacing,
        )
    elif spatial_mode == 'circle':
        center = params.get('center', {})
        if strategy == 'random':
            return spatial_data_service.random_points_in_circle(
                center_lat=center.get('lat', 0),
                center_lon=center.get('lon', 0),
                radius_km=params.get('radius_km', 25),
                n_points=int(n_points),
            )
        return spatial_data_service.generate_grid_from_circle(
            center_lat=center.get('lat', 0),
            center_lon=center.get('lon', 0),
            radius_km=params.get('radius_km', 25),
            grid_spacing=grid_spacing,
        )
    elif spatial_mode == 'admin':
        admin_kwargs = {
            'admin_name': params.get('admin_name', ''),
            'level': params.get('admin_level', 'admin1'),
            # Parent context disambiguates names that aren't unique
            # (e.g. duplicate "Jefferson" counties across US states).
            'parent_admin1': params.get('admin_parent_admin1') or None,
            'parent_country': params.get('admin_country') or None,
        }
        if strategy == 'random':
            return spatial_data_service.random_points_in_admin(
                n_points=int(n_points), **admin_kwargs)
        return spatial_data_service.generate_grid_from_admin(
            grid_spacing=grid_spacing, **admin_kwargs)
    else:
        raise ValueError(f"Unknown spatial_mode: {spatial_mode}")


# ---------------------------------------------------------------------------
# Sampling
# ---------------------------------------------------------------------------

def _sample_points(grid_points, nens, strategy='random'):
    """
    Select nens points from grid_points.

    Parameters
    ----------
    grid_points : list of (lon, lat)
    nens : int
    strategy : str
        'random' or 'systematic'

    Returns
    -------
    list of (lon, lat)
    """
    n = len(grid_points)

    if strategy == 'systematic':
        # Evenly spaced selection
        if nens >= n:
            return list(grid_points)
        step = n / nens
        indices = [int(i * step) for i in range(nens)]
        return [grid_points[i] for i in indices]
    else:
        # Random sampling with replacement if nens > n
        if nens <= n:
            return random.sample(grid_points, nens)
        else:
            return random.choices(grid_points, k=nens)


# ---------------------------------------------------------------------------
# Quantile computation
# ---------------------------------------------------------------------------

def _compute_mc_quantiles(results):
    """
    Compute enhanced quantile statistics across MC treatments.

    Returns dict with yield and maturity quantile objects.
    """
    yields = []
    maturities = []

    for r in results:
        s = r.get('summary', {})
        hwam = _safe_float(s.get('hwam'))
        if hwam is not None:
            yields.append(hwam)
        # ``mat`` is days-after-planting; ``mdat`` is the YYYYDDD calendar
        # date that the cleaning pass already used to derive ``mat``.
        # Aggregating ``mdat`` directly produces values in the millions,
        # which is meaningless for "days to maturity."
        mat_dap = _safe_float(s.get('mat'))
        if mat_dap is not None:
            maturities.append(mat_dap)

    quantiles = {}

    if yields:
        quantiles['yield_kg_ha'] = _quantile_stats(yields)

    if maturities:
        quantiles['maturity_days'] = _quantile_stats(maturities)

    return quantiles


def _quantile_stats(values):
    """Compute percentile-based statistics for a list of values."""
    if not values:
        return {}

    values_sorted = sorted(values)
    n = len(values_sorted)
    mean = sum(values_sorted) / n

    stats = {
        'mean': round(mean, 1),
        'min': round(values_sorted[0], 1),
        'max': round(values_sorted[-1], 1),
        'count': n,
    }

    if n > 1:
        variance = sum((v - mean) ** 2 for v in values_sorted) / (n - 1)
        stats['std'] = round(math.sqrt(variance), 1)
    else:
        stats['std'] = 0.0

    # Percentiles
    for p in [5, 10, 25, 50, 75, 90, 95]:
        stats[f'p{p}'] = round(_percentile(values_sorted, p), 1)

    return stats


def _percentile(sorted_values, p):
    """Compute the p-th percentile from a sorted list."""
    n = len(sorted_values)
    if n == 1:
        return sorted_values[0]
    k = (n - 1) * p / 100.0
    f = int(k)
    c = f + 1
    if c >= n:
        return sorted_values[-1]
    return sorted_values[f] + (k - f) * (sorted_values[c] - sorted_values[f])
