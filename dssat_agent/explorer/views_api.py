"""
REST API views for soils, cultivars, weather, simulations, and experiment wizard.
Thin wrappers around service modules that return JSON.
"""

import json
import logging
import os

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from django.views import View

from earthrise_agents_base.models import Chat, Message
from earthrise_agents_base.agent.chat_agent import ConversationMemory

from dssat_agent.services import crop_service, soil_service, cultivar_service
from dssat_agent.services.codes_service import get_codes_with_descriptions
from dssat_agent.services.experiment_service import validate_experiment_only
from dssat_agent.services.ensemble_service import run_ensemble
from data_agent import services as data_services
from data_agent.services import list_admin_units

logger = logging.getLogger(__name__)


@method_decorator(csrf_exempt, name='dispatch')
class SoilsAPI(View):
    """REST API for soil profiles."""

    def get(self, request):
        try:
            page = int(request.GET.get('page', 1))
            result = soil_service.list_soils(
                source=request.GET.get('source'),
                search=request.GET.get('search'),
                country=request.GET.get('country'),
                page=page,
                page_size=int(request.GET.get('page_size', 25)),
            )
            logger.info("SoilsAPI result keys: %s", list(result.keys()) if isinstance(result, dict) else type(result))
            return JsonResponse({'success': True, **result})
        except Exception as e:
            logger.exception("SoilsAPI error")
            return JsonResponse({'error': str(e)}, status=500)


@method_decorator(csrf_exempt, name='dispatch')
class SoilDetailAPI(View):
    """REST API for a single soil profile."""

    def get(self, request, soil_id):
        try:
            result = soil_service.get_soil_profile(soil_id)
            return JsonResponse({'success': True, **result})
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=400)


@method_decorator(csrf_exempt, name='dispatch')
class CropsAPI(View):
    """REST API for listing crops."""

    def get(self, request):
        try:
            crops = crop_service.list_crops()
            return JsonResponse({'success': True, 'crops': crops})
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=400)


@method_decorator(csrf_exempt, name='dispatch')
class CultivarsAPI(View):
    """REST API for listing cultivars for a crop."""

    def get(self, request, crop_code):
        dssat_model = request.GET.get('dssat_model', None)
        try:
            result = crop_service.list_cultivars(crop_code, dssat_model=dssat_model)
            return JsonResponse({'success': True, **result})
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=400)


@method_decorator(csrf_exempt, name='dispatch')
class CultivarSchemaAPI(View):
    """GET /api/cultivars/<crop_code>/schema/?dssat_model=..."""

    def get(self, request, crop_code):
        dssat_model = request.GET.get('dssat_model', None)
        try:
            schema = cultivar_service.get_cultivar_schema(crop_code, dssat_model)
            return JsonResponse({'success': True, **schema})
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=400)


@method_decorator(csrf_exempt, name='dispatch')
class CultivarCreateAPI(View):
    """POST /api/cultivars/<crop_code>/create/"""

    def post(self, request, crop_code):
        try:
            data = json.loads(request.body)
            c = cultivar_service.create_cultivar(
                crop_code=crop_code,
                dssat_model=data['dssat_model'],
                cultivar_code=data['cultivar_code'],
                cultivar_name=data.get('cultivar_name', ''),
                params=data.get('params', {}),
                ecotype_id=data.get('ecotype_id'),
                source=data.get('source', 'custom'),
            )
            return JsonResponse({'success': True, 'id': str(c.id)})
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=400)


@method_decorator(csrf_exempt, name='dispatch')
class CultivarDetailAPI(View):
    """GET/PUT/DELETE /api/cultivars/<crop_code>/<cultivar_id>/"""

    def get(self, request, crop_code, cultivar_id):
        try:
            result = cultivar_service.get_cultivar_detail_db(cultivar_id)
            if 'error' in result:
                return JsonResponse(result, status=404)
            return JsonResponse({'success': True, **result})
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=400)

    def put(self, request, crop_code, cultivar_id):
        try:
            data = json.loads(request.body)
            c = cultivar_service.update_cultivar(
                cultivar_id,
                cultivar_code=data.get('cultivar_code'),
                cultivar_name=data.get('cultivar_name'),
                params=data.get('params'),
                ecotype_id=data.get('ecotype_id'),
            )
            return JsonResponse({'success': True, 'id': str(c.id)})
        except ValueError as e:
            return JsonResponse({'error': str(e)}, status=403)
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=400)

    def delete(self, request, crop_code, cultivar_id):
        try:
            cultivar_service.delete_cultivar(cultivar_id)
            return JsonResponse({'success': True})
        except ValueError as e:
            return JsonResponse({'error': str(e)}, status=403)
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=400)


@method_decorator(csrf_exempt, name='dispatch')
class CultivarCloneAPI(View):
    """POST /api/cultivars/<crop_code>/<cultivar_id>/clone/"""

    def post(self, request, crop_code, cultivar_id):
        try:
            data = json.loads(request.body)
            c = cultivar_service.clone_cultivar(
                source_id=cultivar_id,
                new_code=data['cultivar_code'],
                new_name=data.get('cultivar_name', ''),
                clone_ecotype=data.get('clone_ecotype', False),
            )
            return JsonResponse({'success': True, 'id': str(c.id)})
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=400)


@method_decorator(csrf_exempt, name='dispatch')
class CultivarValidateAPI(View):
    """POST /api/cultivars/<crop_code>/<cultivar_id>/validate/"""

    def post(self, request, crop_code, cultivar_id):
        try:
            result = cultivar_service.validate_cultivar_with_dssat(cultivar_id)
            return JsonResponse({'success': True, **result})
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=400)


@method_decorator(csrf_exempt, name='dispatch')
class EcotypeListAPI(View):
    """GET /api/ecotypes/<crop_code>/?dssat_model=..."""

    def get(self, request, crop_code):
        dssat_model = request.GET.get('dssat_model', None)
        from dssat_agent.models import DSSATEcotype
        qs = DSSATEcotype.objects.filter(crop_code=crop_code.upper())
        if dssat_model:
            qs = qs.filter(dssat_model=dssat_model.upper())
        qs = qs.order_by('source', 'ecotype_code')
        ecotypes = [
            {
                'id': str(e.id),
                'ecotype_code': e.ecotype_code,
                'ecotype_name': e.ecotype_name,
                'dssat_model': e.dssat_model,
                'source': e.source,
                'params': e.params,
            }
            for e in qs
        ]
        return JsonResponse({'success': True, 'ecotypes': ecotypes})


@method_decorator(csrf_exempt, name='dispatch')
class EcotypeCreateAPI(View):
    """POST /api/ecotypes/<crop_code>/create/"""

    def post(self, request, crop_code):
        try:
            data = json.loads(request.body)
            e = cultivar_service.create_ecotype(
                crop_code=crop_code,
                dssat_model=data['dssat_model'],
                ecotype_code=data['ecotype_code'],
                ecotype_name=data.get('ecotype_name', ''),
                params=data.get('params', {}),
                source=data.get('source', 'custom'),
            )
            return JsonResponse({'success': True, 'id': str(e.id)})
        except ValueError as e:
            return JsonResponse({'error': str(e)}, status=400)
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=400)


@method_decorator(csrf_exempt, name='dispatch')
class EcotypeDetailAPI(View):
    """GET/PUT /api/ecotypes/<crop_code>/<ecotype_id>/"""

    def get(self, request, crop_code, ecotype_id):
        from dssat_agent.models import DSSATEcotype
        try:
            e = DSSATEcotype.objects.get(id=ecotype_id)
            return JsonResponse({
                'success': True,
                'id': str(e.id),
                'ecotype_code': e.ecotype_code,
                'ecotype_name': e.ecotype_name,
                'crop_code': e.crop_code,
                'dssat_model': e.dssat_model,
                'source': e.source,
                'params': e.params,
            })
        except DSSATEcotype.DoesNotExist:
            return JsonResponse({'error': 'Ecotype not found'}, status=404)

    def put(self, request, crop_code, ecotype_id):
        try:
            data = json.loads(request.body)
            e = cultivar_service.update_ecotype(
                ecotype_id,
                ecotype_code=data.get('ecotype_code'),
                ecotype_name=data.get('ecotype_name'),
                params=data.get('params'),
            )
            return JsonResponse({'success': True, 'id': str(e.id)})
        except ValueError as e:
            return JsonResponse({'error': str(e)}, status=403)
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=400)


@method_decorator(csrf_exempt, name='dispatch')
class WeatherAvailabilityAPI(View):
    """REST API for weather data availability check."""

    def post(self, request):
        try:
            data = json.loads(request.body)
            result = data_services.check_availability(
                source=data.get('source', ''),
                start_date=data.get('start_date'),
                end_date=data.get('end_date'),
            )
            return JsonResponse({'success': True, 'result': result})
        except (Exception, json.JSONDecodeError) as e:
            return JsonResponse({'error': str(e)}, status=400)


# =============================================================================
# Experiment Wizard APIs
# =============================================================================

@method_decorator(csrf_exempt, name='dispatch')
class AdminUnitsAPI(View):
    """REST API for listing admin boundary units (proxied from SimulationAgent)."""

    def get(self, request):
        try:
            result = list_admin_units(
                level=request.GET.get('level', 'admin1'),
                parent=request.GET.get('parent'),
            )
            return JsonResponse({'success': True, **result})
        except Exception as e:
            logger.exception("AdminUnitsAPI error")
            return JsonResponse({'error': str(e)}, status=500)


# Per-event-type field labels, ordered for readable output. Keys are the
# DSSAT abbreviations the wizard emits (see event_table.js `EVENT_SCHEMAS`).
_EVENT_FIELD_LABELS = {
    'fertilizer': [
        ('fdate', 'date'),
        ('famn',  'N kg/ha'),
        ('fmcd',  'material'),
        ('facd',  'method'),
        ('fdep',  'depth cm'),
    ],
    'irrigation': [
        ('idate', 'date'),
        ('irval', 'amount mm'),
        ('irop',  'method'),
    ],
    'tillage': [
        ('tdate', 'date'),
        ('timpl', 'implement'),
        ('tdep',  'depth cm'),
    ],
    'chemical': [
        ('cdate', 'date'),
        ('chcod', 'material'),
        ('chme',  'method'),
        ('chamt', 'amount'),
    ],
    'residue': [
        ('rdate', 'date'),
        ('rcod',  'material'),
        ('ramt',  'amount kg/ha'),
        ('rinp',  'incorporation %'),
    ],
}


def _format_event(event_type, ev):
    """Render one event dict as a single bullet body.

    Date (if present) leads; remaining fields follow as ``label value`` pairs.
    Missing fields are silently skipped so we don't emit noise.
    """
    if not isinstance(ev, dict):
        return str(ev)
    schema = _EVENT_FIELD_LABELS.get(event_type, [])
    date_key = next((k for k, _ in schema if k.endswith('date')), None)
    date_val = ev.get(date_key) if date_key else None
    pieces = []
    for key, label in schema:
        if key == date_key:
            continue
        v = ev.get(key)
        if v is None or v == '':
            continue
        pieces.append(f"{label} {v}")
    body = ", ".join(pieces) if pieces else "(no fields set)"
    return f"{date_val} — {body}" if date_val else body


def _format_event_block(event_type, events):
    """Render a list of events as a labeled block with one bullet per event."""
    lines = ["", f"{event_type.capitalize()}:"]
    for ev in events:
        lines.append(f"- {_format_event(event_type, ev)}")
    return lines


def _format_irrigation(irr):
    """Irrigation is either {method: automatic, threshold, efficiency} or
    {method: fixed, events: [...]} (or a bare list of events)."""
    if isinstance(irr, list):
        return _format_event_block('irrigation', irr)
    if not isinstance(irr, dict):
        return []
    method = irr.get('method')
    if method == 'automatic':
        return [
            "",
            f"Irrigation: automatic "
            f"(threshold {irr.get('threshold', 50)}%, "
            f"efficiency {irr.get('efficiency', 90)}%)",
        ]
    if method == 'fixed':
        events = irr.get('events') or []
        if not events:
            return ["", "Irrigation: fixed (no events configured)"]
        return (
            ["", "Irrigation (fixed):"]
            + [f"- {_format_event('irrigation', ev)}" for ev in events]
        )
    if method:
        return ["", f"Irrigation: {method}"]
    return []


def _format_harvest(h):
    """Harvest is a small dict — show key fields inline."""
    if not isinstance(h, dict) or not h:
        return []
    bits = []
    for key in ('method', 'date', 'stage', 'component', 'size'):
        v = h.get(key)
        if v is not None and v != '':
            bits.append(f"{key} {v}")
    body = ", ".join(bits) if bits else "configured"
    return ["", f"Harvest: {body}"]


def _format_wizard_submission_message(params, extracted_params):
    """
    Build a structured, easy-to-parse user message that summarizes the
    wizard-submitted experiment parameters.

    The chat agent already has the full wizard payload on the Chat object
    (``agent_state['wizard_params']`` and ``agent_memory.wizard_params``),
    so this message is purely for the LLM's convenience: each parameter
    sits on its own line under a section heading, which is easier for it
    to scan than the compressed one-liner we used previously.
    """
    def _nz(v):
        return v is not None and v != '' and v != []

    exp_type = extracted_params.get('experiment_type') or 'single'
    lines = [
        "Run the experiment I just configured in the wizard.",
        "",
        "The full configuration is attached as wizard_params; a human-readable "
        "summary follows so you can choose the right tool and double-check "
        "anything important.",
        "",
        f"Experiment type: {exp_type}",
    ]

    # ---- Crop ----
    crop_lines = []
    if _nz(extracted_params.get('crop_code')):
        crop_lines.append(f"- Code: {extracted_params['crop_code']}")
    if _nz(extracted_params.get('crop_name')):
        crop_lines.append(f"- Name: {extracted_params['crop_name']}")
    if _nz(extracted_params.get('cultivar_code')):
        crop_lines.append(f"- Cultivar: {extracted_params['cultivar_code']}")
    if crop_lines:
        lines.extend(["", "Crop:", *crop_lines])

    # ---- Location ----
    loc_lines = []
    if _nz(extracted_params.get('latitude')):
        loc_lines.append(f"- Latitude: {extracted_params['latitude']}")
    if _nz(extracted_params.get('longitude')):
        loc_lines.append(f"- Longitude: {extracted_params['longitude']}")
    if _nz(extracted_params.get('elevation')):
        loc_lines.append(f"- Elevation: {extracted_params['elevation']} m")
    if _nz(extracted_params.get('location_name')):
        loc_lines.append(f"- Name: {extracted_params['location_name']}")
    if loc_lines:
        lines.extend(["", "Location:", *loc_lines])

    # ---- Planting ----
    plant_lines = []
    if _nz(extracted_params.get('planting_date')):
        plant_lines.append(f"- Date: {extracted_params['planting_date']}")
    if _nz(extracted_params.get('plant_population')):
        plant_lines.append(f"- Population: {extracted_params['plant_population']} plants/m²")
    if _nz(extracted_params.get('row_spacing')):
        plant_lines.append(f"- Row spacing: {extracted_params['row_spacing']} cm")
    src = params.get('base', params)
    if _nz(src.get('planting_method')):
        plant_lines.append(f"- Method: {src['planting_method']}")
    if plant_lines:
        lines.extend(["", "Planting:", *plant_lines])

    # ---- Soil ----
    soil_lines = []
    if _nz(extracted_params.get('soil_id')):
        soil_lines.append(f"- Profile ID: {extracted_params['soil_id']}")
    if _nz(extracted_params.get('inline_soil')):
        soil_lines.append("- Inline soil: provided (see wizard_params.inline_soil)")
    if soil_lines:
        lines.extend(["", "Soil:", *soil_lines])

    # ---- Weather ----
    if _nz(extracted_params.get('weather_source')):
        lines.extend([
            "",
            "Weather:",
            f"- Source: {extracted_params['weather_source']}",
        ])

    # ---- Simulation controls ----
    sc = src.get('simulation_controls') or {}
    sc_lines = []
    if _nz(sc.get('start_date')):
        sc_lines.append(f"- Start date: {sc['start_date']}")
    if _nz(sc.get('num_years')):
        sc_lines.append(f"- Number of years: {sc['num_years']}")
    if _nz(sc.get('num_reps')):
        sc_lines.append(f"- Number of replicates: {sc['num_reps']}")
    flags = []
    if 'water' in sc: flags.append(f"water={sc['water']}")
    if 'nitrogen' in sc: flags.append(f"nitrogen={sc['nitrogen']}")
    if 'co2' in sc: flags.append(f"co2={sc['co2']}")
    if flags:
        sc_lines.append(f"- Options: {', '.join(flags)}")
    if sc_lines:
        lines.extend(["", "Simulation controls:", *sc_lines])

    # ---- Management (top-level for single/sensitivity; per-treatment otherwise) ----
    mgmt_lines = []
    fert = extracted_params.get('fertilizer')
    if isinstance(fert, list) and fert:
        mgmt_lines.extend(_format_event_block('fertilizer', fert))
    irr = extracted_params.get('irrigation')
    mgmt_lines.extend(_format_irrigation(irr))
    for key in ('tillage', 'chemical', 'residue'):
        val = src.get(key)
        if isinstance(val, list) and val:
            mgmt_lines.extend(_format_event_block(key, val))
    mgmt_lines.extend(_format_harvest(extracted_params.get('harvest')))
    if _nz(extracted_params.get('initial_conditions')):
        mgmt_lines.extend(["", "Initial conditions: configured (see wizard_params.initial_conditions)"])
    if mgmt_lines:
        lines.extend(["", "Management:", *mgmt_lines])

    # ---- Monte Carlo ----
    if exp_type == 'monte_carlo':
        mc_lines = []
        spatial_mode = params.get('spatial_mode') or 'circle'
        mc_lines.append(f"- Spatial mode: {spatial_mode}")
        if spatial_mode == 'circle':
            center = params.get('center') or {}
            if _nz(center.get('lat')) and _nz(center.get('lon')):
                mc_lines.append(f"- Center: {center['lat']}, {center['lon']}")
            if _nz(params.get('radius_km')):
                mc_lines.append(f"- Radius: {params['radius_km']} km")
        elif spatial_mode == 'bbox':
            bbox = params.get('bbox') or {}
            if all(_nz(bbox.get(k)) for k in ('min_lat', 'max_lat', 'min_lon', 'max_lon')):
                mc_lines.append(
                    f"- Bounds: {bbox['min_lat']}–{bbox['max_lat']} N, "
                    f"{bbox['min_lon']}–{bbox['max_lon']} E"
                )
        elif spatial_mode == 'admin':
            if _nz(params.get('admin_level')):
                mc_lines.append(f"- Admin level: {params['admin_level']}")
            if _nz(params.get('admin_name')):
                mc_lines.append(f"- Admin unit: {params['admin_name']}")
        if _nz(params.get('grid_spacing')):
            mc_lines.append(f"- Grid spacing: {params['grid_spacing']}°")
        if _nz(params.get('nens')):
            mc_lines.append(f"- Ensemble members (nens): {params['nens']}")
        if _nz(params.get('sampling_strategy')):
            mc_lines.append(f"- Sampling strategy: {params['sampling_strategy']}")
        if mc_lines:
            lines.extend(["", "Monte Carlo:", *mc_lines])

    # ---- Ensemble / sensitivity treatments ----
    treatments = params.get('treatments') or []
    if treatments:
        lines.extend(["", f"Treatments ({len(treatments)}):"])
        for i, t in enumerate(treatments, start=1):
            name = t.get('name') or f"T{i}"
            lines.extend(["", f"{name}:"])
            planting_bits = []
            if _nz(t.get('planting_date')):    planting_bits.append(f"date {t['planting_date']}")
            if _nz(t.get('plant_population')): planting_bits.append(f"population {t['plant_population']}")
            if _nz(t.get('row_spacing')):      planting_bits.append(f"row spacing {t['row_spacing']}")
            if _nz(t.get('planting_method')):  planting_bits.append(f"method {t['planting_method']}")
            if planting_bits:
                lines.append(f"- Planting: {', '.join(planting_bits)}")
            fert = t.get('fertilizer')
            if isinstance(fert, list) and fert:
                lines.append("- Fertilizer:")
                for ev in fert:
                    lines.append(f"  - {_format_event('fertilizer', ev)}")
            irr = t.get('irrigation')
            if isinstance(irr, dict) and irr.get('method'):
                method = irr['method']
                if method == 'automatic':
                    lines.append(
                        f"- Irrigation: automatic "
                        f"(threshold {irr.get('threshold', 50)}%, "
                        f"efficiency {irr.get('efficiency', 90)}%)"
                    )
                elif method == 'fixed' and irr.get('events'):
                    lines.append("- Irrigation (fixed):")
                    for ev in irr['events']:
                        lines.append(f"  - {_format_event('irrigation', ev)}")
                else:
                    lines.append(f"- Irrigation: {method}")
            elif isinstance(irr, list) and irr:
                lines.append("- Irrigation:")
                for ev in irr:
                    lines.append(f"  - {_format_event('irrigation', ev)}")
            for key in ('tillage', 'chemical', 'residue'):
                evs = t.get(key)
                if isinstance(evs, list) and evs:
                    lines.append(f"- {key.capitalize()}:")
                    for ev in evs:
                        lines.append(f"  - {_format_event(key, ev)}")
            h = t.get('harvest')
            if isinstance(h, dict) and h:
                bits = [f"{k} {v}" for k, v in h.items() if v not in (None, '')]
                lines.append(f"- Harvest: {', '.join(bits) if bits else 'configured'}")
            # Empty treatment (inherits base only)
            if (
                not planting_bits
                and not (isinstance(fert, list) and fert)
                and not (isinstance(irr, dict) and irr.get('method'))
                and not (isinstance(irr, list) and irr)
                and not any(isinstance(t.get(k), list) and t.get(k) for k in ('tillage', 'chemical', 'residue'))
                and not (isinstance(h, dict) and h)
            ):
                lines.append("- (inherits base)")

    # ---- Sensitivity sweep config ----
    sens = params.get('sensitivity') or {}
    if exp_type == 'sensitivity' or sens:
        s_lines = []
        if _nz(sens.get('parameter')):
            s_lines.append(f"- Parameter: {sens['parameter']}")
        if _nz(sens.get('min')):
            s_lines.append(f"- Min: {sens['min']}")
        if _nz(sens.get('max')):
            s_lines.append(f"- Max: {sens['max']}")
        if _nz(sens.get('steps')):
            s_lines.append(f"- Steps: {sens['steps']}")
        if s_lines:
            lines.extend(["", "Sensitivity analysis:", *s_lines])

    # ---- Site reference data (Step 3 selections) ----
    # For non-MC experiments the user has already clicked Apply, so the
    # concrete soil_id / planting_date land in the Soil: and Planting:
    # sections above. The source-name reference is redundant for those.
    # For MC there is no single applied value — the backend resolves per
    # grid point at run time using these source selections, so surface them.
    if exp_type == 'monte_carlo':
        in_situ = params.get('in_situ_selections') or {}
        ref_lines = []
        if _nz(in_situ.get('soil')):
            ref_lines.append(f"- Soil source: {in_situ['soil']}")
        if _nz(in_situ.get('planting_date')):
            ref_lines.append(f"- Planting date source: {in_situ['planting_date']}")
        if ref_lines:
            lines.extend([
                "",
                "Site reference data (resolved per grid point at run time):",
                *ref_lines,
            ])

    # Collapse any consecutive blank lines (helpers and sections both emit
    # their own leading blank, which doubles up when sub-blocks follow a
    # section header).
    compact = []
    for line in lines:
        if line == "" and compact and compact[-1] == "":
            continue
        compact.append(line)
    return "\n".join(compact)


@method_decorator(csrf_exempt, name='dispatch')
class ExperimentSubmitAPI(View):
    """
    Legacy wizard submission endpoint.

    Receives the flat / ensemble / MC / batch wizard payload as JSON, builds
    the legacy ``extracted_params`` summary, and dispatches via the shared
    ``draft_submit._create_chat_and_queue`` helper. The new draft-backed
    SPA writes directly to ``WizardDraftSubmitAPI``; this endpoint is kept
    for backward compatibility with any client (or saved curl) still
    POSTing the old shape.
    """

    def post(self, request):
        try:
            params = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({'error': 'Invalid JSON'}, status=400)

        # Detect new ensemble format: { experiment_type: 'ensemble', base: {...}, treatments: [...] }
        is_new_ensemble = 'base' in params and params.get('experiment_type') == 'ensemble'
        is_monte_carlo = params.get('experiment_type') == 'monte_carlo'
        src = params.get('base', params) if is_new_ensemble else params

        # For ensemble with treatments, use first treatment's planting date for display
        planting_date = src.get('planting_date')
        if is_new_ensemble and not planting_date and params.get('treatments'):
            planting_date = params['treatments'][0].get('planting_date')

        # For MC: use representative location from spatial config
        if is_monte_carlo:
            center = params.get('center', {})
            if center and not src.get('latitude'):
                src['latitude'] = center.get('lat')
                src['longitude'] = center.get('lon')

        # Build params dict from wizard submission
        extracted_params = {
            'crop_code': src.get('crop_code'),
            'crop_name': src.get('crop_name'),
            'cultivar_code': src.get('cultivar_code'),
            'latitude': src.get('latitude'),
            'longitude': src.get('longitude'),
            'elevation': src.get('elevation'),
            'planting_date': planting_date,
            'plant_population': src.get('plant_population'),
            'row_spacing': src.get('row_spacing'),
            'soil_id': src.get('soil_id'),
            'inline_soil': src.get('inline_soil'),
            'weather_source': src.get('weather_source'),
            'experiment_type': params.get('experiment_type'),
            'fertilizer': src.get('fertilizer') or None,
            'irrigation': src.get('irrigation') or None,
            'harvest': src.get('harvest') or None,
            'initial_conditions': src.get('initial_conditions') or None,
            'num_years': src.get('simulation_controls', {}).get('num_years', 1),
        }
        # Remove None values
        extracted_params = {k: v for k, v in extracted_params.items() if v is not None}

        # Set location_name from coordinates if missing
        if extracted_params.get('latitude') and not extracted_params.get('location_name'):
            extracted_params['location_name'] = f"{extracted_params['latitude']}, {extracted_params.get('longitude')}"

        # Check required fields
        required = ['crop_code', 'planting_date']
        missing = [f for f in required if not extracted_params.get(f)]
        if not extracted_params.get('location_name') and not extracted_params.get('latitude'):
            missing.append('location')
        is_complete = len(missing) == 0

        if is_complete:
            from dssat_agent.services.draft_submit import _create_chat_and_queue
            try:
                chat_id, redirect_url = _create_chat_and_queue(
                    params=extracted_params,
                    wizard_params=params,
                    user=request.user if getattr(request, 'user', None) else None,
                    draft=None,
                )
            except Exception as e:
                logger.exception("Legacy submit dispatch failed")
                return JsonResponse({'error': str(e)}, status=500)

            return JsonResponse({
                'success': True,
                'chat_id': chat_id,
                'redirect_url': redirect_url,
                'is_complete': True,
            })

        # Incomplete payloads still get a chat with a welcome message
        # explaining what's missing — no Celery dispatch.
        from earthrise_agents_base.agent.chat_agent import ConversationMemory
        memory = ConversationMemory()
        memory.confirmed_params = extracted_params
        memory.wizard_params = params

        try:
            from earthrise_agents_base.agent.discovery import discover_agents
            dssat_label = next(
                (label for label, meta in discover_agents().items()
                 if meta.get('app_label') == 'dssat_agent'),
                None,
            )
        except Exception:
            dssat_label = None

        crop_label = (
            extracted_params.get('crop_name')
            or extracted_params.get('crop_code')
            or 'Experiment')
        chat = Chat.objects.create(
            title=f'Wizard: {crop_label}',
            agent_state={'wizard_params': params},
            agent_memory=memory.to_dict(),
            enabled_agents=[dssat_label] if dssat_label else [],
            agents_locked=bool(dssat_label),
        )
        missing_str = ', '.join(missing)
        welcome = (
            f"I've loaded your experiment configuration from the wizard. "
            f"The following parameters still need to be set: **{missing_str}**. "
            f"Let's configure the rest together!"
        )
        Message.objects.create(
            chat=chat,
            message_type='assistant',
            content=welcome,
            status='completed',
        )
        subpath = os.environ.get('SUBPATH', '')
        base_path = f'/{subpath}' if subpath else ''
        return JsonResponse({
            'success': True,
            'chat_id': str(chat.id),
            'redirect_url': f'{base_path}/chats/{chat.id}/',
            'is_complete': False,
        })


@method_decorator(csrf_exempt, name='dispatch')
class ExperimentValidateAPI(View):
    """Validate an experiment configuration without running it."""

    def post(self, request):
        try:
            data = json.loads(request.body)
            result = validate_experiment_only(data)
            return JsonResponse({'success': True, **result})
        except (Exception, json.JSONDecodeError) as e:
            return JsonResponse({'error': str(e)}, status=400)


# Map (category, context) -> CropModel field name.
# `context` disambiguates `facd` / application_method, which is used by both
# fertilizer and chemical events. Other categories are unambiguous.
_CURATED_FIELD_MAP = {
    # Unambiguous mappings (context=None)
    ('plme', None): 'planting_methods',
    ('planting', None): 'planting_methods',
    ('planting_method', None): 'planting_methods',
    ('fmcd', None): 'fertilizer_materials',
    ('fertilizer', None): 'fertilizer_materials',
    ('irop', None): 'irrigation_methods',
    ('irrigation', None): 'irrigation_methods',
    ('irrigation_method', None): 'irrigation_methods',
    ('chcod', None): 'chemical_materials',
    ('chemical', None): 'chemical_materials',
    ('rcod', None): 'residue_materials',
    ('residue', None): 'residue_materials',
    ('hcom', None): 'harvest_components',
    ('harvest_component', None): 'harvest_components',
    # Application method — context-dependent
    ('facd', 'fertilizer'): 'fertilizer_applications',
    ('application', 'fertilizer'): 'fertilizer_applications',
    ('application_method', 'fertilizer'): 'fertilizer_applications',
    ('facd', 'chemical'): 'chemical_applications',
    ('application', 'chemical'): 'chemical_applications',
    ('application_method', 'chemical'): 'chemical_applications',
    # Default context for facd is fertilizer
    ('facd', None): 'fertilizer_applications',
    ('application', None): 'fertilizer_applications',
    ('application_method', None): 'fertilizer_applications',
}


def _resolve_curated_field(category, context):
    """Find the CropModel field name for a category/context, or None."""
    return _CURATED_FIELD_MAP.get((category, context))


@method_decorator(csrf_exempt, name='dispatch')
class CodesAPI(View):
    """List valid DSSAT codes for a category.

    Query params:
        crop: optional crop code — if provided AND a crop-wide CropModel row
              exists for this crop AND the category maps to a curated field,
              returns only the curated subset.
        context: optional disambiguator for categories like `application_method`
                 that apply to both fertilizer and chemical events. Accepted
                 values: 'fertilizer', 'chemical'. Ignored for other categories.
    """

    def get(self, request, category):
        try:
            all_codes = get_codes_with_descriptions(category)

            crop = request.GET.get('crop')
            context = request.GET.get('context') or None
            curated_codes = self._get_curated_codes(crop, category, context)

            if curated_codes is not None:
                # Filter full list to just curated codes (preserves ordering +
                # descriptions from the master DSSAT code source)
                curated_set = set(curated_codes)
                filtered = [c for c in all_codes if c['code'] in curated_set]
                return JsonResponse({
                    'success': True,
                    'category': category,
                    'crop': crop,
                    'curated': True,
                    'codes': filtered,
                })

            return JsonResponse({
                'success': True,
                'category': category,
                'curated': False,
                'codes': all_codes,
            })
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=400)

    @staticmethod
    def _get_curated_codes(crop, category, context):
        """Return curated code list for this crop + category, or None if no curation."""
        if not crop:
            return None
        field_name = _resolve_curated_field(category, context)
        if not field_name:
            return None
        from dssat_agent.models import CropModel
        row = CropModel.objects.filter(
            crop_code=crop.upper(), dssat_model=''
        ).first()
        if row is None:
            return None
        codes = getattr(row, field_name, None)
        if not codes:
            # Empty array means "no curation for this category" — show full list
            return None
        return codes


@method_decorator(csrf_exempt, name='dispatch')
class WeatherDatesAPI(View):
    """Calculate weather date range needed for a crop simulation."""

    def post(self, request):
        try:
            data = json.loads(request.body)
            crop_code = data.get('crop_code')
            planting_date = data.get('planting_date')
            if not crop_code or not planting_date:
                return JsonResponse({'error': 'crop_code and planting_date are required'}, status=400)
            result = crop_service.calculate_weather_dates(
                crop_code=crop_code,
                planting_date=planting_date,
                num_years=data.get('num_years', 1),
            )
            return JsonResponse({'success': True, **result})
        except (Exception, json.JSONDecodeError) as e:
            return JsonResponse({'error': str(e)}, status=400)


@method_decorator(csrf_exempt, name='dispatch')
class ExperimentEnsembleAPI(View):
    """Run an ensemble simulation for validation preview."""

    def post(self, request):
        try:
            data = json.loads(request.body)
            result = run_ensemble(data)
            return JsonResponse({'success': True, **result})
        except (Exception, json.JSONDecodeError) as e:
            return JsonResponse({'error': str(e)}, status=400)


# ---------------------------------------------------------------------------
# In-situ reference data lookup endpoints
# ---------------------------------------------------------------------------

@method_decorator(csrf_exempt, name='dispatch')
class InSituSourcesAPI(View):
    """
    GET /api/insitu/sources/?lat=<float>&lon=<float>

    Returns the available in-situ data sources at a coordinate, grouped by
    parameter. Each source includes a coverage flag so the wizard can grey
    out sources that don't cover the location.
    """

    def get(self, request):
        try:
            lat = float(request.GET['lat'])
            lon = float(request.GET['lon'])
        except (KeyError, ValueError, TypeError):
            return JsonResponse(
                {'error': 'lat and lon query params are required (floats)'},
                status=400,
            )
        try:
            from dssat_agent.services.insitu_lookup import (
                list_available_in_situ_sources,
            )
            sources = list_available_in_situ_sources(lat, lon)
            return JsonResponse({'success': True, 'sources': sources})
        except Exception as e:
            logger.exception("InSituSourcesAPI error")
            return JsonResponse({'error': str(e)}, status=500)


@method_decorator(csrf_exempt, name='dispatch')
class InSituPreviewAPI(View):
    """
    POST /api/insitu/preview/

    Body:
        {
          "lat": float, "lon": float,
          "parameter": "soil" | "planting_date",
          "source": "<source_id>",
          "year": int (optional, defaults to current year)
        }

    Returns the values that the source would fill, in the unified shape
    documented on the underlying lookup functions. Errors are returned as
    data (``{'error': '...'}``) so the wizard can degrade gracefully.
    """

    def post(self, request):
        try:
            data = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({'error': 'Invalid JSON'}, status=400)

        try:
            lat = float(data['lat'])
            lon = float(data['lon'])
            parameter = str(data['parameter'])
            source = str(data['source'])
        except (KeyError, ValueError, TypeError):
            return JsonResponse(
                {'error': 'lat, lon, parameter, and source are required'},
                status=400,
            )

        from datetime import date as _date
        year = int(data.get('year') or _date.today().year)

        try:
            from dssat_agent.services.insitu_lookup import (
                lookup_soil,
                lookup_planting_date,
            )
        except Exception as e:
            logger.exception("InSituPreviewAPI import error")
            return JsonResponse({'error': str(e)}, status=500)

        try:
            if parameter == 'soil':
                result = lookup_soil(lat, lon, source=source)
            elif parameter == 'planting_date':
                result = lookup_planting_date(lat, lon, year, source=source)
            else:
                return JsonResponse(
                    {'error': f'Unknown parameter: {parameter}'},
                    status=400,
                )
        except Exception as e:
            logger.exception("InSituPreviewAPI lookup error")
            return JsonResponse({'error': str(e)}, status=500)

        return JsonResponse(result, safe=False)


@method_decorator(csrf_exempt, name='dispatch')
class InSituCoverageAPI(View):
    """
    POST /api/insitu/coverage/

    Body (one of two shapes):

      ## A) MC-driven (existing — Step 4 MC planting date):
        {
          "monte_carlo": { spatial_mode, center, radius_km, grid_spacing,
                           bbox, admin_name, admin_level },
          "parameter": "soil" | "planting_date" | "elevation",
          "source": "<source_id>",
          "year": int (optional)
        }

      ## B) Explicit points (Step 3 Auto soil/elevation):
        {
          "points": [[lat, lon], ...],
          "parameter": "soil" | "planting_date" | "elevation",
          "source": "<source_id>"
        }

    Either generates the MC grid using monte_carlo_service._generate_grid
    or uses the supplied ``points`` directly, then checks per-point
    coverage for the selected source. Reused by:

      * Step 3 Auto-soil / Auto-elevation Coverage buttons (per-field
        explicit points)
      * Step 4 MC In-situ planting Coverage button (MC grid)
    """

    COVERAGE_SAMPLE_CAP = 200
    UNCOVERED_REPORT_CAP = 50

    def post(self, request):
        try:
            payload = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({'error': 'Invalid JSON'}, status=400)

        parameter = str(payload.get('parameter') or '')
        source = str(payload.get('source') or '')

        if parameter not in ('soil', 'planting_date', 'elevation'):
            return JsonResponse(
                {'error': f"Invalid parameter: {parameter!r}"},
                status=400,
            )
        if not source:
            return JsonResponse({'error': 'source is required'}, status=400)

        # Path B: explicit points list — used by Step 3 per-field checks.
        points_raw = payload.get('points')
        grid = None  # list of (lon, lat) tuples
        if points_raw is not None:
            if not isinstance(points_raw, list):
                return JsonResponse(
                    {'error': 'points must be a list of [lat, lon] pairs'},
                    status=400,
                )
            grid = []
            for p in points_raw:
                try:
                    lat, lon = float(p[0]), float(p[1])
                except (TypeError, ValueError, IndexError):
                    continue
                grid.append((lon, lat))
        else:
            # Path A: MC-driven grid generation.
            mc = payload.get('monte_carlo') or {}
            spatial_mode = mc.get('spatial_mode')
            if spatial_mode not in ('circle', 'bbox', 'admin'):
                return JsonResponse(
                    {'error': f"Invalid spatial_mode: {spatial_mode!r}"},
                    status=400,
                )
            try:
                from dssat_agent.services.monte_carlo_service import (
                    _generate_grid,
                )
            except Exception as e:
                logger.exception("InSituCoverageAPI import error")
                return JsonResponse({'error': str(e)}, status=500)
            try:
                grid = _generate_grid(mc, spatial_mode, weather_source=None)
            except Exception as e:
                logger.exception("InSituCoverageAPI grid generation error")
                return JsonResponse(
                    {'error': f'Grid generation failed: {e}'}, status=400)

        if not grid:
            return JsonResponse({
                'success': True,
                'total_points': 0,
                'covered': 0,
                'coverage_ratio': 0.0,
                'uncovered': [],
                'sampled': False,
            })

        sampled = False
        if len(grid) > self.COVERAGE_SAMPLE_CAP:
            from dssat_agent.services.monte_carlo_service import _sample_points
            grid = _sample_points(grid, self.COVERAGE_SAMPLE_CAP, 'systematic')
            sampled = True

        try:
            is_covered = self._build_coverage_probe(source, parameter)
        except Exception as e:
            logger.exception("InSituCoverageAPI probe setup error")
            return JsonResponse({'error': str(e)}, status=500)

        covered = 0
        uncovered = []
        for (lon, lat) in grid:
            try:
                ok = is_covered(lat, lon)
            except Exception:
                logger.exception(
                    "InSituCoverageAPI probe failed at lat=%s lon=%s source=%s",
                    lat, lon, source,
                )
                ok = False
            if ok:
                covered += 1
            elif len(uncovered) < self.UNCOVERED_REPORT_CAP:
                uncovered.append([round(float(lat), 4), round(float(lon), 4)])

        total = len(grid)
        logger.info(
            "InSituCoverage: source=%s parameter=%s total=%d covered=%d sampled=%s",
            source, parameter, total, covered, sampled,
        )
        return JsonResponse({
            'success': True,
            'total_points': total,
            'covered': covered,
            'coverage_ratio': (covered / total) if total else 0.0,
            'uncovered': uncovered,
            'sampled': sampled,
        })

    # ---- Per-source coverage probes ----
    #
    # Rather than relying on registered coverage_extent bboxes (which can be
    # stale or missing), we sample the actual raster / data source at each
    # point. "Covered" means the source returns usable data there.
    def _build_coverage_probe(self, source, parameter):
        from data_agent.services import sample_raster_at_point

        if source == 'dssat_soil_lookup_v1':
            def probe(lat, lon):
                vals = sample_raster_at_point(source, lat, lon, bands=[1])
                return vals.get(1) is not None
            return probe

        if source == 'dssat_planting_date_lookup_v1':
            def probe(lat, lon):
                vals = sample_raster_at_point(source, lat, lon, bands=[1])
                return vals.get(1) is not None
            return probe

        if source == 'srtm_v1' or parameter == 'elevation':
            # SRTM elevation raster — covered iff the sample returns a
            # non-NoData value. Same shape as the soil/planting probes.
            actual = source if source else 'srtm_v1'
            def probe(lat, lon):
                vals = sample_raster_at_point(actual, lat, lon, bands=[1])
                return vals.get(1) is not None
            return probe

        if source == 'soilgrids':
            # Global — treat as covered everywhere on land; a null value would
            # only appear over oceans, which the spatial grid shouldn't hit.
            return lambda lat, lon: True

        if source == 'ssurgo':
            # US bbox; matches list_available_in_situ_sources.
            return lambda lat, lon: (18.0 <= lat <= 72.0 and -180.0 <= lon <= -65.0)

        # Unknown source: nothing to verify; report as uncovered so the user sees it.
        logger.warning("InSituCoverageAPI: unknown source %r (parameter=%s)", source, parameter)
        return lambda lat, lon: False


@method_decorator(csrf_exempt, name='dispatch')
class ResolvePlantingDatesAPI(View):
    """
    POST /api/planting-dates/resolve/

    Body:
        {
          "source": "<source_id>",       # e.g. dssat_planting_date_lookup_v1
          "year":   int,                  # from Step 1
          "points": [[lat, lon], ...]     # one entry per (treatment, field) pair
        }

    Returns:
        {
          "resolved": [
            {"lat": ..., "lon": ..., "doy": int, "date": "YYYY-MM-DD"} | null,
            ...
          ]
        }

    The resolver loops `lookup_planting_date` over each point and packs
    the result. Uncovered points (or any source error) come back as
    ``null`` at that index — the SPA treats that as "this pair couldn't
    be resolved, abort the lock and tell the user which (treatment,
    field) failed". This endpoint is the source of truth for the Step 4
    Auto-mode resolve-at-lock pass; the same DOY values are persisted
    into ``step_state.step4.resolved_planting_dates`` and consumed by
    the run pipeline at submit time.
    """

    POINT_CAP = 200

    def post(self, request):
        try:
            data = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({'error': 'Invalid JSON'}, status=400)

        source = str(data.get('source') or '')
        if not source:
            return JsonResponse({'error': 'source is required'}, status=400)
        try:
            year = int(data.get('year'))
        except (TypeError, ValueError):
            return JsonResponse({'error': 'year is required'}, status=400)
        raw_points = data.get('points')
        if not isinstance(raw_points, list) or not raw_points:
            return JsonResponse(
                {'error': 'points must be a non-empty list of [lat, lon] pairs'},
                status=400,
            )
        if len(raw_points) > self.POINT_CAP:
            return JsonResponse(
                {'error': f'too many points (max {self.POINT_CAP})'},
                status=400,
            )

        try:
            from dssat_agent.services.insitu_lookup import lookup_planting_date
        except Exception as e:
            logger.exception("ResolvePlantingDatesAPI import error")
            return JsonResponse({'error': str(e)}, status=500)

        resolved = []
        for raw in raw_points:
            try:
                lat = float(raw[0]); lon = float(raw[1])
            except (TypeError, ValueError, IndexError):
                resolved.append(None)
                continue
            try:
                r = lookup_planting_date(lat, lon, year, source=source)
            except Exception:
                logger.exception(
                    "ResolvePlantingDatesAPI lookup error at (%s, %s) src=%s",
                    lat, lon, source,
                )
                resolved.append(None)
                continue
            doy = r.get('julian_day')
            date_iso = r.get('planting_date')
            if not r.get('in_coverage') or doy is None or date_iso is None:
                resolved.append(None)
                continue
            resolved.append({
                'lat': round(lat, 4),
                'lon': round(lon, 4),
                'doy': int(doy),
                'date': str(date_iso),
            })
        return JsonResponse({'success': True, 'resolved': resolved})


@method_decorator(csrf_exempt, name='dispatch')
class AdminCentroidAPI(View):
    """
    GET /api/admin-units/centroid/?level=<level>&name=<admin_name>

    Returns {lat, lon} for the centroid of a single admin unit. Used by the
    wizard to populate a representative point for MC admin mode.
    """

    def get(self, request):
        level = request.GET.get('level', 'admin1')
        name = request.GET.get('name') or ''
        if not name:
            return JsonResponse({'error': 'name is required'}, status=400)
        try:
            from dssat_agent.services.spatial_data_service import get_admin_centroid
            c = get_admin_centroid(name, level=level)
            return JsonResponse({'success': True, **c})
        except ValueError as e:
            return JsonResponse({'error': str(e)}, status=404)
        except Exception as e:
            logger.exception("AdminCentroidAPI error")
            return JsonResponse({'error': str(e)}, status=500)


@method_decorator(csrf_exempt, name='dispatch')
class MCPreviewGridAPI(View):
    """
    POST /api/mc/preview-grid/

    Returns the list of (lat, lon) points that would be sampled for a
    given Monte Carlo spatial config. Used by the wizard's Step 2 MC
    panel to render a live preview overlay so the user can see exactly
    what the run will sample before they commit.

    Body (matches `monte_carlo_service._generate_grid`'s param shape):

      {
        "spatial_mode": "circle" | "bbox" | "admin",
        "center": {"lat": ..., "lon": ...},          # circle
        "radius_km": ...,                             # circle
        "bbox": {"min_lat", "max_lat", "min_lon", "max_lon"},
        "admin_name": "...", "admin_level": "admin1",
        "grid_spacing": 0.1,                          # systematic
        "n_points": 30,                               # random
        "sampling_strategy": "systematic" | "random",
      }

    Response: ``{"points": [[lat, lon], [lat, lon], ...], "truncated": bool}``

    Caps the response at 500 points so a careless `grid_spacing=0.001`
    over a continent doesn't ship megabytes; the SPA can still render the
    truncated set as a visual indicator.
    """

    PREVIEW_CAP = 500

    def post(self, request):
        try:
            payload = json.loads(request.body) if request.body else {}
        except json.JSONDecodeError:
            return JsonResponse({'error': 'invalid JSON'}, status=400)
        spatial_mode = payload.get('spatial_mode')
        if spatial_mode not in ('circle', 'bbox', 'admin'):
            return JsonResponse(
                {'error': f'invalid spatial_mode: {spatial_mode!r}'},
                status=400,
            )
        try:
            from dssat_agent.services.monte_carlo_service import _generate_grid
            grid = _generate_grid(payload, spatial_mode, weather_source=None)
        except ValueError as e:
            return JsonResponse({'error': str(e)}, status=400)
        except Exception as e:
            logger.exception("MCPreviewGridAPI failed")
            return JsonResponse({'error': str(e)}, status=500)

        # `_generate_grid` returns (lon, lat) tuples; normalise to
        # (lat, lon) for the SPA so callers don't have to remember.
        points = [[round(float(p[1]), 4), round(float(p[0]), 4)] for p in grid]
        truncated = False
        if len(points) > self.PREVIEW_CAP:
            points = points[:self.PREVIEW_CAP]
            truncated = True
        return JsonResponse({
            'success': True,
            'count': len(points),
            'truncated': truncated,
            'points': points,
        })


@method_decorator(csrf_exempt, name='dispatch')
class ElevationAPI(View):
    """
    GET /api/elevation/?lat=<float>&lon=<float>

    Samples the SRTM static raster at a point and returns
    ``{elevation_m: float | null}``. Returns null (not an error) when the
    raster is not registered or the point falls outside its coverage,
    which lets the SPA fall back to manual entry without a 4xx flash.
    """

    RASTER_NAME = 'srtm_v1'

    def get(self, request):
        try:
            lat = float(request.GET['lat'])
            lon = float(request.GET['lon'])
        except (KeyError, ValueError, TypeError):
            return JsonResponse(
                {'error': 'lat and lon query params are required (floats)'},
                status=400,
            )
        try:
            from data_agent.services import sample_raster_at_point
            vals = sample_raster_at_point(self.RASTER_NAME, lat, lon, bands=[1])
            elev = vals.get(1)
            if elev is None:
                return JsonResponse({'success': True, 'elevation_m': None})
            return JsonResponse({'success': True, 'elevation_m': round(float(elev), 1)})
        except ValueError:
            # Raster not registered (e.g. SRTM tif not yet ingested) — degrade
            # gracefully so the wizard still works.
            return JsonResponse({'success': True, 'elevation_m': None})
        except Exception as e:
            logger.exception("ElevationAPI error")
            return JsonResponse({'error': str(e)}, status=500)
