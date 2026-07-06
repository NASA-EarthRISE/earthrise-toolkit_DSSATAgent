"""
Dynamic crop class factory.

Parses CUL/ECO file headers to infer parameter schemas and builds
Crop subclasses at runtime for crop models that lack hand-written classes.
"""

import glob
import logging
import os
import re
from collections import OrderedDict

from .base.partypes import Crop, NumberType, DescriptionType, Record
from .base.utils import detect_encoding

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Header parsing
# ---------------------------------------------------------------------------

def parse_header_columns(header_line, prefix_width=7):
    """Parse a @VAR# or @ECO# header line into an OrderedDict of {name: width}.

    Parameters
    ----------
    header_line : str
        The full header line, e.g. ``@VAR#  VRNAME.......... EXPNO   ECO# ...``
    prefix_width : int
        Number of characters to strip from the front (default 7 covers
        ``@VAR#  `` and ``@ECO#  ``).

    Returns
    -------
    OrderedDict[str, int]
        Column name (lowercased, dots stripped) -> column width in characters.
    """
    rest = header_line[prefix_width:]
    columns = OrderedDict()
    prev_end = -1
    for m in re.finditer(r'\S+', rest):
        name = m.group().rstrip('.').lower()
        end = m.end() - 1
        width = end + 1 if prev_end == -1 else end - prev_end - 1
        columns[name] = width
        prev_end = end
    return columns


# ---------------------------------------------------------------------------
# Schema inference
# ---------------------------------------------------------------------------

def infer_cul_schema(columns, has_eco_file):
    """Infer cul_dtypes and cul_pars_fmt from parsed CUL header columns.

    Returns
    -------
    tuple[dict, dict]
        (cul_dtypes, cul_pars_fmt) ready for use as Crop class attributes.
    """
    dtypes = {}
    pars_fmt = {}
    for i, (name, width) in enumerate(columns.items()):
        if i == 0:
            # cultivar name column (VRNAME / VAR-NAME)
            dtypes[name] = DescriptionType
            pars_fmt[name] = f'.<{width}'
        elif i == 1:
            # experiment number column (EXPNO / EXP#)
            dtypes[name] = DescriptionType
            pars_fmt[name] = f'>{width}'
        elif i == 2:
            # ecotype code column (ECO#)
            dtypes[name] = Record if has_eco_file else DescriptionType
            pars_fmt[name] = f'>{width}'
        else:
            dtypes[name] = NumberType
            pars_fmt[name] = f'>{width}.1f'
    return dtypes, pars_fmt


def infer_eco_schema(columns):
    """Infer eco_dtypes and eco_pars_fmt from parsed ECO header columns.

    Returns
    -------
    tuple[dict, dict]
        (eco_dtypes, eco_pars_fmt) ready for use as Crop class attributes.
    """
    dtypes = {}
    pars_fmt = {}
    for i, (name, width) in enumerate(columns.items()):
        if i == 0:
            # econame / eco-name description column
            dtypes[name] = DescriptionType
            pars_fmt[name] = f'.<{width}'
        elif name in ('mg', 'tm') and width <= 2:
            # Maturity group / thermal-time group codes in CROPGRO ecotypes
            dtypes[name] = DescriptionType
            pars_fmt[name] = f'>{width}'
        else:
            dtypes[name] = NumberType
            pars_fmt[name] = f'>{width}.1f'
    return dtypes, pars_fmt


# ---------------------------------------------------------------------------
# File helpers
# ---------------------------------------------------------------------------

def _find_header_line(file_path, prefix):
    """Find the @VAR# or @ECO# header line in a genotype file.

    Parameters
    ----------
    prefix : str
        ``"@VAR#"`` or ``"@ECO#"``

    Returns
    -------
    str or None
    """
    encoding = detect_encoding(file_path)
    with open(file_path, 'r', encoding=encoding) as f:
        for line in f:
            stripped = line.rstrip()
            if stripped.startswith(prefix):
                return stripped
            # Also handle @ECO (without #) used by some files
            if prefix == "@ECO#" and stripped.startswith("@ECO "):
                return stripped
    return None


def _extract_crop_name(file_path):
    """Extract human-readable crop name from the first comment line of a CUL file.

    Parses lines like ``*MAIZE CULTIVAR COEFFICIENTS: MZCER048 MODEL``
    or ``$CULTIVARS:WHCRP048.20200721 ...``
    """
    encoding = detect_encoding(file_path)
    with open(file_path, 'r', encoding=encoding) as f:
        for line in f:
            line = line.lstrip('\ufeff').strip()
            if line.startswith('*') or line.startswith('$'):
                # Try pattern: *CROP_NAME CULTIVAR COEFFICIENTS
                m = re.match(r'[*$]\s*(\w[\w\s/\-]*?)\s+CULTIVAR', line, re.I)
                if m:
                    return m.group(1).strip().title()
                # Fallback: use first word after * or $
                m = re.match(r'[*$]\s*(\w+)', line)
                if m:
                    return m.group(1).title()
    return None


# ---------------------------------------------------------------------------
# Dynamic class builder
# ---------------------------------------------------------------------------

def build_crop_class(crop_code, smodel, genotype_path, version,
                     cul_dtypes, cul_pars_fmt, eco_dtypes, eco_pars_fmt):
    """Create a Crop subclass dynamically using ``type()``.

    Parameters
    ----------
    crop_code : str
        Two-letter crop code (e.g. ``"CO"``).
    smodel : str
        Model identifier (e.g. ``"COGRO"``).
    genotype_path : str
        Absolute path to the Genotype directory.
    version : str
        DSSAT version string (e.g. ``"048"``).
    cul_dtypes, cul_pars_fmt : dict
        Cultivar parameter type and format dicts.
    eco_dtypes, eco_pars_fmt : dict or None
        Ecotype parameter dicts, or None if no ECO file.

    Returns
    -------
    type
        A new class inheriting from ``Crop``.
    """
    spe_file = f'{smodel}{version}.SPE'
    spe_path = os.path.join(genotype_path, spe_file)

    class_name = f'Auto_{smodel}'

    # Use a default-argument closure to capture Crop.__init__ safely
    def _init(self, cultivar_code, _super_init=Crop.__init__):
        _super_init(self, cultivar_code)

    cls = type(class_name, (Crop,), {
        'code': crop_code,
        'smodel': smodel,
        'spe_file': spe_file,
        'spe_path': spe_path,
        'cul_dtypes': cul_dtypes,
        'cul_pars_fmt': cul_pars_fmt,
        'eco_dtypes': eco_dtypes,
        'eco_pars_fmt': eco_pars_fmt,
        '__init__': _init,
    })
    return cls


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def discover_all_models(genotype_path, version='048'):
    """Scan the Genotype directory and build a crop class for every CUL file.

    Parameters
    ----------
    genotype_path : str
        Path to ``dssat-csm-os/Data/Genotype/``.
    version : str
        DSSAT version suffix (default ``"048"``).

    Returns
    -------
    dict[str, type]
        Mapping of smodel (e.g. ``"MZCER"``) to dynamically created Crop
        subclass.  Keys are uppercase.
    dict[str, str]
        Mapping of smodel to human-readable crop name.
    """
    pattern = os.path.join(genotype_path, f'*{version}.CUL')
    classes = {}
    names = {}

    for cul_path in sorted(glob.glob(pattern)):
        basename = os.path.basename(cul_path)
        # e.g. "MZCER048.CUL" -> smodel="MZCER", crop_code="MZ"
        smodel = basename[:-(len(version) + 4)]  # strip "048.CUL"
        if len(smodel) < 4:
            logger.warning("Skipping unexpected CUL filename: %s", basename)
            continue
        crop_code = smodel[:2].upper()
        smodel = smodel.upper()

        try:
            # Parse CUL header
            cul_header = _find_header_line(cul_path, "@VAR#")
            if cul_header is None:
                logger.warning("No @VAR# header in %s, skipping", basename)
                continue
            cul_columns = parse_header_columns(cul_header)
            if len(cul_columns) < 3:
                logger.warning("Too few columns in %s header, skipping", basename)
                continue

            # Check for ECO file with a proper @ECO# header
            eco_path = cul_path[:-3] + 'ECO'
            has_eco_file = False
            eco_dtypes = None
            eco_pars_fmt = None
            if os.path.exists(eco_path):
                eco_header = _find_header_line(eco_path, "@ECO#")
                if eco_header is not None:
                    eco_columns = parse_header_columns(eco_header)
                    if len(eco_columns) >= 2:
                        eco_dtypes, eco_pars_fmt = infer_eco_schema(eco_columns)
                        has_eco_file = True

            cul_dtypes, cul_pars_fmt = infer_cul_schema(cul_columns, has_eco_file)

            cls = build_crop_class(
                crop_code, smodel, genotype_path, version,
                cul_dtypes, cul_pars_fmt, eco_dtypes, eco_pars_fmt,
            )
            classes[smodel] = cls

            # Extract crop name
            crop_name = _extract_crop_name(cul_path)
            if crop_name:
                names[smodel] = crop_name

        except Exception:
            logger.warning("Failed to auto-generate class for %s", basename,
                           exc_info=True)

    return classes, names
