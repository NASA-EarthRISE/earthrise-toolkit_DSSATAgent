"""
Spatial data service for PostGIS weather rasters and admin boundaries.

Provides direct psycopg2 queries to the shared PostgreSQL database for:
- Admin boundary geometries
- Raster pixel centroid extraction
- Daily weather time series from raster tables
- Grid point generation (bbox, circle, admin boundary)

Uses raw SQL (not Django ORM) because PostGIS raster functions
and the admin/weather tables live in the DataAgent schema.
"""

import logging
import math
import random

import psycopg2
from django.conf import settings

logger = logging.getLogger(__name__)


def get_connection():
    """Raw psycopg2 connection using Django DATABASES['default'] settings."""
    from earthrise_agents_base.db import get_raw_connection
    return get_raw_connection()


def _schema():
    """Return the data agent schema where vectors are loaded."""
    return getattr(settings, 'DATAAGENT_SCHEMA', 'dataagent')


# ---------------------------------------------------------------------------
# Admin boundaries
# ---------------------------------------------------------------------------

def list_admin_units(level='admin1', parent=None):
    """
    List distinct admin unit names at a given level.

    Parameters
    ----------
    level : str
        'country', 'admin1', or 'admin2'.
    parent : str, optional
        Filter children within a parent (e.g. parent country for admin1).

    Returns
    -------
    dict
        {"level": ..., "units": [...]}
    """
    schema = _schema()
    col_map = {'country': 'country', 'admin1': 'admin1', 'admin2': 'admin2'}
    col = col_map.get(level, 'admin1')

    con = get_connection()
    try:
        cur = con.cursor()
        if parent and level != 'country':
            parent_col = 'country' if level == 'admin1' else 'admin1'
            query = f"SELECT DISTINCT {col} FROM {schema}.admin WHERE {parent_col} = %s ORDER BY {col}"
            cur.execute(query, (parent,))
        else:
            query = f"SELECT DISTINCT {col} FROM {schema}.admin ORDER BY {col}"
            cur.execute(query)
        rows = cur.fetchall()
        cur.close()
        return {"level": level, "parent": parent, "units": [r[0] for r in rows if r[0]]}
    finally:
        con.close()


def get_admin_boundary(admin_name, level='admin1'):
    """
    Get the PostGIS geometry for an admin boundary.

    Returns GeoJSON-serializable geometry dict.
    """
    schema = _schema()
    col_map = {'country': 'country', 'admin1': 'admin1', 'admin2': 'admin2'}
    col = col_map.get(level, 'admin1')

    con = get_connection()
    try:
        cur = con.cursor()
        query = f"""
            SELECT ST_AsGeoJSON(ST_Union(geom))
            FROM {schema}.admin
            WHERE {col} = %s
        """
        cur.execute(query, (admin_name,))
        row = cur.fetchone()
        cur.close()
        if not row or not row[0]:
            raise ValueError(f"Admin boundary not found: {admin_name} (level={level})")
        import json
        return json.loads(row[0])
    finally:
        con.close()


def get_admin_boundaries_geojson(level='admin1', parent=None):
    """
    Return all admin boundaries at a given level as a GeoJSON FeatureCollection.

    Parameters
    ----------
    level : str
        'country', 'admin1', or 'admin2'.
    parent : str, optional
        Filter to children of a parent admin unit.

    Returns
    -------
    dict
        GeoJSON FeatureCollection.
    """
    schema = _schema()
    level_map = {'country': 0, 'admin1': 1, 'admin2': 2}
    admin_level = level_map.get(level, 1)

    con = get_connection()
    try:
        cur = con.cursor()
        if parent:
            parent_col = 'country' if admin_level == 1 else 'admin1'
            query = f"""
                SELECT country, admin1, admin2, admin_level,
                       ST_AsGeoJSON(geom)
                FROM {schema}.admin
                WHERE admin_level = %s AND {parent_col} = %s
                ORDER BY COALESCE(admin2, admin1, country)
            """
            cur.execute(query, (admin_level, parent))
        else:
            query = f"""
                SELECT country, admin1, admin2, admin_level,
                       ST_AsGeoJSON(geom)
                FROM {schema}.admin
                WHERE admin_level = %s
                ORDER BY COALESCE(admin2, admin1, country)
            """
            cur.execute(query, (admin_level,))

        import json as _json
        features = []
        for row in cur.fetchall():
            country, admin1, admin2, alevel, geom_json = row
            if not geom_json:
                continue
            features.append({
                "type": "Feature",
                "properties": {
                    "country": country,
                    "admin1": admin1,
                    "admin2": admin2,
                    "admin_level": alevel,
                },
                "geometry": _json.loads(geom_json),
            })
        cur.close()
        return {"type": "FeatureCollection", "features": features}
    finally:
        con.close()


def get_admin_sub_unit_centroids(parent_name, parent_level='admin1', child_level='admin2'):
    """
    Get centroids of all child admin units within a parent.

    Example: get_admin_sub_unit_centroids('Alabama', 'admin1', 'admin2')
    returns [{'name': 'Autauga', 'lat': 32.53, 'lon': -86.64}, ...]

    Parameters
    ----------
    parent_name : str
        Name of the parent admin unit (e.g. 'Alabama').
    parent_level : str
        Level of the parent ('country' or 'admin1').
    child_level : str
        Level of the children to return ('admin1' or 'admin2').

    Returns
    -------
    list of dict
        [{'name': str, 'lat': float, 'lon': float}, ...]
    """
    schema = _schema()
    parent_col_map = {'country': 'country', 'admin1': 'admin1'}
    child_col_map = {'admin1': 'admin1', 'admin2': 'admin2'}
    level_num_map = {'admin1': 1, 'admin2': 2}

    parent_col = parent_col_map.get(parent_level, 'admin1')
    child_col = child_col_map.get(child_level, 'admin2')
    child_level_num = level_num_map.get(child_level, 2)

    con = get_connection()
    try:
        cur = con.cursor()
        query = f"""
            SELECT {child_col},
                   ST_Y(ST_Centroid(ST_Union(geom))),
                   ST_X(ST_Centroid(ST_Union(geom)))
            FROM {schema}.admin
            WHERE {parent_col} = %s AND admin_level = %s
            GROUP BY {child_col}
            ORDER BY {child_col}
        """
        cur.execute(query, (parent_name, child_level_num))
        rows = cur.fetchall()
        cur.close()
        return [
            {'name': r[0], 'lat': round(float(r[1]), 4), 'lon': round(float(r[2]), 4)}
            for r in rows if r[0]
        ]
    finally:
        con.close()


def get_admin_centroid(admin_name, level='admin2'):
    """
    Get the centroid (lat, lon) of a single admin unit.

    Parameters
    ----------
    admin_name : str
        Name of the admin unit.
    level : str
        'country', 'admin1', or 'admin2'.

    Returns
    -------
    dict
        {'lat': float, 'lon': float}
    """
    schema = _schema()
    col_map = {'country': 'country', 'admin1': 'admin1', 'admin2': 'admin2'}
    col = col_map.get(level, 'admin2')

    con = get_connection()
    try:
        cur = con.cursor()
        query = f"""
            SELECT ST_Y(ST_Centroid(ST_Union(geom))),
                   ST_X(ST_Centroid(ST_Union(geom)))
            FROM {schema}.admin
            WHERE {col} = %s
        """
        cur.execute(query, (admin_name,))
        row = cur.fetchone()
        cur.close()
        if not row or row[0] is None:
            raise ValueError(f"Admin unit not found: {admin_name} (level={level})")
        return {'lat': round(float(row[0]), 4), 'lon': round(float(row[1]), 4)}
    finally:
        con.close()


def get_weather_grid_points(geometry_geojson, weather_prefix, ref_date):
    """
    Extract raster pixel centroids within a geometry.

    Uses ST_PixelAsCentroids(ST_Clip(rast, geom)) on the rain raster table.

    Parameters
    ----------
    geometry_geojson : dict
        GeoJSON geometry object.
    weather_prefix : str
        Raster table prefix (e.g. 'power', 'era5').
    ref_date : str
        Reference date to identify raster tiles (YYYY-MM-DD).

    Returns
    -------
    list of (lon, lat) tuples
    """
    schema = _schema()
    import json
    geom_str = json.dumps(geometry_geojson)

    con = get_connection()
    try:
        cur = con.cursor()
        query = f"""
            WITH pts AS (
                SELECT (ST_PixelAsCentroids(
                    ST_Clip(wt.rast, ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326))
                )).geom AS geom
                FROM {schema}.{weather_prefix}_rain AS wt
                WHERE fdate = %s
                AND ST_Intersects(wt.rast, ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326))
            )
            SELECT ROUND(ST_X(geom)::numeric, 4), ROUND(ST_Y(geom)::numeric, 4)
            FROM pts
        """
        cur.execute(query, (geom_str, ref_date, geom_str))
        rows = cur.fetchall()
        cur.close()
        return [(float(r[0]), float(r[1])) for r in rows]
    finally:
        con.close()


def get_weather_for_point(weather_prefix, lon, lat, start_date, end_date):
    """
    Extract daily weather time series for a point from 4 raster tables.

    Queries {prefix}_tmax, _tmin, _rain, _srad using ST_NearestValue.

    Parameters
    ----------
    weather_prefix : str
        Raster table prefix.
    lon, lat : float
        Point coordinates.
    start_date, end_date : str
        Date range (YYYY-MM-DD).

    Returns
    -------
    list of dict
        [{"date": "2024-04-01", "tmax": 28.0, "tmin": 15.0, "rain": 0.0, "srad": 18.5}, ...]
    """
    schema = _schema()
    variables = ['tmax', 'tmin', 'rain', 'srad']

    con = get_connection()
    try:
        cur = con.cursor()
        # Each per-variable CTE collapses overlapping raster tiles to one row
        # per date and prefers non-sentinel values: the regional_json_api
        # ingestion sometimes leaves NoData as the IEEE Float32 sentinel
        # ~-3.4e38 instead of NULL, so a naive DISTINCT ON or LATERAL LIMIT 1
        # can pick the sentinel-only tile. The ``(val IS NULL OR val<-1e30)``
        # ordering pushes valid values first; ``DESC NULLS LAST`` then
        # disambiguates the rare two-valid-values case.
        query = f"""
            WITH tmax_v AS (
                SELECT DISTINCT ON (fdate) fdate, val FROM (
                    SELECT fdate,
                           ST_NearestValue(rast, ST_SetSRID(ST_MakePoint(%s, %s), 4326)) AS val
                    FROM {schema}.{weather_prefix}_tmax
                    WHERE fdate BETWEEN %s AND %s
                ) t
                ORDER BY fdate, (val IS NULL OR val < -1e30) ASC, val DESC NULLS LAST
            ),
            tmin_v AS (
                SELECT DISTINCT ON (fdate) fdate, val FROM (
                    SELECT fdate,
                           ST_NearestValue(rast, ST_SetSRID(ST_MakePoint(%s, %s), 4326)) AS val
                    FROM {schema}.{weather_prefix}_tmin
                    WHERE fdate BETWEEN %s AND %s
                ) t
                ORDER BY fdate, (val IS NULL OR val < -1e30) ASC, val DESC NULLS LAST
            ),
            rain_v AS (
                SELECT DISTINCT ON (fdate) fdate, val FROM (
                    SELECT fdate,
                           ST_NearestValue(rast, ST_SetSRID(ST_MakePoint(%s, %s), 4326)) AS val
                    FROM {schema}.{weather_prefix}_rain
                    WHERE fdate BETWEEN %s AND %s
                ) t
                ORDER BY fdate, (val IS NULL OR val < -1e30) ASC, val DESC NULLS LAST
            ),
            srad_v AS (
                SELECT DISTINCT ON (fdate) fdate, val FROM (
                    SELECT fdate,
                           ST_NearestValue(rast, ST_SetSRID(ST_MakePoint(%s, %s), 4326)) AS val
                    FROM {schema}.{weather_prefix}_srad
                    WHERE fdate BETWEEN %s AND %s
                ) t
                ORDER BY fdate, (val IS NULL OR val < -1e30) ASC, val DESC NULLS LAST
            )
            SELECT tx.fdate, tx.val AS tmax, tn.val AS tmin, rn.val AS rain, sr.val AS srad
            FROM tmax_v tx
            LEFT JOIN tmin_v tn USING (fdate)
            LEFT JOIN rain_v rn USING (fdate)
            LEFT JOIN srad_v sr USING (fdate)
            ORDER BY tx.fdate
        """
        cur.execute(query, (
            lon, lat, start_date, end_date,
            lon, lat, start_date, end_date,
            lon, lat, start_date, end_date,
            lon, lat, start_date, end_date,
        ))
        rows = cur.fetchall()
        cur.close()

        # Treat PostGIS Float32 NoData sentinels (~-3.4e38) as missing — the
        # rasters loaded by the regional_json_api format don't always set a
        # NODATA value, so ``ST_NearestValue`` returns the raw pixel sentinel
        # instead of NULL.
        def _scrub(v):
            if v is None:
                return None
            try:
                fv = float(v)
            except (TypeError, ValueError):
                return None
            if fv < -1e30 or fv > 1e30:
                return None
            return round(fv, 1)

        records = []
        for row in rows:
            fdate, tmax, tmin, rain, srad = row
            tmax_v = _scrub(tmax)
            tmin_v = _scrub(tmin)
            rain_v = _scrub(rain)
            srad_v = _scrub(srad)
            if tmax_v is None and tmin_v is None and rain_v is None and srad_v is None:
                continue
            records.append({
                'date': fdate.isoformat() if hasattr(fdate, 'isoformat') else str(fdate),
                'tmax': tmax_v,
                'tmin': tmin_v,
                'rain': rain_v,
                'srad': srad_v,
            })
        return records
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Grid generation
# ---------------------------------------------------------------------------

def generate_grid_from_bbox(min_lat, max_lat, min_lon, max_lon, spacing_deg=0.1):
    """
    Generate regular grid points within a bounding box.

    Returns list of (lon, lat) tuples.
    """
    points = []
    lat = min_lat
    while lat <= max_lat:
        lon = min_lon
        while lon <= max_lon:
            points.append((round(lon, 4), round(lat, 4)))
            lon += spacing_deg
        lat += spacing_deg
    return points


def generate_grid_from_circle(center_lat, center_lon, radius_km, grid_spacing):
    """
    Generate a regular lattice inside a circle.

    Lays down a lat/lon grid at `grid_spacing` (degrees) over the circle's
    bounding box, keeping only points inside the elliptical (degree-space)
    circle. Deterministic; the sampling step downstream picks `nens` from
    the result.

    Returns list of (lon, lat) tuples.
    """
    radius_deg_lat = radius_km / 111.0
    radius_deg_lon = radius_km / (111.0 * math.cos(math.radians(center_lat)))

    points = []
    lat = center_lat - radius_deg_lat
    while lat <= center_lat + radius_deg_lat:
        lon = center_lon - radius_deg_lon
        while lon <= center_lon + radius_deg_lon:
            dlat = lat - center_lat
            dlon = lon - center_lon
            if (dlat / radius_deg_lat) ** 2 + (dlon / radius_deg_lon) ** 2 <= 1.0:
                points.append((round(lon, 4), round(lat, 4)))
            lon += grid_spacing
        lat += grid_spacing

    return points


def random_points_in_circle(center_lat, center_lon, radius_km, n_points):
    """Uniform random sampling within a circle.

    Used by Monte Carlo's ``random`` sampling strategy as the
    counterpart to the systematic-lattice ``generate_grid_from_circle``.

    Returns list of (lon, lat) tuples of length ``n_points`` exactly.
    """
    if n_points <= 0:
        return []
    radius_deg_lat = radius_km / 111.0
    radius_deg_lon = radius_km / (111.0 * math.cos(math.radians(center_lat)))
    out = []
    # Rejection-sample inside the elliptical-in-degrees circle. Average
    # acceptance ratio is π/4 ≈ 0.785, so the bound is generous.
    while len(out) < n_points:
        dlat = random.uniform(-radius_deg_lat, radius_deg_lat)
        dlon = random.uniform(-radius_deg_lon, radius_deg_lon)
        if (dlat / radius_deg_lat) ** 2 + (dlon / radius_deg_lon) ** 2 <= 1.0:
            out.append((round(center_lon + dlon, 4),
                        round(center_lat + dlat, 4)))
    return out


def random_points_in_bbox(min_lat, max_lat, min_lon, max_lon, n_points):
    """Uniform random sampling within a bbox. Returns N (lon, lat) tuples."""
    if n_points <= 0:
        return []
    out = []
    for _ in range(n_points):
        lat = random.uniform(min_lat, max_lat)
        lon = random.uniform(min_lon, max_lon)
        out.append((round(lon, 4), round(lat, 4)))
    return out


def _admin_where(level, admin_name, parent_admin1=None, parent_country=None):
    """Build the (where_sql, params) tuple for an admin polygon lookup.

    Parent qualifiers disambiguate names that aren't unique on their own
    (e.g. "Jefferson" county exists in 25+ US states; "Washington" exists
    as both a US state and an English town). When the caller supplies the
    full hierarchy we filter to the single matching polygon.
    """
    col_map = {'country': 'country', 'admin1': 'admin1', 'admin2': 'admin2'}
    col = col_map.get(level, 'admin1')
    where = [f"{col} = %s"]
    args = [admin_name]
    if parent_admin1 and level == 'admin2':
        where.append("admin1 = %s")
        args.append(parent_admin1)
    if parent_country and level in ('admin1', 'admin2'):
        where.append("country = %s")
        args.append(parent_country)
    return ' AND '.join(where), args


def random_points_in_admin(admin_name, level, n_points,
                           parent_admin1=None, parent_country=None):
    """Uniform random sampling within an admin polygon.

    Uses PostGIS ``ST_GeneratePoints`` to draw exactly ``n_points`` points
    that are guaranteed to fall inside the union geometry of the named
    admin unit. ``parent_admin1`` / ``parent_country`` disambiguate names
    that aren't unique (see ``_admin_where``).
    """
    if n_points <= 0:
        return []
    schema = _schema()
    where_sql, where_args = _admin_where(
        level, admin_name, parent_admin1, parent_country)

    con = get_connection()
    try:
        cur = con.cursor()
        query = f"""
            WITH poly AS (
                SELECT ST_Union(geom) AS g
                FROM {schema}.admin
                WHERE {where_sql}
            ),
            pts AS (
                SELECT (ST_DumpPoints(ST_GeneratePoints(g, %s))).geom AS pt
                FROM poly
            )
            SELECT ST_X(pt)::float, ST_Y(pt)::float FROM pts
        """
        cur.execute(query, (*where_args, int(n_points)))
        rows = cur.fetchall()
        cur.close()
        return [(round(float(r[0]), 4), round(float(r[1]), 4)) for r in rows]
    finally:
        con.close()


def generate_grid_from_admin(admin_name, level, grid_spacing,
                             weather_prefix=None, ref_date=None,
                             parent_admin1=None, parent_country=None):
    """
    Generate a regular lattice over an admin boundary.

    Builds a lattice at `grid_spacing` (degrees) over the admin unit's
    bounding box and clips to the polygon using PostGIS `ST_Contains`.
    ``parent_admin1`` / ``parent_country`` disambiguate names that aren't
    unique (see ``_admin_where``).

    `weather_prefix` and `ref_date` are accepted but ignored (kept so
    existing callers pass without error).

    Returns list of (lon, lat) tuples.
    """
    schema = _schema()
    spacing = float(grid_spacing)
    where_sql, where_args = _admin_where(
        level, admin_name, parent_admin1, parent_country)

    con = get_connection()
    try:
        cur = con.cursor()
        query = f"""
            WITH poly AS (
                SELECT ST_Union(geom) AS g
                FROM {schema}.admin
                WHERE {where_sql}
            )
            SELECT x::float, y::float
            FROM poly,
                 generate_series(
                     ST_XMin(g)::numeric,
                     ST_XMax(g)::numeric,
                     %s::numeric
                 ) AS x,
                 generate_series(
                     ST_YMin(g)::numeric,
                     ST_YMax(g)::numeric,
                     %s::numeric
                 ) AS y
            WHERE ST_Contains(g, ST_SetSRID(ST_MakePoint(x, y), 4326))
        """
        cur.execute(query, (*where_args, spacing, spacing))
        rows = cur.fetchall()
        cur.close()
        return [(round(float(r[0]), 4), round(float(r[1]), 4)) for r in rows]
    finally:
        con.close()
