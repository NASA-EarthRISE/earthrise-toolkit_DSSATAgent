"""
DSSAT code description service.

Parses the DETAIL.CDE file from vendored DSSATTools at import time to provide
human-readable descriptions for DSSAT codes (fertilizers, chemicals, tillage
implements, etc.). Enriches the bare code lists from CODE_VARS with descriptions.
"""

import logging
import os

from DSSATTools import __file__ as _dssat_module_path
from DSSATTools.base.partypes import CODE_VARS

logger = logging.getLogger(__name__)

# Locate DETAIL.CDE using the same pattern as crop.py uses for GENOTYPE_PATH
_DSSAT_DIR = os.path.dirname(_dssat_module_path)
_DETAIL_CDE_PATH = os.path.join(_DSSAT_DIR, 'dssat-csm-os', 'Data', 'DETAIL.CDE')

# Parsed data: {section_name: {code: description}}
_CDE_DATA = {}


def _parse_detail_cde():
    """Parse DETAIL.CDE into a dict of {section_name: {code: description}}."""
    result = {}
    current_section = None

    try:
        with open(_DETAIL_CDE_PATH, 'r', encoding='utf-8', errors='replace') as f:
            for line in f:
                line = line.rstrip('\n\r')

                # Skip empty lines and comments
                if not line or line.startswith('!'):
                    continue

                # Section header: starts with *
                if line.startswith('*'):
                    current_section = line[1:].strip()
                    result[current_section] = {}
                    continue

                # Header line: starts with @
                if line.startswith('@'):
                    continue

                # Data line: fixed-width format
                # chars 0-8 = code (left-aligned, may be shorter)
                # chars 9-72 = description
                # chars 73+ = source (SO)
                if current_section is not None and len(line) >= 2:
                    # Code is the first whitespace-delimited token (up to 8 chars)
                    code = line[:9].strip()
                    if code:
                        # Description runs from position 9 to ~72
                        desc = line[9:73].strip() if len(line) > 9 else ''
                        result[current_section][code] = desc

    except FileNotFoundError:
        logger.warning("DETAIL.CDE not found at %s", _DETAIL_CDE_PATH)
    except Exception as e:
        logger.error("Failed to parse DETAIL.CDE: %s", e)

    return result


# Parse once at import time
_CDE_DATA = _parse_detail_cde()

# Map DSSAT variable names (from CODE_VARS) to DETAIL.CDE section names
_VAR_TO_SECTION = {
    'fmcd': 'Fertilizers, Inoculants and Amendments',
    'facd': 'Methods - Fertilizer and Chemical Applications',
    'plme': 'Planting Material/Method',
    'plds': 'Plant Distribution',
    'fldt': 'Drainage',
    'sltx': 'Soil Texture',
    'hcom': 'Harvest components',
    'hsize': 'Harvest size categories',
    'cr': 'Crop and Weed Species',
    'chcod': 'Chemicals (Herbicides, Insecticides, Fungicides, etc.)',
    'irop': 'Methods - Irrigation and Water Management (Units for associated data)',
    'iame': 'Methods - Irrigation and Water Management (Units for associated data)',
    'timpl': 'Tillage Implements',
    'rcod': 'Residues and Organic Fertilizer',
    'flhst': 'Field History',
}


_ALIAS_TO_VAR = {
    'planting': 'plme',
    'planting_method': 'plme',
    'planting_distribution': 'plds',
    'distribution': 'plds',
    'fertilizer': 'fmcd',
    'application': 'facd',
    'application_method': 'facd',
    'irrigation': 'irop',
    'irrigation_method': 'irop',
    'tillage': 'timpl',
    'chemical': 'chcod',
    'residue': 'rcod',
    'harvest': 'hcom',
    'harvest_size': 'hsize',
    'drainage': 'fldt',
    'soil_texture': 'sltx',
    'crop': 'cr',
    'field_history': 'flhst',
}


def get_codes_with_descriptions(dssat_var):
    """
    Return a list of {code, description} dicts for a DSSAT variable.

    Looks up codes from CODE_VARS and enriches with descriptions from
    DETAIL.CDE. Codes not found in DETAIL.CDE get an empty description.

    Parameters
    ----------
    dssat_var : str
        The DSSAT variable name (e.g. 'fmcd', 'facd', 'plme') or a
        human-readable alias (e.g. 'planting', 'fertilizer').

    Returns
    -------
    list[dict]
        Each dict has 'code' and 'description' keys.
    """
    dssat_var = _ALIAS_TO_VAR.get(dssat_var, dssat_var)
    codes = CODE_VARS.get(dssat_var, [])
    section_name = _VAR_TO_SECTION.get(dssat_var)
    section_data = _CDE_DATA.get(section_name, {}) if section_name else {}

    result = []
    for code in codes:
        if code is None:
            continue
        desc = section_data.get(code, '')
        result.append({'code': code, 'description': desc})

    return result


def get_section_names():
    """Return all parsed DETAIL.CDE section names (for debugging)."""
    return list(_CDE_DATA.keys())


# =============================================================================
# GRSTAGE.CDE — Phenological / Growth Stage Codes per (crop, DSSAT model)
# =============================================================================

_GRSTAGE_CDE_PATH = os.path.join(_DSSAT_DIR, 'dssat-csm-os', 'Data', 'GRSTAGE.CDE')

# Section header (free text after the leading `*`) -> (crop_code, dssat_model).
# The DSSAT binary reads the same GRSTAGE codes per crop-model; this map
# lets us attach them to our CropModel rows keyed by (crop_code, dssat_model).
# Entries not in this map (e.g. the "Cereal Crops - Zadoks" generic scale
# and the closing bibliography) are skipped.
_GRSTAGE_SECTION_MAP = {
    'Growth and Development Codes - Maize (CERES Version 4.5)':          ('MZ', 'MZCER'),
    'Growth and Development Codes - Sweetcorn (CERES Version 4.5)':      ('SW', 'SWCER'),
    'Growth and Development Codes - Soybean (CROPGRO Version 4.5)':      ('SB', 'SBGRO'),
    'Growth and Development Codes - Wheat (CERES Version 4.5)':          ('WH', 'WHCER'),
    'Growth and Development Codes - Barley (CERES Version 4.5)':         ('BA', 'BACER'),
    'Growth and Development Codes - Rice (CERES Version 4.5)':           ('RI', 'RICER'),
    'Growth and Development Codes - Sorghum (CERES Version 4.5)':        ('SG', 'SGCER'),
    'Growth and Development Codes - Millet (CERES Version 4.5)':         ('ML', 'MLCER'),
    'Growth and Development Codes - Sunflower (OilcropSun Version 6.1)': ('SU', 'SUOIL'),
    'Growth and Development Codes - Potato (SUBSTOR Version 4.5)':       ('PT', 'PTSUB'),
    'Growth and Development Codes - Cassava (CSSIM Version 4.5)':        ('CS', 'CSYCA'),
    'Growth and Development Codes - Cotton (CROPGRO Version 4.5)':       ('CO', 'COGRO'),
    'Growth and Development Codes - Peanut (CROPGRO Version 4.5)':       ('PN', 'PNGRO'),
    'Growth and Development Codes - Dry Bean (CROPGRO Version 4.5)':     ('BN', 'BNGRO'),
    'Growth and Development Codes - Chickpea (CROPGRO Version 4.5)':     ('CH', 'CHGRO'),
    'Growth and Development Codes - Cowpea (CROPGRO Version 4.5)':       ('CP', 'CPGRO'),
    'Growth and Development Codes - Faba Bean (CROPGRO Version 4.5)':    ('FB', 'FBGRO'),
    'Growth and Development Codes - Green Bean (CROPGRO Version 4.5)':   ('GB', 'GBGRO'),
    'Growth and Development Codes - Velvet Bean (CROPGRO Version 4.5)':  ('VB', 'VBGRO'),
    'Growth and Development Codes - Cabbage (CROPGRO Version 4.5)':      ('CB', 'CBGRO'),
    'Growth and Development Codes - Tanier(AROID Version 4.5)':          ('TN', 'TNARO'),
    'Growth and Development Codes - Taro (AROID Version 4.5)':           ('TR', 'TRARO'),
    'Growth and Development Codes - Sugarcane (CANEGRO Version 4.5)':    ('SC', 'SCCAN'),
    'Growth and Development Codes - Fallow (CROPGRO Version 4.5)':       ('FA', 'FAGRO'),
}


def _parse_grstage_cde():
    """Parse GRSTAGE.CDE into {(crop_code, dssat_model): [stage, ...]}.

    Each stage is ``{'code': 'GS005', 'name': 'R6', 'description': '...'}``.
    Continuation lines (no GS code) are skipped because DSSAT's ``hstg``
    field requires a GS code; the descriptive V1/V2 continuation entries
    have no model-recognized code to pass.
    """
    result = {}
    current_key = None
    current_list = None

    try:
        with open(_GRSTAGE_CDE_PATH, 'r', encoding='utf-8', errors='replace') as f:
            for raw in f:
                line = raw.rstrip('\n\r')
                if not line or line.startswith('!'):
                    continue
                if line.startswith('*'):
                    header = line[1:].strip()
                    mapped = _GRSTAGE_SECTION_MAP.get(header)
                    if mapped:
                        current_key = mapped
                        current_list = result.setdefault(mapped, [])
                    else:
                        current_key = None
                        current_list = None
                    continue
                if line.startswith('@') or current_list is None:
                    continue

                # Fixed-width layout (mirrors the `@CDE  NAME  DESCRIPTION` header):
                #   cols 0-4  CDE (e.g. "GS005")
                #   cols 6-9  NAME (e.g. "R6", "VE")
                #   cols 12+  DESCRIPTION, padded with dots and a SYNONYMS marker
                code = line[0:5].strip() if len(line) >= 5 else ''
                name = line[6:10].strip() if len(line) >= 10 else ''
                desc = line[12:].strip() if len(line) >= 12 else ''
                desc = desc.rstrip('. ').strip()
                # GS000 ("None") is noise for the UI — skip it.
                if not code or code == 'GS000':
                    continue
                current_list.append({
                    'code': code,
                    'name': name,
                    'description': desc,
                })
    except FileNotFoundError:
        logger.warning("GRSTAGE.CDE not found at %s", _GRSTAGE_CDE_PATH)
    except Exception as e:
        logger.error("Failed to parse GRSTAGE.CDE: %s", e)

    return result


# Parse once at import time
_GRSTAGE_DATA = _parse_grstage_cde()


def get_harvest_stages(crop_code, dssat_model):
    """Return [{code, name, description}] for this (crop, model) pairing.

    Returns an empty list if no stages are known for the combo. Caller is
    expected to fall back to whatever default the UI wants in that case.
    """
    key = ((crop_code or '').upper(), (dssat_model or '').upper())
    return list(_GRSTAGE_DATA.get(key, []))


def get_all_harvest_stages():
    """Return the full parsed dict: {(crop_code, dssat_model): [stage, ...]}."""
    return {k: list(v) for k, v in _GRSTAGE_DATA.items()}
