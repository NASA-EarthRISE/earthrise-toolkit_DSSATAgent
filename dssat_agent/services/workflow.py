"""
High-level workflow services for dssat_agent.

These orchestrate data fetching, experiment assembly, and simulation execution.
The chat agent calls these instead of doing domain logic directly.
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_WEATHER_SOURCE = 'power'  # hardcoded fallback; overridden by DSSATConfig

# Weather source name -> PostGIS table prefix. The wizard / data
# explorer surface several aliases for the same underlying NASA POWER
# raster ingest (``power``); add every label users can pick.
SOURCE_TO_PREFIX = {
    'power': 'power', 'nasa_power': 'power',
    'nasa_power_daily': 'power', 'POWER': 'power',
    'era5': 'era5', 'agera5': 'agera5',
    'chirps': 'chirps', 'chirts': 'chirts',
    'chirps_chirts_era5': 'chirps_chirts_era5',
    'chirps_chirts_power': 'chirps_chirts_power',
    'chirps_chirts_agera5': 'chirps_chirts_agera5',
    'chirps_era5': 'chirps_era5',
    'chirps_agera5': 'chirps_agera5',
    'chirps_power': 'chirps_power',
}


def _days_inclusive(start_date: str, end_date: str) -> int:
    d0 = datetime.strptime(start_date, "%Y-%m-%d").date()
    d1 = datetime.strptime(end_date, "%Y-%m-%d").date()
    return (d1 - d0).days + 1


def _covers_full_range(records, start_date: str, end_date: str) -> bool:
    # True iff `records` contains an entry for every day in [start_date, end_date].
    d0 = datetime.strptime(start_date, "%Y-%m-%d").date()
    d1 = datetime.strptime(end_date, "%Y-%m-%d").date()
    needed = set()
    cur = d0
    while cur <= d1:
        needed.add(cur.isoformat())
        cur += timedelta(days=1)
    have = {str(r.get('date'))[:10] for r in records if r.get('date')}
    return needed.issubset(have)


def prepare_weather(
    crop_code: str,
    planting_date: str,
    latitude: float,
    longitude: float,
    elevation: float = 100.0,
    weather_source: str = None,
    num_years: int = 1,
    start_date_override: str = None,
    end_date_override: str = None,
) -> Dict:
    """
    Internal: prepare weather data for a simulation. Use run_full_simulation instead.
    fetches from remote if needed, queries the data, and formats it
    for DSSAT consumption.

    Returns:
        dict with 'status' ('ready'|'unavailable'|'error'),
        'weather_data' (formatted for DSSAT if ready),
        'start_date', 'end_date' (the range used).
    """
    from data_agent import services as data_svc

    source = weather_source or DEFAULT_WEATHER_SOURCE
    prefix = SOURCE_TO_PREFIX.get(source, source)

    # Calculate date range
    if start_date_override and end_date_override:
        start_date = start_date_override
        end_date = end_date_override
    else:
        try:
            plant_dt = datetime.strptime(planting_date, "%Y-%m-%d")
        except (ValueError, TypeError):
            return {'status': 'error', 'error': f'Invalid planting date: {planting_date}'}

        sdate_dt = plant_dt - timedelta(days=30)
        end_dt = datetime(sdate_dt.year + num_years, sdate_dt.month, sdate_dt.day) - timedelta(days=1)
        start_date = sdate_dt.strftime("%Y-%m-%d")
        end_date = end_dt.strftime("%Y-%m-%d")

    # Check local availability and query
    try:
        weather_result = data_svc.query_raster_data(
            source=prefix,
            variables=['tmax', 'tmin', 'rain', 'srad'],
            lat=latitude,
            lon=longitude,
            start_date=start_date,
            end_date=end_date,
        )
        records = weather_result.get('records', [])
        valid = [r for r in records if all(
            r.get(v) is not None for v in ('tmax', 'tmin', 'rain', 'srad')
        )]
        # Only short-circuit on the local result if it actually covers the
        # full requested range. Partial coverage can happen when historical
        # rasters were stored with a bbox that doesn't contain this point,
        # and we need to fall through to a remote re-fetch so the raster
        # ingest extends coverage for this location.
        if valid and _covers_full_range(valid, start_date, end_date):
            weather_data = _format_weather_for_simulation(
                valid, latitude, longitude, elevation
            )
            return {
                'status': 'ready',
                'weather_data': weather_data,
                'start_date': start_date,
                'end_date': end_date,
            }
        if valid:
            logger.info(
                "Local weather for %s at (%s, %s) is incomplete: %d/%d days. "
                "Falling through to remote fetch.",
                source, latitude, longitude,
                len(valid), _days_inclusive(start_date, end_date),
            )
    except Exception as e:
        logger.warning("Weather query failed: %s", e)

    # Fetch from remote (blocking until complete)
    try:
        bbox = {
            'west': longitude - 0.25,
            'south': latitude - 0.25,
            'east': longitude + 0.25,
            'north': latitude + 0.25,
        }
        logger.info("Fetching weather data for %s (blocking)...", source)
        data_svc.fetch_data_sync(
            source=source,
            bbox=bbox,
            start_date=start_date,
            end_date=end_date,
        )
        logger.info("Weather fetch complete for %s, querying data", source)

        # Query the now-loaded data
        try:
            weather_result = data_svc.query_raster_data(
                source=prefix,
                variables=['tmax', 'tmin', 'rain', 'srad'],
                lat=latitude,
                lon=longitude,
                start_date=start_date,
                end_date=end_date,
            )
            records = weather_result.get('records', [])
            valid = [r for r in records if all(
                r.get(v) is not None for v in ('tmax', 'tmin', 'rain', 'srad')
            )]
            if valid:
                weather_data = _format_weather_for_simulation(
                    valid, latitude, longitude, elevation
                )
                return {
                    'status': 'ready',
                    'weather_data': weather_data,
                    'start_date': start_date,
                    'end_date': end_date,
                }
        except Exception as e:
            logger.warning("Post-fetch query failed: %s", e)

    except Exception as e:
        logger.error("Weather fetch failed: %s", e)

    return {
        'status': 'unavailable',
        'error': f'Weather data not available for {source} ({start_date} to {end_date})',
        'start_date': start_date,
        'end_date': end_date,
    }


def build_experiment(
    params: Dict,
    wizard_params: Optional[Dict] = None,
) -> Dict:
    """
    Internal: assemble a DSSAT experiment definition. Use run_full_simulation instead.

    Args:
        params: dict with crop_code, cultivar_code, soil_id, latitude,
                longitude, elevation, planting_date, weather_data, etc.
        wizard_params: optional pre-loaded wizard configuration.

    Returns:
        dict with 'status' ('ready'|'error'), 'experiment' (the definition).
    """
    from dssat_agent.services.executor import dispatch_skill

    # Resolve experiment type from wizard_params or params
    exp_type = (
        (wizard_params or {}).get('experiment_type')
        or params.get('experiment_type')
        or _infer_experiment_type(params)
    )

    # --- Batch ---
    if exp_type == 'batch':
        # SPA-draft path supplies a pre-built ``pair_params`` list (one
        # per (Field, Treatment) WizardDraftTreatment row); pass it
        # through verbatim so ``_handle_run_batch`` can fan it out as one
        # child per pair. Chat-driven path supplies
        # ``location_selection_mode`` + ``batch_locations`` +
        # ``template_params`` in ``wizard_params``; merge those into
        # ``params`` so ``_build_batch_defaults`` finds them.
        if wizard_params and wizard_params.get('pair_params'):
            experiment = dict(wizard_params)
            return {'status': 'ready', 'experiment': experiment}
        merged = dict(params)
        if wizard_params and wizard_params.get('batch_locations'):
            for k in ('location_selection_mode', 'batch_locations',
                      'template_params', 'sub_experiment_type',
                      'admin_parent', 'admin_parent_level',
                      'admin_child_level', 'bbox', 'grid_spacing'):
                if wizard_params.get(k) is not None:
                    merged[k] = wizard_params[k]
        experiment = _build_batch_defaults(merged)
        return {'status': 'ready', 'experiment': experiment}

    # --- Monte Carlo ---
    if exp_type == 'monte_carlo':
        wp = wizard_params or _build_monte_carlo_defaults(params)
        experiment = _build_monte_carlo(params, wp)
        return {'status': 'ready', 'experiment': experiment}

    # --- Ensemble (and sensitivity, which is materialised as an
    # ensemble of swept treatments in the build path) ---
    if exp_type in ('ensemble', 'sensitivity'):
        wp = wizard_params if (wizard_params and 'base' in wizard_params) else _build_ensemble_defaults(params)
        experiment = _build_ensemble(params, wp)
        # Tag the experiment so the run-skill router treats sensitivity
        # like ensemble. Down-stream artifact builders read
        # ``experiment_type`` to decide chart shapes.
        experiment['experiment_type'] = 'ensemble'
        if exp_type == 'sensitivity':
            experiment['_origin_experiment_type'] = 'sensitivity'
            # Preserve the sweep config so artifact builders can render
            # "swept variable: fertilizer rate (10-30 kg N/ha in 5
            # steps)" etc., instead of just listing 30 anonymous
            # treatments.
            sens = (wizard_params or {}).get('sensitivity')
            if sens:
                experiment['_sensitivity'] = sens
        return {'status': 'ready', 'experiment': experiment}

    # --- Single simulation ---
    assumed = {}  # Track auto-filled parameters

    # Resolve user and per-crop config defaults
    from dssat_agent.services.config import get_config, get_crop_default, resolve_user
    crop_code = params.get('crop_code')
    _user = resolve_user(params.get('user_id'))
    crop_def = get_crop_default(crop_code, user=_user) if crop_code else None

    # Auto-select soil: user input → config default → auto-detect
    soil_id = params.get('soil_id')
    inline_soil = params.get('inline_soil')
    if not soil_id and not inline_soil:
        config_soil = get_config('default_soil_id', None, user=_user)
        if config_soil:
            soil_id = config_soil
            assumed['Soil profile'] = f"{soil_id} (preference)"
        else:
            try:
                result = dispatch_skill('list_soils', {
                    'country': params.get('country', 'US'),
                    'page_size': 5,
                })
                soils = result.get('soils', [])
                soil_id = soils[0]['soil_id'] if soils else 'IB00000001'
            except Exception:
                soil_id = 'IB00000001'
            assumed['Soil profile'] = soil_id
        logger.info("Auto-selected soil: %s", soil_id)

    # Auto-select cultivar: user input → crop default → auto-detect
    cultivar_code = params.get('cultivar_code')
    if not cultivar_code and crop_code:
        if crop_def and crop_def.cultivar_code:
            cultivar_code = crop_def.cultivar_code
            assumed['Cultivar'] = f"{cultivar_code} (preference)"
        else:
            try:
                result = dispatch_skill('list_cultivars', {'crop_code': crop_code})
                cultivars = result.get('cultivars', [])
                if cultivars:
                    cultivar_code = cultivars[0].get('code', '')
                    cultivar_name = cultivars[0].get('name', '')
                    assumed['Cultivar'] = f"{cultivar_code} ({cultivar_name})" if cultivar_name else cultivar_code
                    logger.info("Auto-selected cultivar: %s", cultivar_code)
            except Exception:
                pass

    if not crop_code:
        return {'status': 'error', 'error': 'crop_code is required'}

    planting_date = params.get('planting_date')
    if not planting_date:
        return {'status': 'error', 'error': 'planting_date is required'}

    try:
        plant_dt = datetime.strptime(planting_date, "%Y-%m-%d")
    except (ValueError, TypeError):
        return {'status': 'error', 'error': f'Invalid planting date: {planting_date}'}

    # Resolve defaults: user input → crop default → config → hardcoded
    default_num_years = get_config('default_num_years', 1, user=_user)
    default_elev = get_config('elevation', 100.0, user=_user)
    default_pp = (crop_def.plant_population if crop_def and crop_def.plant_population else 7.0)
    default_rs = (crop_def.row_spacing if crop_def and crop_def.row_spacing else 75.0)

    num_years = params.get('num_years', default_num_years)
    sdate = (plant_dt - timedelta(days=30)).strftime("%Y-%m-%d")

    # Track auto-filled parameters
    if params.get('plant_population') is None:
        assumed['Plant population'] = f'{default_pp} plants/m²'
    if params.get('row_spacing') is None:
        assumed['Row spacing'] = f'{default_rs} cm'
    if params.get('elevation') is None:
        assumed['Elevation'] = f'{default_elev} m'
    if params.get('num_years') is None:
        assumed['Simulation years'] = str(num_years)

    experiment = {
        'crop_code': crop_code,
        'cultivar_code': cultivar_code,
        'soil_id': soil_id,
        'location_name': params.get('location_name', ''),
        'field': {
            'id_field': 'CHAT0001',
            'latitude': params.get('latitude', 32.6),
            'longitude': params.get('longitude', -86.7),
            'elevation': params.get('elevation', default_elev),
        },
        'planting': {
            'date': planting_date,
            'population': params.get('plant_population', default_pp),
            'row_spacing': params.get('row_spacing', default_rs),
        },
        'simulation_controls': {
            'start_date': sdate,
            'num_years': num_years,
        },
        '_assumed_params': assumed,
    }

    # Propagate user_id so downstream builders can access config
    if params.get('user_id'):
        experiment['user_id'] = params['user_id']

    # Weather data
    if params.get('weather_data'):
        experiment['weather_data'] = params['weather_data']

    # Soil
    if inline_soil:
        experiment['inline_soil'] = inline_soil
        experiment.pop('soil_id', None)

    # Management: user input → crop default → nothing
    for key in ('fertilizer', 'irrigation', 'harvest', 'initial_conditions',
                'tillage', 'chemical', 'residue'):
        val = params.get(key)
        if val:
            experiment[key] = val
        elif crop_def and getattr(crop_def, key, None):
            experiment[key] = getattr(crop_def, key)
            assumed[key.replace('_', ' ').title()] = '(preference)'

    # Pass through additional_instructions for response generation
    if params.get('additional_instructions'):
        experiment['additional_instructions'] = params['additional_instructions']

    # Wizard overrides
    if wizard_params:
        _merge_wizard_params(experiment, wizard_params)

    return {'status': 'ready', 'experiment': experiment}


def run_full_simulation(params: Dict, wizard_params: Optional[Dict] = None,
                        chat_id: str = None, user_id: int = None) -> Dict:
    """
    Run a complete crop simulation from user parameters (fetches weather, selects soil/cultivar, runs DSSAT). Use this skill for any simulation request.

    Args:
        params: Experiment parameters.
        wizard_params: Optional wizard configuration.
        chat_id: Optional UUID string of the orchestrator chat session.

    Returns:
        dict with 'status', 'results', 'experiment_id', 'detail_url', or 'error'.
    """
    from earthrise_agents_base.agent.progress import publish_progress

    # Resolve experiment type early to check for batch
    exp_type = (
        (wizard_params or {}).get('experiment_type')
        or params.get('experiment_type')
        or _infer_experiment_type(params)
    )

    # Skip location resolution + weather prep for experiment types that
    # handle weather themselves per-point/per-child:
    #   batch       — each child experiment runs its own pre-build phase
    #   monte_carlo — the MC service queries weather per grid point via
    #                 ``spatial_data_service.get_weather_for_point`` and
    #                 builds a per-point WeatherStation. Running the
    #                 single-point ``prepare_weather`` here would pick a
    #                 representative date/lat/lon, validate the date
    #                 format, and abort the whole MC run on a missing or
    #                 mode-resolved-only planting date — even though the
    #                 actual per-point dates the MC service would use
    #                 are valid.
    if exp_type not in ('batch', 'monte_carlo'):
        publish_progress(
            'resolve_location',
            'Resolving location',
            'Mapping the location to coordinates…',
        )
        # Resolve location if lat/lon missing but location_name present
        if not params.get('latitude') and params.get('location_name'):
            try:
                from data_agent.location import resolve_location
                loc = resolve_location(params['location_name'])
                # `resolve_location` returns an "unresolved" sentinel
                # (missing lat/lon) for inputs it can't match, rather than
                # defaulting to a region centroid. Propagate that as a
                # proper error so the tool wrapper can surface a
                # validation_error envelope to the LLM.
                if not loc.get("lat") or not loc.get("lon"):
                    return {
                        'status': 'error',
                        'error': (
                            f"Could not resolve location "
                            f"'{params['location_name']}'. Provide "
                            "lat/lon coordinates or a recognized admin "
                            "area name."
                        ),
                        'unresolved_location': loc,
                    }
                params['latitude'] = loc['lat']
                params['longitude'] = loc['lon']
                if not params.get('country'):
                    params['country'] = loc.get('country', '')
                logger.info("Resolved location '%s' → lat=%s, lon=%s",
                            params['location_name'], params['latitude'], params['longitude'])
            except Exception as e:
                logger.warning("Could not resolve location '%s': %s", params.get('location_name'), e)
                return {
                    'status': 'error',
                    'error': f"Location resolver failed for '{params.get('location_name')}': {e}",
                }

        # Require lat/lon by this point — no silent defaults.
        if params.get('latitude') is None or params.get('longitude') is None:
            return {
                'status': 'error',
                'error': (
                    "Missing location. Provide either `latitude`+`longitude` "
                    "(point) or `location_name` (admin area name)."
                ),
            }

        # Resolve user for config lookups
        from dssat_agent.services.config import get_config, resolve_user
        _user = resolve_user(user_id or params.get('user_id'))

        # Track weather source assumption — check user/system config first
        if not params.get('weather_source'):
            configured_source = get_config('default_weather_source', DEFAULT_WEATHER_SOURCE, user=_user)
            params['_weather_source_assumed'] = configured_source

        # Resolve config-driven defaults for weather prep
        default_elev = get_config('elevation', 100.0, user=_user)
        default_num_years = get_config('default_num_years', 1, user=_user)

        # Step 1: Prepare weather
        publish_progress(
            'prepare_weather',
            'Preparing weather data',
            'Checking the local weather cache and fetching missing days…',
        )
        weather_result = prepare_weather(
            crop_code=params.get('crop_code', ''),
            planting_date=params.get('planting_date', ''),
            latitude=params.get('latitude', 0),
            longitude=params.get('longitude', 0),
            elevation=params.get('elevation', default_elev),
            weather_source=params.get('weather_source'),
            num_years=params.get('num_years', default_num_years),
        )

        if weather_result['status'] == 'error':
            return weather_result

        if weather_result['status'] == 'ready':
            params['weather_data'] = weather_result['weather_data']
        else:
            logger.warning("Weather unavailable, proceeding without it")

    # Step 2: Build experiment
    publish_progress(
        'build_experiment',
        'Building experiment',
        'Selecting soil and cultivar, applying defaults…',
    )
    build_result = build_experiment(params=params, wizard_params=wizard_params)

    if build_result['status'] == 'error':
        return build_result

    experiment = build_result['experiment']

    # Add weather source to assumed params if it was defaulted
    assumed = experiment.get('_assumed_params', {})
    if params.get('_weather_source_assumed'):
        assumed['Weather source'] = params['_weather_source_assumed']
        experiment['_assumed_params'] = assumed

    # Tag with chat_id if provided (check both kwarg and params dict,
    # since EmbeddedClient may pass chat_id inside the params dict)
    resolved_chat_id = chat_id or params.get('chat_id')
    if resolved_chat_id:
        experiment['chat_id'] = resolved_chat_id

    # Step 3: Run experiment
    publish_progress(
        'run_experiment',
        'Running simulation',
        'Executing DSSAT and collecting outputs…',
    )
    return run_experiment(experiment)


def run_experiment(experiment: Dict) -> Dict:
    """
    Internal: run a pre-built DSSAT experiment definition. Use run_full_simulation instead.

    Routes to the correct skill based on experiment_type.
    Short-circuits with error details on failure.

    Returns:
        dict with 'status' ('completed'|'missing_data'|'failed'|'error'),
        plus results on success or error details on failure.
    """
    from dssat_agent.services.executor import dispatch_skill

    if not experiment:
        return {'status': 'error', 'error': 'No experiment definition provided'}

    exp_type = experiment.get('experiment_type', 'single')

    try:
        if exp_type == 'batch':
            results = dispatch_skill('run_batch', experiment)
            # Batch returns immediately with queued status
            batch_id = results.get('batch_id')
            if results.get('status') == 'queued' and batch_id:
                locations = results.get('locations', [])
                total = results.get('total_locations', len(locations))
                return {
                    'status': 'completed',
                    'results': results,
                    'batch_id': batch_id,
                    'key_results': {
                        'batch_status': 'queued',
                        'total_locations': total,
                        'sub_experiment_type': results.get('sub_experiment_type', 'single'),
                    },
                    'artifacts': [{
                        'type': 'table',
                        'title': f'Batch Experiment — {total} Locations',
                        'data': {
                            'columns': ['#', 'Location'],
                            'rows': [[i + 1, name] for i, name in enumerate(locations[:20])],
                        },
                        'hint': f'Showing first 20 of {total} locations' if total > 20 else None,
                    }],
                    'response_footer': f'\n\n[View batch experiment: {results.get("batch_label", f"{total} locations")}](/explorer/batch/{batch_id}/)',
                    'conversation_context': {
                        'batch_id': batch_id,
                        'batch_status': 'queued',
                        'total_locations': total,
                    },
                }
            elif results.get('error'):
                return {'status': 'error', 'error': results['error']}
            return {'status': 'completed', 'results': results}

        if exp_type == 'monte_carlo':
            results = dispatch_skill('run_monte_carlo', experiment)
        elif exp_type == 'ensemble':
            base_keys = {
                'crop_code', 'cultivar_code', 'soil_id', 'inline_soil',
                'field', 'weather_data', 'simulation_controls', 'planting',
                'fertilizer', 'irrigation', 'harvest', 'initial_conditions',
                'tillage', 'chemical', 'residue',
            }
            base = {k: v for k, v in experiment.items() if k in base_keys}
            treatments = experiment.get('treatments') or [{}]
            results = dispatch_skill('run_ensemble', {
                'base': base,
                'treatments': treatments,
                'chat_id': experiment.get('chat_id'),
            })
        else:
            results = dispatch_skill('run_simulation', experiment)

        status = results.get('status', 'unknown')

        if status == 'completed':
            exp_id = results.get('experiment_id')

            # Extract overview text
            overview = results.get('overview', '')
            if not overview and results.get('dssat_files'):
                overview = results['dssat_files'].get('output', {}).get('OVERVIEW', '')

            # Build artifacts, key results, and stress analysis. Surface
            # the swept-axis config + origin type so ensemble/sensitivity
            # artifact builders can render type-aware narratives.
            additional_instructions = experiment.get('additional_instructions', '')
            for k in ('_sensitivity', '_origin_experiment_type'):
                if k in experiment and k not in results:
                    results[k] = experiment[k]
            build_output = _build_artifacts(
                results, overview,
                additional_instructions=additional_instructions,
            )
            artifacts = build_output['artifacts']
            key_results = build_output['key_results']
            stress_summary = build_output['stress_summary']
            response_text_override = build_output.get('response_text')

            # Add assumed parameters as a table artifact
            assumed = experiment.get('_assumed_params', {})
            if assumed:
                artifacts.append({
                    'type': 'table',
                    'title': 'Assumed Parameters',
                    'hint': 'Auto-selected values not specified by you',
                    'data': {
                        'columns': ['Parameter', 'Value Used'],
                        'rows': [[k, str(v)] for k, v in assumed.items()],
                    },
                })

            # Build a response footer for the chat agent to append
            response_footer = ''
            if exp_id:
                from django.urls import reverse
                label = results.get('experiment_label', '')
                link_text = f"View experiment: {label}" if label else "View full experiment details"
                exp_url = reverse('dssat_agent:experiment_detail', kwargs={'experiment_id': exp_id})
                response_footer = f'\n\n[{link_text}]({exp_url})'

            # Build conversation context for the chat agent to persist
            conversation_context = _build_conversation_context(
                exp_type, exp_id, experiment, results,
                stress_summary, additional_instructions,
            )

            envelope = {
                'status': 'completed',
                'results': results,
                'experiment_id': exp_id,
                'key_results': key_results,
                'artifacts': artifacts,
                'response_footer': response_footer,
                'conversation_context': conversation_context,
            }
            # Pre-built response_text bypasses
            # ``_render_success_fallback``'s generic key-result dump and
            # lets the artifact builder write a type-aware narrative
            # (e.g. ensemble's best-vs-worst comparison).
            if response_text_override:
                envelope['response_text'] = response_text_override
            return envelope
        elif status == 'missing_data':
            return {
                'status': 'missing_data',
                'missing_count': results.get('missing_count', results.get('total_missing', 0)),
                'weather_start_needed': results.get('weather_start_needed'),
                'weather_end_needed': results.get('weather_end_needed'),
                'error': results.get('error', 'Missing weather data'),
            }
        else:
            return {
                'status': 'failed',
                'error': results.get('error', results.get('message', 'Unknown error')),
                'results': results,
            }

    except Exception as e:
        logger.exception("Simulation error: %s", e)
        return {'status': 'error', 'error': str(e)}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _format_weather_for_simulation(
    records: List[Dict], lat: float, lon: float, elev: float
) -> Dict:
    """Convert raster query records to DSSAT weather format."""
    return {
        'station': {
            'lat': lat,
            'lon': lon,
            'elev': elev,
            'name': 'AUTO',
        },
        'records': [
            {
                'date': r.get('fdate', r.get('date', '')),
                'tmax': r.get('tmax'),
                'tmin': r.get('tmin'),
                'rain': r.get('rain'),
                'srad': r.get('srad'),
            }
            for r in records
        ],
    }


def _infer_experiment_type(params: Dict) -> str:
    """Infer experiment type from parameter structure when not explicitly set.

    Returns 'single', 'ensemble', 'monte_carlo', or 'batch'.
    """
    # Batch indicators
    if any(params.get(k) for k in ('batch_locations', 'location_selection_mode', 'admin_parent')):
        return 'batch'
    # Spatial params → Monte Carlo
    if any(params.get(k) for k in ('bbox', 'center', 'admin_name', 'spatial_mode')):
        return 'monte_carlo'
    # Treatments list → Ensemble
    if params.get('treatments'):
        return 'ensemble'
    return 'single'


def _build_monte_carlo_defaults(params: Dict) -> Dict:
    """Build default wizard_params for Monte Carlo from chat-extracted params.

    When the LLM sets experiment_type='monte_carlo' but there's no wizard,
    this constructs the spatial configuration from params.
    """
    from dssat_agent.services.config import get_config, resolve_user
    _user = resolve_user(params.get('user_id'))
    default_ws = get_config('default_weather_source', 'power', user=_user)

    wp = {
        'experiment_type': 'monte_carlo',
        'spatial_mode': params.get('spatial_mode', 'admin'),
        'nens': params.get('nens', 30),
        'sampling_strategy': params.get('sampling_strategy', 'random'),
        'weather_source': params.get('weather_source', default_ws),
        'crop_code': params.get('crop_code'),
        'cultivar_code': params.get('cultivar_code'),
        'soil_id': params.get('soil_id'),
        'user_id': params.get('user_id'),
    }
    # Pass through spatial params
    for key in ('bbox', 'grid_spacing', 'center', 'radius_km',
                'admin_name', 'admin_level'):
        if params.get(key) is not None:
            wp[key] = params[key]

    # If location_name provided but no spatial params, use admin mode
    if not any(wp.get(k) for k in ('bbox', 'center', 'admin_name')):
        location = params.get('location_name', '')
        if location:
            wp['admin_name'] = location
            wp['spatial_mode'] = 'admin'

    return wp


def _build_batch_defaults(params: Dict) -> Dict:
    """Build batch experiment params from chat-extracted parameters.

    Packages location config and template params for the batch executor.
    """
    # Determine location config
    mode = params.get('location_selection_mode', 'admin_boundary')
    location_config = {'mode': mode}

    if mode == 'admin_boundary':
        # Infer admin_parent from location_name if not explicit
        admin_parent = params.get('admin_parent', '')
        if not admin_parent:
            admin_parent = params.get('location_name', '')
        location_config['admin_parent'] = admin_parent
        location_config['admin_parent_level'] = params.get('admin_parent_level', 'admin1')
        location_config['admin_child_level'] = params.get('admin_child_level', 'admin2')
    elif mode == 'points':
        location_config['points'] = params.get('batch_locations', [])
    elif mode == 'bbox_grid':
        location_config['bbox'] = params.get('bbox', {})
        location_config['spacing_deg'] = params.get('grid_spacing', 0.5)

    # Build template params (everything except batch-specific fields)
    batch_keys = {
        'experiment_type', 'sub_experiment_type', 'location_selection_mode',
        'admin_parent', 'admin_parent_level', 'admin_child_level',
        'batch_locations', '_weather_source_assumed',
    }
    template_params = {k: v for k, v in params.items() if k not in batch_keys and v is not None}

    sub_type = params.get('sub_experiment_type', 'single')

    return {
        'experiment_type': 'batch',
        'sub_experiment_type': sub_type,
        'location_config': location_config,
        'template_params': template_params,
        'chat_id': params.get('chat_id'),
    }


def _build_ensemble_defaults(params: Dict) -> Dict:
    """Build default wizard_params for Ensemble from chat-extracted params.

    When the LLM sets experiment_type='ensemble' but there's no wizard,
    this constructs the base + treatments structure from params.

    If no explicit treatments are provided, auto-generates a meaningful set
    based on what the user asked about (fertilizer, irrigation, planting date,
    etc.) so the ensemble always compares multiple scenarios.
    """
    # Build the base from the common parameters
    base = {}
    for key in ('crop_code', 'cultivar_code', 'soil_id', 'inline_soil',
                'latitude', 'longitude', 'elevation', 'planting_date',
                'plant_population', 'row_spacing', 'weather_source',
                'weather_data', 'num_years', 'user_id'):
        if params.get(key) is not None:
            base[key] = params[key]

    # Copy location_name for weather resolution
    if params.get('location_name'):
        base['location_name'] = params['location_name']

    # Copy management sections into base as defaults
    for key in ('fertilizer', 'irrigation', 'harvest', 'initial_conditions',
                'tillage', 'chemical', 'residue', 'simulation_controls'):
        if params.get(key) is not None:
            base[key] = params[key]

    # Fill missing base fields from per-crop config defaults
    from dssat_agent.services.config import get_crop_default, resolve_user
    crop_def = get_crop_default(base.get('crop_code'), user=resolve_user(params.get('user_id')))
    if crop_def:
        for field in ('cultivar_code', 'plant_population', 'row_spacing'):
            if field not in base and getattr(crop_def, field, None):
                base[field] = getattr(crop_def, field)
        for mgmt in ('fertilizer', 'irrigation', 'harvest', 'tillage',
                      'chemical', 'residue'):
            if mgmt not in base and getattr(crop_def, mgmt, None):
                base[mgmt] = getattr(crop_def, mgmt)

    # Treatments: use LLM-provided list if present
    treatments = params.get('treatments')
    if treatments and len(treatments) >= 2:
        return {
            'experiment_type': 'ensemble',
            'base': base,
            'treatments': treatments,
        }

    # Auto-generate treatments based on user intent
    treatments = _auto_generate_treatments(params, base)

    return {
        'experiment_type': 'ensemble',
        'base': base,
        'treatments': treatments,
    }


def _auto_generate_treatments(params: Dict, base: Dict) -> List[Dict]:
    """Auto-generate ensemble treatments when the user doesn't provide them.

    Examines the user's intent (additional_instructions) and provided params
    to decide what management dimension to vary. Always includes a baseline
    (no management) treatment for comparison.

    Returns a list of treatment override dicts.
    """
    instructions = (params.get('additional_instructions') or '').lower()
    planting_date = base.get('planting_date', '')

    # --- Detect what the user wants to compare ---

    # Irrigation comparison
    if _intent_matches(instructions, ('irrigat', 'water management', 'should i irrigate',
                                       'watering', 'water stress')):
        return _generate_irrigation_treatments(params, base)

    # Fertilizer comparison
    if _intent_matches(instructions, ('fertiliz', 'nitrogen', 'nutrient', 'n rate',
                                       'fertiliser', 'best fertilizer', 'fert plan')):
        return _generate_fertilizer_treatments(params, base, planting_date)

    # Planting date comparison
    if _intent_matches(instructions, ('planting date', 'when to plant', 'when should i plant',
                                       'plant earlier', 'plant later', 'sowing date')):
        return _generate_planting_date_treatments(planting_date)

    # If user provided specific fertilizer or irrigation, compare with/without
    if base.get('fertilizer'):
        return [
            {'label': 'No Fertilizer'},
            {'label': 'User Fertilizer Plan', 'fertilizer': base['fertilizer']},
        ]

    if base.get('irrigation'):
        return [
            {'label': 'Rainfed (No Irrigation)'},
            {'label': 'User Irrigation Plan', 'irrigation': base['irrigation']},
        ]

    # Generic fallback: compare a few common management scenarios
    return _generate_general_treatments(params, base, planting_date)


def _intent_matches(text: str, keywords: tuple) -> bool:
    """Check if any keyword appears in the text."""
    return any(kw in text for kw in keywords)


def _generate_irrigation_treatments(params: Dict, base: Dict) -> List[Dict]:
    """Generate irrigation comparison treatments."""
    planting_date = base.get('planting_date', '')
    treatments = [
        {'label': 'Rainfed (No Irrigation)'},
        {'label': 'Light Irrigation (25mm every 14 days)', 'irrigation': {
            'automatic': False,
            'events': [
                {'date': planting_date, 'amount': 25, 'operation': 'IR001'},
            ] if planting_date else [],
        }},
        {'label': 'Moderate Irrigation (50mm every 14 days)', 'irrigation': {
            'automatic': False,
            'events': [
                {'date': planting_date, 'amount': 50, 'operation': 'IR001'},
            ] if planting_date else [],
        }},
        {'label': 'Auto Irrigation (50% depletion)', 'irrigation': {
            'automatic': True,
            'efficiency': 0.9,
            'management_depth': 30,
            'threshold': 50,
        }},
    ]
    # If user provided a specific plan, include it
    if base.get('irrigation'):
        treatments.insert(1, {
            'label': 'User Irrigation Plan',
            'irrigation': base['irrigation'],
        })
    return treatments


def _generate_fertilizer_treatments(params: Dict, base: Dict,
                                     planting_date: str) -> List[Dict]:
    """Generate fertilizer comparison treatments."""
    treatments = [
        {'label': 'No Fertilizer'},
    ]

    # Common nitrogen rates to test
    n_rates = [50, 100, 150, 200]
    for rate in n_rates:
        events = []
        if planting_date:
            # Split application: 1/3 at planting, 2/3 at 30 DAP
            events = [
                {
                    'date': planting_date,
                    'material': 'FE005',  # Urea
                    'amount': round(rate / 3),
                    'method': 'AP002',  # Broadcast incorporated
                    'depth': 10,
                },
                {
                    'date': _offset_date(planting_date, 30),
                    'material': 'FE005',
                    'amount': round(rate * 2 / 3),
                    'method': 'AP002',
                    'depth': 10,
                },
            ]
        treatments.append({
            'label': f'{rate} kg N/ha (split application)',
            'fertilizer': events,
        })

    # If user provided a specific plan, include it
    if base.get('fertilizer'):
        treatments.insert(1, {
            'label': 'User Fertilizer Plan',
            'fertilizer': base['fertilizer'],
        })

    return treatments


def _generate_planting_date_treatments(planting_date: str) -> List[Dict]:
    """Generate planting date comparison treatments."""
    if not planting_date:
        return [{}]

    offsets = [-30, -15, 0, 15, 30]
    treatments = []
    for offset in offsets:
        new_date = _offset_date(planting_date, offset)
        if offset == 0:
            label = f'{new_date} (baseline)'
        elif offset < 0:
            label = f'{new_date} ({abs(offset)} days earlier)'
        else:
            label = f'{new_date} ({offset} days later)'
        treatments.append({
            'label': label,
            'planting': {'date': new_date},
            'simulation_controls': {
                'start_date': _offset_date(new_date, -30),
            },
        })
    return treatments


def _generate_general_treatments(params: Dict, base: Dict,
                                  planting_date: str) -> List[Dict]:
    """Generate a general set of treatments covering common management questions."""
    treatments = [
        {'label': 'Baseline (No Management)'},
    ]

    # Add a moderate fertilizer treatment
    if planting_date:
        treatments.append({
            'label': 'Moderate Fertilizer (100 kg N/ha)',
            'fertilizer': [
                {
                    'date': planting_date,
                    'material': 'FE005',
                    'amount': 35,
                    'method': 'AP002',
                    'depth': 10,
                },
                {
                    'date': _offset_date(planting_date, 30),
                    'material': 'FE005',
                    'amount': 65,
                    'method': 'AP002',
                    'depth': 10,
                },
            ],
        })

        treatments.append({
            'label': 'High Fertilizer (200 kg N/ha)',
            'fertilizer': [
                {
                    'date': planting_date,
                    'material': 'FE005',
                    'amount': 70,
                    'method': 'AP002',
                    'depth': 10,
                },
                {
                    'date': _offset_date(planting_date, 30),
                    'material': 'FE005',
                    'amount': 130,
                    'method': 'AP002',
                    'depth': 10,
                },
            ],
        })

    # Add an auto-irrigation treatment
    treatments.append({
        'label': 'Auto Irrigation (50% depletion)',
        'irrigation': {
            'automatic': True,
            'efficiency': 0.9,
            'management_depth': 30,
            'threshold': 50,
        },
    })

    # Combined fertilizer + irrigation
    if planting_date:
        treatments.append({
            'label': 'Fertilizer + Irrigation',
            'fertilizer': [
                {
                    'date': planting_date,
                    'material': 'FE005',
                    'amount': 35,
                    'method': 'AP002',
                    'depth': 10,
                },
                {
                    'date': _offset_date(planting_date, 30),
                    'material': 'FE005',
                    'amount': 65,
                    'method': 'AP002',
                    'depth': 10,
                },
            ],
            'irrigation': {
                'automatic': True,
                'efficiency': 0.9,
                'management_depth': 30,
                'threshold': 50,
            },
        })

    return treatments


def _offset_date(date_str: str, days: int) -> str:
    """Offset a YYYY-MM-DD date by N days."""
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        return (dt + timedelta(days=days)).strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return date_str


def _build_monte_carlo(params: Dict, wizard_params: Dict) -> Dict:
    """Build a Monte Carlo experiment definition."""
    from dssat_agent.services.config import get_config, get_crop_default, resolve_user
    _user = resolve_user(params.get('user_id') or wizard_params.get('user_id'))
    crop_code = wizard_params.get('crop_code') or params.get('crop_code')
    crop_def = get_crop_default(crop_code, user=_user) if crop_code else None

    default_ws = get_config('default_weather_source', 'power', user=_user)
    default_pp = (crop_def.plant_population if crop_def and crop_def.plant_population else 7.0)
    default_rs = (crop_def.row_spacing if crop_def and crop_def.row_spacing else 75.0)

    planting_date = params.get('planting_date', wizard_params.get('planting_date'))
    # Auto-mode MC has ``planting_date=None`` at the top level — each
    # field carries its own resolved date instead. Use the earliest
    # per-field date to anchor the shared weather-window start so we
    # don't default to ``datetime.now()`` (which produces a sdate in
    # the future and makes every weather query miss). The MC service
    # still drives per-point planting from each field's own pdate.
    if not planting_date:
        field_dates = [
            f.get('pdate') for f in (wizard_params.get('fields') or [])
            if f.get('pdate')
        ]
        if field_dates:
            planting_date = min(field_dates)
    plant_dt = datetime.strptime(planting_date, "%Y-%m-%d") if planting_date else datetime.now()
    num_years = params.get('num_years', get_config('default_num_years', 1, user=_user))

    cultivar_code = (wizard_params.get('cultivar_code')
                     or wizard_params.get('cultivar')
                     or params.get('cultivar_code'))
    if not cultivar_code and crop_code:
        try:
            from dssat_agent.services import crop_service
            result = crop_service.list_cultivars(crop_code.upper())
            cvs = result.get('cultivars', [])
            if cvs:
                cultivar_code = (cvs[0].get('code')
                                 or cvs[0].get('cultivar_code'))
        except Exception:
            pass

    soil_id = (wizard_params.get('soil_id') or wizard_params.get('soil_profile')
               or params.get('soil_id'))

    experiment = {
        'experiment_type': 'monte_carlo',
        'spatial_mode': wizard_params.get('spatial_mode', 'circle'),
        'nens': wizard_params.get('nens', 30),
        'sampling_strategy': wizard_params.get('sampling_strategy', 'random'),
        'weather_source': wizard_params.get('weather_source', default_ws),
        'crop_code': crop_code,
        'cultivar_code': cultivar_code,
        'soil_id': soil_id,
        'user_id': params.get('user_id'),
        'planting': {
            'pdate': planting_date,
            'ppop': params.get('plant_population', default_pp),
            'plrs': (params.get('row_spacing', default_rs)) / 100.0,
        },
        'simulation_controls': wizard_params.get('simulation_controls_raw') or {
            'sdate': (plant_dt - timedelta(days=30)).strftime("%Y-%m-%d"),
            'nyers': num_years,
        },
    }

    for key in ('bbox', 'grid_spacing', 'center', 'radius_km',
                'admin_name', 'admin_level'):
        if wizard_params.get(key) is not None:
            experiment[key] = wizard_params[key]

    # Critical: propagate the explicit per-point ``fields`` list. The
    # SPA wizard's submit_draft path materialises one Field row per
    # sampled grid point and stamps each with its own
    # lat/lon/elevation/soil/weather/pdate (Auto-mode resolved at
    # Step 4 lock time). The MC service prefers this list over its
    # internal grid generator — without it, the service falls back to
    # ``_generate_grid``, ignores the user's selected sampling, loses
    # every per-point soil/elev/Auto-mode planting date, and (in this
    # bug) reports "All weather points failed" because the regenerated
    # points sit outside the cached weather window.
    if wizard_params.get('fields'):
        experiment['fields'] = wizard_params['fields']
    if wizard_params.get('in_situ_selections'):
        experiment['in_situ_selections'] = wizard_params['in_situ_selections']

    if wizard_params.get('inline_soil') or params.get('inline_soil'):
        experiment['inline_soil'] = wizard_params.get('inline_soil') or params.get('inline_soil')
        experiment.pop('soil_id', None)

    for key in ('fertilizer', 'irrigation', 'harvest', 'initial_conditions',
                'tillage', 'chemical', 'residue'):
        val = wizard_params.get(key)
        if val:
            experiment[key] = val

    return experiment


def _build_ensemble(params: Dict, wizard_params: Dict) -> Dict:
    """Build an ensemble experiment definition."""
    from datetime import datetime as _dt, timedelta as _td
    from dssat_agent.services.config import get_config, get_crop_default, resolve_user

    base = wizard_params['base']

    _user = resolve_user(params.get('user_id') or base.get('user_id'))
    crop_code = base.get('crop_code') or params.get('crop_code')
    crop_def = get_crop_default(crop_code, user=_user) if crop_code else None

    default_elev = get_config('elevation', 100.0, user=_user)
    default_pp = (crop_def.plant_population if crop_def and crop_def.plant_population else 7.0)
    default_rs = (crop_def.row_spacing if crop_def and crop_def.row_spacing else 75.0)
    default_num_years = get_config('default_num_years', 1, user=_user)

    # Build simulation_controls — ensure sdate is set from planting_date.
    # The downstream ensemble validator requires DSSAT keys (``sdate``,
    # ``nyers``); set both so the chat-shape and SPA-shape paths converge.
    sim_controls = dict(base.get('simulation_controls') or {})
    planting_date = base.get('planting_date') or params.get('planting_date')
    num_years = base.get('num_years') or params.get('num_years', default_num_years)
    if planting_date and not sim_controls.get('sdate') and not sim_controls.get('start_date'):
        try:
            pdt = _dt.strptime(str(planting_date), "%Y-%m-%d")
            sdate_iso = (pdt - _td(days=30)).strftime("%Y-%m-%d")
            sim_controls['start_date'] = sdate_iso
            sim_controls['sdate'] = sdate_iso
            sim_controls['num_years'] = num_years
            sim_controls['nyers'] = num_years
        except (ValueError, TypeError):
            pass

    # Cultivar resolution — prefer explicit, fall back through schema-key
    # aliases, finally auto-pick via list_cultivars (which itself falls
    # back to file parsing when the DB is empty, so this works in test
    # databases that haven't run seed_cultivars).
    cultivar_code = (base.get('cultivar_code') or base.get('cultivar')
                     or params.get('cultivar_code'))
    if not cultivar_code and crop_code:
        try:
            from dssat_agent.services import crop_service
            result = crop_service.list_cultivars(crop_code.upper())
            cvs = result.get('cultivars', [])
            if cvs:
                cultivar_code = (cvs[0].get('code')
                                 or cvs[0].get('cultivar_code'))
        except Exception:
            pass

    soil_id = (base.get('soil_id') or base.get('soil_profile')
               or params.get('soil_id'))

    experiment = {
        'crop_code': crop_code,
        'cultivar_code': cultivar_code,
        'soil_id': soil_id,
        'user_id': params.get('user_id'),
        'field': {
            'id_field': 'CHAT0001',
            'latitude': base.get('latitude') or params.get('latitude', 32.6),
            'longitude': base.get('longitude') or params.get('longitude', -86.7),
            'elevation': base.get('elevation') or params.get('elevation', default_elev),
        },
        'simulation_controls': sim_controls,
        'treatments': wizard_params.get('treatments', []),
        'experiment_type': 'ensemble',
    }

    if planting_date:
        experiment['planting'] = {
            'pdate': planting_date,
            'ppop': base.get('plant_population', default_pp),
            'plrs': base.get('row_spacing', default_rs),
        }

    if base.get('inline_soil'):
        experiment['inline_soil'] = base['inline_soil']
        experiment.pop('soil_id', None)

    if params.get('weather_data'):
        experiment['weather_data'] = params['weather_data']

    for key in ('fertilizer', 'irrigation', 'harvest', 'initial_conditions',
                'tillage', 'chemical', 'residue'):
        val = base.get(key)
        if val:
            experiment[key] = val

    # Pass through additional_instructions for response generation
    ai = params.get('additional_instructions') or base.get('additional_instructions')
    if ai:
        experiment['additional_instructions'] = ai

    return experiment


def _merge_wizard_params(experiment: Dict, wizard_params: Dict):
    """Merge wizard-specific overrides into an experiment definition."""
    sim_controls = wizard_params.get('simulation_controls', {})
    if sim_controls.get('start_date'):
        experiment['simulation_controls']['start_date'] = sim_controls['start_date']
    if sim_controls.get('num_reps'):
        experiment['simulation_controls']['num_reps'] = sim_controls['num_reps']

    # Simulation options
    options = {}
    for opt_key, dssat_key in [('water', 'water'), ('nitrogen', 'nitro')]:
        val = sim_controls.get(opt_key)
        if val is not None:
            options[dssat_key] = 'Y' if (val is True or val == 'Y') else 'N'

    co2_val = sim_controls.get('co2')
    if co2_val is not None:
        if co2_val in ('M', 'D', 'W'):
            options['co2'] = co2_val
        elif co2_val in ('Y', True):
            options['co2'] = 'M'
        else:
            options['co2'] = 'D'
    if options:
        experiment['simulation_controls']['options'] = options

    for key in ('tillage', 'chemical', 'residue'):
        events = wizard_params.get(key)
        if events:
            experiment[key] = events

    if wizard_params.get('treatments'):
        experiment['treatments'] = wizard_params['treatments']
        experiment['experiment_type'] = 'ensemble'


def _chart_artifact(chart: Dict) -> Dict:
    """Convert a chart_service chart config to a UI artifact dict."""
    artifact = {
        'type': 'chart',
        'title': chart['title'],
        'data': chart['data'],
        'chart_type': chart['type'],
    }
    if chart.get('options'):
        artifact['options'] = chart['options']
    return artifact


def _build_artifacts(results: Dict, overview: str,
                     additional_instructions: str = '') -> Dict:
    """
    Build visual artifacts, key results for LLM narrative, and stress analysis.

    Dispatches to experiment-type-specific builders based on result structure.

    Returns a dict with:
        'artifacts': list of artifact dicts for UI display
        'key_results': dict of key values for LLM narrative generation
        'stress_summary': dict of per-factor stress assessments
    """
    # Detect experiment type from result structure
    if results.get('quantiles'):
        return _build_mc_artifacts(results, additional_instructions)
    elif results.get('treatments') and results.get('aggregate'):
        return _build_ensemble_artifacts(results, additional_instructions)
    else:
        return _build_single_artifacts(results, overview, additional_instructions)


def _build_single_artifacts(results: Dict, overview: str,
                            additional_instructions: str = '') -> Dict:
    """Build artifacts for a single simulation run."""
    from dssat_agent.services.chart_service import (
        format_agricultural_charts, build_stress_charts,
        build_rainfall_chart,
    )

    artifacts = []
    summary = results.get('summary', {})
    plant_growth = results.get('plant_growth', [])

    # --- Key results for LLM narrative (ONLY the highlighted variables) ---
    key_results = {'experiment_type': 'single'}
    if summary:
        yield_val = summary.get('harwt') or summary.get('hwam') or summary.get('hwah')
        if yield_val is not None:
            key_results['Harvested yield (kg/ha, dry weight)'] = yield_val
        flo = summary.get('flo')
        if flo is not None:
            key_results['Days to flowering'] = flo
        mat = summary.get('mat')
        if mat is not None:
            key_results['Days to maturity'] = mat
        precip = summary.get('rain') or summary.get('prcm')
        if precip is not None:
            key_results['Total precipitation (mm)'] = precip
        irrig = summary.get('ircm') or summary.get('tirr') or 0
        key_results['Irrigation applied (mm)'] = irrig

    # --- Stress analysis ---
    stress_summary, significant_stress = _analyze_stress(
        plant_growth, additional_instructions
    )

    if significant_stress:
        key_results['stress'] = significant_stress

    # --- Pre-computed narrative insights ---
    key_results['_insights'] = _build_single_insights(key_results, significant_stress)

    # --- Summary table ---
    if summary:
        table = _build_summary_table(summary)
        if table:
            artifacts.append(table)

    # --- Stress table ---
    if significant_stress:
        artifacts.append(_build_stress_table(significant_stress))

    # --- Charts ---
    phenology = {
        'flowering_dap': summary.get('flo') if summary else None,
        'maturity_dap': summary.get('mat') if summary else None,
    }

    try:
        charts = format_agricultural_charts(results, phenology=phenology)
        for chart in charts:
            artifacts.append(_chart_artifact(chart))
    except Exception as e:
        logger.warning("Chart generation failed: %s", e)

    try:
        weather_output = results.get('weather_output', [])
        if weather_output:
            rain_chart = build_rainfall_chart(weather_output, phenology=phenology)
            if rain_chart:
                artifacts.append(_chart_artifact(rain_chart))
    except Exception as e:
        logger.warning("Rainfall chart failed: %s", e)

    if significant_stress and plant_growth:
        try:
            stress_charts = build_stress_charts(
                plant_growth, significant_stress, phenology=phenology
            )
            for chart in stress_charts:
                artifacts.append(_chart_artifact(chart))
        except Exception as e:
            logger.warning("Stress charts failed: %s", e)

    # --- Resource productivity from overview ---
    if overview:
        resource_text = _extract_resource_productivity(overview)
        if resource_text:
            artifacts.append({
                'type': 'text',
                'title': 'Resource Productivity',
                'data': {'content': resource_text},
            })

    # --- Dynamic available variables/charts ---
    _append_available_extras(artifacts, significant_stress)

    return {
        'artifacts': artifacts,
        'key_results': key_results,
        'stress_summary': stress_summary,
    }


def _build_ensemble_artifacts(results: Dict,
                              additional_instructions: str = '') -> Dict:
    """Build artifacts for an ensemble (multi-treatment) run."""
    from dssat_agent.services.chart_service import (
        build_ensemble_yield_chart, build_ensemble_growth_overlay,
        build_ensemble_maturity_chart,
        format_agricultural_charts, build_rainfall_chart,
    )

    artifacts = []
    treatments = results.get('treatments', [])
    aggregate = results.get('aggregate', {})
    treatment_count = results.get('treatment_count', len(treatments))
    origin_type = results.get('_origin_experiment_type') or 'ensemble'
    sensitivity_cfg = results.get('_sensitivity') or {}

    # --- Key results for LLM narrative ---
    key_results = {
        'experiment_type': origin_type,
        'treatment_count': treatment_count,
    }

    if aggregate:
        if aggregate.get('mean_yield') is not None:
            key_results['Mean yield (kg/ha)'] = aggregate['mean_yield']
        if aggregate.get('min_yield') is not None:
            key_results['Min yield (kg/ha)'] = aggregate['min_yield']
        if aggregate.get('max_yield') is not None:
            key_results['Max yield (kg/ha)'] = aggregate['max_yield']
        if aggregate.get('std_yield') is not None:
            key_results['Yield std dev (kg/ha)'] = aggregate['std_yield']
        if aggregate.get('mean_maturity') is not None:
            key_results['Mean days to maturity'] = aggregate['mean_maturity']

    # Per-treatment summaries (kept private so the fallback renderer
    # doesn't dump the whole list as inline JSON; the response_text we
    # build below renders this nicely instead).
    per_treatment = []
    best_yield = None
    best_label = None
    best_idx = None
    worst_yield = None
    worst_label = None
    worst_idx = None
    for i, t in enumerate(treatments):
        summary = t.get('summary', {})
        label = t.get('label', f'Treatment {i + 1}')
        yield_val = summary.get('harwt') or summary.get('hwam')
        mat = summary.get('mat')
        entry = {'label': label, 'index': i}
        if yield_val is not None:
            entry['yield_kg_ha'] = float(yield_val)
            if best_yield is None or float(yield_val) > best_yield:
                best_yield = float(yield_val)
                best_label = label
                best_idx = i
            if worst_yield is None or float(yield_val) < worst_yield:
                worst_yield = float(yield_val)
                worst_label = label
                worst_idx = i
        if mat is not None:
            entry['days_to_maturity'] = mat
        if t.get('overrides'):
            entry['params'] = t['overrides']
        per_treatment.append(entry)

    # Hide the verbose per-treatment list from the generic fallback
    # text (private key starts with ``_``) — but keep it on the LLM
    # context path so a synthesizer can still see it if needed.
    key_results['_treatments'] = per_treatment
    if best_label:
        key_results['Best treatment'] = best_label
        key_results['Best yield (kg/ha)'] = best_yield
    if worst_label:
        key_results['Worst treatment'] = worst_label
        key_results['Worst yield (kg/ha)'] = worst_yield

    mean_yield = aggregate.get('mean_yield') if aggregate else None

    # --- Insights ---
    insights = []
    if best_label and worst_label and best_yield is not None and worst_yield is not None:
        diff = best_yield - worst_yield
        pct = (diff / worst_yield * 100.0) if worst_yield > 0 else None
        msg = (
            f"Across {treatment_count} treatments, yields ranged from "
            f"{worst_yield:.0f} kg/ha ({worst_label}) to "
            f"{best_yield:.0f} kg/ha ({best_label}), "
            f"a difference of {diff:.0f} kg/ha"
        )
        if pct is not None:
            msg += f" ({pct:+.1f}% over the lowest)"
        insights.append(msg + ".")
        if mean_yield is not None:
            best_vs_mean = (best_yield - mean_yield)
            worst_vs_mean = (worst_yield - mean_yield)
            insights.append(
                f"The best treatment beats the mean ({mean_yield:.0f} kg/ha) "
                f"by {best_vs_mean:+.0f} kg/ha; the worst falls "
                f"{worst_vs_mean:+.0f} kg/ha below it."
            )
    elif mean_yield is not None:
        insights.append(f"The mean yield across treatments was {mean_yield} kg/ha.")

    # Sensitivity sweep summary (when available).
    sweep_label = None
    if sensitivity_cfg:
        cat = sensitivity_cfg.get('category')
        axes = sensitivity_cfg.get('axes') or {}
        if cat == 'planting':
            doy = axes.get('dateCentralDoy')
            off = axes.get('dateOffsetDays')
            steps = axes.get('dateSteps')
            if doy and off and steps:
                sweep_label = (
                    f"planting date around DOY {doy} ± {off} d, "
                    f"{steps} step(s)"
                )
        elif cat == 'cultivar':
            cvs = sensitivity_cfg.get('cultivars') or []
            if cvs:
                sweep_label = f"cultivar list ({', '.join(map(str, cvs))})"
        elif cat == 'management':
            practice = axes.get('practice') or 'management'
            amt_min = axes.get('amountMin')
            amt_max = axes.get('amountMax')
            steps = axes.get('amountSteps')
            apps_min = axes.get('appsMin')
            apps_max = axes.get('appsMax')
            gaps = axes.get('gapSteps')
            sweep_label = (
                f"{practice} amount {amt_min}-{amt_max} ({steps} step(s)) "
                f"× {apps_min}-{apps_max} applications "
                f"× {gaps} gap-day step(s)"
            )
        if sweep_label:
            key_results['Sweep'] = sweep_label
            insights.append(f"Sweep: {sweep_label}.")

    if not insights:
        insights.append("Ensemble simulation completed.")
    key_results['_insights'] = " ".join(insights)

    # --- Aggregate summary table ---
    if aggregate:
        agg_rows = []
        if aggregate.get('mean_yield') is not None:
            agg_rows.append(['Mean yield', f"{aggregate['mean_yield']:.1f}", 'kg/ha'])
        if aggregate.get('min_yield') is not None:
            agg_rows.append(['Min yield', f"{aggregate['min_yield']:.1f}", 'kg/ha'])
        if aggregate.get('max_yield') is not None:
            agg_rows.append(['Max yield', f"{aggregate['max_yield']:.1f}", 'kg/ha'])
        if aggregate.get('std_yield') is not None:
            agg_rows.append(['Std deviation', f"{aggregate['std_yield']:.1f}", 'kg/ha'])
        if aggregate.get('yield_count') is not None:
            agg_rows.append(['Treatment count', str(aggregate['yield_count']), ''])
        if aggregate.get('mean_maturity') is not None:
            agg_rows.append(['Mean maturity', f"{aggregate['mean_maturity']:.0f}", 'days'])
        if agg_rows:
            artifacts.append({
                'type': 'table',
                'title': 'Ensemble Aggregate Statistics',
                'data': {'columns': ['Statistic', 'Value', 'Units'], 'rows': agg_rows},
            })

    # --- Per-treatment comparison table ---
    if per_treatment:
        rows = []
        for entry in per_treatment:
            row = [entry['label']]
            row.append(f"{entry['yield_kg_ha']:.0f}" if 'yield_kg_ha' in entry else 'N/A')
            row.append(str(entry.get('days_to_maturity', 'N/A')))
            rows.append(row)
        artifacts.append({
            'type': 'table',
            'title': 'Per-Treatment Results',
            'data': {'columns': ['Treatment', 'Yield (kg/ha)', 'Days to Maturity'], 'rows': rows},
        })

    # --- Charts ---
    try:
        chart = build_ensemble_yield_chart(treatments)
        if chart:
            artifacts.append(_chart_artifact(chart))
    except Exception as e:
        logger.warning("Ensemble yield chart failed: %s", e)

    try:
        chart = build_ensemble_growth_overlay(treatments)
        if chart:
            artifacts.append(_chart_artifact(chart))
    except Exception as e:
        logger.warning("Ensemble growth overlay failed: %s", e)

    try:
        chart = build_ensemble_maturity_chart(treatments)
        if chart:
            artifacts.append(_chart_artifact(chart))
    except Exception as e:
        logger.warning("Ensemble maturity chart failed: %s", e)

    # Weather is shared across treatments (one field), so reuse single's
    # rainfall + agricultural charts off any treatment that has weather
    # output. Use the best treatment's record so phenology markers
    # (flowering/maturity) match the highlighted line in the growth
    # chart.
    src_idx = best_idx if best_idx is not None else 0
    if 0 <= src_idx < len(treatments):
        src = treatments[src_idx]
        phenology = {
            'flowering_dap': (src.get('summary') or {}).get('flo'),
            'maturity_dap': (src.get('summary') or {}).get('mat'),
        }
        try:
            for ch in format_agricultural_charts(src, phenology=phenology):
                # Skip per-treatment biomass — we already have the
                # multi-treatment overlay above.
                if ch.get('title', '').startswith('Plant Growth'):
                    continue
                artifacts.append(_chart_artifact(ch))
        except Exception as e:
            logger.warning("Ensemble agricultural charts failed: %s", e)
        try:
            wo = src.get('weather_output') or []
            if wo:
                rain_chart = build_rainfall_chart(wo, phenology=phenology)
                if rain_chart:
                    artifacts.append(_chart_artifact(rain_chart))
        except Exception as e:
            logger.warning("Ensemble rainfall chart failed: %s", e)

    # --- Response text (markdown) — bypasses the generic key-result
    # dump in ``_render_success_fallback`` for a type-aware narrative.
    response_text = _build_ensemble_response_text(
        origin_type=origin_type,
        treatment_count=treatment_count,
        aggregate=aggregate,
        per_treatment=per_treatment,
        best_idx=best_idx,
        worst_idx=worst_idx,
        mean_yield=mean_yield,
        sweep_label=sweep_label,
        sensitivity_cfg=sensitivity_cfg,
    )

    return {
        'artifacts': artifacts,
        'key_results': key_results,
        'stress_summary': {},
        'response_text': response_text,
    }


def _build_ensemble_response_text(
    *, origin_type, treatment_count, aggregate, per_treatment,
    best_idx, worst_idx, mean_yield, sweep_label, sensitivity_cfg,
) -> str:
    """Render a clean markdown narrative for ensemble/sensitivity runs.

    Replaces the generic ``_render_success_fallback`` output (which
    dumps the per-treatment list as inline JSON) with a structured
    summary: aggregate stats, best vs worst vs mean comparison, and the
    swept-axis description when applicable.
    """
    label = 'Sensitivity analysis' if origin_type == 'sensitivity' else 'Ensemble run'
    lines = [f"### {label} — {treatment_count} treatments completed", ""]

    if sweep_label:
        lines.append(f"**Swept variable:** {sweep_label}")
        lines.append("")

    if aggregate:
        if mean_yield is not None:
            lines.append(
                f"**Yield (kg/ha):** mean **{mean_yield:.0f}**"
                + (f", min {aggregate.get('min_yield', 0):.0f}"
                   if aggregate.get('min_yield') is not None else "")
                + (f", max {aggregate.get('max_yield', 0):.0f}"
                   if aggregate.get('max_yield') is not None else "")
                + (f", σ {aggregate['std_yield']:.0f}"
                   if aggregate.get('std_yield') is not None else "")
            )
        if aggregate.get('mean_maturity') is not None:
            lines.append(
                f"**Mean days to maturity:** {aggregate['mean_maturity']:.0f}"
            )
        lines.append("")

    # Best vs worst vs mean comparison block.
    def _params_md(entry):
        if not entry or not entry.get('params'):
            return ""
        p = entry['params']
        bits = []
        if p.get('planting_date'):
            bits.append(f"planting {p['planting_date']}")
        if p.get('plant_population') is not None:
            bits.append(f"pop {p['plant_population']}")
        if p.get('row_spacing') is not None:
            bits.append(f"row {p['row_spacing']} cm")
        if p.get('cultivar_code'):
            bits.append(f"cv {p['cultivar_code']}")
        ferts = p.get('fertilizer') or []
        if ferts:
            total_n = sum(
                float(e.get('famn') or e.get('n_kg_ha') or 0) for e in ferts
            )
            bits.append(f"N {total_n:.0f} kg/ha in {len(ferts)} app(s)")
        return ", ".join(bits)

    best = per_treatment[best_idx] if best_idx is not None else None
    worst = per_treatment[worst_idx] if worst_idx is not None else None

    if best and worst and best is not worst:
        lines.append("**Best vs worst vs mean**")
        lines.append("")
        lines.append("| | Treatment | Yield (kg/ha) | Δ vs mean | Configuration |")
        lines.append("|---|---|---|---|---|")
        if best.get('yield_kg_ha') is not None:
            delta = (best['yield_kg_ha'] - mean_yield) if mean_yield is not None else None
            lines.append(
                f"| Best | {best['label']} | "
                f"{best['yield_kg_ha']:.0f} | "
                f"{(f'{delta:+.0f}' if delta is not None else '—')} | "
                f"{_params_md(best) or '—'} |"
            )
        if mean_yield is not None:
            lines.append(
                f"| Mean | (across {treatment_count}) | {mean_yield:.0f} | 0 | — |"
            )
        if worst.get('yield_kg_ha') is not None:
            delta = (worst['yield_kg_ha'] - mean_yield) if mean_yield is not None else None
            lines.append(
                f"| Worst | {worst['label']} | "
                f"{worst['yield_kg_ha']:.0f} | "
                f"{(f'{delta:+.0f}' if delta is not None else '—')} | "
                f"{_params_md(worst) or '—'} |"
            )
        lines.append("")

    return "\n".join(lines).rstrip()


def _build_mc_artifacts(results: Dict,
                        additional_instructions: str = '') -> Dict:
    """Build artifacts for a Monte Carlo (spatial uncertainty) run."""
    from dssat_agent.services.chart_service import (
        build_yield_histogram, build_yield_boxplot,
        build_spatial_yield_scatter,
    )

    artifacts = []
    quantiles = results.get('quantiles', {})
    treatments = results.get('treatments', [])
    yield_q = quantiles.get('yield_kg_ha', {})
    mat_q = quantiles.get('maturity_days', {})
    spatial_mode = results.get('spatial_mode', '')
    n_run = results.get('treatments_run', len(treatments))

    # --- Key results for LLM narrative ---
    # Don't dump the raw quantile dicts \u2014 the response_text below carries
    # the narrative and the tables surface every percentile cleanly.
    key_results = {
        'experiment_type': 'monte_carlo',
        'treatments_run': n_run,
        'spatial_mode': spatial_mode,
    }

    if yield_q:
        key_results['Median yield (P50, kg/ha)'] = yield_q.get('p50')
        key_results['Mean yield (kg/ha)'] = yield_q.get('mean')
        key_results['Yield range P10\u2013P90 (kg/ha)'] = (
            f"{yield_q.get('p10', '?')} \u2013 {yield_q.get('p90', '?')}"
        )
        key_results['Observed min (kg/ha)'] = yield_q.get('min')
        key_results['Observed max (kg/ha)'] = yield_q.get('max')
        key_results['Yield std dev (kg/ha)'] = yield_q.get('std')

    if mat_q:
        key_results['Median days to maturity'] = mat_q.get('p50')
        key_results['Days-to-maturity range (min\u2013max)'] = (
            f"{mat_q.get('min', '?')} \u2013 {mat_q.get('max', '?')}"
        )

    # --- Quantile summary table \u2014 show actual min/max alongside the
    # percentile cutoffs so "best case (P95)" doesn't look inconsistent
    # with the histogram's tail.
    if yield_q:
        q_rows = [
            ['Observed max', str(yield_q.get('max', 'N/A')), 'kg/ha'],
            ['Best case (P95 cutoff)', str(yield_q.get('p95', 'N/A')), 'kg/ha'],
            ['Upper quartile (P75)', str(yield_q.get('p75', 'N/A')), 'kg/ha'],
            ['Median (P50)', str(yield_q.get('p50', 'N/A')), 'kg/ha'],
            ['Lower quartile (P25)', str(yield_q.get('p25', 'N/A')), 'kg/ha'],
            ['Worst case (P5 cutoff)', str(yield_q.get('p5', 'N/A')), 'kg/ha'],
            ['Observed min', str(yield_q.get('min', 'N/A')), 'kg/ha'],
            ['Mean', str(yield_q.get('mean', 'N/A')), 'kg/ha'],
            ['Std deviation', str(yield_q.get('std', 'N/A')), 'kg/ha'],
            ['Sample count', str(yield_q.get('count', 'N/A')), ''],
        ]
        artifacts.append({
            'type': 'table',
            'title': 'Yield Distribution (Monte Carlo)',
            'data': {'columns': ['Statistic', 'Value', 'Units'], 'rows': q_rows},
        })

    if mat_q:
        m_rows = [
            ['Median (P50)', str(mat_q.get('p50', 'N/A')), 'days'],
            ['P25\u2013P75 range',
             f"{mat_q.get('p25', '?')} \u2013 {mat_q.get('p75', '?')}", 'days'],
            ['Min \u2013 Max',
             f"{mat_q.get('min', '?')} \u2013 {mat_q.get('max', '?')}", 'days'],
            ['Mean', str(mat_q.get('mean', 'N/A')), 'days'],
        ]
        artifacts.append({
            'type': 'table',
            'title': 'Days-to-Maturity Distribution',
            'data': {'columns': ['Statistic', 'Value', 'Units'], 'rows': m_rows},
        })

    # --- Charts ---
    try:
        chart = build_yield_histogram(treatments)
        if chart:
            artifacts.append(_chart_artifact(chart))
    except Exception as e:
        logger.warning("MC yield histogram failed: %s", e)

    try:
        chart = build_yield_boxplot(yield_q)
        if chart:
            artifacts.append(_chart_artifact(chart))
    except Exception as e:
        logger.warning("MC yield boxplot failed: %s", e)

    try:
        chart = build_spatial_yield_scatter(treatments)
        if chart:
            artifacts.append(_chart_artifact(chart))
    except Exception as e:
        logger.warning("MC spatial scatter failed: %s", e)

    response_text = _build_mc_response_text(
        n_run=n_run,
        spatial_mode=spatial_mode,
        yield_q=yield_q,
        mat_q=mat_q,
        treatments=treatments,
    )

    return {
        'artifacts': artifacts,
        'key_results': key_results,
        'stress_summary': {},
        'response_text': response_text,
    }


def _build_mc_response_text(*, n_run, spatial_mode, yield_q, mat_q,
                            treatments) -> str:
    """Markdown narrative for a Monte Carlo run.

    Mirrors the ensemble narrative style: aggregate stats, distribution
    shape, best-vs-worst spatial picks. Distinguishes percentile cutoffs
    (P5/P95) from the observed min/max so the response is consistent
    with the histogram's tails.
    """
    mode = spatial_mode.replace('_', ' ').strip() or 'spatial'
    lines = [
        f"### Monte Carlo \u2014 {n_run} spatial samples ({mode})",
        "",
    ]

    if not yield_q:
        lines.append("No yield data was returned \u2014 every sample point failed.")
        return "\n".join(lines).rstrip()

    p50 = yield_q.get('p50')
    p25 = yield_q.get('p25')
    p75 = yield_q.get('p75')
    p10 = yield_q.get('p10')
    p90 = yield_q.get('p90')
    p5 = yield_q.get('p5')
    p95 = yield_q.get('p95')
    y_min = yield_q.get('min')
    y_max = yield_q.get('max')
    mean = yield_q.get('mean')
    std = yield_q.get('std')

    if p50 is not None and mean is not None:
        lines.append(
            f"**Yield (kg/ha):** median **{p50:.0f}**, mean {mean:.0f}"
            + (f", \u03c3 {std:.0f}" if std is not None else "")
        )
    if p10 is not None and p90 is not None:
        lines.append(
            f"**Middle 80% (P10\u2013P90):** {p10:.0f} \u2013 {p90:.0f} kg/ha"
        )
    if p5 is not None and p95 is not None:
        lines.append(
            f"**Tail cutoffs (P5 / P95):** {p5:.0f} / {p95:.0f} kg/ha \u2014 "
            f"these are percentile thresholds, not the absolute extremes."
        )
    if y_min is not None and y_max is not None:
        lines.append(
            f"**Observed range:** {y_min:.0f} \u2013 {y_max:.0f} kg/ha "
            f"(actual best/worst points across the {n_run} samples)."
        )

    if std is not None and mean is not None and mean > 0:
        cv = (std / mean) * 100
        if cv < 10:
            shape = (
                f"Variability is **low** (CV={cv:.1f}%) \u2014 yields are "
                "consistent across the region."
            )
        elif cv < 25:
            shape = (
                f"Variability is **moderate** (CV={cv:.1f}%) \u2014 some "
                "spatial differences but no extreme outliers."
            )
        else:
            shape = (
                f"Variability is **high** (CV={cv:.1f}%) \u2014 strong "
                "spatial differences in growing conditions."
            )
        lines.append("")
        lines.append(shape)

    # Best/worst sample details if we have per-treatment yields with coords.
    yields_with_meta = []
    for t in treatments:
        s = t.get('summary') or {}
        y = s.get('hwam') or s.get('harwt') or s.get('hwah')
        if y is None:
            continue
        try:
            y = float(y)
        except (TypeError, ValueError):
            continue
        yields_with_meta.append({
            'yield_kg_ha': y,
            'lat': t.get('lat'),
            'lon': t.get('lon'),
            'idx': t.get('index'),
        })

    if len(yields_with_meta) >= 2:
        yields_with_meta.sort(key=lambda r: r['yield_kg_ha'])
        worst = yields_with_meta[0]
        best = yields_with_meta[-1]
        lines.append("")
        lines.append("**Best vs worst spatial sample**")
        lines.append("")
        lines.append("| | Yield (kg/ha) | Location |")
        lines.append("|---|---|---|")
        def _sample_loc(r):
            # lat/lon are optional (t.get(...)); render them only when present.
            if r.get('lat') is not None and r.get('lon') is not None:
                return f"{r['lat']:.3f}, {r['lon']:.3f} (sample #{r.get('idx')})"
            return f"(sample #{r.get('idx')})"
        lines.append(
            f"| Best  | {best['yield_kg_ha']:.0f} | {_sample_loc(best)} |"
        )
        if mean is not None:
            lines.append(
                f"| Mean  | {mean:.0f} | (across {n_run} samples) |"
            )
        lines.append(
            f"| Worst | {worst['yield_kg_ha']:.0f} | {_sample_loc(worst)} |"
        )

    if mat_q:
        m_p50 = mat_q.get('p50')
        m_min = mat_q.get('min')
        m_max = mat_q.get('max')
        if m_p50 is not None:
            lines.append("")
            extra = ""
            if m_min is not None and m_max is not None and m_min != m_max:
                extra = f" (range {m_min:.0f}\u2013{m_max:.0f})"
            lines.append(
                f"**Days to maturity:** median {m_p50:.0f}{extra}."
            )

    return "\n".join(lines).rstrip()


# ---------------------------------------------------------------------------
# Shared artifact helpers
# ---------------------------------------------------------------------------

def _analyze_stress(plant_growth: List[Dict],
                    additional_instructions: str = '') -> tuple:
    """Run stress analysis and return (stress_summary, significant_stress)."""
    stress_summary = _compute_stress_summary(plant_growth)
    additional_lower = (additional_instructions or '').lower()

    _STRESS_THRESHOLDS = {
        'water_photo': 0.10, 'water_expansion': 0.10, 'nitrogen': 0.20,
        'phosphorus_photo': 0.10, 'phosphorus_growth': 0.10, 'potassium': 0.10,
    }
    _ALWAYS_CHECK = {'water_photo', 'water_expansion', 'nitrogen'}
    _ON_REQUEST = {'phosphorus_photo', 'phosphorus_growth', 'potassium'}
    _REQUEST_KEYWORDS = {
        'phosphorus_photo': ('phosph', 'p stress'),
        'phosphorus_growth': ('phosph', 'p stress'),
        'potassium': ('potass', 'k stress'),
    }

    significant_stress = {}
    for factor, threshold in _STRESS_THRESHOLDS.items():
        avg = stress_summary.get(factor, {}).get('avg', 0.0)
        if factor in _ALWAYS_CHECK and avg > threshold:
            significant_stress[factor] = stress_summary[factor]
        elif factor in _ON_REQUEST:
            keywords = _REQUEST_KEYWORDS.get(factor, ())
            requested = any(kw in additional_lower for kw in keywords)
            if requested or avg > threshold:
                significant_stress[factor] = stress_summary[factor]

    return stress_summary, significant_stress


def _build_single_insights(key_results: Dict, significant_stress: Dict) -> str:
    """Build pre-computed narrative insights for a single simulation."""
    _STRESS_NAMES = {
        'water_photo': 'water stress affecting photosynthesis',
        'water_expansion': 'water stress affecting leaf expansion',
        'nitrogen': 'nitrogen stress',
        'phosphorus_photo': 'phosphorus stress',
        'phosphorus_growth': 'phosphorus stress affecting growth',
        'potassium': 'potassium stress',
    }

    insights = []
    yield_val = key_results.get('Harvested yield (kg/ha, dry weight)')
    if yield_val is not None:
        if yield_val < 500:
            insights.append(f"The yield of {yield_val} kg/ha is low, suggesting the crop experienced growth limitations.")
        elif yield_val < 3000:
            insights.append(f"The yield of {yield_val} kg/ha is moderate.")
        else:
            insights.append(f"The yield of {yield_val} kg/ha is strong.")

    if significant_stress:
        stress_descriptions = []
        for factor, data in significant_stress.items():
            avg = data.get('avg', 0)
            severity = 'severe' if avg > 0.50 else 'moderate' if avg > 0.20 else 'mild'
            name = _STRESS_NAMES.get(factor, factor)
            stress_descriptions.append(f"{severity} {name} (avg {avg:.2f})")
        insights.append("The simulation detected: " + "; ".join(stress_descriptions) + ".")
    else:
        insights.append("No significant water, nitrogen, or other stresses were detected in this simulation.")

    suggestions = []
    if significant_stress:
        if any(k.startswith('water_') for k in significant_stress):
            suggestions.append("adding irrigation or adjusting the planting date to a wetter period")
        if 'nitrogen' in significant_stress:
            suggestions.append("increasing nitrogen fertilizer application")
        if any(k.startswith('phosphorus') for k in significant_stress):
            suggestions.append("applying phosphorus fertilizer")
    if not suggestions:
        suggestions.append("trying different planting dates or locations to compare yields")

    insights.append("Suggestion: Consider " + ", or ".join(suggestions) + ".")
    return " ".join(insights)


def _build_summary_table(summary: Dict) -> Optional[Dict]:
    """Build the simulation summary table artifact."""
    field_labels = {
        'harwt': ('Harvested yield', 'kg/ha (dry weight)'),
        'hwam': ('Yield at maturity', 'kg/ha (dry weight)'),
        'hwah': ('Harvested yield', 'kg/ha (dry weight)'),
        'topwt': ('Above-ground biomass', 'kg/ha (dry weight)'),
        'cwam': ('Above-ground biomass', 'kg/ha (dry weight)'),
        'hiam': ('Harvest index', 'fraction'),
        'flo': ('Days to flowering', 'days after planting'),
        'mat': ('Days to maturity', 'days after planting'),
        'rain': ('Total precipitation', 'mm'),
        'prcm': ('Total precipitation', 'mm'),
        'ircm': ('Irrigation applied', 'mm'),
        'etcm': ('Evapotranspiration', 'mm'),
        'cet': ('Evapotranspiration', 'mm'),
        'nucm': ('N uptake', 'kg N/ha'),
        'nicm': ('N applied', 'kg N/ha'),
        'nlcm': ('N leached', 'kg N/ha'),
        'laix': ('Max LAI', ''),
        'n2oem': ('N2O emissions', 'kg N/ha'),
    }
    rows = []
    seen_labels = set()
    for key, (label, units) in field_labels.items():
        val = summary.get(key)
        if val is not None and label not in seen_labels:
            rows.append([label, val, units])
            seen_labels.add(label)
    if not rows:
        return None
    return {
        'type': 'table',
        'title': 'Simulation Summary',
        'data': {'columns': ['Field', 'Value', 'Units'], 'rows': rows},
    }


def _build_stress_table(significant_stress: Dict) -> Dict:
    """Build the stress analysis table artifact."""
    _STRESS_LABELS = {
        'water_photo': 'Water stress (photosynthesis)',
        'water_expansion': 'Water stress (leaf expansion)',
        'nitrogen': 'Nitrogen stress',
        'phosphorus_photo': 'Phosphorus stress (photosynthesis)',
        'phosphorus_growth': 'Phosphorus stress (vegetative growth)',
        'potassium': 'Potassium stress',
    }
    stress_rows = []
    for factor, data in significant_stress.items():
        label = _STRESS_LABELS.get(factor, factor)
        avg = data.get('avg', 0)
        max_val = data.get('max', 0)
        days = data.get('stress_days', 0)
        severity = 'Severe' if avg > 0.50 else 'Moderate' if avg > 0.20 else 'Mild'
        stress_rows.append([label, f"{avg:.3f}", f"{max_val:.3f}", str(days), severity])
    return {
        'type': 'table',
        'title': 'Stress Analysis',
        'hint': 'Stress scale: 0 = none, 1 = maximum',
        'data': {
            'columns': ['Stress Factor', 'Season Avg', 'Peak', 'Stress Days', 'Severity'],
            'rows': stress_rows,
        },
    }


def _build_conversation_context(
    exp_type: str, exp_id: str, experiment: Dict, results: Dict,
    stress_summary: Dict, additional_instructions: str,
) -> Dict:
    """Build conversation context for the chat agent to persist across turns."""
    ctx = {
        'experiment_id': exp_id,
        'experiment_type': exp_type,
        'experiment_label': results.get('experiment_label', ''),
        'crop_code': experiment.get('crop_code', ''),
        'location_name': experiment.get('location_name', ''),
        'additional_instructions': additional_instructions,
    }

    if exp_type == 'monte_carlo':
        quantiles = results.get('quantiles', {})
        yield_q = quantiles.get('yield_kg_ha', {})
        ctx['yield_median'] = yield_q.get('p50')
        ctx['yield_mean'] = yield_q.get('mean')
        ctx['yield_p10'] = yield_q.get('p10')
        ctx['yield_p90'] = yield_q.get('p90')
        ctx['treatments_run'] = results.get('treatments_run', 0)
        ctx['spatial_mode'] = results.get('spatial_mode', '')

    elif exp_type == 'ensemble':
        aggregate = results.get('aggregate', {})
        ctx['treatment_count'] = results.get('treatment_count', 0)
        ctx['mean_yield'] = aggregate.get('mean_yield')
        ctx['min_yield'] = aggregate.get('min_yield')
        ctx['max_yield'] = aggregate.get('max_yield')

    else:
        # Single simulation
        summary = results.get('summary') or {}
        ctx['planting_date'] = (
            (experiment.get('planting', {}) or {}).get('pdate')
            or (experiment.get('planting', {}) or {}).get('date', '')
        )
        ctx['cultivar_code'] = experiment.get('cultivar_code', '')
        ctx['soil_id'] = experiment.get('soil_id', '')
        ctx['yield_kg_ha'] = summary.get('harwt') or summary.get('hwam')
        ctx['days_to_maturity'] = summary.get('mat')
        ctx['days_to_flowering'] = summary.get('flo')
        ctx['stress_flags'] = {
            k: data.get('avg', 0) > 0
            for k, data in stress_summary.items()
        }

    return ctx


def _extract_resource_productivity(overview: str) -> Optional[str]:
    """Extract Resource Productivity section from DSSAT overview text."""
    overview_lines = overview.strip().split('\n')
    resource_lines = []
    capturing = False
    for line in overview_lines:
        if '*Resource Productivity' in line:
            capturing = True
            continue
        if capturing:
            if line.strip().startswith('***'):
                break
            if line.strip().startswith('---'):
                continue
            if line.strip():
                resource_lines.append(line.rstrip())
    return '\n'.join(resource_lines) if resource_lines else None


def _append_available_extras(artifacts: List, significant_stress: Dict):
    """Append available-variables and available-charts text artifacts."""
    shown_chart_titles = {a['title'] for a in artifacts if a.get('type') == 'chart'}
    shown_stress = set(significant_stress.keys())

    all_variables = [
        ('Above-ground biomass, harvest index', True),
        ('Evapotranspiration, plant transpiration, soil evaporation', True),
        ('N uptake, N applied, N leached, grain N content', True),
        ('Max leaf area index (LAI)', True),
        ('Phosphorus stress (photosynthesis and growth)', 'phosphorus_photo' not in shown_stress),
        ('Potassium stress', 'potassium' not in shown_stress),
        ('N2O emissions, CO2 emissions, methane emissions', True),
        ('Daily plant growth time-series', True),
        ('Soil water content by layer', True),
        ('Soil nitrogen dynamics', True),
        ('Root weight and depth over time', True),
    ]
    extra_vars = [v for v, show in all_variables if show is True or show]
    if extra_vars:
        artifacts.append({
            'type': 'text',
            'title': 'Additional Output Variables',
            'hint': 'Ask about any of these for more detail',
            'data': {'content': 'You can ask about any of the following:\n'
                     + '\n'.join(f'- {v}' for v in extra_vars)},
        })

    all_charts = [
        ('Leaf area index (LAI) over time', 'Leaf Area Index' not in shown_chart_titles),
        ('Soil water content over time', 'Soil Water Content' not in shown_chart_titles),
        ('Water stress over time', 'Water Stress Over Time' not in shown_chart_titles),
        ('Nitrogen stress over time', 'Nitrogen Stress Over Time' not in shown_chart_titles),
        ('Phosphorus and potassium stress over time', True),
        ('ET components (transpiration vs soil evaporation)', True),
        ('Soil nitrogen dynamics (nitrate, ammonium, uptake)', True),
        ('Temperature and solar radiation over the growing season', True),
    ]
    extra_charts = [c for c, show in all_charts if show is True or show]
    if extra_charts:
        artifacts.append({
            'type': 'text',
            'title': 'Available Charts',
            'hint': 'Ask to generate any of these',
            'data': {'content': 'Additional charts you can request:\n'
                     + '\n'.join(f'- {c}' for c in extra_charts)},
        })


# --- Stress computation helpers ---

# Stress factor column mappings in PlantGro output
# All on 0=no stress, 1=max stress scale (already inverted in DSSAT output)
_STRESS_COLUMNS = {
    'water_photo': ('WSPD', 'wspd'),
    'water_expansion': ('WSGD', 'wsgd'),
    'nitrogen': ('NSTD', 'nstd'),
    'phosphorus_photo': ('PST1A', 'pst1a'),
    'phosphorus_growth': ('PST2A', 'pst2a'),
    'potassium': ('KSTD', 'kstd'),
}


def _compute_stress_summary(plant_growth: List[Dict]) -> Dict:
    """
    Compute season-average and peak stress from daily PlantGro records.

    Returns dict keyed by stress factor name, each with:
        'avg': season average (0=none, 1=max)
        'max': peak daily stress value
        'stress_days': count of days where stress > 0.05
        'daily': list of (dap, value) for charting
    """
    if not plant_growth:
        return {}

    stress_data = {}
    for factor, (upper_key, lower_key) in _STRESS_COLUMNS.items():
        values = []
        daily = []
        for record in plant_growth:
            dap = record.get('DAP') or record.get('dap')
            val = record.get(upper_key) or record.get(lower_key)
            if dap is not None and val is not None:
                try:
                    v = float(val)
                    values.append(v)
                    daily.append((int(dap), v))
                except (ValueError, TypeError):
                    pass

        if values:
            avg = sum(values) / len(values)
            stress_data[factor] = {
                'avg': round(avg, 4),
                'max': round(max(values), 4),
                'stress_days': sum(1 for v in values if v > 0.05),
                'daily': daily,
            }

    return stress_data
