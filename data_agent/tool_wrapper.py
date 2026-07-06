"""
Tool adapters for data_agent's query + availability + resolver surface.

Each callable validates its input Pydantic model, delegates to the
underlying service function in `data_agent.services`, and returns a ReAct
envelope. Resolver tools return `status: ok` with bounded result lists;
primary query tools return `status: ok` with the payload.

These functions are the ones the chat orchestrator dispatches once the
owning skill (`data-query` or `data-availability`) is loaded.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from pydantic import ValidationError

from earthrise_agents_base.schemas import validation_error_to_react
from data_agent.schemas import (
    CheckAvailabilityInput,
    QueryDataInput,
    ResolveDatasetInput,
    ResolveLocationInput,
    build_dataset_enum,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tool descriptions
# ---------------------------------------------------------------------------

QUERY_DATA_TOOL_DESCRIPTION = (
    "Extract time-series data from a raster dataset at a location over a "
    "date range. Returns per-day records for the requested variables. "
    "Call resolve_dataset first if you're unsure which dataset identifier "
    "matches the user's request; call resolve_location if you only have an "
    "admin-area name and need lat/lon."
)

CHECK_AVAILABILITY_TOOL_DESCRIPTION = (
    "Summarize what data is present locally for a dataset across a date "
    "range. Use this before calling query_data when the user's asked "
    "window might be partially or fully uncovered. The summary includes "
    "first/last loaded dates and record counts."
)

RESOLVE_DATASET_TOOL_DESCRIPTION = (
    "Look up dataset identifiers that match a free-text query. Returns "
    "a bounded list of candidates with id, name, kind, and available "
    "variables. Use when the user names a dataset imprecisely or when "
    "you want to show the catalog."
)

RESOLVE_LOCATION_TOOL_DESCRIPTION = (
    "Resolve a free-text admin area name (e.g. 'Fresno, California') to "
    "center lat/lon, bounding box, and country. Use before calling "
    "query_data or check_availability when you only have a place name "
    "from the user."
)


# ---------------------------------------------------------------------------
# Helpers — translate the LocationInput discriminated union into the flat
# (lat, lon[, bbox]) kwargs that data_agent.services.* already accept.
# ---------------------------------------------------------------------------


def _flatten_location(loc) -> Dict[str, Any]:
    """Return {lat, lon, bbox?, name?} from a LocationInput instance."""
    if loc.kind == "point":
        return {"lat": loc.lat, "lon": loc.lon}
    # admin_area → resolve via data_agent.location.resolve_location (shared util)
    from data_agent.location import resolve_location as _resolve

    parts = [loc.county, loc.state, loc.country]
    name = ", ".join(p for p in parts if p)
    resolved = _resolve(name)
    return {
        "lat": resolved.get("lat"),
        "lon": resolved.get("lon"),
        "bbox": resolved.get("bbox"),
        "name": resolved.get("name") or name,
    }


def _dataset_match_hint(source: str) -> Dict[str, List[str]]:
    """Compute fuzzy candidate list for an unknown dataset name."""
    import difflib

    all_codes = build_dataset_enum()
    return {
        "source": all_codes,
    } if source not in all_codes else {}


# ---------------------------------------------------------------------------
# query_data
# ---------------------------------------------------------------------------


def query_data_tool(raw: Dict[str, Any], *, user: Any = None) -> Dict[str, Any]:
    raw = dict(raw or {})
    try:
        params = QueryDataInput.model_validate(raw)
    except ValidationError as e:
        return validation_error_to_react(
            e,
            field_values={"source": build_dataset_enum()},
        )

    loc = _flatten_location(params.location)
    if loc.get("lat") is None or loc.get("lon") is None:
        return {
            "status": "needs_input",
            "prompt": (
                "I couldn't pinpoint a location for that request. Could you "
                "provide latitude/longitude or a more specific place name?"
            ),
            "schema": ResolveLocationInput.model_json_schema(),
            "known": {"partial_location": loc.get("name", "")},
        }

    from data_agent.services import query_raster_data

    result = query_raster_data(
        source=params.source,
        variables=list(params.variables),
        lat=loc["lat"],
        lon=loc["lon"],
        start_date=params.start_date.isoformat(),
        end_date=params.end_date.isoformat(),
    )

    if isinstance(result, dict) and result.get("error"):
        return {
            "status": "error",
            "error": result["error"],
            "details": {
                "source": params.source,
                "variables": params.variables,
                "location": loc,
                "window": [params.start_date.isoformat(), params.end_date.isoformat()],
            },
        }

    records = (result or {}).get("records", []) if isinstance(result, dict) else []
    return {
        "status": "ok",
        "source": params.source,
        "variables": list(params.variables),
        "location": loc,
        "window": [params.start_date.isoformat(), params.end_date.isoformat()],
        "record_count": len(records),
        "records": records,
    }


# ---------------------------------------------------------------------------
# check_availability
# ---------------------------------------------------------------------------


def check_availability_tool(raw: Dict[str, Any], *, user: Any = None) -> Dict[str, Any]:
    raw = dict(raw or {})
    try:
        params = CheckAvailabilityInput.model_validate(raw)
    except ValidationError as e:
        return validation_error_to_react(
            e,
            field_values={"source": build_dataset_enum()},
        )

    from data_agent.services import check_availability

    result = check_availability(
        source=params.source,
        start_date=params.start_date.isoformat(),
        end_date=params.end_date.isoformat(),
    )
    if not isinstance(result, dict):
        result = {"result": result}

    # Backend returns {"error": "..."} when the source is unknown. Translate
    # to a proper validation_error envelope so the ReAct loop can retry
    # with a corrected source (or surface the issue to the user).
    if result.get("error"):
        return {
            "status": "validation_error",
            "error": result["error"],
            "issues": [{
                "field": "source",
                "message": result["error"],
                "received": params.source,
                "valid_values": build_dataset_enum(),
            }],
            "hint": (
                "Call `resolve_dataset(query=...)` to find a valid "
                "dataset identifier, or surface the candidate list to the user."
            ),
        }

    # Location is preserved in the envelope for the remote-probe / fetch
    # flow that task #8 wires on top of this tool; the local check itself
    # does not use it.
    loc_payload: Any = None
    if params.location is not None:
        try:
            loc_payload = params.location.model_dump()
        except Exception:
            loc_payload = None
    return {
        "status": "ok",
        "source": params.source,
        "window": [params.start_date.isoformat(), params.end_date.isoformat()],
        "availability": result,
        "location": loc_payload,
    }


# ---------------------------------------------------------------------------
# resolve_dataset
# ---------------------------------------------------------------------------


def resolve_dataset_tool(raw: Dict[str, Any], *, user: Any = None) -> Dict[str, Any]:
    raw = dict(raw or {})
    try:
        params = ResolveDatasetInput.model_validate(raw)
    except ValidationError as e:
        return validation_error_to_react(e)

    from data_agent.services import list_all_data_sources

    try:
        sources = list_all_data_sources() or {}
    except Exception as e:
        logger.warning("[data] list_all_data_sources failed: %s", e)
        sources = {}

    # Flatten — the listing may come back shaped per-category.
    flat: List[Dict[str, Any]] = []
    if isinstance(sources, dict):
        for v in sources.values():
            if isinstance(v, list):
                flat.extend(v)
    elif isinstance(sources, list):
        flat = list(sources)

    query = (params.query or "").strip().lower()
    if query:
        def _matches(s: Dict[str, Any]) -> bool:
            for key in ("id", "name", "dataset_name", "dataset_subtype"):
                val = s.get(key)
                if isinstance(val, str) and query in val.lower():
                    return True
            return False
        flat = [s for s in flat if _matches(s)]

    flat = flat[: params.limit]

    if not flat:
        return {
            "status": "needs_input",
            "prompt": (
                f"I couldn't find a dataset matching '{params.query}'. "
                "Could you clarify which source you want? (Options include "
                f"{', '.join(build_dataset_enum()[:6])}, ...)"
            ),
            "schema": ResolveDatasetInput.model_json_schema(),
            "known": {"query": params.query},
        }

    return {"status": "ok", "query": params.query, "candidates": flat}


# ---------------------------------------------------------------------------
# resolve_location
# ---------------------------------------------------------------------------


def resolve_location_tool(raw: Dict[str, Any], *, user: Any = None) -> Dict[str, Any]:
    raw = dict(raw or {})
    try:
        params = ResolveLocationInput.model_validate(raw)
    except ValidationError as e:
        return validation_error_to_react(e)

    # Geospatial location resolution lives in data_agent (its own domain).
    from data_agent.location import resolve_location as _resolve

    resolved = _resolve(params.name) or {}
    if not resolved.get("lat") or not resolved.get("lon"):
        return {
            "status": "needs_input",
            "prompt": (
                f"I couldn't resolve '{params.name}' to coordinates. "
                "Could you give a more specific place name, or provide "
                "lat/lon directly?"
            ),
            "schema": ResolveLocationInput.model_json_schema(),
            "known": {"name": params.name},
        }
    return {
        "status": "ok",
        "name": resolved.get("name") or params.name,
        "lat": resolved["lat"],
        "lon": resolved["lon"],
        "bbox": resolved.get("bbox"),
        "country": resolved.get("country"),
    }
