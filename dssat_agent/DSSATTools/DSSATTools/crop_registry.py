"""
Central crop class registry.

Combines the 18 hand-written crop classes from ``crop.py`` with
auto-generated classes from :mod:`crop_factory` so that every CUL file
in the Genotype directory is represented.  Hand-written classes always
take priority.
"""

import logging
import os
from collections import defaultdict

from . import VERSION
from . import __file__ as _module_path
from .crop import (
    Maize, Sorghum, Wheat, Rice, PearlMillet, Sugarbeet, SweetCorn,
    Soybean, Canola, Sunflower, Tomato, Cabbage, DryBean,
    Alfalfa, Bermudagrass, Potato, Sugarcane, Cassava,
)
from .crop_factory import discover_all_models

logger = logging.getLogger(__name__)

# Path to the Genotype directory bundled with DSSATTools
_DSSAT_MODULE_PATH = os.path.dirname(_module_path)
_GENOTYPE_PATH = os.path.join(
    _DSSAT_MODULE_PATH, 'dssat-csm-os', 'Data', 'Genotype'
)

# ── Hand-written classes (always preferred) ──────────────────────────────

_HAND_WRITTEN = [
    Maize, Sorghum, Wheat, Rice, PearlMillet, Sugarbeet, SweetCorn,
    Soybean, Canola, Sunflower, Tomato, Cabbage, DryBean,
    Alfalfa, Bermudagrass, Potato, Sugarcane, Cassava,
]

# ── Build the registry ───────────────────────────────────────────────────

def _build_registry():
    """Construct all registry data structures.

    Returns (CROP_MODEL_CLASSES, CROP_CODE_MODELS, DEFAULT_MODEL, ALL_CROP_NAMES).
    """
    # smodel -> class
    model_classes = {}
    # smodel -> crop name (from factory)
    factory_names = {}

    # 1. Discover all models from CUL files
    auto_classes, auto_names = discover_all_models(_GENOTYPE_PATH, VERSION)
    model_classes.update(auto_classes)
    factory_names.update(auto_names)

    # 2. Overlay hand-written classes — they replace the auto-generated class
    #    for the SAME CUL file.  Some hand-written classes have a generic
    #    smodel (e.g. DryBean.smodel="CRGRO") that differs from the CUL-file
    #    key (e.g. "BNGRO").  We derive the CUL-file key from spe_file and
    #    replace the auto-generated entry rather than adding a duplicate.
    for cls in _HAND_WRITTEN:
        # Derive the key the factory would have used for this class's CUL file
        # spe_file is e.g. "BNGRO048.SPE" -> cul_key = "BNGRO"
        cul_key = cls.spe_file[:-(len(VERSION) + 4)].upper()  # strip "048.SPE"
        if cul_key in model_classes:
            model_classes[cul_key] = cls
        else:
            # No factory entry (shouldn't happen), register under smodel
            model_classes[cls.smodel] = cls

    # 3. Build crop_code -> [smodel, ...] mapping
    code_models = defaultdict(list)
    for smodel, cls in sorted(model_classes.items()):
        code_models[cls.code].append(smodel)

    # Ensure hand-written default is first in each list
    hw_spe_keys = set()
    for cls in _HAND_WRITTEN:
        cul_key = cls.spe_file[:-(len(VERSION) + 4)].upper()
        hw_spe_keys.add(cul_key)
    for code, smodels in code_models.items():
        # Partition into hand-written-first, then alphabetical rest
        hw = [s for s in smodels if s in hw_spe_keys]
        rest = sorted(s for s in smodels if s not in hw_spe_keys)
        code_models[code] = hw + rest

    code_models = dict(code_models)

    # 4. Default model per crop code (first in list = hand-written if available)
    default_model = {code: smodels[0] for code, smodels in code_models.items()}

    # 5. Human-readable names: prefer a canonical set, fall back to factory names
    _CANONICAL_NAMES = {
        "AL": "Alfalfa", "BA": "Barley", "BC": "Carinata", "BH": "Bahia Grass",
        "BM": "Bermudagrass", "BN": "Dry Bean", "BR": "Brachiaria",
        "BS": "Sugarbeet", "CB": "Cabbage", "CH": "Chickpea", "CI": "Chia",
        "CN": "Canola", "CO": "Cotton", "CP": "Cowpea", "CS": "Cassava",
        "FB": "Faba Bean", "G0": "Bahia", "GB": "Green Bean",
        "GG": "Guinea Grass", "GY": "Guar", "ML": "Pearl Millet",
        "MZ": "Maize", "PI": "Pineapple", "PN": "Peanut", "PP": "Pigeon Pea",
        "PR": "Bell Pepper", "PT": "Potato", "QU": "Quinoa", "RI": "Rice",
        "SB": "Soybean", "SC": "Sugarcane", "SF": "Safflower", "SG": "Sorghum",
        "SR": "Strawberry", "SU": "Sunflower", "SW": "Sweet Corn",
        "TF": "Teff", "TM": "Tomato", "TN": "Tanier", "TR": "Taro",
        "VB": "Velvet Bean", "WH": "Wheat",
    }
    all_crop_names = {}
    for code in code_models:
        if code in _CANONICAL_NAMES:
            all_crop_names[code] = _CANONICAL_NAMES[code]
        else:
            # Try to get name from the default model's factory name
            default_smodel = default_model[code]
            all_crop_names[code] = factory_names.get(default_smodel, code)

    # Model-family friendly names for display in UI
    _MODEL_DISPLAY_NAMES = {
        "MZCER": "CERES", "MZIXM": "IXIM",
        "SGCER": "CERES",
        "CSCER": "CERES", "WHAPS": "APSIM", "WHCRP": "CROPSIM",
        "RICER": "CERES",
        "MLCER": "CERES",
        "BSCER": "CERES",
        "SWCER": "CERES",
        "CRGRO": "CROPGRO",
        "PRFRM": "FORAGE",
        "PTSUB": "SUBSTOR",
        "SCCAN": "CANEGRO", "SCCSP": "CASUPRO", "SCSAM": "SAMUCA",
        "CSYCA": "CSYCA", "CSCAS": "CASCAS",
        "BACER": "CERES", "BACRP": "CROPSIM",
        "BRFRM": "FORAGE", "BRGRO": "CROPGRO",
        "TFCER": "CERES", "TFAPS": "APSIM",
        "SUOIL": "OILCROP",
        "PIALO": "ALOHA",
        "TNARO": "AROIDS", "TRARO": "AROIDS",
    }

    return model_classes, code_models, default_model, all_crop_names, _MODEL_DISPLAY_NAMES


# Build once at import time
(
    CROP_MODEL_CLASSES,
    CROP_CODE_MODELS,
    DEFAULT_MODEL,
    ALL_CROP_NAMES,
    MODEL_DISPLAY_NAMES,
) = _build_registry()


# ── Public API ────────────────────────────────────────────────────────────

def get_crop_class(crop_code, dssat_model=None):
    """Look up a crop class by code and optional model.

    Parameters
    ----------
    crop_code : str
        Two-letter DSSAT crop code (e.g. ``"MZ"``).
    dssat_model : str, optional
        Specific model identifier (e.g. ``"MZIXM"``).  When omitted or
        empty, returns the default model for the crop code.

    Returns
    -------
    type
        A :class:`Crop` subclass.

    Raises
    ------
    KeyError
        If the crop code or model is not found.
    """
    crop_code = crop_code.upper()
    if dssat_model:
        dssat_model = dssat_model.upper()
        cls = CROP_MODEL_CLASSES.get(dssat_model)
        if cls is None:
            available = CROP_CODE_MODELS.get(crop_code, [])
            raise KeyError(
                f"Unknown DSSAT model '{dssat_model}' for crop '{crop_code}'. "
                f"Available: {available}"
            )
        return cls

    # No model specified — use default
    default_smodel = DEFAULT_MODEL.get(crop_code)
    if default_smodel is None:
        raise KeyError(
            f"Unknown crop code '{crop_code}'. "
            f"Available: {sorted(DEFAULT_MODEL.keys())}"
        )
    return CROP_MODEL_CLASSES[default_smodel]


def get_model_display_name(smodel):
    """Return a short human-friendly name for a model identifier.

    Falls back to extracting the model suffix from the smodel string
    (e.g. ``"COGRO"`` -> ``"GRO"`` -> ``"CROPGRO"`` heuristic).
    """
    smodel = smodel.upper()
    name = MODEL_DISPLAY_NAMES.get(smodel)
    if name:
        return name
    # Heuristic: derive from the suffix
    suffix = smodel[2:]
    _SUFFIX_MAP = {
        "GRO": "CROPGRO", "CER": "CERES", "FRM": "FORAGE",
        "SUB": "SUBSTOR", "CAN": "CANEGRO", "ARO": "AROIDS",
        "ALO": "ALOHA", "OIL": "OILCROP", "APS": "APSIM",
        "CRP": "CROPSIM", "CAS": "CASCAS", "YCA": "CSYCA",
        "CSP": "CASUPRO", "SAM": "SAMUCA", "IXM": "IXIM",
    }
    return _SUFFIX_MAP.get(suffix, suffix)
