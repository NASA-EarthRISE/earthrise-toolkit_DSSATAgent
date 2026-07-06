"""
SoilGrids 2.0 REST client.

Queries the ISRIC SoilGrids 2.0 service for layered soil properties at a
point and converts them into the layer-dict shape that
``soil_service.create_soil_from_texture()`` expects.

Reference: Poggio et al. 2021, "SoilGrids 2.0: producing soil information
for the globe with quantified spatial uncertainty", SOIL journal.
DOI: 10.5194/soil-7-217-2021

Endpoint: https://rest.isric.org/soilgrids/v2.0/properties/query

Unit conversions (the API returns mapping units, not natural units):
  - clay, sand, silt are returned as ``g/kg`` × 10  → divide by 10 for percent
  - bulk density (bdod) is returned as ``cg/cm^3`` × 100  → divide by 100 for g/cm^3
  - organic carbon (soc) is returned as ``dg/kg`` × 10  → divide by 100 for percent
  - depths are: 0-5, 5-15, 15-30, 30-60, 60-100, 100-200 cm

The wrapper picks the ``mean`` aggregation for each property at each depth.
The 6 SoilGrids depths happen to be exactly the standard DSSAT layer
schedule, so no interpolation is needed — each depth maps 1:1 to a DSSAT
layer with ``slb`` set to the depth's bottom (5, 15, 30, 60, 100, 200).
"""

import logging
from typing import Dict, List, Optional

from .base import ExternalSoilSource, ExternalSoilSourceError

logger = logging.getLogger(__name__)


SOILGRIDS_URL = 'https://rest.isric.org/soilgrids/v2.0/properties/query'

# SoilGrids depth-bin labels in their API → DSSAT layer bottom depth (cm)
DEPTH_TO_DSSAT_SLB = [
    ('0-5cm',     5),
    ('5-15cm',   15),
    ('15-30cm',  30),
    ('30-60cm',  60),
    ('60-100cm', 100),
    ('100-200cm', 200),
]

# SoilGrids property names → (kind, divisor) for mapping-unit conversion.
# kind ∈ {'pct', 'bdod', 'soc'}
PROPERTY_DECODERS = {
    'clay': ('pct', 10.0),    # g/kg × 10 → percent
    'sand': ('pct', 10.0),
    'silt': ('pct', 10.0),
    'bdod': ('bdod', 100.0),  # cg/cm³ × 100 → g/cm³
    'soc':  ('soc', 100.0),   # dg/kg × 10 → percent (× 10 for the multiplier, × 10 to convert dg/kg → %)
}

REQUEST_TIMEOUT_SECONDS = 8.0


class SoilGridsClient(ExternalSoilSource):
    source_name = 'soilgrids'
    cache_ttl_days = 30
    soil_id_prefix = 'SG'
    coord_round_decimals = 2  # ~1km cache hit radius

    def coverage_check(self, lat: float, lon: float) -> bool:
        # Global product
        return -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0

    def _fetch(self, lat: float, lon: float) -> Dict:
        """Query SoilGrids and normalize into a layered profile dict."""
        import requests

        params = [
            ('lat', f'{lat}'),
            ('lon', f'{lon}'),
            ('value', 'mean'),
        ]
        for prop in PROPERTY_DECODERS.keys():
            params.append(('property', prop))
        for depth_label, _ in DEPTH_TO_DSSAT_SLB:
            params.append(('depth', depth_label))

        try:
            response = requests.get(
                SOILGRIDS_URL,
                params=params,
                timeout=REQUEST_TIMEOUT_SECONDS,
                headers={'User-Agent': 'EarthRISEAgents/1.0 (in-situ soil lookup)'},
            )
        except requests.Timeout:
            raise ExternalSoilSourceError(
                f"SoilGrids request timed out after {REQUEST_TIMEOUT_SECONDS}s"
            )
        except requests.RequestException as e:
            raise ExternalSoilSourceError(f"SoilGrids request failed: {e}")

        if response.status_code != 200:
            raise ExternalSoilSourceError(
                f"SoilGrids returned HTTP {response.status_code}: {response.text[:200]}"
            )

        try:
            payload = response.json()
        except ValueError:
            raise ExternalSoilSourceError("SoilGrids returned non-JSON response")

        layers = self._parse_response(payload)
        if not layers:
            raise ExternalSoilSourceError(
                f"SoilGrids returned no usable data at ({lat}, {lon}) "
                f"(possibly all-NoData ocean or polar region)"
            )

        return {
            'name': f'SoilGrids 2.0 at ({lat}, {lon})',
            'classification': '',
            'layers': layers,
            # Surface params: SoilGrids doesn't ship these. Use sane defaults
            # (matching create_soil_from_texture's defaults if absent).
            'salb': 0.13,
            'slu1': 6.0,
            'sldr': 0.60,
            'slro': 73.0,
            'slnf': 1.0,
            'slpf': 1.0,
        }

    @staticmethod
    def _parse_response(payload: Dict) -> List[Dict]:
        """
        Convert the SoilGrids JSON response into a list of layer dicts in
        the create_soil_from_texture() input shape.

        SoilGrids JSON shape (simplified):
            properties.layers[].name: 'clay' | 'sand' | ...
            properties.layers[].depths[].label: '0-5cm' | ...
            properties.layers[].depths[].values.mean: <int in mapping units>
        """
        sg_layers = (
            payload.get('properties', {}).get('layers', [])
            or []
        )

        # Build {depth_label: {property_name: natural_value}}
        bins: Dict[str, Dict[str, float]] = {
            label: {} for label, _ in DEPTH_TO_DSSAT_SLB
        }

        for layer in sg_layers:
            prop_name = layer.get('name')
            if prop_name not in PROPERTY_DECODERS:
                continue
            kind, divisor = PROPERTY_DECODERS[prop_name]
            for depth_entry in layer.get('depths', []):
                label = depth_entry.get('label')
                if label not in bins:
                    continue
                values = depth_entry.get('values', {}) or {}
                raw = values.get('mean')
                if raw is None:
                    continue
                try:
                    value = float(raw) / divisor
                except (TypeError, ValueError):
                    continue
                bins[label][prop_name] = value

        # Convert to create_soil_from_texture() layer dicts
        result_layers: List[Dict] = []
        for label, slb in DEPTH_TO_DSSAT_SLB:
            data = bins.get(label, {})
            clay = data.get('clay')
            silt = data.get('silt')
            if clay is None or silt is None:
                # Skip layers with missing required texture
                continue
            layer_dict = {
                'depth': float(slb),
                'clay_pct': float(clay),
                'silt_pct': float(silt),
            }
            if 'bdod' in data:
                layer_dict['bulk_density'] = float(data['bdod'])
            if 'soc' in data:
                layer_dict['organic_carbon'] = float(data['soc'])
            result_layers.append(layer_dict)

        return result_layers
