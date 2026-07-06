"""
Centralized input validation using DSSATTools types and CODE_VARS.
"""

import logging
from datetime import date, datetime

from DSSATTools.base.partypes import CODE_VARS

from .crop_service import CROP_CLASSES, get_crop_class
from DSSATTools.crop_registry import CROP_CODE_MODELS

logger = logging.getLogger(__name__)


def _parse_date(val):
    """Parse a date string or return date object."""
    if isinstance(val, date):
        return val
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, str):
        for fmt in ('%Y-%m-%d', '%Y%m%d', '%d/%m/%Y'):
            try:
                return datetime.strptime(val, fmt).date()
            except ValueError:
                continue
    return None


def validate_crop_code(crop_code, dssat_model=None):
    """Validate a 2-letter crop code and optional model."""
    errors = []
    if not crop_code:
        errors.append("crop_code is required")
        return errors
    code = crop_code.upper()
    if code not in CROP_CLASSES:
        errors.append(f"Unknown crop code: {crop_code}. Valid: {sorted(CROP_CLASSES.keys())}")
    elif dssat_model:
        available = CROP_CODE_MODELS.get(code, [])
        if dssat_model.upper() not in available:
            errors.append(
                f"Unknown model '{dssat_model}' for crop '{code}'. "
                f"Available: {available}"
            )
    return errors


def validate_cultivar_code(crop_code, cultivar_code, dssat_model=None):
    """Validate that a cultivar exists for the given crop/model.

    Checks DB first (handles custom/cloned cultivars), then falls back
    to file-based validation.
    """
    errors = []
    if not cultivar_code:
        errors.append("cultivar_code is required")
        return errors

    # Check DB first — covers custom, cloned, and seeded DSSAT cultivars
    from dssat_agent.models import DSSATCultivar
    db_qs = DSSATCultivar.objects.filter(
        cultivar_code=cultivar_code, crop_code=crop_code.upper())
    if dssat_model:
        db_qs = db_qs.filter(dssat_model=dssat_model.upper())
    if db_qs.exists():
        return errors  # Valid

    # Fallback to file-based validation
    try:
        cls = get_crop_class(crop_code.upper(), dssat_model)
    except KeyError:
        return errors  # crop_code validation handles this

    try:
        available = cls.cultivar_list()
        if cultivar_code not in available:
            errors.append(
                f"Unknown cultivar {cultivar_code} for crop {crop_code}. "
                f"Use list_cultivars to see available options."
            )
    except Exception as e:
        errors.append(f"Could not validate cultivar: {e}")

    return errors


def validate_code_var(param_name, value):
    """Validate a parameter against CODE_VARS."""
    errors = []
    if param_name in CODE_VARS:
        allowed = CODE_VARS[param_name]
        if allowed and value not in allowed and value is not None:
            errors.append(
                f"Invalid value '{value}' for {param_name}. "
                f"Allowed: {allowed}"
            )
    return errors


def validate_soil_layers(layers):
    """Validate soil layer constraints."""
    errors = []
    if not layers:
        errors.append("At least one soil layer is required")
        return errors

    prev_depth = 0
    for i, layer in enumerate(layers):
        prefix = f"Layer {i}"

        slb = layer.get('slb')
        if slb is None or slb <= 0:
            errors.append(f"{prefix}: slb (depth) must be positive")
        elif slb <= prev_depth:
            errors.append(f"{prefix}: slb ({slb}) must be greater than previous layer depth ({prev_depth})")
        else:
            prev_depth = slb

        slll = layer.get('slll')
        sdul = layer.get('sdul')
        ssat = layer.get('ssat')

        if slll is not None and sdul is not None and slll >= sdul:
            errors.append(f"{prefix}: slll ({slll}) must be less than sdul ({sdul})")
        if sdul is not None and ssat is not None and sdul >= ssat:
            errors.append(f"{prefix}: sdul ({sdul}) must be less than ssat ({ssat})")

        for field in ('slll', 'sdul', 'ssat', 'srgf'):
            val = layer.get(field)
            if val is not None and (val < 0 or val > 1):
                errors.append(f"{prefix}: {field} ({val}) must be between 0 and 1")

        sbdm = layer.get('sbdm')
        if sbdm is not None and (sbdm < 0.5 or sbdm > 2.5):
            errors.append(f"{prefix}: sbdm ({sbdm}) should be between 0.5 and 2.5 g/cm3")

        sloc = layer.get('sloc')
        if sloc is not None and sloc < 0:
            errors.append(f"{prefix}: sloc ({sloc}) must be non-negative")

    return errors


def validate_planting(planting, crop_code=None):
    """Validate planting parameters."""
    errors = []
    if not planting:
        errors.append("planting parameters are required")
        return errors

    pdate = _parse_date(planting.get('pdate'))
    if pdate is None:
        errors.append("planting.pdate is required and must be a valid date")

    for field in ('ppop', 'plrs'):
        if planting.get(field) is None:
            errors.append(f"planting.{field} is required")

    plme = planting.get('plme')
    if plme:
        errors.extend(validate_code_var('plme', plme))

    plds = planting.get('plds')
    if plds:
        errors.extend(validate_code_var('plds', plds))

    # Potato-specific
    if crop_code and crop_code.upper() == 'PT':
        if planting.get('plwt') is None:
            errors.append("planting.plwt is required for Potato")

    return errors


def validate_planting_ranges(planting, crop_code, dssat_model=None):
    """Return list of warnings for ppop/plrs/pldp outside CropModel ranges.

    Ranges come from the crop-wide CropModel row (keyed on ``crop_code``),
    falling back to the model-specific row if only that is seeded. Missing
    ranges return no warning — validation is a soft guardrail, not a hard
    constraint. Returns an empty list if no CropModel row or ranges are
    defined for the crop.
    """
    warnings = []
    if not planting or not crop_code:
        return warnings

    try:
        from dssat_agent.services.crop_model_service import get_crop_model
        cm = get_crop_model(crop_code, dssat_model)
    except Exception:
        return warnings
    if cm is None:
        return warnings

    _RANGES = [
        ('ppop', 'plant_population_range', 'plant population'),
        ('plrs', 'row_spacing_range',      'row spacing'),
        ('pldp', 'planting_depth_range',   'planting depth'),
    ]

    for field, cm_attr, label in _RANGES:
        val = planting.get(field)
        rng = getattr(cm, cm_attr) or None
        if val is None or not rng:
            continue
        try:
            v = float(val)
        except (TypeError, ValueError):
            continue
        lo = rng.get('min')
        hi = rng.get('max')
        unit = rng.get('unit', '')
        if lo is not None and v < float(lo):
            warnings.append(
                f"planting.{field}={v} is below the typical minimum "
                f"({lo} {unit}) for {label} of {crop_code.upper()}"
            )
        if hi is not None and v > float(hi):
            warnings.append(
                f"planting.{field}={v} is above the typical maximum "
                f"({hi} {unit}) for {label} of {crop_code.upper()}"
            )

    return warnings


def validate_simulation_controls(sim_controls):
    """Validate simulation controls."""
    errors = []
    if not sim_controls:
        errors.append("simulation_controls are required")
        return errors

    sdate = _parse_date(sim_controls.get('sdate'))
    if sdate is None:
        errors.append("simulation_controls.sdate is required and must be a valid date")

    nyers = sim_controls.get('nyers', 1)
    if not isinstance(nyers, int) or nyers < 1:
        errors.append("simulation_controls.nyers must be a positive integer")

    # Validate options sub-section
    options = sim_controls.get('options', {})
    for key in ('water', 'nitro', 'symbi', 'phosp', 'potas', 'dises', 'chem', 'till'):
        val = options.get(key)
        if val is not None and val not in ('Y', 'N'):
            errors.append(f"simulation_controls.options.{key} must be 'Y' or 'N'")

    # Validate methods sub-section
    methods = sim_controls.get('methods', {})
    if 'evapo' in methods:
        errors.extend(validate_code_var('evapo', methods['evapo']))
    if 'mesom' in methods:
        errors.extend(validate_code_var('mesom', methods['mesom']))

    return errors


def validate_fertilizer_events(events):
    """Validate fertilizer event list.

    Each event must carry either a valid absolute ``fdate`` (inline user
    input) or a non-negative integer ``fdap`` (stored defaults). The
    DAP→absolute resolution happens later in the experiment builder.
    """
    errors = []
    if not events:
        return errors

    for i, ev in enumerate(events):
        prefix = f"fertilizer[{i}]"
        errors.extend(_validate_event_timing(ev, 'fdate', 'fdap', prefix))
        if ev.get('fmcd'):
            errors.extend(validate_code_var('fmcd', ev['fmcd']))
        if ev.get('facd'):
            errors.extend(validate_code_var('facd', ev['facd']))
        if ev.get('famn') is not None and ev['famn'] < 0:
            errors.append(f"{prefix}.famn must be non-negative")

    return errors


def _validate_event_timing(ev, date_key, dap_key, prefix):
    """Ensure a management event has a valid date OR a non-negative int DAP."""
    errors = []
    raw_date = ev.get(date_key)
    raw_dap = ev.get(dap_key)
    if raw_date:
        if _parse_date(raw_date) is None:
            errors.append(f"{prefix}.{date_key} is not a valid date")
        return errors
    if raw_dap is not None:
        try:
            dap_int = int(raw_dap)
        except (TypeError, ValueError):
            errors.append(f"{prefix}.{dap_key} must be an integer")
            return errors
        if dap_int < 0:
            errors.append(f"{prefix}.{dap_key} must be non-negative")
        return errors
    errors.append(
        f"{prefix} must specify either {date_key} (absolute) or "
        f"{dap_key} (days after planting)"
    )
    return errors


def validate_irrigation_events(irrigation):
    """Validate irrigation configuration (automatic config or fixed event list)."""
    errors = []
    if not irrigation:
        return errors

    # Irrigation can be a dict (automatic/fixed config) or a plain list of events
    if isinstance(irrigation, dict):
        # Check for automatic mode (either via 'method' key or 'automatic' flag)
        method = irrigation.get('method')
        is_auto = irrigation.get('automatic', False)
        if method == 'automatic' or is_auto:
            # Automatic irrigation: validate threshold/efficiency
            threshold = irrigation.get('threshold')
            if threshold is not None and (threshold < 0 or threshold > 100):
                errors.append("irrigation.threshold must be between 0 and 100")
            efficiency = irrigation.get('efficiency')
            if efficiency is not None and (efficiency < 0 or efficiency > 100):
                errors.append("irrigation.efficiency must be between 0 and 100")
            return errors
        elif method == 'fixed' or 'events' in irrigation:
            # Fixed irrigation: validate the events list
            events = irrigation.get('events', [])
        else:
            # Dict without recognized keys — skip validation
            return errors
    else:
        # Legacy format: plain list of events
        events = irrigation

    for i, ev in enumerate(events):
        prefix = f"irrigation[{i}]"
        errors.extend(_validate_event_timing(ev, 'idate', 'idap', prefix))
        if ev.get('irval') is not None and ev['irval'] < 0:
            errors.append(f"{prefix}.irval must be non-negative")

    return errors


# -----------------------------------------------------------------------------
# Stored-default validation (CropDefault rows + DSSATConfig fallback_* keys)
#
# Defaults are written without an anchoring planting date, so any event-style
# timing must be expressed as days-after-planting (DAP) — absolute date keys
# are rejected. ``validate_default_management(payload)`` returns a list of
# error strings, empty when the payload is acceptable.
# -----------------------------------------------------------------------------

_DEFAULT_EVENT_DATE_KEYS = {
    'fertilizer': ('fdate', 'fdap'),
    'tillage': ('tdate', 'tdap'),
    'chemical': ('cdate', 'cdap'),
    'residue': ('rdate', 'rdap'),
}

_HARVEST_DEFAULT_OPTIONS = {'', 'auto', 'maturity', 'growth_stage', 'dap'}


def _validate_default_event_list(events, date_key, dap_key, label):
    errors = []
    if events in (None, []):
        return errors
    if not isinstance(events, list):
        errors.append(f"{label} must be a list of events or null")
        return errors
    for i, ev in enumerate(events):
        if not isinstance(ev, dict):
            errors.append(f"{label}[{i}] must be an object")
            continue
        prefix = f"{label}[{i}]"
        if date_key in ev and ev[date_key] not in (None, ''):
            errors.append(
                f"{prefix}: stored defaults must use {dap_key} "
                f"(days after planting), not absolute {date_key}"
            )
            continue
        raw_dap = ev.get(dap_key)
        if raw_dap is None or raw_dap == '':
            errors.append(f"{prefix}.{dap_key} is required (days after planting)")
            continue
        try:
            dap_int = int(raw_dap)
        except (TypeError, ValueError):
            errors.append(f"{prefix}.{dap_key} must be an integer")
            continue
        if dap_int < 0:
            errors.append(f"{prefix}.{dap_key} must be non-negative")
    return errors


def _validate_default_irrigation(value):
    errors = []
    if value in (None, {}):
        return errors
    if not isinstance(value, dict):
        errors.append("irrigation default must be an object or null")
        return errors
    method = value.get('method')
    if method == 'automatic':
        threshold = value.get('threshold')
        if threshold is not None and (threshold < 0 or threshold > 100):
            errors.append("irrigation.threshold must be between 0 and 100")
        efficiency = value.get('efficiency')
        if efficiency is not None and (efficiency < 0 or efficiency > 100):
            errors.append("irrigation.efficiency must be between 0 and 100")
        return errors
    if method == 'fixed':
        events = value.get('events') or []
        errors.extend(
            _validate_default_event_list(events, 'idate', 'idap', 'irrigation.events')
        )
        return errors
    if method in (None, ''):
        return errors
    errors.append(f"irrigation.method must be 'automatic', 'fixed', or null")
    return errors


def _validate_default_harvest(value):
    errors = []
    if value in (None, {}):
        return errors
    if not isinstance(value, dict):
        errors.append("harvest default must be an object or null")
        return errors
    option = value.get('option') or ''
    if option not in _HARVEST_DEFAULT_OPTIONS:
        errors.append(
            f"harvest.option must be one of {sorted(_HARVEST_DEFAULT_OPTIONS - {''})} "
            "(on_date is not allowed in stored defaults)"
        )
        return errors
    if 'date' in value and value['date'] not in (None, ''):
        errors.append(
            "harvest stored defaults cannot carry an absolute date; use "
            "option='dap' with a 'dap' integer instead"
        )
    if option == 'dap':
        raw_dap = value.get('dap')
        if raw_dap is None or raw_dap == '':
            errors.append("harvest.dap is required when option='dap'")
        else:
            try:
                dap_int = int(raw_dap)
            except (TypeError, ValueError):
                errors.append("harvest.dap must be an integer")
            else:
                if dap_int < 0:
                    errors.append("harvest.dap must be non-negative")
    return errors


def validate_default_management(payload):
    """Validate a management-defaults payload (per-crop row or fallbacks dict).

    Accepts a dict whose keys are any subset of fertilizer/irrigation/harvest/
    tillage/chemical/residue. Returns ``list[str]`` of error messages.
    """
    errors = []
    if not isinstance(payload, dict):
        return ["payload must be an object"]
    for key, (date_key, dap_key) in _DEFAULT_EVENT_DATE_KEYS.items():
        if key in payload:
            errors.extend(
                _validate_default_event_list(payload[key], date_key, dap_key, key)
            )
    if 'irrigation' in payload:
        errors.extend(_validate_default_irrigation(payload['irrigation']))
    if 'harvest' in payload:
        errors.extend(_validate_default_harvest(payload['harvest']))
    return errors


def validate_field(field_params):
    """Validate field parameters."""
    errors = []
    if not field_params:
        errors.append("field parameters are required")
        return errors

    if not field_params.get('id_field'):
        errors.append("field.id_field is required")

    return errors


def validate_experiment(params):
    """
    Full validation of experiment parameters.

    Returns (errors: list[str], warnings: list[str])
    """
    errors = []
    warnings = []

    crop_code = params.get('crop_code', '')
    v_dssat_model = params.get('dssat_model', '').upper() or None
    errors.extend(validate_crop_code(crop_code, v_dssat_model))

    cultivar_code = params.get('cultivar_code', '')
    if crop_code and crop_code.upper() in CROP_CLASSES:
        errors.extend(validate_cultivar_code(crop_code, cultivar_code, v_dssat_model))

    errors.extend(validate_field(params.get('field')))
    errors.extend(validate_planting(params.get('planting'), crop_code))
    # Soft-warn on ppop/plrs/pldp outside the crop's typical ranges.
    warnings.extend(validate_planting_ranges(
        params.get('planting'), crop_code, v_dssat_model,
    ))
    errors.extend(validate_simulation_controls(params.get('simulation_controls')))
    errors.extend(validate_fertilizer_events(params.get('fertilizer', [])))
    errors.extend(validate_irrigation_events(params.get('irrigation', [])))

    # Inline soil validation
    if not params.get('soil_id') and not params.get('inline_soil'):
        errors.append("Either soil_id or inline_soil is required")
    elif params.get('inline_soil'):
        inline = params['inline_soil']
        layers = inline.get('layers', inline.get('table', []))
        errors.extend(validate_soil_layers(layers))

    # Weather check
    if not params.get('weather_data'):
        warnings.append("No weather_data provided. Simulation will fail without weather.")

    # Forage crop warnings
    if crop_code and crop_code.upper() in ('AL', 'BM') and not params.get('mow'):
        warnings.append(f"Crop {crop_code} is a perennial forage. Consider providing mow parameters.")

    return errors, warnings
