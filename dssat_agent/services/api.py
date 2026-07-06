"""
Public service API for the dssat_agent.

Provides clean Python-callable functions that can be imported directly
by other Django apps (e.g. chat orchestrator) without going through A2A HTTP.

Delegates to the A2A executor's dispatch_skill function.
"""

import logging
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


def _dispatch(skill, params):
    """Lazy import to break circular dependency with services.executor."""
    from dssat_agent.services.executor import dispatch_skill
    return dispatch_skill(skill, params)


def run_simulation(params: Dict) -> Dict:
    """Run a single DSSAT crop simulation."""
    return _dispatch('run_simulation', params)


def run_ensemble(params: Dict) -> Dict:
    """Run an ensemble simulation with base + treatments."""
    return _dispatch('run_ensemble', params)


def run_monte_carlo(params: Dict) -> Dict:
    """Run a Monte Carlo spatial simulation."""
    return _dispatch('run_monte_carlo', params)


def run_batch(params: Dict) -> Dict:
    """Run a batch multi-location experiment across multiple discrete locations."""
    return _dispatch('run_batch', params)


def get_batch(batch_id: str) -> Dict:
    """Get batch experiment status and results."""
    return _dispatch('get_batch', {'batch_id': batch_id})


def validate_experiment(params: Dict) -> Dict:
    """Validate experiment configuration without running."""
    return _dispatch('validate_experiment', params)


def list_crops() -> Dict:
    """List all 18 supported crop types."""
    return _dispatch('list_crops', {})


def list_cultivars(crop_code: str) -> Dict:
    """List cultivars for a specific crop."""
    return _dispatch('list_cultivars', {'crop_code': crop_code})


def get_cultivar_details(crop_code: str, cultivar_code: str) -> Dict:
    """Get full cultivar + ecotype parameters."""
    return _dispatch('get_cultivar_details', {
        'crop_code': crop_code,
        'cultivar_code': cultivar_code,
    })


def list_soils(**kwargs) -> Dict:
    """List stored soil profiles with optional filters."""
    return _dispatch('list_soils', kwargs)


def get_soil_profile(soil_id: str) -> Dict:
    """Get a full soil profile with layers."""
    return _dispatch('get_soil_profile', {'soil_id': soil_id})


def get_experiment(experiment_id: str) -> Dict:
    """Get full experiment session details with results."""
    return _dispatch('get_experiment', {'experiment_id': experiment_id})


def list_codes(category: str) -> Dict:
    """List valid DSSAT codes for a category."""
    return _dispatch('list_codes', {'category': category})


def calculate_weather_dates(crop_code: str, planting_date: str, num_years: int = 1) -> Dict:
    """Calculate weather date range needed for a simulation."""
    return _dispatch('calculate_weather_dates', {
        'crop_code': crop_code,
        'planting_date': planting_date,
        'num_years': num_years,
    })


def resolve_location(location_str: str = '') -> Dict:
    """Resolve a location name to coordinates, bbox, and country."""
    from data_agent.location import resolve_location as _resolve
    return _resolve(location_str)


# ---------------------------------------------------------------------------
# In-situ reference data lookups
#
# Auto-discovered as chat-callable skills via subagent_executor._discover_all_skills().
# Two generalized dispatchers — one per parameter — each take a ``source``
# kwarg. The underlying per-source implementations are private helpers in
# dssat_agent/services/insitu_lookup.py.
# ---------------------------------------------------------------------------

def lookup_soil(lat: float, lon: float, source: str = 'auto') -> Dict:
    """Look up a soil profile at a coordinate. Sources: auto, in_house, soilgrids, ssurgo."""
    from dssat_agent.services.insitu_lookup import lookup_soil as _impl
    return _impl(lat, lon, source=source)


def lookup_planting_date(lat: float, lon: float, year: int, source: str = 'auto') -> Dict:
    """Look up the typical planting date at a coordinate for a given year. Sources: auto, in_house."""
    from dssat_agent.services.insitu_lookup import lookup_planting_date as _impl
    return _impl(lat, lon, year, source=source)


def analyze_results(experiment_id: str, focus: str = None) -> Dict:
    """Analyze a simulation experiment's results, highlighting key findings."""
    experiment = _dispatch('get_experiment', {'experiment_id': experiment_id})
    if experiment.get('error'):
        return experiment

    summary = experiment.get('aggregate_summary', {})
    results = experiment.get('results', [])

    analysis = {
        'experiment_id': experiment_id,
        'status': experiment.get('status'),
        'crop_code': experiment.get('crop_code'),
        'summary': summary,
        'result_count': len(results),
    }

    # Extract key metrics from first result
    if results:
        r = results[0]
        s = r.get('summary', {})
        analysis['yield_kg_ha'] = s.get('yield_kg_ha')
        analysis['maturity_days'] = s.get('maturity_days')
        analysis['water_stress'] = s.get('water_stress')
        analysis['nitrogen_stress'] = s.get('nitrogen_stress')

    return analysis


def query_experiment(experiment_id: str = None, query_type: str = 'variables',
                     variables: list = None, chart_type: str = None, **kwargs) -> Dict:
    """
    Query specific data from an existing experiment. Use this when the user asks
    follow-up questions about a previously run simulation, such as requesting
    additional variables, charts, or stress details.

    Args:
        experiment_id: UUID of the experiment to query.
        query_type: One of 'variables', 'chart', 'stress', 'summary'.
        variables: List of variable names to retrieve (for query_type='variables').
        chart_type: Type of chart to generate (for query_type='chart').
                    Options: 'lai', 'soil_water', 'water_stress', 'nitrogen_stress',
                    'phosphorus_stress', 'potassium_stress', 'et_components',
                    'soil_nitrogen', 'temperature'.

    Returns:
        dict with requested data, artifacts, and/or key_results.
    """
    from dssat_agent.models import SimulationResult
    from dssat_agent.services.chart_service import build_stress_charts

    if not experiment_id:
        return {'error': 'experiment_id is required'}

    try:
        result = SimulationResult.objects.filter(
            experiment_id=experiment_id
        ).order_by('-completed_at').first()

        if not result:
            return {'error': f'No results found for experiment {experiment_id}'}

        response = {'status': 'completed', 'experiment_id': experiment_id}
        artifacts = []

        summary = result.summary or {}
        plant_growth = result.plant_growth or []
        soil_water = result.soil_water or []
        soil_nitrogen = result.soil_nitrogen or []

        # Phenology for chart annotations
        phenology = {
            'flowering_dap': summary.get('flo'),
            'maturity_dap': summary.get('mat'),
        }

        if query_type == 'variables' and variables:
            # Return specific variable values
            field_labels = {
                'yield': [('harwt', 'Harvested yield', 'kg/ha (dry weight)'),
                          ('hwam', 'Yield at maturity', 'kg/ha (dry weight)')],
                'biomass': [('topwt', 'Above-ground biomass', 'kg/ha (dry weight)'),
                            ('cwam', 'Above-ground biomass', 'kg/ha (dry weight)')],
                'harvest_index': [('hiam', 'Harvest index', 'fraction')],
                'flowering': [('flo', 'Days to flowering', 'days after planting')],
                'maturity': [('mat', 'Days to maturity', 'days after planting')],
                'precipitation': [('rain', 'Total precipitation', 'mm'),
                                  ('prcm', 'Total precipitation', 'mm')],
                'irrigation': [('ircm', 'Irrigation applied', 'mm')],
                'evapotranspiration': [('etcm', 'Evapotranspiration', 'mm'),
                                       ('cet', 'Evapotranspiration', 'mm')],
                'n_uptake': [('nucm', 'N uptake', 'kg N/ha')],
                'n_applied': [('nicm', 'N applied', 'kg N/ha')],
                'n_leached': [('nlcm', 'N leached', 'kg N/ha')],
                'lai': [('laix', 'Max LAI', '')],
                'n2o': [('n2oem', 'N2O emissions', 'kg N/ha')],
            }
            rows = []
            for var_name in variables:
                candidates = field_labels.get(var_name.lower(), [])
                for key, label, units in candidates:
                    val = summary.get(key)
                    if val is not None:
                        rows.append([label, val, units])
                        break
            if rows:
                artifacts.append({
                    'type': 'table',
                    'title': 'Requested Variables',
                    'data': {'columns': ['Field', 'Value', 'Units'], 'rows': rows},
                })
                response['key_results'] = {r[0]: r[1] for r in rows}

        elif query_type == 'chart' and chart_type:
            from dssat_agent.services.chart_service import (
                _build_lai_chart, _build_water_chart, build_rainfall_chart,
                build_stress_charts,
            )
            from dssat_agent.services.workflow import _chart_artifact

            # Support comma-separated string or list
            if isinstance(chart_type, list):
                chart_types = chart_type
            else:
                chart_types = [ct.strip() for ct in str(chart_type).split(',') if ct.strip()]

            factor_map = {
                'water_stress': {'water_photo': {}, 'water_expansion': {}},
                'nitrogen_stress': {'nitrogen': {}},
                'phosphorus_stress': {'phosphorus_photo': {}, 'phosphorus_growth': {}},
                'potassium_stress': {'potassium': {}},
            }

            for ct in chart_types:
                chart = None
                if ct == 'lai' and plant_growth:
                    chart = _build_lai_chart(plant_growth)
                elif ct == 'soil_water' and soil_water:
                    chart = _build_water_chart(soil_water)
                elif ct == 'temperature' and result.weather_output:
                    chart = _build_temperature_chart(result.weather_output)
                elif ct in factor_map and plant_growth:
                    factors = factor_map[ct]
                    stress_charts = build_stress_charts(plant_growth, factors, phenology=phenology)
                    for c in stress_charts:
                        artifacts.append(_chart_artifact(c))
                    continue

                if chart:
                    artifacts.append(_chart_artifact(chart))

        elif query_type == 'stress':
            # Full stress analysis
            from dssat_agent.services.workflow import _compute_stress_summary
            stress = _compute_stress_summary(plant_growth)
            response['stress_summary'] = stress
            # Build stress table
            _LABELS = {
                'water_photo': 'Water stress (photosynthesis)',
                'water_expansion': 'Water stress (leaf expansion)',
                'nitrogen': 'Nitrogen stress',
                'phosphorus_photo': 'Phosphorus stress (photosynthesis)',
                'phosphorus_growth': 'Phosphorus stress (vegetative growth)',
                'potassium': 'Potassium stress',
            }
            rows = []
            for factor, data in stress.items():
                avg = data.get('avg', 0)
                severity = 'Severe' if avg > 0.50 else 'Moderate' if avg > 0.20 else 'Mild' if avg > 0.05 else 'None'
                rows.append([_LABELS.get(factor, factor), f"{avg:.3f}", f"{data.get('max', 0):.3f}",
                             str(data.get('stress_days', 0)), severity])
            if rows:
                artifacts.append({
                    'type': 'table', 'title': 'Full Stress Analysis',
                    'hint': 'Stress scale: 0 = none, 1 = maximum',
                    'data': {'columns': ['Factor', 'Season Avg', 'Peak', 'Stress Days', 'Severity'],
                             'rows': rows},
                })

        elif query_type == 'summary':
            response['key_results'] = summary

        response['artifacts'] = artifacts

        # Build a direct response so the chat agent doesn't re-summarize
        if artifacts:
            chart_count = sum(1 for a in artifacts if a.get('type') == 'chart')
            table_count = sum(1 for a in artifacts if a.get('type') == 'table')
            parts = []
            if chart_count:
                parts.append(f"{chart_count} chart{'s' if chart_count > 1 else ''}")
            if table_count:
                parts.append(f"{table_count} table{'s' if table_count > 1 else ''}")
            response['response_text'] = f"I've added the requested {' and '.join(parts)} below."
        else:
            response['response_text'] = "I couldn't find the requested data for this experiment."

        return response

    except Exception as e:
        logger.error("query_experiment failed: %s", e)
        return {'error': str(e), 'experiment_id': experiment_id}


def _build_temperature_chart(weather_output):
    """Build a temperature chart from weather output data."""
    daps, tmax_vals, tmin_vals = [], [], []
    for record in weather_output:
        dap = record.get('DAP') or record.get('dap')
        tmax = record.get('TMAX') or record.get('tmax') or record.get('TMXD') or record.get('tmxd')
        tmin = record.get('TMIN') or record.get('tmin') or record.get('TMND') or record.get('tmnd')
        if dap is not None:
            daps.append(int(dap))
            tmax_vals.append(float(tmax) if tmax is not None else None)
            tmin_vals.append(float(tmin) if tmin is not None else None)
    if not daps:
        return None
    datasets = []
    if any(v is not None for v in tmax_vals):
        datasets.append({
            'label': 'Max Temperature (°C)', 'data': tmax_vals,
            'borderColor': '#E53935', 'fill': False, 'tension': 0.3, 'pointRadius': 0,
        })
    if any(v is not None for v in tmin_vals):
        datasets.append({
            'label': 'Min Temperature (°C)', 'data': tmin_vals,
            'borderColor': '#1565C0', 'fill': False, 'tension': 0.3, 'pointRadius': 0,
        })
    if not datasets:
        return None
    return {'type': 'line', 'title': 'Temperature Over Growing Season',
            'data': {'labels': daps, 'datasets': datasets}}


def compare_experiments(experiment_ids: list) -> Dict:
    """Compare multiple simulation experiments side by side."""
    comparisons = []
    for eid in experiment_ids:
        try:
            exp = _dispatch('get_experiment', {'experiment_id': eid})
            if exp.get('error'):
                comparisons.append({'experiment_id': eid, 'error': exp['error']})
                continue
            summary = exp.get('aggregate_summary', {})
            results = exp.get('results', [])
            first_result = results[0].get('summary', {}) if results else {}
            comparisons.append({
                'experiment_id': eid,
                'crop_code': exp.get('crop_code'),
                'cultivar_code': exp.get('cultivar_code'),
                'status': exp.get('status'),
                'yield_kg_ha': first_result.get('yield_kg_ha'),
                'maturity_days': first_result.get('maturity_days'),
                'water_stress': first_result.get('water_stress'),
                'nitrogen_stress': first_result.get('nitrogen_stress'),
            })
        except Exception as e:
            comparisons.append({'experiment_id': eid, 'error': str(e)})

    return {'experiments': comparisons, 'count': len(comparisons)}


# ---- Required DSSAT weather variables ----
_DSSAT_REQUIRED_VARS = {'srad', 'tmax', 'tmin', 'rain'}

# Map generic chat intents to this agent's internal handling
_INTENT_MAP = {
    'task': 'simulation',
    'simulation': 'simulation',
    'knowledge': 'knowledge',
    'browse': 'crop_info',
    'crop_info': 'crop_info',
}
_INTENTS = list(set(_INTENT_MAP.values()))


# Common name aliases for crops the LLM might not match correctly
_CROP_ALIASES = {
    'MZ': 'Maize (Corn)',
    'SB': 'Soybean (Soybeans)',
    'RI': 'Rice (Paddy)',
    'PN': 'Peanut (Groundnut)',
    'PT': 'Potato (Irish Potato)',
    'PP': 'Pepper (Bell Pepper)',
    'ML': 'Pearl Millet (Millet)',
    'BN': 'Dry Bean (Common Bean)',
    'CO': 'Cotton (Upland Cotton)',
    'BA': 'Barley (Spring Barley)',
}


def _build_crop_values() -> List[Dict]:
    """Fetch crop list from crop_service and format as valid_values with aliases."""
    from dssat_agent.services.crop_service import list_crops as _list_crops
    crops = _list_crops()
    result = []
    for c in crops:
        name = _CROP_ALIASES.get(c['code'], c['name'])
        entry = {'code': c['code'], 'name': name}
        if c.get('available_models'):
            entry['available_models'] = c['available_models']
            entry['default_model'] = c.get('default_model', '')
        result.append(entry)
    return result


def _build_weather_source_values() -> List[Dict]:
    """
    Fetch raster sources from data_agent and return only those that
    provide all 4 DSSAT weather variables (srad, tmax, tmin, rain).
    """
    from data_agent.services import list_raster_sources
    result = list_raster_sources()

    sources = []

    # Single sources that cover all 4 variables
    for src in result.get('single_sources', []):
        vars_set = {v.lower() for v in src.get('variables', [])}
        if _DSSAT_REQUIRED_VARS.issubset(vars_set):
            sources.append({
                'name': src['name'],
                'prefix': src.get('prefix', ''),
                'resolution': src.get('resolution', ''),
                'type': 'single',
            })

    # Combined sources always cover all 4 (they have rain/tmax/tmin/srad sources)
    for combo in result.get('combined_sources', []):
        sources.append({
            'name': combo['name'],
            'prefix': combo.get('prefix', ''),
            'resolution': combo.get('target_resolution', ''),
            'type': 'combined',
        })

    return sources


def get_parameter_schema(intent: str = 'task') -> Dict:
    """
    Return the parameter schema that the chat agent should use for extraction.

    Accepts both generic intents ('task', 'browse', 'knowledge') and
    internal ones ('simulation', 'crop_info'). Maps generics to internal.

    Returns:
        dict with 'fields' (list of field definitions) and 'intents' (list of valid intents)
    """
    # Map generic intent to internal
    intent = _INTENT_MAP.get(intent, intent)

    if intent == 'simulation':
        # -- Crop values --
        try:
            crop_values = _build_crop_values()
        except Exception as exc:
            logger.warning("Could not fetch crop list for schema: %s", exc)
            crop_values = []

        # -- Weather source values --
        try:
            weather_values = _build_weather_source_values()
        except Exception as exc:
            logger.warning("Could not fetch weather sources for schema: %s", exc)
            weather_values = []

        fields = [
            {
                'name': 'crop_code',
                'type': 'string',
                'required': True,
                'description': 'DSSAT crop code',
                'valid_values': crop_values,
            },
            {
                'name': 'crop_name',
                'type': 'string',
                'required': True,
                'description': 'Human-readable crop name',
            },
            {
                'name': 'dssat_model',
                'type': 'string',
                'required': False,
                'description': 'DSSAT simulation model code (e.g. MZCER, WHAPS). Auto-selected if omitted.',
            },
            {
                'name': 'cultivar_code',
                'type': 'string',
                'required': False,
                'description': 'DSSAT cultivar code (auto-selected if null)',
            },
            {
                'name': 'location_name',
                'type': 'string',
                'required': True,
                'description': 'Name of the location (city, county, region)',
            },
            {
                'name': 'latitude',
                'type': 'number',
                'required': False,
                'description': 'Latitude in decimal degrees (resolved from location_name if omitted)',
            },
            {
                'name': 'longitude',
                'type': 'number',
                'required': False,
                'description': 'Longitude in decimal degrees (resolved from location_name if omitted)',
            },
            {
                'name': 'planting_date',
                'type': 'string',
                'required': True,
                'description': 'Planting date in YYYY-MM-DD format',
            },
            {
                'name': 'soil_id',
                'type': 'string',
                'required': False,
                'description': 'Soil profile ID (auto-selected if null)',
            },
            {
                'name': 'weather_source',
                'type': 'string',
                'required': False,
                'description': 'Weather data source name (must provide all 4 DSSAT vars: srad, tmax, tmin, rain)',
                'valid_values': weather_values,
            },
            {
                'name': 'fertilizer',
                'type': 'array',
                'required': False,
                'description': 'List of fertilizer application events',
            },
            {
                'name': 'irrigation',
                'type': 'object',
                'required': False,
                'description': 'Irrigation management configuration',
            },
            {
                'name': 'experiment_type',
                'type': 'string',
                'required': False,
                'description': (
                    'Type of experiment to run. '
                    '"single": one simulation for a specific scenario (default). '
                    '"ensemble": compare multiple management strategies side by side '
                    '(use when user says compare, best, optimize, sensitivity, vs, try different). '
                    '"monte_carlo": spatial uncertainty analysis across a region '
                    '(use when user asks about expected/average yield for a state, county, or area). '
                    '"batch": run independent experiments at multiple discrete locations '
                    '(use when user says "across all counties", "at these locations", '
                    '"for each state", "multi-location", "every county in").'
                ),
                'valid_values': [
                    'single',
                    'ensemble',
                    'monte_carlo',
                    'batch',
                ],
            },
            {
                'name': 'num_years',
                'type': 'integer',
                'required': False,
                'description': 'Number of simulation years',
                'default': 1,
            },
            {
                'name': 'plant_population',
                'type': 'number',
                'required': False,
                'description': 'Plant population (plants per m2)',
            },
            {
                'name': 'row_spacing',
                'type': 'number',
                'required': False,
                'description': 'Row spacing in cm',
            },
            {
                'name': 'additional_instructions',
                'type': 'string',
                'required': False,
                'description': (
                    'The user\'s analysis intent and any special requests. Extract verbatim from the user message. '
                    'For ensemble experiments, this drives automatic treatment generation: '
                    '"compare fertilizer plans", "find the best irrigation strategy", '
                    '"test planting dates", "should I irrigate". '
                    'For single experiments: "include phosphorus stress", "show soil nitrogen dynamics". '
                    'IMPORTANT: Always include the user\'s comparison/optimization intent here.'
                ),
            },
            # Batch-specific fields
            {
                'name': 'sub_experiment_type',
                'type': 'string',
                'required': False,
                'description': (
                    'For batch experiments only: which experiment type to run at each location. '
                    'Defaults to "single". Set to "ensemble" if the user wants to compare '
                    'treatments at every location.'
                ),
                'valid_values': ['single', 'ensemble', 'monte_carlo'],
            },
            {
                'name': 'location_selection_mode',
                'type': 'string',
                'required': False,
                'description': (
                    'For batch experiments: how locations are specified. '
                    '"admin_boundary": expand an admin region into sub-units '
                    '(e.g. all counties in a state). '
                    '"points": explicit list of lat/lon points.'
                ),
                'valid_values': ['admin_boundary', 'points', 'bbox_grid'],
            },
            {
                'name': 'admin_parent',
                'type': 'string',
                'required': False,
                'description': (
                    'For batch admin_boundary mode: parent admin unit name '
                    '(e.g. "Alabama" to get all counties in Alabama, '
                    '"United States" to get all states).'
                ),
            },
            {
                'name': 'admin_child_level',
                'type': 'string',
                'required': False,
                'description': (
                    'For batch admin_boundary mode: child admin level to expand to. '
                    'Use "admin2" for counties (default), "admin1" for states.'
                ),
                'valid_values': ['admin1', 'admin2'],
            },
            {
                'name': 'batch_locations',
                'type': 'array',
                'required': False,
                'description': (
                    'For batch experiments with mode "points": '
                    'list of location objects [{lat, lon, name}, ...].'
                ),
            },
        ]

    elif intent == 'knowledge':
        fields = [
            {
                'name': 'query',
                'type': 'string',
                'required': True,
                'description': "The user's question to search DSSAT knowledge base",
            },
        ]

    elif intent == 'crop_info':
        fields = [
            {
                'name': 'info_type',
                'type': 'string',
                'required': True,
                'description': 'Category of crop information to retrieve',
                'valid_values': ['crops', 'cultivars', 'soils', 'codes'],
            },
            {
                'name': 'crop_code',
                'type': 'string',
                'required': False,
                'description': 'DSSAT crop code for filtering (optional)',
            },
        ]

    else:
        return {
            'error': f"Unknown intent: {intent}. Valid intents: {_INTENTS}",
            'intents': _INTENTS,
        }

    return {
        'intent': intent,
        'fields': fields,
        'intents': _INTENTS,
    }
