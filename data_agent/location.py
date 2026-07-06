"""
Geospatial location-resolution utilities (owned by data_agent).

Domain-agnostic geocoding: resolves place strings to bounding boxes /
lat-lon. Lives in data_agent so all geospatial code has a single home and
consuming sub-agents import it via ``data_agent.location``. Do NOT make
data_agent import a consumer for location data — that would re-create the
circular dependency this placement was designed to avoid.

Supports named counties (via county_boundaries.json) and any-location via
lat/lon extraction.
"""

import json
import logging
import os
import re
from typing import Dict, Optional

logger = logging.getLogger(__name__)

_BOUNDARIES_CACHE: Optional[Dict] = None


def _load_boundaries() -> Dict:
    """Load and cache county_boundaries.json."""
    global _BOUNDARIES_CACHE
    if _BOUNDARIES_CACHE is not None:
        return _BOUNDARIES_CACHE

    possible_paths = [
        os.path.join(os.path.dirname(__file__), "data", "county_boundaries.json"),
        "county_boundaries.json",
        os.path.join(os.path.dirname(__file__), "county_boundaries.json"),
        os.path.join(os.path.dirname(__file__), "..", "county_boundaries.json"),
    ]

    for path in possible_paths:
        if os.path.exists(path):
            with open(path, 'r') as f:
                _BOUNDARIES_CACHE = json.load(f)
            logger.info(f"Loaded county boundaries from: {path} ({len(_BOUNDARIES_CACHE)} counties)")
            return _BOUNDARIES_CACHE

    logger.warning("Could not find county_boundaries.json")
    _BOUNDARIES_CACHE = {}
    return _BOUNDARIES_CACHE


def resolve_location(location_str: str) -> Dict:
    """
    Resolve any location string to a location dict.

    Tries a named-region (county) boundary match first, then falls back to
    extracting lat/lon from the string. Returns an empty dict (no `lat`/
    `lon` keys) when the input can't be resolved — callers must check
    `result.get('lat')` and surface an error rather than silently running
    with defaults. Returning an empty dict (rather than a default centroid)
    forces callers to detect unresolved input instead of silently mapping
    an out-of-region place to the default region.

    Returns:
        dict with keys: name, lat, lon, bbox, country when resolved;
        {'resolved': False, 'input': location_str} when not.
    """
    if not location_str:
        return {"resolved": False, "input": "", "reason": "empty location"}

    # Try a named-region (county) boundary match
    boundaries = _load_boundaries()
    key = location_str.lower().strip()

    for bkey, entry in boundaries.items():
        if key == bkey or key == entry['name'].lower():
            bbox = entry['bbox']
            lat = (bbox['north'] + bbox['south']) / 2
            lon = (bbox['east'] + bbox['west']) / 2
            return {
                'name': f"{entry['name']} County",
                'lat': lat,
                'lon': lon,
                'bbox': bbox,
                'country': 'US',
            }

    # Prefix match
    for bkey, entry in boundaries.items():
        if bkey.startswith(key) or key.startswith(bkey):
            bbox = entry['bbox']
            lat = (bbox['north'] + bbox['south']) / 2
            lon = (bbox['east'] + bbox['west']) / 2
            return {
                'name': f"{entry['name']} County",
                'lat': lat,
                'lon': lon,
                'bbox': bbox,
                'country': 'US',
            }

    # Try to extract lat/lon from the string
    latlon_pattern = r'(-?\d+\.?\d*)\s*[,\s]\s*(-?\d+\.?\d*)'
    match = re.search(latlon_pattern, location_str)
    if match:
        lat = float(match.group(1))
        lon = float(match.group(2))
        # Simple validation
        if -90 <= lat <= 90 and -180 <= lon <= 180:
            bbox = {
                'west': lon - 0.25,
                'south': lat - 0.25,
                'east': lon + 0.25,
                'north': lat + 0.25,
            }
            return {
                'name': location_str,
                'lat': lat,
                'lon': lon,
                'bbox': bbox,
                'country': 'US',
            }

    # No match — return an unresolved sentinel. Callers must handle.
    logger.warning("Could not resolve location %r; returning unresolved sentinel.", location_str)
    return {
        "resolved": False,
        "input": location_str,
        "reason": "no match in county boundaries and no lat/lon detected",
    }
