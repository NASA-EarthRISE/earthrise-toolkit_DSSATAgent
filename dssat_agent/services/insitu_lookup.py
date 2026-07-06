"""
In-situ reference data lookup service.

The DSSAT-domain semantic layer for the Site Reference Data feature. Translates
generic raster point-query results from data_agent into DSSAT-specific values
(soil_id strings, ISO planting dates, layered soil profiles).

Supported sources today:
  - In-house DSSAT soil-type raster (3 bands, dominant + 2 alternates)
  - In-house DSSAT planting date raster (Julian day)
  - SoilGrids 2.0 (global REST API, layered soil profile)
  - SSURGO (US-only, USDA Soil Data Access web service)

The first two are looked up via data_agent.services.sample_raster_at_point().
The last two are external HTTP clients in dssat_agent/services/external_data/
that funnel results through soil_service.create_soil_from_texture() to produce
real StoredSoilProfile rows.

Domain separation contract: data_agent knows nothing about soils or planting
dates. It only knows how to sample a raster at a point. Translation lives here.
"""

import logging
from datetime import date, timedelta
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


# Module-level legend cache. The active legend cache lives in
# data_agent.services.get_raster_legend; these are retained only for
# callers that still reference them.
_legend_cache: Dict[str, Dict[int, Dict[str, str]]] = {}
_legend_cache_hash: Dict[str, str] = {}


# ---------------------------------------------------------------------------
# Public API — discovery
# ---------------------------------------------------------------------------

def list_available_in_situ_sources(lat: float, lon: float) -> Dict[str, List[Dict]]:
    """
    Return all in-situ reference sources available at a given coordinate,
    grouped by the experiment parameter they fill.

    Each source entry has: ``source`` (id), ``label`` (human name),
    ``in_coverage`` (bool), ``description`` (one-liner), and optional
    ``coverage_extent``. The wizard renders this as categorized dropdowns.

    Returns
    -------
    {
      'soil':         [{...}, {...}, ...],
      'planting_date': [{...}, ...],
    }
    """
    from data_agent.services import is_point_in_coverage

    sources: Dict[str, List[Dict]] = {'soil': [], 'planting_date': []}

    # In-house SE-US soil raster
    in_house_soil_in_coverage = is_point_in_coverage(
        'dssat_soil_lookup_v1', lat, lon
    )
    sources['soil'].append({
        'source': 'dssat_soil_lookup_v1',
        'label': 'In-house SE-US soil raster (5km)',
        'in_coverage': in_house_soil_in_coverage,
        'description': (
            'Dominant + 2 subdominant SSURGO soils per pixel from the project '
            'in-house raster. Best for southeast US locations.'
        ),
    })

    # SoilGrids 2.0 — always in coverage (global)
    sources['soil'].append({
        'source': 'soilgrids',
        'label': 'SoilGrids 2.0 (global, ISRIC)',
        'in_coverage': True,
        'description': (
            'Global 250m predictions from the USDA Rosetta pedotransfer model. '
            'Returns a fully layered profile.'
        ),
    })

    # SSURGO — US only
    ssurgo_in_coverage = (
        18.0 <= lat <= 72.0 and -180.0 <= lon <= -65.0
    )
    sources['soil'].append({
        'source': 'ssurgo',
        'label': 'SSURGO (USDA, US-only, high resolution)',
        'in_coverage': ssurgo_in_coverage,
        'description': (
            'Field-surveyed by USDA NRCS soil scientists. The highest-fidelity '
            'option for US sites.'
        ),
    })

    # In-house SE-US planting date raster
    in_house_planting_in_coverage = is_point_in_coverage(
        'dssat_planting_date_lookup_v1', lat, lon
    )
    sources['planting_date'].append({
        'source': 'dssat_planting_date_lookup_v1',
        'label': 'In-house SE-US planting date (5km)',
        'in_coverage': in_house_planting_in_coverage,
        'description': (
            'Typical planting day-of-year from the project in-house raster. '
            'Available only for southeast US locations.'
        ),
    })

    return sources


# ---------------------------------------------------------------------------
# Public API — generalized dispatchers (one skill per parameter)
#
# These are the only functions exposed to the chat orchestrator. Each takes
# a ``source`` kwarg; the actual per-source implementations live below as
# private underscore-prefixed helpers.
# ---------------------------------------------------------------------------

# Canonical source aliases. Keeps the LLM's life easy: it can say
# source='in_house' or source='soilgrids' without needing to know the exact
# RasterDataset name.
_SOIL_SOURCE_ALIASES = {
    'auto': None,
    'in_house': 'dssat_soil_lookup_v1',
    'in_house_raster': 'dssat_soil_lookup_v1',
    'dssat_soil_lookup_v1': 'dssat_soil_lookup_v1',
    'soilgrids': 'soilgrids',
    'soilgrids_v2': 'soilgrids',
    'ssurgo': 'ssurgo',
}

_PLANTING_DATE_SOURCE_ALIASES = {
    'auto': None,
    'in_house': 'dssat_planting_date_lookup_v1',
    'in_house_raster': 'dssat_planting_date_lookup_v1',
    'dssat_planting_date_lookup_v1': 'dssat_planting_date_lookup_v1',
}


def lookup_soil(lat: float, lon: float, source: str = 'auto') -> Dict:
    """
    Look up a soil profile at a geographic coordinate.

    Supported sources:
      - ``auto`` (default): try the in-house DSSAT raster first (covers
        southeast US), fall back to SoilGrids (global) if out of coverage.
      - ``in_house`` / ``dssat_soil_lookup_v1``: in-house 5km SSURGO raster
        (southeast US only; returns dominant + 2 alternate soil types).
      - ``soilgrids``: ISRIC SoilGrids 2.0 global REST API (~250m, layered
        profile from Rosetta pedotransfer).
      - ``ssurgo``: USDA Soil Data Access (US-only, field-surveyed,
        high-fidelity layered profile).

    Returns a dict with the same shape across all sources:
    ``{dominant, alternates, source, in_coverage, cached?, error?}``.
    External sources (SoilGrids, SSURGO) also include ``layered_profile``
    with layer dicts compatible with ``create_soil_from_texture()``.
    """
    resolved = _SOIL_SOURCE_ALIASES.get((source or 'auto').lower())
    if resolved is None and (source or 'auto').lower() != 'auto':
        return {
            'dominant': None, 'alternates': [], 'source': source,
            'in_coverage': False,
            'error': f"Unknown soil source '{source}'. "
                     f"Valid: {sorted(set(_SOIL_SOURCE_ALIASES.values()) - {None})}",
        }

    if resolved is None:  # auto
        # Try in-house first, fall back to SoilGrids.
        from data_agent.services import is_point_in_coverage
        if is_point_in_coverage('dssat_soil_lookup_v1', lat, lon):
            return _lookup_soil_in_house(lat, lon)
        return _lookup_soil_soilgrids(lat, lon)

    if resolved == 'dssat_soil_lookup_v1':
        return _lookup_soil_in_house(lat, lon)
    if resolved == 'soilgrids':
        return _lookup_soil_soilgrids(lat, lon)
    if resolved == 'ssurgo':
        return _lookup_soil_ssurgo(lat, lon)

    return {'dominant': None, 'alternates': [], 'source': source,
            'in_coverage': False, 'error': f"Unhandled source '{resolved}'"}


def lookup_planting_date(lat: float, lon: float, year: int, source: str = 'auto') -> Dict:
    """
    Look up the typical planting date at a geographic coordinate for a year.

    Supported sources:
      - ``auto`` (default) or ``in_house``: the in-house DSSAT 5km raster
        (southeast US only). Returns Julian day converted to an ISO date
        for the given year.

    Returns: ``{planting_date, julian_day, source, in_coverage}``.

    (Future sources like the SAGE crop calendar can be added as additional
    branches without changing the function signature.)
    """
    resolved = _PLANTING_DATE_SOURCE_ALIASES.get((source or 'auto').lower())
    if resolved is None and (source or 'auto').lower() != 'auto':
        return {
            'planting_date': None, 'julian_day': None, 'source': source,
            'in_coverage': False,
            'error': f"Unknown planting_date source '{source}'. "
                     f"Valid: {sorted(set(_PLANTING_DATE_SOURCE_ALIASES.values()) - {None})}",
        }
    if resolved is None:  # auto
        resolved = 'dssat_planting_date_lookup_v1'
    return _lookup_planting_date_in_house(lat, lon, year, resolved)


# ---------------------------------------------------------------------------
# Private per-source implementations (not exposed as chat skills)
# ---------------------------------------------------------------------------

def _lookup_soil_in_house(lat: float, lon: float) -> Dict:
    """
    Dominant + alternate soil profiles from the in-house DSSAT soil-type raster.
    """
    from data_agent.services import sample_raster_at_point, is_point_in_coverage

    raster_name = 'dssat_soil_lookup_v1'
    in_coverage = is_point_in_coverage(raster_name, lat, lon)
    if not in_coverage:
        return {
            'dominant': None,
            'alternates': [],
            'source': raster_name,
            'in_coverage': False,
        }

    band_values = sample_raster_at_point(
        raster_name, lat, lon,
        bands=[1, 2, 3],
        no_data_value=0,
    )

    legend = _load_soil_legend_cached(raster_name)

    def resolve(band_idx: int) -> Optional[Dict[str, str]]:
        v = band_values.get(band_idx)
        if v is None:
            return None
        try:
            v_int = int(v)
        except (TypeError, ValueError):
            return None
        return legend.get(v_int)

    dominant = resolve(1)
    alternates = [r for r in (resolve(2), resolve(3)) if r is not None]

    return {
        'dominant': dominant,
        'alternates': alternates,
        'source': raster_name,
        'in_coverage': True,
    }


def _lookup_soil_soilgrids(lat: float, lon: float) -> Dict:
    """SoilGrids 2.0 (ISRIC) layered profile lookup. Global coverage."""
    from dssat_agent.services.external_data.soilgrids_client import SoilGridsClient
    return SoilGridsClient().lookup(lat, lon)


def _lookup_soil_ssurgo(lat: float, lon: float) -> Dict:
    """SSURGO layered profile lookup (USDA NRCS). US-only coverage."""
    from dssat_agent.services.external_data.ssurgo_client import SsurgoClient
    return SsurgoClient().lookup(lat, lon)


def _lookup_planting_date_in_house(lat: float, lon: float, year: int, raster_name: str) -> Dict:
    """
    Typical planting day-of-year from a single-band in-house raster.
    Converts Julian day to an ISO date for the given year.
    """
    from data_agent.services import sample_raster_at_point, is_point_in_coverage

    in_coverage = is_point_in_coverage(raster_name, lat, lon)
    if not in_coverage:
        return {
            'planting_date': None,
            'julian_day': None,
            'source': raster_name,
            'in_coverage': False,
        }

    band_values = sample_raster_at_point(
        raster_name, lat, lon,
        bands=[1],
        no_data_value=-999,
    )

    doy = band_values.get(1)
    if doy is None:
        return {
            'planting_date': None,
            'julian_day': None,
            'source': raster_name,
            'in_coverage': True,
        }
    try:
        doy_int = int(doy)
    except (TypeError, ValueError):
        return {
            'planting_date': None,
            'julian_day': None,
            'source': raster_name,
            'in_coverage': True,
        }
    if doy_int < 1 or doy_int > 366:
        return {
            'planting_date': None,
            'julian_day': doy_int,
            'source': raster_name,
            'in_coverage': True,
        }

    iso = (date(year, 1, 1) + timedelta(days=doy_int - 1)).isoformat()
    return {
        'planting_date': iso,
        'julian_day': doy_int,
        'source': raster_name,
        'in_coverage': True,
    }


# ---------------------------------------------------------------------------
# Legend cache
# ---------------------------------------------------------------------------

def _load_soil_legend_cached(raster_name: str) -> Dict[int, Dict[str, str]]:
    """
    Return the in-memory legend dict for a categorical soil raster.

    Delegates to data_agent's CategoricalRasterLegend model (the canonical
    source) and reshapes the result into the {band_value: {soil_id, description}}
    format that the insitu lookup functions expect.
    """
    from data_agent.services import get_raster_legend

    raw = get_raster_legend(raster_name)
    # Reshape: data_agent returns {bv: {code, label}};
    # insitu_lookup expects {bv: {soil_id, description}}.
    return {
        bv: {
            'soil_id': entry.get('code', ''),
            'description': entry.get('label', ''),
        }
        for bv, entry in raw.items()
    }
