"""
Minimal input schemas for the data_agent resolver helper tools.

Resolvers are cheap, bounded lookups the LLM calls to disambiguate free-text
input against the data_agent's catalogs before a primary query tool is
invoked. Kept separate from the heavier query schemas.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class ResolveDatasetInput(BaseModel):
    """Parameters for `resolve_dataset` — finds datasets matching a query."""

    query: Optional[str] = Field(None, description=(
        "Free-text name or partial identifier of a dataset (e.g. 'chirps', "
        "'power', 'era5'). Omit to list every registered dataset. Matching "
        "is case-insensitive substring search over dataset_subtype and "
        "dataset_name."
    ))
    limit: int = Field(10, ge=1, le=50, description=(
        "Maximum number of candidates to return."
    ))


class ResolveLocationInput(BaseModel):
    """Parameters for `resolve_location` — admin-area name → coords."""

    name: str = Field(..., min_length=2, description=(
        "Free-text admin area name (e.g. 'Fresno, California', 'Kenya', "
        "'Nairobi County'). The resolver returns center lat/lon, bbox, "
        "and inferred country."
    ))
