"""
Location input schema — discriminated union of Point and AdminArea.

The `kind` literal is the discriminator; tool-calling LLMs route on it
reliably (oneOf with discriminator in JSON schema). Admin-area lookups
use GADM-style country/state/county names (case-insensitive match against
the admin-boundary tables).
"""

from __future__ import annotations

from typing import Annotated, Literal, Optional, Union

from pydantic import BaseModel, Field


class LocationPoint(BaseModel):
    """A GPS point location."""

    kind: Literal["point"] = Field("point", description=(
        "Discriminator — set to 'point' when supplying latitude/longitude."
    ))
    lat: float = Field(..., ge=-90, le=90, description=(
        "Latitude in decimal degrees, WGS84. Positive is north of the equator. "
        "Required when kind='point'."
    ))
    lon: float = Field(..., ge=-180, le=180, description=(
        "Longitude in decimal degrees, WGS84. Positive is east of the prime "
        "meridian. Required when kind='point'."
    ))
    elevation_m: Optional[float] = Field(None, ge=-500, le=9000, description=(
        "Optional elevation above sea level in meters. If omitted the system "
        "pulls elevation from a DEM at the lat/lon."
    ))


class LocationAdminArea(BaseModel):
    """An administrative area (country / state / county)."""

    kind: Literal["admin_area"] = Field("admin_area", description=(
        "Discriminator — set to 'admin_area' when supplying country/state/county."
    ))
    country: str = Field(..., min_length=2, description=(
        "Country name or ISO-3166 alpha-2 code (e.g. 'US', 'KE', 'Ethiopia'). "
        "Matched case-insensitively against the GADM-level-0 feature set."
    ))
    state: Optional[str] = Field(None, description=(
        "First-level admin subdivision: state, province, or region. Free-form; "
        "matched against GADM level-1 features within the chosen country."
    ))
    county: Optional[str] = Field(None, description=(
        "Second-level admin subdivision: county or district. Matched against "
        "GADM level-2 features. Specify `state` too when ambiguous."
    ))


LocationInput = Annotated[
    Union[LocationPoint, LocationAdminArea],
    Field(
        discriminator="kind",
        description=(
            "JSON object describing where to run the query or simulation. "
            "Must be a real object — not a free-text label. **Exactly two "
            "valid shapes**, distinguished by `kind`:\n"
            '  point:       {"kind": "point", "lat": 34.18, "lon": -86.84}\n'
            '  admin area:  {"kind": "admin_area", "country": "US", '
            '"state": "California", "county": "Fresno"}\n'
            "Always include `kind`; pick the shape that matches the user's "
            "input (coordinates → point; country/state/county name → "
            "admin_area).\n"
            "**Do NOT** use `kind: \"circle\"`, `\"bbox\"`, or `\"admin\"` "
            "here — those are Monte Carlo *region* shapes that belong on "
            "the separate `spatial.mode` field, not on `location.kind`. "
            "For a Monte Carlo around a center point, set `location` to "
            "the point itself (kind=\"point\") and put the circle/bbox/admin "
            "geometry on `spatial`."
        ),
    ),
]
