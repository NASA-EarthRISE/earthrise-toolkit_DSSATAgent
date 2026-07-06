"""
data_agent A2A surface — skill table + agent card + URL patterns.

Single module holding the skill table, agent card, and URL patterns.
The A2A URL contract is mounted at ``/data_agent/`` (the
app_label) — verified by ``data_agent/tests/test_a2a_contract.py``.

The bespoke ``TileView`` (tile-server endpoint under ``/tiles/...``)
lives at the top level of the app in ``views.py`` — it's not an
A2A-protocol concern.
"""

from __future__ import annotations

from typing import Any, Dict

from django.urls import path

from earthrise_agents_base.a2a import (
    SkillTable,
    build_agent_card,
    build_urlpatterns,
    register_agent,
)


def _prefix_map() -> Dict[str, str]:
    """Return the DB-derived source name → PostGIS table prefix map.

    Delegates to ``data_agent.services._get_prefix_map()`` which reads
    from the ``RasterDataset`` table (populated by ``register_raster_source``
    at product startup — see the registering sub-agent's ``apps.py``).

    Kept as an accessor so callers don't have to know about the caching
    layer, and so that no source-specific names ever appear in
    ``data_agent``'s code — the agent is source-agnostic; the product
    that ships it registers whichever sources it needs.
    """
    from .services import _get_prefix_map
    return _get_prefix_map()


skills = SkillTable()


@skills.skill(
    id="list_sources",
    name="List Data Sources",
    description="List available weather data sources.",
)
def _list_sources(params: Dict[str, Any]) -> Any:
    from .handlers import handle_list_sources
    return handle_list_sources()


@skills.skill(
    id="check_availability",
    name="Check Data Availability",
    description="Check what weather data dates exist in the database.",
)
def _check_availability(params: Dict[str, Any]) -> Any:
    from .handlers import handle_check_availability
    return handle_check_availability(params, _prefix_map())


@skills.skill(
    id="query_time_series",
    name="Query Time-Series Data",
    description="Query stored weather data.",
)
def _query_time_series(params: Dict[str, Any]) -> Any:
    from .handlers import handle_query_time_series
    return handle_query_time_series(params, _prefix_map())


@skills.skill(
    id="fetch_data",
    name="Fetch Data",
    description="Download weather data for a region and date range.",
)
def _fetch_data(params: Dict[str, Any]) -> Any:
    from .handlers import handle_fetch_data
    return handle_fetch_data(params, _prefix_map())


@skills.skill(
    id="check_remote",
    name="Check Remote Availability",
    description="Probe upstream URLs to check data availability.",
)
def _check_remote(params: Dict[str, Any]) -> Any:
    from .handlers import handle_check_remote
    return handle_check_remote(params)


AGENT_CARD = build_agent_card(
    name="DataAgent - Weather Data Service",
    description=(
        "Downloads, transforms, and loads gridded weather/climate data. "
        "Supports observational and forecast sources plus combined datasets. "
        "Provides variables such as TMAX, TMIN, RAIN, and SRAD."
    ),
    version="1.1.0",
    skills=skills,
    capabilities={"streaming": False, "tiles": True},
)


register_agent(name="data_agent", card=AGENT_CARD, skills=skills)


# ---------------------------------------------------------------------------
# URL patterns
# ---------------------------------------------------------------------------

# Bespoke TileView route in addition to the framework A2A endpoints.
from . import views  # noqa: E402  (registration must happen first)

app_name = "data_agent_a2a"

urlpatterns = build_urlpatterns(agent_name="data_agent") + [
    path(
        "tiles/<str:source>/<str:variable>/<str:date>/<int:z>/<int:x>/<int:y>.png",
        views.TileView.as_view(),
        name="tile",
    ),
]
