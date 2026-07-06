"""
Runtime-built enums and resolver helpers for DSSAT catalog fields.

- `build_crop_enum()` — ~50 items; inline enum is fine.
- `build_cultivar_enum(crop)` — 10–200 per crop; inline enum is fine once
  `crop` is known. Without `crop` this returns an empty list (the LLM must
  resolve the crop first).
- Soil profiles — **thousands** of rows, so NOT enumified. Exposed instead
  via `list_soil_profiles_page(filters)` and `resolve_soil_profile(query, ...)`
  which return bounded results. StoredSoilProfile is a shared catalog (no
  per-user visibility filter — all soils are visible to every user by design).

Django ORM imports are lazy (inside functions) so the module can be imported
before Django's `AppConfig.ready()` fires.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def build_crop_enum() -> List[str]:
    """Return all supported DSSAT crop codes, sorted."""
    from dssat_agent.services.crop_service import list_crops

    return sorted({entry["code"] for entry in list_crops() if entry.get("code")})


def build_cultivar_enum(crop: Optional[str]) -> List[str]:
    """Return cultivar codes for the given crop.

    Returns empty list when `crop` is None/unknown — the LLM is expected to
    resolve the crop first, then call this.
    """
    if not crop:
        return []
    from dssat_agent.services.cultivar_service import list_cultivars_db

    result = list_cultivars_db(crop.upper())
    return sorted({c["code"] for c in result.get("cultivars", []) if c.get("code")})


# ── Soil profile resolvers (NOT an enum — thousands of rows) ──────────────


_SOIL_PAGE_SIZE = 50

# StoredSoilProfile.SOURCE_CHOICES codes (see dssat_agent.models).
_VALID_SOIL_SOURCES = {"default", "custom", "imported"}


def list_soil_profiles_page(
    *,
    country: Optional[str] = None,
    source: Optional[str] = None,
    page: int = 1,
) -> Dict[str, Any]:
    """
    Bounded paginated listing of soil profiles.

    Returns at most `_SOIL_PAGE_SIZE` entries per call plus `has_more`.
    Intended for the `list_soil_profiles` tool in the dssat-experiment-run
    skill bundle.

    Filters:
        country — case-insensitive exact match on the profile's country field.
        source  — one of 'default' | 'custom' | 'imported'.
    """
    from dssat_agent.models import StoredSoilProfile

    qs = StoredSoilProfile.objects.all()
    if country:
        qs = qs.filter(country__iexact=country)
    if source:
        qs = qs.filter(source=source)

    qs = qs.order_by("source", "soil_id")
    total = qs.count()
    offset = max(0, (page - 1) * _SOIL_PAGE_SIZE)
    rows = list(
        qs[offset : offset + _SOIL_PAGE_SIZE].values(
            "soil_id", "name", "country", "site", "source",
        )
    )
    return {
        "soils": rows,
        "page": page,
        "page_size": _SOIL_PAGE_SIZE,
        "total": total,
        "has_more": (offset + len(rows)) < total,
    }


def resolve_soil_profile(
    query: str,
    *,
    country: Optional[str] = None,
    source: Optional[str] = None,
    limit: int = 10,
) -> List[Dict[str, Any]]:
    """
    Fuzzy resolver for soil profiles — returns top matches by id/name/site.

    The LLM calls this when it has a partial soil label ("Cullman silty clay")
    and needs candidate IDs to pick from. The tool wrapper should return
    these as-is when the match is unambiguous, or as a `needs_input` envelope
    when multiple plausible candidates exist.
    """
    from django.db.models import Q

    from dssat_agent.models import StoredSoilProfile

    qs = StoredSoilProfile.objects.all()
    if country:
        qs = qs.filter(country__iexact=country)
    if source:
        qs = qs.filter(source=source)

    if query:
        qs = qs.filter(
            Q(soil_id__icontains=query)
            | Q(name__icontains=query)
            | Q(site__icontains=query)
        )

    rows = list(
        qs.order_by("source", "soil_id")[:limit].values(
            "soil_id", "name", "country", "site", "source",
        )
    )
    return rows


def soil_profile_exists(soil_id: str) -> bool:
    """Lightweight existence check used by Pydantic model validators."""
    from dssat_agent.models import StoredSoilProfile

    return StoredSoilProfile.objects.filter(soil_id=soil_id).exists()
