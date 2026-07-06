"""
data_agent app-level views (non-A2A).

Only ``TileView`` lives here — it's an XYZ tile-server endpoint outside
the A2A protocol standard, so the framework's generic A2A views don't
cover it. The A2A endpoints (agent card, health, RPC dispatch) come
from ``earthrise_agents_base.a2a.build_urlpatterns`` in ``urls.py``.
"""

import logging

import redis as _redis
from django.conf import settings
from django.http import JsonResponse, HttpResponse
from django.views import View

from .services import _get_prefix_map
from .tile_handler import generate_tile, _make_png

logger = logging.getLogger(__name__)


VALID_VARIABLES = {"tmax", "tmin", "rain", "srad"}


def _build_empty_png() -> bytes:
    """Build a 256x256 fully-transparent PNG, with a guaranteed-non-None
    fallback. If ``_make_png`` ever fails (e.g. Pillow import broken),
    we must NOT let ``_EMPTY_PNG`` be None — ``HttpResponse(None)``
    emits the literal 4-byte string "None", which Cloudflare will then
    cheerfully cache as image/png and serve to every user for 4 hours.
    """
    try:
        png = _make_png(256, 256, bytes(256 * 256 * 4))
        if png:
            return png
    except Exception:
        pass
    # Smallest valid 1x1 fully-transparent PNG (67 bytes). Acceptable
    # fallback — Leaflet renders it as nothing, which is what we want.
    return (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00"
        b"\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc"
        b"\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
    )


_EMPTY_PNG = _build_empty_png()


class TileView(View):
    """GET /tiles/<source>/<variable>/<date>/<z>/<x>/<y>.png"""

    def get(self, request, source, variable, date, z, x, y):
        if variable not in VALID_VARIABLES:
            return JsonResponse({"error": f"Invalid variable: {variable}"}, status=400)

        prefix = _get_prefix_map().get(source)
        if not prefix:
            return JsonResponse({"error": f"Unknown source: {source}"}, status=400)

        # Check Redis cache
        cache_key = f"tile:v9:{prefix}:{variable}:{date}:{z}:{x}:{y}"
        redis_url = getattr(settings, "CELERY_BROKER_URL", None)
        rclient = None
        if redis_url:
            try:
                rclient = _redis.from_url(redis_url)
                cached = rclient.get(cache_key)
                if cached is not None:
                    return HttpResponse(
                        cached, content_type="image/png",
                        headers={"Cache-Control": "public, max-age=3600"},
                    )
            except Exception:
                pass

        png_bytes = generate_tile(prefix, variable, date, z, x, y)

        is_empty = not png_bytes
        if is_empty:
            content = _EMPTY_PNG
        else:
            content = png_bytes
            if rclient:
                try:
                    rclient.setex(cache_key, 3600, content)
                except Exception:
                    pass

        # Defense-in-depth: never let a None body slip through to
        # HttpResponse — see 2026-04-24 cache-poison incident.
        if not content:
            content = _EMPTY_PNG or b""

        # Real PNGs cache aggressively (4h). Empty/error tiles cache
        # briefly so a subsequent backend fix propagates quickly without
        # depending on a Cloudflare purge.
        cache_header = (
            "public, max-age=60" if is_empty else "public, max-age=14400"
        )
        return HttpResponse(
            content, content_type="image/png",
            headers={"Cache-Control": cache_header},
        )
