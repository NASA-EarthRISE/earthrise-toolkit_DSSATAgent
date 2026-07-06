"""
SimulationAgent skill executor.

Routes A2A skill requests to the appropriate service handler.
"""

import logging

from DSSATTools.base.partypes import CODE_VARS

from dssat_agent.services import crop_service, soil_service, weather_service, experiment_service, ensemble_service, monte_carlo_service, spatial_data_service
from dssat_agent.services.codes_service import get_codes_with_descriptions
from dssat_agent.models import StoredSoilProfile, StoredCropFile, ExperimentSession, SimulationResult
from dssat_agent.services.normalizer import normalize_params

logger = logging.getLogger(__name__)


def _build_experiment_label(params: dict, experiment_type: str = 'single') -> str:
    """Build a human-readable label from experiment identifiers.

    Produces labels like "Maize in Alabama — Single Simulation".
    """
    from datetime import datetime
    from dssat_agent.services.crop_service import CROP_NAMES

    type_labels = {
        'single': 'Single Simulation',
        'ensemble': 'Ensemble Comparison',
        'monte_carlo': 'Monte Carlo Analysis',
    }

    # Crop name
    crop_code = params.get('crop_code', '')
    crop_name = CROP_NAMES.get(crop_code.upper(), crop_code.upper()) if crop_code else ''

    # Location: prefer name, fall back to coordinates
    location = params.get('location_name', '')
    if not location:
        field = params.get('field') or {}
        lat = _first_not_none(field, 'latitude', 'lat') or _first_not_none(params, 'latitude', 'lat')
        lon = _first_not_none(field, 'longitude', 'lon') or _first_not_none(params, 'longitude', 'lon')
        if lat is not None and lon is not None:
            location = f"{abs(lat):.2f}{'N' if lat >= 0 else 'S'}, {abs(lon):.2f}{'W' if lon < 0 else 'E'}"

    # Experiment type
    type_label = type_labels.get(experiment_type, experiment_type.replace('_', ' ').title())

    # Build label
    if crop_name and location:
        label = f"{crop_name} in {location} — {type_label}"
    elif crop_name:
        label = f"{crop_name} — {type_label}"
    elif location:
        label = f"{type_label} in {location}"
    else:
        label = f"{type_label} ({datetime.utcnow().strftime('%b %d, %Y %H:%M')})"

    return label[:200]


def _first_not_none(d: dict, *keys):
    """Return the first value from d[key] that is not None."""
    for k in keys:
        v = d.get(k)
        if v is not None:
            return v
    return None

# Code categories for list_codes skill
CODE_CATEGORIES = {
    'fertilizer_materials': 'fmcd',
    'application_methods': 'facd',
    'planting_methods': 'plme',
    'planting_distributions': 'plds',
    'drainage_types': 'fldt',
    'soil_textures': 'sltx',
    'soil_classifications': 'soil_clasification',
    'soil_colors': 'scom',
    'evaporation_methods': 'evapo',
    'som_methods': 'mesom',
    'harvest_components': 'hcom',
    'harvest_sizes': 'hsize',
    'crop_codes': 'cr',
    'water_switch': 'water',
    'nitrogen_switch': 'nitro',
    'plant_management': 'plant',
    'irrigation_management': 'irrig',
    'harvest_management': 'harvs',
    'tillage_implements': 'timpl',
    'chemical_codes': 'chcod',
    'residue_codes': 'rcod',
    'irrigation_methods': 'irop',
}

# Short aliases so both "planting" and "planting_methods" work
CATEGORY_ALIASES = {
    'planting': 'planting_methods',
    'fertilizer': 'fertilizer_materials',
    'irrigation': 'irrigation_methods',
    'tillage': 'tillage_implements',
    'chemical': 'chemical_codes',
    'residue': 'residue_codes',
    'crop': 'crop_codes',
    'application': 'application_methods',
    'soil': 'soil_textures',
    'drainage': 'drainage_types',
    'harvest': 'harvest_components',
}


def dispatch_skill(skill_name, params):
    """
    Route a skill request to the appropriate handler.

    Parameters
    ----------
    skill_name : str
        The skill ID from the A2A message.
    params : dict
        Skill parameters.

    Returns
    -------
    dict
        Result data to return in the A2A response.
    """
    from django.db import close_old_connections
    close_old_connections()

    params = normalize_params(skill_name, params)
    handler = SKILL_HANDLERS.get(skill_name)
    if handler is None:
        return {"error": f"Unknown skill: {skill_name}"}
    return handler(params)


# ---- Skill handlers ----

def _handle_list_crops(params):
    return {"crops": crop_service.list_crops()}


def _handle_list_cultivars(params):
    crop_code = params.get('crop_code')
    if not crop_code:
        return {"error": "crop_code is required"}
    return crop_service.list_cultivars(crop_code)


def _handle_get_cultivar_details(params):
    crop_code = params.get('crop_code')
    cultivar_code = params.get('cultivar_code')
    if not crop_code or not cultivar_code:
        return {"error": "crop_code and cultivar_code are required"}
    return crop_service.get_cultivar_details(crop_code, cultivar_code)


def _handle_list_soils(params):
    return soil_service.list_soils(
        source=params.get('source'),
        search=params.get('search'),
        country=params.get('country'),
        classification=params.get('classification'),
        page=params.get('page', 1),
        page_size=params.get('page_size', 50),
    )


def _handle_get_soil_profile(params):
    soil_id = params.get('soil_id')
    if not soil_id:
        return {"error": "soil_id is required"}
    return soil_service.get_soil_profile(soil_id)


def _handle_create_soil_profile(params):
    return soil_service.create_soil_profile(params)


def _handle_create_soil_from_texture(params):
    return soil_service.create_soil_from_texture(params)


def _handle_add_soil_layer(params):
    soil_id = params.get('soil_id')
    if not soil_id:
        return {"error": "soil_id is required"}
    layer_params = {k: v for k, v in params.items() if k != 'soil_id'}
    return soil_service.add_soil_layer(soil_id, layer_params)


def _handle_update_soil_layer(params):
    soil_id = params.get('soil_id')
    layer_index = params.get('layer_index')
    if not soil_id or layer_index is None:
        return {"error": "soil_id and layer_index are required"}
    updated_params = {k: v for k, v in params.items() if k not in ('soil_id', 'layer_index')}
    return soil_service.update_soil_layer(soil_id, layer_index, updated_params)


def _handle_remove_soil_layer(params):
    soil_id = params.get('soil_id')
    layer_index = params.get('layer_index')
    if not soil_id or layer_index is None:
        return {"error": "soil_id and layer_index are required"}
    return soil_service.remove_soil_layer(soil_id, layer_index)


def _handle_estimate_soil_properties(params):
    clay_pct = params.get('clay_pct')
    silt_pct = params.get('silt_pct')
    if clay_pct is None or silt_pct is None:
        return {"error": "clay_pct and silt_pct are required"}
    return soil_service.estimate_soil_properties(
        clay_pct=clay_pct,
        silt_pct=silt_pct,
        bulk_density=params.get('bulk_density'),
        organic_carbon=params.get('organic_carbon'),
    )


def _handle_run_simulation(params):
    # Create ExperimentSession before running
    session = ExperimentSession.objects.create(
        status='running',
        experiment_type='single',
        crop_code=params.get('crop_code') or '',
        cultivar_code=params.get('cultivar_code') or '',
        dssat_model=params.get('dssat_model') or '',
        label=_build_experiment_label(params, 'single'),
        raw_params=params,
        planting=params.get('planting'),
        fertilizer=params.get('fertilizer'),
        irrigation=params.get('irrigation'),
        harvest=params.get('harvest'),
        initial_conditions=params.get('initial_conditions'),
        simulation_controls=params.get('simulation_controls'),
        weather_data=params.get('weather_data'),
        field_params=params.get('field'),
        inline_soil=params.get('inline_soil'),
        chat_id=params.get('chat_id'),
        user_id=params.get('user_id'),
    )
    # Resolve soil FK if soil_id provided
    soil_id = params.get('soil_id')
    if soil_id:
        try:
            session.soil_profile = StoredSoilProfile.objects.get(soil_id=soil_id)
            session.save(update_fields=['soil_profile'])
        except StoredSoilProfile.DoesNotExist:
            pass

    result = experiment_service.run_simulation(params)

    if result.get('status') == 'completed':
        SimulationResult.objects.create(
            experiment=session,
            summary=result.get('summary', {}),
            plant_growth=result.get('plant_growth'),
            soil_water=result.get('soil_water'),
            soil_organic=result.get('soil_organic'),
            soil_nitrogen=result.get('soil_nitrogen'),
            weather_output=result.get('weather_output'),
            overview=result.get('overview', ''),
            stdout=result.get('stdout', ''),
            dssat_files=result.get('dssat_files'),
        )
        session.status = 'completed'
    elif result.get('status') == 'missing_data':
        session.status = 'failed'
        session.validation_errors = ['missing_data']
    else:
        session.status = 'failed'
        session.validation_errors = result.get('errors', [result.get('error', 'Unknown')])
        # Persist whatever DSSAT wrote before crashing so the failed
        # ExperimentSession's detail view still has FILEX/SOIL.SOL/
        # WARNING.OUT for diagnosis. The runner now returns dssat_files
        # in its failure envelope.
        if result.get('dssat_files'):
            SimulationResult.objects.create(
                experiment=session,
                summary={},
                stdout=result.get('stdout', ''),
                dssat_files=result.get('dssat_files'),
            )
    session.save(update_fields=['status', 'validation_errors'])

    result['experiment_id'] = str(session.pk)
    result['experiment_label'] = session.label
    return result


def _handle_validate_experiment(params):
    return experiment_service.validate_experiment_only(params)


def _handle_run_ensemble(params):
    base = params.get('base', {})
    # Coerce None → '' so the NOT NULL constraint doesn't crash when an
    # upstream builder couldn't resolve a cultivar/model.
    session = ExperimentSession.objects.create(
        status='running',
        experiment_type='ensemble',
        crop_code=base.get('crop_code') or '',
        cultivar_code=base.get('cultivar_code') or '',
        dssat_model=base.get('dssat_model') or '',
        label=_build_experiment_label(base, 'ensemble'),
        raw_params=params,
        chat_id=params.get('chat_id') or base.get('chat_id'),
        user_id=params.get('user_id') or base.get('user_id'),
    )

    result = ensemble_service.run_ensemble(params)

    if result.get('status') == 'completed':
        # Create one SimulationResult per treatment
        treatments = result.get('treatments', [])
        for i, t in enumerate(treatments):
            SimulationResult.objects.create(
                experiment=session,
                summary=t.get('summary', {}),
                plant_growth=t.get('plant_growth'),
                soil_water=t.get('soil_water'),
                soil_organic=t.get('soil_organic'),
                soil_nitrogen=t.get('soil_nitrogen'),
                weather_output=t.get('weather_output'),
                overview=t.get('overview', ''),
                stdout=t.get('stdout', ''),
                dssat_files=t.get('dssat_files'),
            )
        session.aggregate_summary = result.get('aggregate', result.get('summary'))
        session.status = 'completed'
    else:
        session.status = 'failed'
        session.validation_errors = result.get('errors', [result.get('error', 'Unknown')])
        # Persist captured FILEX/SOIL.SOL/WARNING.OUT so the failed
        # ExperimentSession is diagnosable.
        if result.get('dssat_files'):
            SimulationResult.objects.create(
                experiment=session,
                summary={},
                stdout=result.get('stdout', ''),
                dssat_files=result.get('dssat_files'),
            )
    session.save(update_fields=['status', 'validation_errors', 'aggregate_summary'])

    result['experiment_id'] = str(session.pk)
    result['experiment_label'] = session.label
    return result


def _handle_list_codes(params):
    category = params.get('category')
    if not category:
        all_cats = list(CODE_CATEGORIES.keys()) + list(CATEGORY_ALIASES.keys())
        return {"error": "category is required", "available_categories": all_cats}

    # Resolve alias to canonical name
    resolved = CATEGORY_ALIASES.get(category, category)

    code_var_name = CODE_CATEGORIES.get(resolved)
    if code_var_name is None:
        all_cats = list(CODE_CATEGORIES.keys()) + list(CATEGORY_ALIASES.keys())
        return {"error": f"Unknown category: {category}", "available_categories": all_cats}

    codes = get_codes_with_descriptions(code_var_name)
    return {
        "category": resolved,
        "dssat_variable": code_var_name,
        "codes": codes,
    }


def _handle_get_soil_sol_file(params):
    soil_id = params.get('soil_id')
    if not soil_id:
        return {"error": "soil_id is required"}
    try:
        profile = StoredSoilProfile.objects.get(soil_id=soil_id)
    except StoredSoilProfile.DoesNotExist:
        return {"error": f"Soil profile '{soil_id}' not found"}
    if not profile.sol_file:
        return {"error": f"No .SOL file generated for '{soil_id}'"}
    return {"soil_id": soil_id, "content": profile.sol_file, "filename": f"{soil_id}.SOL"}


def _handle_get_crop_cul_file(params):
    crop_code = params.get('crop_code', '').upper()
    dssat_model = params.get('dssat_model', '')
    if not crop_code:
        return {"error": "crop_code is required"}
    try:
        cf = StoredCropFile.objects.get(crop_code=crop_code, dssat_model=dssat_model)
    except StoredCropFile.DoesNotExist:
        # Fallback: try without model filter
        cf = StoredCropFile.objects.filter(crop_code=crop_code).first()
        if cf is None:
            return {"error": f"No stored .CUL file for crop '{crop_code}'"}
    return {"crop_code": crop_code, "content": cf.cul_file, "filename": f"{crop_code}.CUL"}


def _handle_get_crop_eco_file(params):
    crop_code = params.get('crop_code', '').upper()
    dssat_model = params.get('dssat_model', '')
    if not crop_code:
        return {"error": "crop_code is required"}
    try:
        cf = StoredCropFile.objects.get(crop_code=crop_code, dssat_model=dssat_model)
    except StoredCropFile.DoesNotExist:
        cf = StoredCropFile.objects.filter(crop_code=crop_code).first()
        if cf is None:
            return {"error": f"No stored .ECO file for crop '{crop_code}'"}
    if not cf.eco_file:
        return {"error": f"Crop '{crop_code}' does not have an .ECO file"}
    return {"crop_code": crop_code, "content": cf.eco_file, "filename": f"{crop_code}.ECO"}


def _handle_get_simulation_files(params):
    result_id = params.get('result_id')
    experiment_id = params.get('experiment_id')

    if result_id:
        try:
            result = SimulationResult.objects.get(pk=result_id)
        except SimulationResult.DoesNotExist:
            return {"error": f"Result '{result_id}' not found"}
    elif experiment_id:
        try:
            result = SimulationResult.objects.filter(
                experiment_id=experiment_id
            ).order_by('-completed_at').first()
        except Exception as e:
            return {"error": str(e)}
    else:
        return {"error": "experiment_id or result_id is required"}

    if not result:
        return {"error": "No result found"}
    if not result.dssat_files:
        return {"error": "No DSSAT files captured for this simulation"}
    listing = {"input": {}, "output": {}}
    for ftype in ("input", "output"):
        files = result.dssat_files.get(ftype, {})
        for key, content in files.items():
            listing[ftype][key] = len(content) if isinstance(content, str) else 0
    return {"experiment_id": str(result.experiment_id), "result_id": str(result.pk), "files": listing}


def _handle_get_simulation_file(params):
    result_id = params.get('result_id')
    experiment_id = params.get('experiment_id')
    file_type = params.get('file_type')
    file_key = params.get('file_key')
    if not file_type or not file_key:
        return {"error": "file_type and file_key are required"}
    if file_type not in ('input', 'output'):
        return {"error": "file_type must be 'input' or 'output'"}

    if result_id:
        try:
            result = SimulationResult.objects.get(pk=result_id)
        except SimulationResult.DoesNotExist:
            return {"error": f"Result '{result_id}' not found"}
    elif experiment_id:
        try:
            result = SimulationResult.objects.filter(
                experiment_id=experiment_id
            ).order_by('-completed_at').first()
        except Exception as e:
            return {"error": str(e)}
    else:
        return {"error": "experiment_id or result_id is required"}

    if not result or not result.dssat_files:
        return {"error": "No DSSAT files found"}
    content = result.dssat_files.get(file_type, {}).get(file_key)
    if content is None:
        return {"error": f"File '{file_key}' not found in {file_type} files"}
    return {"content": content, "file_key": file_key, "file_type": file_type}


def _handle_run_monte_carlo(params):
    session = ExperimentSession.objects.create(
        status='running',
        experiment_type='monte_carlo',
        crop_code=params.get('crop_code') or '',
        cultivar_code=params.get('cultivar_code') or '',
        dssat_model=params.get('dssat_model') or '',
        label=_build_experiment_label(params, 'monte_carlo'),
        raw_params=params,
        chat_id=params.get('chat_id'),
        user_id=params.get('user_id'),
    )

    result = monte_carlo_service.run_monte_carlo(params)

    if result.get('status') == 'completed':
        # Create one SimulationResult per sampled point. The shared input
        # bundle (real multi-treatment FileX, SOIL.SOL, etc.) is duplicated
        # onto each row so the UI download path stays uniform with single
        # and ensemble runs.
        treatments = result.get('treatments', [])
        for t in treatments:
            SimulationResult.objects.create(
                experiment=session,
                summary=t.get('summary', {}),
                dssat_files=t.get('dssat_files'),
            )
        session.aggregate_summary = result.get('quantiles')
        session.status = 'completed'
    else:
        session.status = 'failed'
        session.validation_errors = result.get('errors', [result.get('error', 'Unknown')])
        # Persist captured FILEX/SOIL.SOL/WARNING.OUT for diagnosis.
        if result.get('dssat_files'):
            SimulationResult.objects.create(
                experiment=session,
                summary={},
                dssat_files=result.get('dssat_files'),
            )
    session.save(update_fields=['status', 'validation_errors', 'aggregate_summary'])

    result['experiment_id'] = str(session.pk)
    result['experiment_label'] = session.label
    return result


def _handle_get_experiment(params):
    experiment_id = params.get('experiment_id')
    if not experiment_id:
        return {"error": "experiment_id is required"}
    try:
        session = ExperimentSession.objects.get(pk=experiment_id)
    except ExperimentSession.DoesNotExist:
        return {"error": f"Experiment '{experiment_id}' not found"}

    data = {
        'id': str(session.pk),
        'status': session.status,
        'experiment_type': session.experiment_type,
        'crop_code': session.crop_code,
        'cultivar_code': session.cultivar_code,
        'soil_profile': session.soil_profile.soil_id if session.soil_profile else None,
        'raw_params': session.raw_params,
        'aggregate_summary': session.aggregate_summary,
        'created_at': session.created_at.isoformat(),
        'updated_at': session.updated_at.isoformat(),
    }

    # Include related results
    results = []
    for r in session.results.order_by('completed_at'):
        result_data = {
            'id': str(r.pk),
            'summary': r.summary,
            'has_plant_growth': r.plant_growth is not None,
            'has_dssat_files': r.dssat_files is not None and bool(r.dssat_files),
            'dssat_files_listing': None,
            'overview': r.overview[:500] if r.overview else '',
            'completed_at': r.completed_at.isoformat() if r.completed_at else None,
        }
        # Build file listing inline
        if r.dssat_files:
            listing = {'input': {}, 'output': {}}
            for ftype in ('input', 'output'):
                for key, content in r.dssat_files.get(ftype, {}).items():
                    listing[ftype][key] = len(content) if isinstance(content, str) else 0
            result_data['dssat_files_listing'] = listing
        results.append(result_data)
    data['results'] = results
    return data


def _handle_list_experiments(params):
    qs = ExperimentSession.objects.all()

    # Optional filters
    exp_type = params.get('experiment_type')
    if exp_type:
        qs = qs.filter(experiment_type=exp_type)
    status = params.get('status')
    if status:
        qs = qs.filter(status=status)
    crop_code = params.get('crop_code')
    if crop_code:
        qs = qs.filter(crop_code=crop_code)

    # Pagination
    page = int(params.get('page', 1))
    page_size = min(int(params.get('page_size', 50)), 100)
    total = qs.count()
    offset = (page - 1) * page_size
    sessions = qs[offset:offset + page_size]

    items = []
    for s in sessions:
        items.append({
            'id': str(s.pk),
            'status': s.status,
            'experiment_type': s.experiment_type,
            'crop_code': s.crop_code,
            'cultivar_code': s.cultivar_code,
            'created_at': s.created_at.isoformat(),
            'results_count': s.results.count(),
        })

    return {
        'experiments': items,
        'total': total,
        'page': page,
        'page_size': page_size,
    }


def _handle_list_admin_units(params):
    return spatial_data_service.list_admin_units(
        level=params.get('level', 'admin1'),
        parent=params.get('parent'),
    )


def _handle_get_admin_geojson(params):
    return spatial_data_service.get_admin_boundaries_geojson(
        level=params.get('level', 'admin1'),
        parent=params.get('parent'),
    )


def _handle_calculate_weather_dates(params):
    crop_code = params.get('crop_code')
    planting_date = params.get('planting_date')
    if not crop_code or not planting_date:
        return {"error": "crop_code and planting_date are required"}
    return crop_service.calculate_weather_dates(
        crop_code=crop_code,
        planting_date=planting_date,
        num_years=params.get('num_years', 1),
    )


def _handle_prepare_weather(params):
    from dssat_agent.services.workflow import prepare_weather
    return prepare_weather(**params)


def _handle_build_experiment(params):
    from dssat_agent.services.workflow import build_experiment
    return build_experiment(
        params=params.get('params', params),
        wizard_params=params.get('wizard_params'),
    )


def _handle_run_experiment(params):
    from dssat_agent.services.workflow import run_experiment
    return run_experiment(params.get('experiment', params))


def _handle_run_full_simulation(params):
    from dssat_agent.services.workflow import run_full_simulation
    return run_full_simulation(
        params=params.get('params', params),
        wizard_params=params.get('wizard_params'),
        chat_id=params.get('chat_id'),
        user_id=params.get('user_id'),
    )


def _handle_run_batch(params):
    """Create a batch experiment and dispatch async execution.

    Two payload shapes are accepted:

    1. **Wizard draft (per-pair)** — ``params['pair_params']`` is a list
       of fully-resolved per-pair param dicts (one per
       ``WizardDraftTreatment`` row). Each pair becomes one child
       ``ExperimentSession`` directly; no shared template flattening.
    2. **Legacy (template + location_config)** — chat-driven path.
       ``params['location_config']`` resolves to N locations; each gets
       ``params['template_params']`` merged with its lat/lon.
    """
    from dssat_agent.models import BatchExperiment
    from dssat_agent.services.batch_service import resolve_batch_locations, MAX_BATCH_LOCATIONS
    from dssat_agent.tasks import run_batch_async

    pair_params = params.get('pair_params')
    sub_type = params.get('sub_experiment_type', 'single')

    if pair_params:
        # ---- Wizard-draft per-pair path ----
        if not isinstance(pair_params, list) or not pair_params:
            return {"status": "error", "error": "pair_params must be a non-empty list"}
        if len(pair_params) > MAX_BATCH_LOCATIONS:
            return {
                "status": "error",
                "error": f"Too many pairs ({len(pair_params)}). "
                         f"Maximum is {MAX_BATCH_LOCATIONS}.",
            }
        from dssat_agent.services.crop_service import CROP_NAMES
        first = pair_params[0]
        crop = (first.get('crop_code') or '').upper()
        crop_name = CROP_NAMES.get(crop, crop) if crop else 'Batch'
        label = (f"{crop_name or 'Batch'} — {len(pair_params)} pairs "
                 f"(wizard)")[:200]
        batch = BatchExperiment.objects.create(
            status='queued',
            sub_experiment_type=sub_type,
            location_selection_mode='wizard_pairs',
            location_config={},
            template_params={},
            total_locations=len(pair_params),
            label=label,
            chat_id=params.get('chat_id'),
        )
        run_batch_async.delay(str(batch.pk), params)
        return {
            "status": "queued",
            "batch_id": str(batch.pk),
            "batch_label": label,
            "total_locations": len(pair_params),
            "locations": [
                (p.get('location_name') or p.get('location_label')
                 or f'Pair {i+1}')
                for i, p in enumerate(pair_params)
            ],
            "sub_experiment_type": sub_type,
            "detail_url": f"/explorer/batch/{batch.pk}/",
        }

    # ---- Legacy template + location_config path ----
    location_config = params.get('location_config', {})
    template_params = params.get('template_params', {})

    # Pre-validate location resolution
    try:
        locations = resolve_batch_locations(location_config)
    except ValueError as e:
        return {"status": "error", "error": str(e)}

    if len(locations) > MAX_BATCH_LOCATIONS:
        return {
            "status": "error",
            "error": f"Too many locations ({len(locations)}). Maximum is {MAX_BATCH_LOCATIONS}.",
        }

    # Build human-readable label
    from dssat_agent.services.crop_service import CROP_NAMES
    mode = location_config.get('mode', 'admin_boundary')
    parent = location_config.get('admin_parent', '')
    crop = template_params.get('crop_code', '')
    crop_name = CROP_NAMES.get(crop.upper(), crop.upper()) if crop else ''

    type_labels = {'single': 'Single', 'ensemble': 'Ensemble', 'monte_carlo': 'Monte Carlo'}
    sub_label = type_labels.get(sub_type, sub_type)

    if crop_name and parent:
        label = f"{crop_name} across {parent} — {len(locations)} locations ({sub_label})"
    elif crop_name:
        label = f"{crop_name} — {len(locations)} locations ({sub_label})"
    elif parent:
        label = f"Batch across {parent} — {len(locations)} locations ({sub_label})"
    else:
        label = f"Batch — {len(locations)} locations ({sub_label})"
    label = label[:200]

    batch = BatchExperiment.objects.create(
        status='queued',
        sub_experiment_type=sub_type,
        location_selection_mode=mode,
        location_config=location_config,
        template_params=template_params,
        total_locations=len(locations),
        label=label,
        chat_id=params.get('chat_id'),
    )

    # Dispatch async
    run_batch_async.delay(str(batch.pk), params)

    return {
        "status": "queued",
        "batch_id": str(batch.pk),
        "batch_label": label,
        "total_locations": len(locations),
        "locations": [loc['name'] for loc in locations],
        "sub_experiment_type": sub_type,
        "detail_url": f"/explorer/batch/{batch.pk}/",
    }


def _handle_get_batch(params):
    """Get batch experiment status and results."""
    from dssat_agent.models import BatchExperiment

    batch_id = params.get('batch_id')
    if not batch_id:
        return {"error": "batch_id is required"}

    try:
        batch = BatchExperiment.objects.get(pk=batch_id)
    except BatchExperiment.DoesNotExist:
        return {"error": f"Batch '{batch_id}' not found"}

    data = {
        'batch_id': str(batch.pk),
        'status': batch.status,
        'sub_experiment_type': batch.sub_experiment_type,
        'location_selection_mode': batch.location_selection_mode,
        'total_locations': batch.total_locations,
        'completed': batch.completed_count,
        'failed': batch.failed_count,
        'label': batch.label,
        'aggregate_summary': batch.aggregate_summary,
        'created_at': batch.created_at.isoformat(),
        'updated_at': batch.updated_at.isoformat(),
    }

    # Include per-child summary
    children = []
    for child in batch.children.order_by('location_label'):
        child_data = {
            'experiment_id': str(child.pk),
            'location_label': child.location_label,
            'status': child.status,
        }
        result = child.results.order_by('-completed_at').first()
        if result and result.summary:
            child_data['yield_kg_ha'] = result.summary.get('yield_kg_ha') or result.summary.get('hwam')
            child_data['maturity_days'] = result.summary.get('maturity_days') or result.summary.get('mat')
        children.append(child_data)

    data['children'] = children
    return data


SKILL_HANDLERS = {
    'list_crops': _handle_list_crops,
    'list_cultivars': _handle_list_cultivars,
    'get_cultivar_details': _handle_get_cultivar_details,
    'calculate_weather_dates': _handle_calculate_weather_dates,
    'list_soils': _handle_list_soils,
    'get_soil_profile': _handle_get_soil_profile,
    'create_soil_profile': _handle_create_soil_profile,
    'create_soil_from_texture': _handle_create_soil_from_texture,
    'add_soil_layer': _handle_add_soil_layer,
    'update_soil_layer': _handle_update_soil_layer,
    'remove_soil_layer': _handle_remove_soil_layer,
    'estimate_soil_properties': _handle_estimate_soil_properties,
    'run_simulation': _handle_run_simulation,
    'run_ensemble': _handle_run_ensemble,
    'run_monte_carlo': _handle_run_monte_carlo,
    'validate_experiment': _handle_validate_experiment,
    'list_admin_units': _handle_list_admin_units,
    'get_admin_geojson': _handle_get_admin_geojson,
    'list_codes': _handle_list_codes,
    'get_soil_sol_file': _handle_get_soil_sol_file,
    'get_crop_cul_file': _handle_get_crop_cul_file,
    'get_crop_eco_file': _handle_get_crop_eco_file,
    'get_simulation_files': _handle_get_simulation_files,
    'get_simulation_file': _handle_get_simulation_file,
    'get_experiment': _handle_get_experiment,
    'list_experiments': _handle_list_experiments,
    # High-level workflow skills
    'prepare_weather': _handle_prepare_weather,
    'build_experiment': _handle_build_experiment,
    'run_experiment': _handle_run_experiment,
    'run_full_simulation': _handle_run_full_simulation,
    # Batch (multi-location)
    'run_batch': _handle_run_batch,
    'get_batch': _handle_get_batch,
}
