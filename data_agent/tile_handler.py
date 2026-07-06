"""
XYZ raster tile generation from PostGIS weather raster tables.

Queries source pixel centroids + values from PostGIS, maps them onto a
256x256 tile grid using geographic coordinates, applies per-variable
color ramps in Python, and renders PNG with zlib + struct.

This bypasses PostGIS raster rendering (ST_ColorMap, ST_Resize,
ST_Resample, ST_MapAlgebra) which don't reliably handle extent
mapping across different pixel scales.

Pipeline: PostGIS (ST_Union + ST_PixelAsCentroids) → Python rendering
"""

import logging
import math
import struct
import zlib

import psycopg2
from django.conf import settings

logger = logging.getLogger(__name__)


def tile_bounds(z, x, y):
    """Convert XYZ tile coordinates to EPSG:4326 bounding box (w, s, e, n)."""
    n = 2.0 ** z

    lon_w = x / n * 360.0 - 180.0
    lon_e = (x + 1) / n * 360.0 - 180.0

    lat_n_rad = math.atan(math.sinh(math.pi * (1 - 2 * y / n)))
    lat_s_rad = math.atan(math.sinh(math.pi * (1 - 2 * (y + 1) / n)))

    lat_n = math.degrees(lat_n_rad)
    lat_s = math.degrees(lat_s_rad)

    return lon_w, lat_s, lon_e, lat_n


def _get_connection():
    """Raw psycopg2 connection using Django DATABASES['default'] settings."""
    from earthrise_agents_base.db import get_raw_connection
    return get_raw_connection()


# ---------------------------------------------------------------------------
# Color ramps per variable — list of (value, R, G, B, A) breakpoints.
# Linear interpolation between entries. Absolute values for consistent
# colors across tiles regardless of local min/max.
# ---------------------------------------------------------------------------
COLOR_RAMPS = {
    'tmax': [
        (-10, 50, 100, 200, 255),
        (0, 100, 200, 255, 255),
        (15, 255, 255, 100, 255),
        (30, 255, 100, 0, 255),
        (45, 180, 0, 0, 255),
    ],
    'tmin': [
        (-20, 50, 100, 200, 255),
        (-5, 100, 200, 255, 255),
        (10, 255, 255, 100, 255),
        (20, 255, 100, 0, 255),
        (35, 180, 0, 0, 255),
    ],
    'rain': [
        (0, 240, 250, 255, 255),
        (0.1, 180, 220, 255, 255),
        (5, 100, 180, 255, 255),
        (15, 50, 100, 220, 255),
        (30, 0, 0, 180, 255),
        (50, 0, 0, 120, 255),
    ],
    'srad': [
        (0, 255, 255, 220, 255),
        (5, 255, 255, 150, 255),
        (15, 255, 255, 0, 255),
        (25, 255, 150, 0, 255),
        (35, 200, 0, 0, 255),
    ],
}


def _apply_ramp(value, ramp):
    """Map a float value to (R, G, B, A) via linear interpolation."""
    if value <= ramp[0][0]:
        return ramp[0][1:]
    if value >= ramp[-1][0]:
        return ramp[-1][1:]
    for i in range(len(ramp) - 1):
        v0, r0, g0, b0, a0 = ramp[i]
        v1, r1, g1, b1, a1 = ramp[i + 1]
        if v0 <= value <= v1:
            t = (value - v0) / (v1 - v0) if v1 != v0 else 0
            return (
                int(r0 + t * (r1 - r0)),
                int(g0 + t * (g1 - g0)),
                int(b0 + t * (b1 - b0)),
                int(a0 + t * (a1 - a0)),
            )
    return ramp[-1][1:]


def _make_png(width, height, rgba_buf):
    """Create a minimal valid PNG from raw RGBA pixel data."""
    def _chunk(ctype, data):
        c = ctype + data
        return (
            struct.pack('>I', len(data)) + c +
            struct.pack('>I', zlib.crc32(c) & 0xffffffff)
        )

    # Build raw scanlines: filter-byte 0 (None) + row RGBA data
    raw = bytearray()
    stride = width * 4
    for y in range(height):
        raw.append(0)
        raw.extend(rgba_buf[y * stride:(y + 1) * stride])

    return (
        b'\x89PNG\r\n\x1a\n' +
        _chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 6, 0, 0, 0)) +
        _chunk(b'IDAT', zlib.compress(bytes(raw), 6)) +
        _chunk(b'IEND', b'')
    )


def _resolve_tile_source(source, variable):
    """
    Classify a (source, variable) pair into the right PostGIS table and band.

    Handles two cases:
      - **Weather (per-variable table)**: source matches a RasterDataset whose
        ``subroutines.load_to_postgis.table_prefix`` produces a per-variable
        table (including combined sources registered as RasterDataset entries
        with ``source_groups`` in ``granule_info``). Returns band 1, has_date=True.
      - **Reference (multi-band single table)**: source matches a RasterDataset
        with ``subroutines.reference_lookup.table_name``; the variable maps to
        a band index via ``band_meanings``. Returns the right band, has_date=False.

    Returns a dict ``{table, band, has_date, value_type, no_data_value}`` or
    ``None`` if unresolvable.
    """
    from data_agent.models import RasterDataset

    schema = getattr(settings, 'DATAAGENT_SCHEMA', 'dataagent')

    # 1. Try matching as a RasterDataset by dataset_subtype.
    ds = RasterDataset.objects.filter(dataset_subtype=source).first()
    if ds:
        info = ds.dataset_information or {}
        subroutines = info.get('subroutines') or {}

        # Reference raster: multi-band single table
        ref_table = (subroutines.get('reference_lookup') or {}).get('table_name')
        if ref_table:
            band_meanings = info.get('band_meanings') or {}
            # Resolve variable → band index. variable can be the meaning
            # (e.g. 'dominant') or a 1-indexed string ('1') or int.
            band = None
            for band_idx, meaning in band_meanings.items():
                if meaning == variable or str(band_idx) == str(variable):
                    band = int(band_idx)
                    break
            if band is None:
                try:
                    band = int(variable)
                except (TypeError, ValueError):
                    band = 1  # default to first band
            return {
                'table': ref_table if '.' in ref_table else f'{schema}.{ref_table}',
                'band': band,
                'has_date': False,
                'value_type': info.get('value_type', 'continuous'),
                'no_data_value': info.get('no_data_value'),
                'variables_metadata': info.get('variables', {}),
            }

        # Time-series raster: per-variable table named {prefix}_{variable}
        prefix = (subroutines.get('load_to_postgis') or {}).get('table_prefix')
        if prefix:
            return {
                'table': f'{schema}.{prefix}_{variable}',
                'band': 1,
                'has_date': True,
                'value_type': 'continuous',
                'no_data_value': None,
                'variables_metadata': info.get('variables', {}),
            }

    # 2. Legacy fall-through: assume it's a weather table prefix.
    return {
        'table': f'{schema}.{source}_{variable}',
        'band': 1,
        'has_date': True,
        'value_type': 'continuous',
        'no_data_value': None,
    }


def _categorical_rgba(value):
    """
    Generate a deterministic RGBA color from a categorical pixel value.

    Used for reference rasters like the soil-type lookup where pixel values
    are class IDs without a meaningful gradient. Hash the integer to RGB and
    set full opacity.
    """
    try:
        v = int(value)
    except (TypeError, ValueError):
        return (0, 0, 0, 0)
    # Simple deterministic hash → distinct hues per category.
    h = (v * 2654435761) & 0xFFFFFFFF
    return ((h >> 16) & 0xFF, (h >> 8) & 0xFF, h & 0xFF, 200)


def generate_tile(source, variable, date, z, x, y):
    """
    Generate a 256x256 PNG tile for any registered raster source.

    Queries source pixel centroids from PostGIS, maps each onto the
    tile grid using geographic coordinates, applies a color ramp
    (continuous variables) or a categorical hash palette (reference
    rasters), and renders a PNG with transparent empty areas.

    Parameters
    ----------
    source : str
        Source identifier — RasterDataset.dataset_subtype or a legacy weather
        table prefix.
    variable : str
        For weather sources: the weather variable (tmax, tmin, rain, srad).
        For reference rasters: the band meaning (e.g. 'dominant') or a 1-indexed
        band string.
    date : str | None
        Date string YYYY-MM-DD. Ignored for static reference rasters.
    z, x, y : int
        XYZ tile coordinates.

    Returns
    -------
    bytes or None
        PNG image bytes, or None if no data intersects the tile.
    """
    w, s, e, n = tile_bounds(z, x, y)
    tile_id = f"{source}/{variable}/{date} z{z}/{x}/{y}"

    resolved = _resolve_tile_source(source, variable)
    if not resolved:
        logger.error("Unresolvable tile source: %s/%s", source, variable)
        return None

    table = resolved['table']
    band = resolved['band']
    has_date = resolved['has_date']
    value_type = resolved['value_type']
    no_data_value = resolved['no_data_value']

    # Pick a color strategy. Numeric → use a color ramp. Categorical → hash palette.
    is_categorical = (value_type == 'categorical')
    ramp = None
    if not is_categorical:
        # Try data-driven ramp from variables metadata first, then hardcoded fallback
        var_meta = resolved.get('variables_metadata', {})
        if isinstance(var_meta, dict):
            vm = var_meta.get(variable, {})
            cm = vm.get('color_map', {}) if isinstance(vm, dict) else {}
            meta_ramp = cm.get('ramp')
            if meta_ramp:
                # Convert [value, R, G, B] → [value, R, G, B, 255] (add alpha)
                ramp = [tuple(list(bp) + [255]) if len(bp) == 4 else tuple(bp) for bp in meta_ramp]
        if not ramp:
            ramp = COLOR_RAMPS.get(variable)
        if not ramp:
            logger.warning("No color ramp for variable %s; using categorical fallback", variable)
            is_categorical = True

    con = _get_connection()
    try:
        cur = con.cursor()

        # Query pixel centroids, values, and source pixel scale.
        # ST_PixelAsCentroids returns one row per pixel with geographic
        # position — we map these onto the 256x256 tile grid in Python.
        if has_date:
            query = f"""
                WITH source AS (
                    SELECT ST_Union(r.rast) AS rast
                    FROM {table} r
                    WHERE r.fdate = %s
                      AND ST_Intersects(r.rast,
                            ST_MakeEnvelope(%s, %s, %s, %s, 4326))
                )
                SELECT
                    ST_ScaleX(source.rast),
                    ST_ScaleY(source.rast),
                    ST_X(pix.geom),
                    ST_Y(pix.geom),
                    pix.val
                FROM source,
                     LATERAL ST_PixelAsCentroids(source.rast, %s, FALSE)
                         AS pix(geom, val, x, y)
                WHERE source.rast IS NOT NULL
            """
            cur.execute(query, (date, w, s, e, n, band))
        else:
            # Static reference raster — no fdate column.
            query = f"""
                WITH source AS (
                    SELECT ST_Union(r.rast) AS rast
                    FROM {table} r
                    WHERE ST_Intersects(r.rast,
                            ST_MakeEnvelope(%s, %s, %s, %s, 4326))
                )
                SELECT
                    ST_ScaleX(source.rast),
                    ST_ScaleY(source.rast),
                    ST_X(pix.geom),
                    ST_Y(pix.geom),
                    pix.val
                FROM source,
                     LATERAL ST_PixelAsCentroids(source.rast, %s, FALSE)
                         AS pix(geom, val, x, y)
                WHERE source.rast IS NOT NULL
            """
            cur.execute(query, (w, s, e, n, band))
        rows = cur.fetchall()
        cur.close()

        if not rows:
            logger.info("EMPTY tile %s — no source pixels", tile_id)
            return None

        # Source pixel half-dimensions for block filling
        src_sx = abs(rows[0][0])
        src_sy = abs(rows[0][1])
        half_sx = src_sx / 2.0
        half_sy = src_sy / 2.0

        tile_w = e - w
        tile_h = n - s

        # 256x256 RGBA buffer — initialized to all zeros (transparent)
        buf = bytearray(256 * 256 * 4)
        pixel_count = 0

        for _, _, lon, lat, val in rows:
            if val is None:
                continue
            if no_data_value is not None and val == no_data_value:
                continue

            # Source pixel geographic extent (centroid ± half pixel)
            px_left = lon - half_sx
            px_right = lon + half_sx
            px_top = lat + half_sy
            px_bottom = lat - half_sy

            # Map to tile pixel coordinates [0, 256)
            col0 = int((px_left - w) / tile_w * 256)
            col1 = int(math.ceil((px_right - w) / tile_w * 256))
            row0 = int((n - px_top) / tile_h * 256)
            row1 = int(math.ceil((n - px_bottom) / tile_h * 256))

            # Clip to tile bounds
            col0 = max(0, col0)
            col1 = min(256, col1)
            row0 = max(0, row0)
            row1 = min(256, row1)

            if col0 >= col1 or row0 >= row1:
                continue

            if is_categorical:
                r, g, b, a = _categorical_rgba(val)
            else:
                r, g, b, a = _apply_ramp(val, ramp)
            pixel_count += 1

            # Fill the block of tile pixels this source pixel covers
            for py in range(row0, row1):
                row_offset = py * 256 * 4
                for px in range(col0, col1):
                    idx = row_offset + px * 4
                    buf[idx] = r
                    buf[idx + 1] = g
                    buf[idx + 2] = b
                    buf[idx + 3] = a

        if pixel_count == 0:
            logger.info("EMPTY tile %s — no pixels within bbox", tile_id)
            return None

        png = _make_png(256, 256, bytes(buf))
        logger.info(
            "SERVE tile %s — %d bytes (%d source pixels)",
            tile_id, len(png), pixel_count,
        )
        return png

    except Exception:
        logger.exception("FAIL  tile %s", tile_id)
        return None
    finally:
        con.close()
