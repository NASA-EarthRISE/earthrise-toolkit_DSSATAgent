"""
SSURGO (USDA NRCS Soil Survey Geographic Database) client.

Queries the USDA Soil Data Access (SDA) web service for layered soil
properties at a coordinate inside the United States. Returns a layered
profile in the same shape as SoilGridsClient so the wizard treats them
uniformly.

Endpoint: https://sdmdataaccess.sc.egov.usda.gov/Tabular/post.rest

Query strategy (two-step):
  1. Spatial point-in-polygon: ``mukey`` at the requested coordinate via
     ``mupolygon`` table.
  2. Tabular join through component → chorizon for the dominant component
     of that map unit, ordered by horizon depth.

SSURGO horizon depths don't match the standard DSSAT layer schedule, so we
re-bin into the standard 5/15/30/60/100/200 cm layers using a depth-weighted
average over the horizons that intersect each DSSAT layer's depth range.
"""

import logging
from typing import Dict, List, Optional, Tuple

from .base import ExternalSoilSource, ExternalSoilSourceError

logger = logging.getLogger(__name__)


SDA_URL = 'https://sdmdataaccess.sc.egov.usda.gov/Tabular/post.rest'

# Standard DSSAT layer schedule (slb = depth at bottom of layer, cm)
DSSAT_LAYER_BOTTOMS = [5, 15, 30, 60, 100, 200]

REQUEST_TIMEOUT_SECONDS = 12.0


class SsurgoClient(ExternalSoilSource):
    source_name = 'ssurgo'
    cache_ttl_days = 90
    soil_id_prefix = 'US'
    coord_round_decimals = 3  # ~110m at the equator — SSURGO is high-res

    def coverage_check(self, lat: float, lon: float) -> bool:
        # Continental US + AK + HI bbox; SDA itself returns no result outside.
        return 18.0 <= lat <= 72.0 and -180.0 <= lon <= -65.0

    def _fetch(self, lat: float, lon: float) -> Dict:
        """Query SDA for the dominant horizon stack at this coordinate."""
        import requests

        # Single SQL: spatially intersect to find mukey, then join through
        # to chorizon for the dominant (majcompflag) component.
        sql = f"""
        SELECT TOP 50
            ch.hzdept_r AS top_cm,
            ch.hzdepb_r AS bottom_cm,
            ch.claytotal_r AS clay_pct,
            ch.silttotal_r AS silt_pct,
            ch.sandtotal_r AS sand_pct,
            ch.dbthirdbar_r AS bulk_density,
            ch.om_r AS organic_matter_pct,
            ch.ph1to1h2o_r AS ph,
            mu.muname AS map_unit_name,
            c.compname AS component_name,
            c.taxorder AS tax_order,
            c.taxsubgrp AS tax_subgroup
        FROM mupolygon mp
        INNER JOIN mapunit mu ON mu.mukey = mp.mukey
        INNER JOIN component c ON c.mukey = mp.mukey
        INNER JOIN chorizon ch ON ch.cokey = c.cokey
        WHERE mp.mupolygongeo.STIntersects(geometry::STGeomFromText('POINT({lon} {lat})', 4326)) = 1
          AND c.majcompflag = 'Yes'
        ORDER BY ch.hzdept_r ASC
        """

        try:
            response = requests.post(
                SDA_URL,
                json={'format': 'JSON+COLUMNNAME', 'query': sql},
                timeout=REQUEST_TIMEOUT_SECONDS,
                headers={'User-Agent': 'EarthRISEAgents/1.0 (in-situ soil lookup)'},
            )
        except requests.Timeout:
            raise ExternalSoilSourceError(
                f"SSURGO request timed out after {REQUEST_TIMEOUT_SECONDS}s"
            )
        except requests.RequestException as e:
            raise ExternalSoilSourceError(f"SSURGO request failed: {e}")

        if response.status_code != 200:
            raise ExternalSoilSourceError(
                f"SSURGO returned HTTP {response.status_code}: {response.text[:200]}"
            )

        try:
            payload = response.json()
        except ValueError:
            raise ExternalSoilSourceError("SSURGO returned non-JSON response")

        rows = payload.get('Table') or []
        if not rows or len(rows) < 2:
            raise ExternalSoilSourceError(
                f"SSURGO returned no rows at ({lat}, {lon}); the location may "
                f"be outside the surveyed area or over water."
            )

        # First row is column names; rest are data rows.
        col_names = rows[0]
        data_rows = [dict(zip(col_names, r)) for r in rows[1:]]

        # All data rows should be from the same dominant component, but the
        # SQL filter on majcompflag may return horizons from multiple equally-
        # dominant components if SSURGO has them. Use the first component
        # encountered as the canonical one.
        canonical_component = data_rows[0].get('component_name', '')
        canonical_horizons = [
            r for r in data_rows if r.get('component_name') == canonical_component
        ]

        horizons = self._parse_horizons(canonical_horizons)
        if not horizons:
            raise ExternalSoilSourceError(
                f"SSURGO horizons at ({lat}, {lon}) had no usable texture data"
            )

        layers = self._rebucket_to_dssat_schedule(horizons)
        if not layers:
            raise ExternalSoilSourceError(
                f"SSURGO horizons at ({lat}, {lon}) could not be rebucketed "
                f"into DSSAT layer schedule (gaps too large?)"
            )

        return {
            'name': (
                data_rows[0].get('map_unit_name')
                or f'SSURGO at ({lat}, {lon})'
            ),
            'classification': data_rows[0].get('tax_subgroup', ''),
            'scs_family': data_rows[0].get('tax_order', ''),
            'country': 'USA',
            'layers': layers,
            # Surface defaults — SSURGO doesn't supply most of these directly.
            'salb': 0.13,
            'slu1': 6.0,
            'sldr': 0.60,
            'slro': 73.0,
            'slnf': 1.0,
            'slpf': 1.0,
        }

    @staticmethod
    def _parse_horizons(rows: List[Dict]) -> List[Dict]:
        """Extract numeric horizon records from raw SDA rows."""
        out = []
        for r in rows:
            try:
                top = float(r['top_cm'])
                bottom = float(r['bottom_cm'])
            except (TypeError, ValueError, KeyError):
                continue
            if bottom <= top:
                continue
            try:
                clay = float(r['clay_pct'])
                silt = float(r['silt_pct'])
            except (TypeError, ValueError, KeyError):
                continue
            try:
                bd = float(r['bulk_density']) if r.get('bulk_density') is not None else None
            except (TypeError, ValueError):
                bd = None
            try:
                om = float(r['organic_matter_pct']) if r.get('organic_matter_pct') is not None else None
            except (TypeError, ValueError):
                om = None
            # SSURGO reports organic matter %; DSSAT wants organic carbon %.
            # Conventional conversion: OC = OM / 1.724 (Van Bemmelen factor).
            oc = (om / 1.724) if om is not None else None
            out.append({
                'top': top,
                'bottom': bottom,
                'clay_pct': clay,
                'silt_pct': silt,
                'bulk_density': bd,
                'organic_carbon': oc,
            })
        return out

    @staticmethod
    def _rebucket_to_dssat_schedule(horizons: List[Dict]) -> List[Dict]:
        """
        Re-bin SSURGO horizons into the standard DSSAT layer schedule by
        depth-weighted averaging. Each DSSAT layer's value is the weighted
        mean of the SSURGO horizons that intersect its depth range.
        """
        layers: List[Dict] = []
        prev_bottom = 0.0
        for slb in DSSAT_LAYER_BOTTOMS:
            top = prev_bottom
            bottom = float(slb)
            prev_bottom = bottom

            total_thick = 0.0
            sums = {'clay_pct': 0.0, 'silt_pct': 0.0, 'bulk_density': 0.0, 'organic_carbon': 0.0}
            counts = {'clay_pct': 0.0, 'silt_pct': 0.0, 'bulk_density': 0.0, 'organic_carbon': 0.0}

            for h in horizons:
                overlap_top = max(top, h['top'])
                overlap_bottom = min(bottom, h['bottom'])
                thickness = overlap_bottom - overlap_top
                if thickness <= 0:
                    continue
                total_thick += thickness
                for key in sums.keys():
                    val = h.get(key)
                    if val is None:
                        continue
                    sums[key] += val * thickness
                    counts[key] += thickness

            if total_thick == 0:
                # No SSURGO horizons cover this DSSAT layer — skip.
                continue

            layer = {'depth': bottom}
            if counts['clay_pct'] > 0:
                layer['clay_pct'] = sums['clay_pct'] / counts['clay_pct']
            if counts['silt_pct'] > 0:
                layer['silt_pct'] = sums['silt_pct'] / counts['silt_pct']
            if counts['bulk_density'] > 0:
                layer['bulk_density'] = sums['bulk_density'] / counts['bulk_density']
            if counts['organic_carbon'] > 0:
                layer['organic_carbon'] = sums['organic_carbon'] / counts['organic_carbon']

            # create_soil_from_texture requires clay_pct + silt_pct on every layer.
            if 'clay_pct' not in layer or 'silt_pct' not in layer:
                continue
            layers.append(layer)

        return layers
