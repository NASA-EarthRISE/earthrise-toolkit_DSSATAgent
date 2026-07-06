"""
Crop and cultivar reference service.

Uses DSSATTools crop classes to introspect available crops, cultivars,
and their parameters.  Supports all DSSAT crop models via the dynamic
crop registry (hand-written classes take priority over auto-generated).
"""

import logging

from DSSATTools.crop_registry import (
    CROP_MODEL_CLASSES,
    CROP_CODE_MODELS,
    DEFAULT_MODEL,
    ALL_CROP_NAMES,
    get_crop_class,
    get_model_display_name,
)

logger = logging.getLogger(__name__)

# ── Backwards-compatible dict: crop_code -> default class ────────────────
# Consumers that do ``CROP_CLASSES[crop_code]`` continue to work.
CROP_CLASSES = {code: get_crop_class(code) for code in CROP_CODE_MODELS}

# Human-readable names (all crops)
CROP_NAMES = dict(ALL_CROP_NAMES)

# Model family string per crop code (default model)
CROP_MODELS = {}
for _code, _smodel in DEFAULT_MODEL.items():
    _display = get_model_display_name(_smodel)
    CROP_MODELS[_code] = f"{_display}-{ALL_CROP_NAMES.get(_code, _code)}"


# Typical maximum growth durations per crop (days from planting to harvest).
# Used to compute weather date ranges. These are generous upper bounds;
# actual durations depend on cultivar and conditions but DSSAT will stop
# at maturity regardless.
CROP_GROWTH_DURATIONS = {
    "MZ": 180,   # Maize
    "SG": 160,   # Sorghum
    "WH": 270,   # Wheat (winter wheat can be long)
    "RI": 180,   # Rice
    "ML": 140,   # Pearl Millet
    "BS": 210,   # Sugarbeet
    "SW": 120,   # Sweet Corn
    "SB": 170,   # Soybean
    "CN": 200,   # Canola
    "SU": 160,   # Sunflower
    "TM": 180,   # Tomato
    "CB": 150,   # Cabbage
    "BN": 140,   # Dry Bean
    "AL": 365,   # Alfalfa (perennial, use 1 year)
    "BM": 365,   # Bermudagrass (perennial)
    "PT": 160,   # Potato
    "SC": 450,   # Sugarcane (long cycle)
    "CS": 365,   # Cassava (long cycle)
    # New crops — sensible defaults
    "BA": 200,   # Barley
    "BC": 180,   # Carinata
    "BH": 365,   # Bahia Grass (perennial)
    "BR": 365,   # Brachiaria (perennial)
    "CH": 160,   # Chickpea
    "CI": 150,   # Chia
    "CO": 200,   # Cotton
    "CP": 140,   # Cowpea
    "FB": 160,   # Faba Bean
    "G0": 365,   # Bahia (perennial)
    "GB": 120,   # Green Bean
    "GG": 365,   # Guinea Grass (perennial)
    "GY": 140,   # Guar
    "PI": 540,   # Pineapple (long cycle)
    "PN": 170,   # Peanut
    "PP": 200,   # Pigeon Pea
    "PR": 150,   # Bell Pepper
    "QU": 160,   # Quinoa
    "SF": 160,   # Safflower
    "SR": 300,   # Strawberry
    "TF": 140,   # Teff
    "TN": 300,   # Tanier
    "TR": 300,   # Taro
    "VB": 180,   # Velvet Bean
}

# Days before planting to start the simulation (soil initialization buffer)
SIM_PRE_PLANT_BUFFER = 30


# ── Public API ────────────────────────────────────────────────────────────

def _crop_groups_by_code():
    """Return {crop_code: crop_group} from crop-wide CropModel rows.

    One query, cheap, used to decorate list_crops / list_crop_entries
    results with a ``crop_group`` attribute for UI filtering.
    Returns an empty dict if the CropModel table is unavailable (e.g.
    during migrations before 0010 is applied).
    """
    try:
        from dssat_agent.models import CropModel
        rows = CropModel.objects.filter(dssat_model='').values_list(
            'crop_code', 'crop_group',
        )
        return {c: (g or '') for c, g in rows}
    except Exception:
        return {}


def list_crops():
    """Return metadata for all supported crops."""
    groups = _crop_groups_by_code()
    results = []
    for code in sorted(CROP_CODE_MODELS.keys()):
        cls = get_crop_class(code)
        try:
            cultivar_count = len(cls.cultivar_list())
        except Exception:
            cultivar_count = 0
        results.append({
            "code": code,
            "name": CROP_NAMES.get(code, code),
            "model": CROP_MODELS.get(code, ""),
            "default_model": DEFAULT_MODEL.get(code, ""),
            "available_models": CROP_CODE_MODELS.get(code, []),
            "cultivar_count": cultivar_count,
            "crop_group": groups.get(code, ""),
        })
    return results


def list_crop_entries():
    """Return a flat list of (crop_code, model) entries for the cultivar grid.

    Single-model crops produce one entry with just the crop name.
    Multi-model crops produce one entry per model with the model name
    appended in parentheses.
    """
    groups = _crop_groups_by_code()
    entries = []
    for code in sorted(CROP_CODE_MODELS.keys()):
        models = CROP_CODE_MODELS[code]
        crop_name = CROP_NAMES.get(code, code)

        for smodel in models:
            cls = CROP_MODEL_CLASSES[smodel]
            try:
                cultivar_count = len(cls.cultivar_list())
            except Exception:
                cultivar_count = 0

            if len(models) == 1:
                display_name = crop_name
            else:
                model_label = get_model_display_name(smodel)
                display_name = f"{crop_name} ({model_label})"

            entries.append({
                "code": code,
                "name": display_name,
                "dssat_model": smodel,
                "cultivar_count": cultivar_count,
                "crop_group": groups.get(code, ""),
            })
    return entries


def list_cultivars(crop_code, dssat_model=None):
    """Return all cultivars for a given crop code and optional model.

    Uses DB records if available (seeded by seed_cultivars), falls back
    to on-the-fly file parsing otherwise.
    """
    crop_code = crop_code.upper()

    # Try DB first
    from .cultivar_service import list_cultivars_db
    db_result = list_cultivars_db(crop_code, dssat_model)
    if db_result.get('cultivars'):
        return db_result

    # Fallback to file parsing
    try:
        cls = get_crop_class(crop_code, dssat_model)
    except KeyError as e:
        return {"error": str(e)}

    cultivar_codes = cls.cultivar_list()
    cultivars = []
    for cul_code in cultivar_codes:
        try:
            crop_instance = cls(cul_code)
            # Extract cultivar name — stored as 'vrname' or 'var-name'
            cname = ""
            for name_key in ('vrname', 'var-name'):
                try:
                    val = crop_instance[name_key]
                    if val and str(val).strip() and str(val).strip() != '-99':
                        cname = str(val).strip()
                        break
                except (KeyError, TypeError):
                    continue
            # Extract ecotype code
            eco_code = ""
            try:
                eco = crop_instance['eco#']
                if hasattr(eco, 'str'):
                    eco_code = eco.str
                else:
                    eco_code = str(eco).strip()
            except (KeyError, TypeError):
                pass
            cultivars.append({
                "code": cul_code,
                "name": cname,
                "ecotype": eco_code,
            })
        except Exception:
            cultivars.append({
                "code": cul_code,
                "name": "",
                "ecotype": "",
            })

    return {
        "crop_code": crop_code,
        "crop_name": CROP_NAMES.get(crop_code, crop_code),
        "dssat_model": dssat_model or DEFAULT_MODEL.get(crop_code, ""),
        "cultivars": cultivars,
    }


def _to_json_safe(val):
    """Convert a DSSATTools value to a JSON-serializable Python type."""
    import math
    if val is None:
        return None
    if hasattr(val, 'item'):
        val = val.item()
    if isinstance(val, float):
        if math.isnan(val) or math.isinf(val):
            return None
        return val
    if isinstance(val, (int, str, bool)):
        return val
    if hasattr(val, 'tolist'):
        return val.tolist()
    return str(val)


def calculate_weather_dates(crop_code, planting_date, num_years=1):
    """
    Calculate the weather data date range required for a DSSAT simulation.

    Uses crop-specific growth durations and a pre-planting soil
    initialisation buffer to determine exactly what date range the
    simulation will need.

    Parameters
    ----------
    crop_code : str
        Two-letter DSSAT crop code (e.g. "MZ").
    planting_date : str
        Planting date in YYYY-MM-DD format.
    num_years : int
        Number of simulation years (default 1).

    Returns
    -------
    dict
        start_date, end_date (YYYY-MM-DD), growth_duration_days,
        pre_plant_buffer_days, crop_name.
    """
    from datetime import datetime, timedelta

    crop_code = crop_code.upper()
    growth_days = CROP_GROWTH_DURATIONS.get(crop_code, 210)  # fallback 210
    crop_name = CROP_NAMES.get(crop_code, crop_code)

    try:
        pdate = datetime.strptime(planting_date, "%Y-%m-%d")
    except (ValueError, TypeError):
        return {"error": f"Invalid planting_date format: {planting_date}. Use YYYY-MM-DD."}

    # Simulation start: buffer days before planting
    sim_start = pdate - timedelta(days=SIM_PRE_PLANT_BUFFER)

    # Simulation end: planting + (growth_days * num_years) + small buffer
    total_growth = growth_days * num_years
    sim_end = pdate + timedelta(days=total_growth + 30)  # 30-day post-harvest buffer

    return {
        "crop_code": crop_code,
        "crop_name": crop_name,
        "planting_date": planting_date,
        "start_date": sim_start.strftime("%Y-%m-%d"),
        "end_date": sim_end.strftime("%Y-%m-%d"),
        "growth_duration_days": growth_days,
        "pre_plant_buffer_days": SIM_PRE_PLANT_BUFFER,
        "num_years": num_years,
        "total_days": (sim_end - sim_start).days,
    }


def get_cultivar_details(crop_code, cultivar_code, dssat_model=None):
    """Return full cultivar + ecotype parameters.

    Uses DB record if available, falls back to file parsing.
    """
    crop_code = crop_code.upper()

    # Try DB first
    from .cultivar_service import list_cultivars_db, get_cultivar_detail_db
    from dssat_agent.models import DSSATCultivar
    dm = dssat_model.upper() if dssat_model else None
    qs = DSSATCultivar.objects.filter(cultivar_code=cultivar_code, crop_code=crop_code)
    if dm:
        qs = qs.filter(dssat_model=dm)
    record = qs.first()
    if record:
        return get_cultivar_detail_db(record.id)

    # Fallback to file parsing
    try:
        cls = get_crop_class(crop_code, dssat_model)
    except KeyError as e:
        return {"error": str(e)}

    try:
        crop_instance = cls(cultivar_code)
    except Exception as e:
        return {"error": f"Failed to load cultivar {cultivar_code}: {e}"}

    # Extract cultivar parameters
    cultivar_params = {}
    if hasattr(cls, 'cul_dtypes') and cls.cul_dtypes:
        for param_name in cls.cul_dtypes:
            try:
                val = crop_instance[param_name]
                if param_name == 'eco#':
                    # Show just the ecotype code, not the full CropPars object
                    cultivar_params[param_name] = val.str if hasattr(val, 'str') else str(val).strip()
                else:
                    cultivar_params[param_name] = _to_json_safe(val)
            except (KeyError, TypeError):
                pass

    # Extract ecotype parameters
    ecotype_code = ""
    ecotype_params = {}
    if hasattr(cls, 'eco_dtypes') and cls.eco_dtypes:
        try:
            eco = crop_instance['eco#']
            ecotype_code = eco.str if hasattr(eco, 'str') else str(eco).strip()
            if hasattr(eco, 'pars_fmt'):
                for param_name in eco.pars_fmt:
                    try:
                        val = eco[param_name]
                        ecotype_params[param_name] = _to_json_safe(val)
                    except (KeyError, TypeError):
                        pass
        except (KeyError, TypeError):
            pass

    return {
        "crop_code": crop_code,
        "crop_name": CROP_NAMES.get(crop_code, crop_code),
        "cultivar_code": cultivar_code,
        "dssat_model": dssat_model or DEFAULT_MODEL.get(crop_code, ""),
        "cultivar_params": cultivar_params,
        "ecotype_code": ecotype_code,
        "ecotype_params": ecotype_params,
    }
