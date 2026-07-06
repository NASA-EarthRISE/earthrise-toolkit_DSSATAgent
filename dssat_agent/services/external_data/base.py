"""
Base class for external soil data sources (SoilGrids, SSURGO).

Provides:
  - Coordinate-keyed cache via ExternalSoilLookupCache (5km grid rounding)
  - Per-source TTL (30d for SoilGrids, 90d for SSURGO)
  - Coverage check
  - Standard error response shape
  - Soil-id generation: 10-char alphanumeric (e.g. "SG12abcdef") deterministic
    from rounded coordinates so re-lookups at the same site reuse the same
    StoredSoilProfile row instead of creating duplicates.
  - Unified return shape, identical to lookup_soil_at:
      {
        'dominant':  {'soil_id': 'SG12abcdef', 'description': '...'},
        'alternates': [],
        'layered_profile': {layers: [...], ...},  # only for external sources
        'source': 'soilgrids' | 'ssurgo',
        'in_coverage': bool,
        'cached': bool,
        'error': '...' | None,
      }
"""

import hashlib
import logging
from datetime import datetime, timedelta
from typing import Dict, Optional

logger = logging.getLogger(__name__)


class ExternalSoilSourceError(Exception):
    """Raised when an external soil source request fails permanently."""


class ExternalSoilSource:
    """Abstract base for SoilGrids / SSURGO clients."""

    source_name: str = ''  # 'soilgrids' or 'ssurgo'
    cache_ttl_days: int = 30
    coord_round_decimals: int = 2  # ~1 km grid for SoilGrids ~250m data; ~1km cache hit radius
    soil_id_prefix: str = 'XX'  # 'SG' for SoilGrids, 'US' for SSURGO

    def coverage_check(self, lat: float, lon: float) -> bool:
        """Override in subclasses. Default is global."""
        return True

    def lookup(self, lat: float, lon: float) -> Dict:
        """
        Coordinate-keyed lookup with cache.

        Returns the unified result shape. Errors are returned as data
        (``{'error': '...'}``) rather than raised, so the wizard can degrade
        gracefully and surface the message to the user.
        """
        if not self.coverage_check(lat, lon):
            return self._not_in_coverage(lat, lon)

        lat_r = round(lat, self.coord_round_decimals)
        lon_r = round(lon, self.coord_round_decimals)

        cached = self._get_cached(lat_r, lon_r)
        if cached is not None:
            return self._build_result(
                cached_payload=cached,
                lat=lat_r, lon=lon_r,
                from_cache=True,
            )

        try:
            payload = self._fetch(lat_r, lon_r)
        except ExternalSoilSourceError as e:
            return self._error_result(str(e), lat_r, lon_r)
        except Exception as e:
            logger.warning("%s lookup unexpected error: %s", self.source_name, e, exc_info=True)
            return self._error_result(f"unexpected: {e}", lat_r, lon_r)

        self._save_cache(lat_r, lon_r, payload)
        return self._build_result(
            cached_payload=payload,
            lat=lat_r, lon=lon_r,
            from_cache=False,
        )

    # ----------------------------------------------------------------------
    # Subclass hooks
    # ----------------------------------------------------------------------

    def _fetch(self, lat: float, lon: float) -> Dict:
        """
        Fetch and normalize the raw response from the external service.

        Must return a dict with at least ``layers`` (list of layer dicts in
        the shape ``create_soil_from_texture()`` expects). May include
        additional fields that get passed through to the result.
        """
        raise NotImplementedError

    # ----------------------------------------------------------------------
    # Cache helpers
    # ----------------------------------------------------------------------

    def _get_cached(self, lat_r: float, lon_r: float) -> Optional[Dict]:
        """Return cached response payload if present and not expired."""
        from dssat_agent.models import ExternalSoilLookupCache
        from django.utils import timezone

        try:
            row = ExternalSoilLookupCache.objects.get(
                source=self.source_name,
                lat_rounded=lat_r,
                lon_rounded=lon_r,
            )
        except ExternalSoilLookupCache.DoesNotExist:
            return None

        age = timezone.now() - row.fetched_at
        if age > timedelta(days=self.cache_ttl_days):
            row.delete()
            return None

        return row.response_json

    def _save_cache(self, lat_r: float, lon_r: float, payload: Dict) -> None:
        from dssat_agent.models import ExternalSoilLookupCache

        ExternalSoilLookupCache.objects.update_or_create(
            source=self.source_name,
            lat_rounded=lat_r,
            lon_rounded=lon_r,
            defaults={'response_json': payload},
        )

    # ----------------------------------------------------------------------
    # Soil ID generation + StoredSoilProfile materialization
    # ----------------------------------------------------------------------

    def _generate_soil_id(self, lat_r: float, lon_r: float) -> str:
        """
        Generate a deterministic 10-char alphanumeric DSSAT soil_id keyed by
        rounded coordinate. Same coords → same id → same StoredSoilProfile
        row (no duplicates on re-lookup).
        """
        h = hashlib.sha1(
            f"{self.source_name}:{lat_r:.4f}:{lon_r:.4f}".encode()
        ).hexdigest()
        return f"{self.soil_id_prefix}{h[:8]}".upper()

    def _materialize_profile(self, soil_id: str, payload: Dict, lat_r: float, lon_r: float) -> Dict:
        """
        Create a StoredSoilProfile from a normalized payload, or reuse the
        existing row if one exists for this soil_id. Returns the dict
        representation of the profile.
        """
        from dssat_agent.models import StoredSoilProfile
        from dssat_agent.services.soil_service import (
            create_soil_from_texture,
            get_soil_profile,
        )

        if StoredSoilProfile.objects.filter(soil_id=soil_id).exists():
            return get_soil_profile(soil_id)

        params = {
            'soil_id': soil_id,
            'name': payload.get('name', f'{self.source_name} @ {lat_r},{lon_r}'),
            'country': payload.get('country', ''),
            'site': payload.get('site', ''),
            'lat': lat_r,
            'lon': lon_r,
            'classification': payload.get('classification', ''),
            'scs_family': payload.get('scs_family', ''),
            'salb': payload.get('salb', 0.13),
            'slu1': payload.get('slu1', 6.0),
            'sldr': payload.get('sldr', 0.60),
            'slro': payload.get('slro', 73.0),
            'slnf': payload.get('slnf', 1.0),
            'slpf': payload.get('slpf', 1.0),
            'layers': payload.get('layers', []),
        }
        result = create_soil_from_texture(params)
        if isinstance(result, dict) and 'error' in result:
            logger.warning(
                "%s: failed to materialize StoredSoilProfile %s: %s",
                self.source_name, soil_id, result['error'],
            )
        return result

    # ----------------------------------------------------------------------
    # Result builders
    # ----------------------------------------------------------------------

    def _build_result(self, cached_payload: Dict, lat: float, lon: float, from_cache: bool) -> Dict:
        soil_id = self._generate_soil_id(lat, lon)
        profile = self._materialize_profile(soil_id, cached_payload, lat, lon)

        return {
            'dominant': {
                'soil_id': soil_id,
                'description': cached_payload.get('name')
                    or f'{self.source_name} @ ({lat}, {lon})',
            },
            'alternates': [],
            'layered_profile': cached_payload,
            'source': self.source_name,
            'in_coverage': True,
            'cached': from_cache,
            'error': None,
        }

    def _not_in_coverage(self, lat: float, lon: float) -> Dict:
        return {
            'dominant': None,
            'alternates': [],
            'layered_profile': None,
            'source': self.source_name,
            'in_coverage': False,
            'cached': False,
            'error': None,
        }

    def _error_result(self, message: str, lat: float, lon: float) -> Dict:
        return {
            'dominant': None,
            'alternates': [],
            'layered_profile': None,
            'source': self.source_name,
            'in_coverage': True,
            'cached': False,
            'error': message,
        }
