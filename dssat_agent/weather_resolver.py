"""
Weather data resolution for DSSAT experiments.

Handles the complete weather data lifecycle:
1. Check if weather data exists in the database
2. Trigger a fetch if data is missing
3. Poll until data is available
4. Query and format weather records for simulation

The dssat_agent owns its weather-data lifecycle here, sourcing data from
data_agent (via data_agent.services / the data A2A skills).
"""

import logging
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# Timeout and polling config
DATA_FETCH_TIMEOUT = 300  # seconds
DATA_FETCH_POLL_INTERVAL = 10  # seconds

# Source name → PostGIS table prefix
SOURCE_TO_PREFIX = {
    'nasa_power': 'power',
    'power': 'power',
    'era5': 'era5',
    'agera5': 'agera5',
    'chirps_chirts_era5': 'chirps_chirts_era5',
    'chirps_chirts_power': 'chirps_chirts_power',
    'chirps_chirts_agera5': 'chirps_chirts_agera5',
    'chirps_era5': 'chirps_era5',
    'chirps_power': 'chirps_power',
    'chirps_agera5': 'chirps_agera5',
}

DEFAULT_WEATHER_SOURCE = 'nasa_power'


def resolve_weather(
    latitude: float,
    longitude: float,
    start_date: str,
    end_date: str,
    weather_source: Optional[str] = None,
    bbox: Optional[Dict] = None,
    elevation: float = 100.0,
) -> Optional[Dict]:
    """
    Resolve weather data for a simulation.

    Checks availability, fetches if missing, queries and formats for DSSAT.

    Args:
        latitude: Point latitude.
        longitude: Point longitude.
        start_date: Weather start date (YYYY-MM-DD).
        end_date: Weather end date (YYYY-MM-DD).
        weather_source: Data source name (default: nasa_power).
        bbox: Bounding box for fetch {west, south, east, north}.
        elevation: Station elevation in meters.

    Returns:
        Weather data dict formatted for SimulationAgent, or None if unavailable.
    """
    from data_agent.services import check_availability, fetch_data, query_data

    source = weather_source or DEFAULT_WEATHER_SOURCE
    prefix = SOURCE_TO_PREFIX.get(source, source)

    if not bbox:
        bbox = {
            'west': longitude - 0.25,
            'south': latitude - 0.25,
            'east': longitude + 0.25,
            'north': latitude + 0.25,
        }

    # 1. Check existing data
    try:
        result = query_data(
            source=prefix,
            variables=['tmax', 'tmin', 'rain', 'srad'],
            lat=latitude,
            lon=longitude,
            start_date=start_date,
            end_date=end_date,
        )
        records = result.get('records', [])
        if records:
            return _format_weather(records, latitude, longitude, elevation)
    except Exception as e:
        logger.warning("Weather query failed: %s", e)

    # 2. Trigger fetch
    try:
        fetch_data(
            source=source,
            bbox=bbox,
            start_date=start_date,
            end_date=end_date,
        )
        logger.info("Triggered weather fetch for %s", source)
    except Exception as e:
        logger.error("Weather fetch failed: %s", e)
        return None

    # 3. Poll for availability
    for attempt in range(3):
        time.sleep(DATA_FETCH_POLL_INTERVAL)
        try:
            result = query_data(
                source=prefix,
                variables=['tmax', 'tmin', 'rain', 'srad'],
                lat=latitude,
                lon=longitude,
                start_date=start_date,
                end_date=end_date,
            )
            records = result.get('records', [])
            if records:
                return _format_weather(records, latitude, longitude, elevation)
        except Exception:
            pass

    logger.warning("Weather data not available after polling")
    return None


def _format_weather(
    records: List[Dict],
    lat: float,
    lon: float,
    elev: float,
) -> Dict:
    """Format weather records for SimulationAgent consumption."""
    formatted_records = []
    for r in records:
        formatted_records.append({
            'date': r.get('fdate', r.get('date', '')),
            'srad': r.get('srad', 0),
            'tmax': r.get('tmax', 0),
            'tmin': r.get('tmin', 0),
            'rain': r.get('rain', 0),
        })

    return {
        'station': {
            'lat': lat,
            'lon': lon,
            'elev': elev,
            'insi': 'DSAT',
        },
        'records': formatted_records,
    }
