"""
Weather data conversion service.

Converts JSON weather records to DSSATTools WeatherStation objects
and detects missing date ranges.
"""

import logging
from datetime import date, datetime, timedelta

from DSSATTools.weather import WeatherStation, WeatherRecord

logger = logging.getLogger(__name__)


def _parse_date(val):
    """Parse a date string or return date object."""
    if isinstance(val, date) and not isinstance(val, datetime):
        return val
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, str):
        for fmt in ('%Y-%m-%d', '%Y%m%d'):
            try:
                return datetime.strptime(val, fmt).date()
            except ValueError:
                continue
    return None


def build_weather_station(weather_data):
    """
    Convert JSON weather data to a DSSATTools WeatherStation object.

    Parameters
    ----------
    weather_data : dict
        {
            "station": {"lat": ..., "lon": ..., "elev": ..., "insi": "ABCD", ...},
            "records": [
                {"date": "2024-04-01", "srad": 18.5, "tmax": 28.0, "tmin": 15.0, "rain": 0.0, ...},
                ...
            ]
        }

    Returns
    -------
    WeatherStation
    """
    station = weather_data.get('station', {})
    records_data = weather_data.get('records', [])

    if not records_data:
        raise ValueError("weather_data.records is empty")

    records = []
    skipped = 0
    for rec in records_data:
        rec_date = _parse_date(rec.get('date'))
        if rec_date is None:
            raise ValueError(f"Invalid date in weather record: {rec.get('date')}")

        # Skip records where any required field is None (e.g. unpublished dates)
        required = ('srad', 'tmax', 'tmin', 'rain')
        if any(rec.get(f) is None for f in required):
            skipped += 1
            continue

        kwargs = {
            'date': rec_date,
            'srad': float(rec['srad']),
            'tmax': float(rec['tmax']),
            'tmin': float(rec['tmin']),
            'rain': float(rec['rain']),
        }
        # Optional fields
        for opt_field in ('dewp', 'wind', 'par', 'evap', 'rhum'):
            if opt_field in rec and rec[opt_field] is not None:
                kwargs[opt_field] = float(rec[opt_field])

        records.append(WeatherRecord(**kwargs))

    if skipped:
        logger.warning("Skipped %d weather records with None values in required fields", skipped)

    if not records:
        raise ValueError("No valid weather records after filtering None values")

    ws_kwargs = {
        'table': records,
        'lat': float(station.get('lat', 0)),
        'long': float(station.get('lon', station.get('long', 0))),
        'insi': station.get('insi', 'WSTA'),
    }
    if station.get('elev') is not None:
        ws_kwargs['elev'] = float(station['elev'])
    if station.get('tav') is not None:
        ws_kwargs['tav'] = float(station['tav'])
    if station.get('amp') is not None:
        ws_kwargs['amp'] = float(station['amp'])
    if station.get('refht') is not None:
        ws_kwargs['refht'] = float(station['refht'])
    if station.get('wndht') is not None:
        ws_kwargs['wndht'] = float(station['wndht'])

    return WeatherStation(**ws_kwargs)


def check_weather_coverage(weather_data, sdate, nyers=1):
    """
    Check if weather data covers the required simulation period.

    Parameters
    ----------
    weather_data : dict
        Same format as build_weather_station input.
    sdate : date or str
        Simulation start date.
    nyers : int
        Number of years to simulate.

    Returns
    -------
    dict
        {
            "complete": True/False,
            "missing_dates": [...],
            "weather_start_needed": "YYYY-MM-DD",
            "weather_end_needed": "YYYY-MM-DD",
            "data_start": "YYYY-MM-DD",
            "data_end": "YYYY-MM-DD",
        }
    """
    sim_start = _parse_date(sdate)
    if sim_start is None:
        return {"error": "Invalid sdate"}

    # Simulation needs weather from sdate to sdate + nyers years
    sim_end = date(sim_start.year + nyers, sim_start.month, sim_start.day) - timedelta(days=1)

    records = weather_data.get('records', [])
    if not records:
        all_dates = set()
    else:
        all_dates = set()
        for rec in records:
            d = _parse_date(rec.get('date'))
            if d:
                all_dates.add(d)

    # Find all required dates
    required_dates = set()
    current = sim_start
    while current <= sim_end:
        required_dates.add(current)
        current += timedelta(days=1)

    missing = sorted(required_dates - all_dates)

    data_start = min(all_dates).isoformat() if all_dates else None
    data_end = max(all_dates).isoformat() if all_dates else None

    return {
        "complete": len(missing) == 0,
        "missing_count": len(missing),
        "missing_dates": [d.isoformat() for d in missing[:100]],  # Cap at 100
        "total_missing": len(missing),
        "weather_start_needed": sim_start.isoformat(),
        "weather_end_needed": sim_end.isoformat(),
        "data_start": data_start,
        "data_end": data_end,
    }


def resolve_weather(params, lat=None, lon=None, elev=None):
    """
    Resolve weather data from either inline JSON or PostGIS rasters.

    If params['weather_data'] exists, return it as-is.
    Otherwise, use params['weather_source'] + lat/lon + simulation date range
    to query PostGIS raster tables via spatial_data_service.

    Parameters
    ----------
    params : dict
        Must contain either 'weather_data' or 'weather_source'.
    lat, lon, elev : float, optional
        Override coordinates (used by MC for per-point weather).

    Returns
    -------
    dict
        weather_data in standard format: {"station": {...}, "records": [...]}
    """
    # Path 1: Inline weather data provided
    weather_data = params.get('weather_data')
    if weather_data:
        return weather_data

    # Path 2: Query PostGIS rasters
    weather_source = params.get('weather_source')
    if not weather_source:
        raise ValueError("Either weather_data or weather_source is required")

    from . import spatial_data_service

    # Determine coordinates
    field = params.get('field', {})
    pt_lat = lat or field.get('lat') or field.get('latitude') or params.get('latitude')
    pt_lon = lon or field.get('lon') or field.get('longitude') or params.get('longitude')
    pt_elev = elev or field.get('elev') or field.get('elevation') or params.get('elevation', 0)
    if pt_lat is None or pt_lon is None:
        raise ValueError("lat/lon required when using weather_source")

    # Determine date range from simulation_controls
    sc = params.get('simulation_controls', {})
    sdate = _parse_date(sc.get('sdate'))
    nyers = sc.get('nyers', 1)
    if not sdate:
        raise ValueError("simulation_controls.sdate required for weather_source")

    end_date = date(sdate.year + nyers, sdate.month, sdate.day) - timedelta(days=1)

    # Query raster tables
    records = spatial_data_service.get_weather_for_point(
        weather_prefix=weather_source,
        lon=pt_lon, lat=pt_lat,
        start_date=sdate.isoformat(),
        end_date=end_date.isoformat(),
    )

    if not records:
        raise ValueError(
            f"No weather data found for ({pt_lat}, {pt_lon}) "
            f"from {weather_source} between {sdate} and {end_date}"
        )

    return {
        "station": {
            "lat": pt_lat,
            "lon": pt_lon,
            "elev": pt_elev or 0,
            "insi": weather_source[:4].upper(),
        },
        "records": records,
    }


def validate_weather_records(records):
    """Validate weather records are within reasonable physical bounds."""
    errors = []
    for i, rec in enumerate(records):
        prefix = f"weather[{i}]"

        srad = rec.get('srad')
        if srad is not None and (srad < 0 or srad > 50):
            errors.append(f"{prefix}.srad ({srad}) should be 0-50 MJ/m2/day")

        tmax = rec.get('tmax')
        tmin = rec.get('tmin')
        if tmax is not None and tmin is not None and tmax < tmin:
            errors.append(f"{prefix}: tmax ({tmax}) < tmin ({tmin})")

        rain = rec.get('rain')
        if rain is not None and rain < 0:
            errors.append(f"{prefix}.rain ({rain}) must be non-negative")

    return errors
