"""
Map Explorer views — Leaflet.js map with weather raster tiles and admin boundaries.

Data is fetched via direct service imports — no A2A indirection.
"""

import logging
import os

import httpx
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from data_agent import services as data_services

logger = logging.getLogger(__name__)

DATAAGENT_URL = os.environ.get('DATA_AGENT_URL', 'http://localhost:5000')

DSSAT_REQUIRED_VARS = {'tmax', 'tmin', 'rain', 'srad'}


class MapExplorerView(View):
    """Render the Leaflet map explorer page."""

    def get(self, request):
        try:
            raw = data_services.list_raster_sources()
        except Exception:
            raw = {}

        qualified = {}
        for s in raw.get('single_sources', []):
            if DSSAT_REQUIRED_VARS.issubset(set(s.get('variables', []))):
                qualified[s['prefix']] = s

        combined = {}
        for c in raw.get('combined_sources', []):
            combined[c['prefix']] = c

        return render(request, 'dssat_agent/explorer/map.html', {
            'qualified_sources': qualified,
            'combined_sources': combined,
        })


@method_decorator(csrf_exempt, name='dispatch')
class TileProxyView(View):
    """Proxy tile requests to the DataAgent tile endpoint (avoids CORS)."""

    def get(self, request, source, variable, date, z, x, y):
        url = f"{DATAAGENT_URL}/tiles/{source}/{variable}/{date}/{z}/{x}/{y}.png"
        try:
            resp = httpx.get(url, timeout=15.0)
            return HttpResponse(
                resp.content,
                content_type='image/png',
                headers={'Cache-Control': 'public, max-age=3600'},
            )
        except httpx.HTTPError as e:
            logger.error("Tile proxy error: %s", e)
            return HttpResponse(status=502)


@method_decorator(csrf_exempt, name='dispatch')
class AdminBoundariesAPI(View):
    """Return admin boundaries as GeoJSON via SimulationAgent."""

    def get(self, request):
        level = request.GET.get('level', 'admin1')
        parent = request.GET.get('parent')
        try:
            geojson = data_services.get_admin_boundaries_geojson(level=level, parent=parent)
            return JsonResponse(geojson, safe=False)
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=400)


@method_decorator(csrf_exempt, name='dispatch')
class MapSourceDatesAPI(View):
    """Return available date range for a source."""

    def get(self, request):
        source = request.GET.get('source', '')
        if not source:
            return JsonResponse({'error': 'Missing source parameter'}, status=400)
        try:
            result = data_services.check_availability(source=source)
            return JsonResponse({'success': True, 'result': result})
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=400)
