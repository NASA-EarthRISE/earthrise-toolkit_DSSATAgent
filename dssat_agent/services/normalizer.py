"""
Input normalizer for SimulationAgent.

Translates human-readable parameter names (e.g. 'latitude', 'planting_date',
'population') to DSSAT abbreviations (e.g. 'lat', 'pdate', 'ppop') so
the SimulationAgent accepts both forms.

Applied once at the top of dispatch_skill() -- the transform is idempotent:
if params already use DSSAT keys they pass through unchanged.
"""

import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


# -- Alias maps ----------------------------------------------------------------
# Each map is  human-readable -> DSSAT key.
# DSSAT keys mapping to themselves are omitted (idempotent by design).

FIELD_ALIASES = {
    'latitude': 'lat',
    'longitude': 'lon',
    'elevation': 'elev',
}

PLANTING_ALIASES = {
    'date': 'pdate',
    'planting_date': 'pdate',
    'population': 'ppop',
    'plant_population': 'ppop',
    'row_spacing': 'plrs',
    'planting_method': 'plme',
    'planting_distribution': 'plds',
    'planting_depth': 'pldp',
    'seed_weight': 'plwt',
}

SIM_CONTROL_ALIASES = {
    'start_date': 'sdate',
    'num_years': 'nyers',
    'years': 'nyers',
    'num_reps': 'nreps',
    'reps': 'nreps',
}

FERTILIZER_EVENT_ALIASES = {
    'date': 'fdate',
    'fertilizer_date': 'fdate',
    'amount': 'famn',
    'nitrogen_amount': 'famn',
    'material': 'fmcd',
    'method': 'facd',
    'application_method': 'facd',
    'depth': 'fdep',
}

IRRIGATION_EVENT_ALIASES = {
    'date': 'idate',
    'irrigation_date': 'idate',
    'amount': 'irval',
}

# Fertilizer material name -> DSSAT code lookup (case-insensitive)
MATERIAL_NAME_TO_CODE = {
    'ammonium nitrate': 'FE001',
    'ammonium sulfate': 'FE002',
    'ammonium nitrate sulfate': 'FE003',
    'anhydrous ammonia': 'FE004',
    'urea': 'FE005',
    'diurea': 'FE006',
    'calcium ammonium nitrate': 'FE007',
    'calcium nitrate': 'FE008',
    'aqua ammonia': 'FE009',
    'dap': 'FE010',
    'diammonium phosphate': 'FE010',
    'monoammonium phosphate': 'FE013',
    'map': 'FE013',
    'triple superphosphate': 'FE012',
    'potassium chloride': 'FE014',
    'potassium nitrate': 'FE015',
    'potassium sulfate': 'FE016',
    'urea ammonium nitrate': 'FE017',
}


# -- Normalizer helpers --------------------------------------------------------

def _remap_keys(d: dict, alias_map: dict) -> dict:
    """Return a new dict with aliased keys renamed.  Unknown keys pass through."""
    out = {}
    for k, v in d.items():
        out[alias_map.get(k, k)] = v
    return out


def _normalize_material(value):
    """Convert a human-readable material name to DSSAT fmcd code."""
    if not isinstance(value, str):
        return value
    # Already looks like a DSSAT code
    if value.upper().startswith('FE') and len(value) == 5:
        return value
    code = MATERIAL_NAME_TO_CODE.get(value.lower())
    if code:
        return code
    return value


def _normalize_field(field: dict) -> dict:
    out = _remap_keys(field, FIELD_ALIASES)
    # Auto-generate id_field if missing
    if not out.get('id_field'):
        out['id_field'] = 'SIMU0001'
    return out


def _normalize_planting(planting: dict) -> dict:
    return _remap_keys(planting, PLANTING_ALIASES)


def _normalize_sim_controls(sc: dict) -> dict:
    out = _remap_keys(sc, SIM_CONTROL_ALIASES)
    # Normalize boolean options to 'Y'/'N' strings
    options = out.get('options')
    if isinstance(options, dict):
        for key in ('water', 'nitro', 'symbi', 'phosp', 'potas', 'dises', 'chem', 'till'):
            val = options.get(key)
            if isinstance(val, bool):
                options[key] = 'Y' if val else 'N'
        # Also handle common aliases: 'nitrogen' -> 'nitro'
        if 'nitrogen' in options:
            val = options.pop('nitrogen')
            if 'nitro' not in options:
                options['nitro'] = 'Y' if val is True or val == 'Y' else 'N' if val is False or val == 'N' else val
        out['options'] = options
    return out


def _normalize_fertilizer_event(ev: dict, planting_date_str=None) -> dict:
    """Normalize a single fertilizer event dict."""
    ev = _remap_keys(ev, FERTILIZER_EVENT_ALIASES)

    # Handle 'day' offset -> compute fdate
    if 'day' in ev and 'fdate' not in ev and planting_date_str:
        try:
            pdate = datetime.strptime(str(planting_date_str), '%Y-%m-%d')
            ev['fdate'] = (pdate + timedelta(days=int(ev.pop('day')))).strftime('%Y-%m-%d')
        except (ValueError, TypeError):
            pass

    # Resolve material name -> code
    if 'fmcd' in ev:
        ev['fmcd'] = _normalize_material(ev['fmcd'])

    return ev


def _normalize_fertilizer(events, planting_date_str=None):
    """Normalize a list of fertilizer events."""
    if not events:
        return events
    return [_normalize_fertilizer_event(ev, planting_date_str) for ev in events]


def _normalize_irrigation_event(ev: dict) -> dict:
    return _remap_keys(ev, IRRIGATION_EVENT_ALIASES)


def _normalize_irrigation(irr):
    """Normalize irrigation -- can be a list of events or a dict with events/table."""
    if not irr:
        return irr
    if isinstance(irr, list):
        return [_normalize_irrigation_event(ev) for ev in irr]
    if isinstance(irr, dict):
        out = dict(irr)
        for key in ('events', 'table'):
            if key in out and isinstance(out[key], list):
                out[key] = [_normalize_irrigation_event(ev) for ev in out[key]]
        return out
    return irr


# -- Public API ----------------------------------------------------------------

def normalize_params(skill_name: str, params: dict) -> dict:
    """
    Normalize human-readable parameter keys to DSSAT abbreviations.

    Idempotent -- DSSAT keys pass through unchanged.

    Parameters
    ----------
    skill_name : str
        The skill being invoked (e.g. 'run_simulation').
    params : dict
        Original parameters dict.

    Returns
    -------
    dict
        Parameters with keys normalized.
    """
    if skill_name not in ('run_simulation', 'run_ensemble', 'validate_experiment', 'run_batch'):
        return params

    params = dict(params)  # shallow copy

    # Determine planting date for fertilizer day-offset computation
    planting_date_str = None
    planting = params.get('planting')
    if isinstance(planting, dict):
        planting_date_str = (
            planting.get('pdate')
            or planting.get('date')
            or planting.get('planting_date')
        )

    # Field
    if 'field' in params and isinstance(params['field'], dict):
        params['field'] = _normalize_field(params['field'])

    # Planting
    if 'planting' in params and isinstance(params['planting'], dict):
        params['planting'] = _normalize_planting(params['planting'])

    # Simulation controls
    if 'simulation_controls' in params and isinstance(params['simulation_controls'], dict):
        params['simulation_controls'] = _normalize_sim_controls(params['simulation_controls'])

    # Fertilizer
    if 'fertilizer' in params:
        params['fertilizer'] = _normalize_fertilizer(params['fertilizer'], planting_date_str)

    # Irrigation
    if 'irrigation' in params:
        params['irrigation'] = _normalize_irrigation(params['irrigation'])

    return params
