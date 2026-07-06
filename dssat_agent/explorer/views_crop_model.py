"""CropModel read API.

Serves the merged (model-specific + crop-wide) view of a crop's admin
policy for consumption by the wizard UI and defaults pages. Admin
editing is done via Django admin; this endpoint is read-only.

URL: ``GET /dssat/api/crop-model/<crop_code>/?dssat_model=MZCER``
"""

import json

from django.http import JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import ensure_csrf_cookie

from dssat_agent.services.crop_model_service import (
    get_crop_model_pair, serialize_for_api,
)


@method_decorator(ensure_csrf_cookie, name='dispatch')
class CropModelAPI(View):
    """Return merged crop-wide + model-specific CropModel data as JSON.

    Response shape (when a row exists):
        {
          "success": true,
          "crop_code": "MZ",
          "data": {
            "crop_code": "MZ",
            "dssat_model": "MZCER",
            "display_name": "Maize",
            "crop_group": "cereal",
            "planting_methods": [...],
            "fertilizer_materials": [...],
            ...
            "plant_population_range": {"min": 4, "max": 10, "unit": "plants/m2"},
            "harvest_stages": [
              {"code": "GS005", "name": "R6", "description": "...maturity"}
            ],
            "supported_harvs_modes": ["auto","maturity","on_date","growth_stage","dap"],
            "supported_management": [...],
            ...
          }
        }

    Returns ``{"success": true, "data": null}`` if no row is found for
    the crop (neither model-specific nor crop-wide).
    """

    def get(self, request, crop_code):
        dssat_model = request.GET.get('dssat_model') or None
        model_row, crop_row = get_crop_model_pair(crop_code, dssat_model)
        return JsonResponse({
            'success': True,
            'crop_code': crop_code.upper(),
            'data': serialize_for_api(model_row, crop_row, crop_code=crop_code),
        })
