"""
Input schemas for the data_agent's query tools.

- `QueryDataInput` — tool `query_data` (time-series/tabular extraction).
- `CheckAvailabilityInput` — tool `check_availability` (DB coverage summary).

`source` is a free-string field at validate time. The LLM-facing tool schema
gets a dynamic `enum` injected via `chat.schemas.tool_schema.build_tool_schema`
with `enum_providers={'source': build_dataset_enum}` so the LLM can only
pick a registered dataset.
"""

from __future__ import annotations

from datetime import date
from typing import List, Optional

from pydantic import BaseModel, Field, model_validator

from data_agent.schemas.location import LocationInput


class QueryDataInput(BaseModel):
    """Parameters for a raster-time-series extraction at a location."""

    source: str = Field(..., description=(
        "Dataset identifier — the registered dataset_subtype (e.g. 'power', "
        "'chirps', 'chirps_chirts_era5'). If unsure, call resolve_dataset first. "
        "The tool returns a validation error with close matches when the value "
        "doesn't resolve."
    ))
    variables: List[str] = Field(..., min_length=1, description=(
        "Variable short-codes to extract (e.g. ['tmax','tmin','rain','srad'] "
        "for weather). Valid set depends on the chosen dataset; call "
        "resolve_dataset to see available variables."
    ))
    location: LocationInput = Field(..., description=(
        "Where to extract data — GPS point or administrative area."
    ))
    start_date: date = Field(..., description=(
        "First day of the extraction window (inclusive). ISO YYYY-MM-DD."
    ))
    end_date: date = Field(..., description=(
        "Last day of the extraction window (inclusive). ISO YYYY-MM-DD. "
        "Must be on or after start_date."
    ))

    @model_validator(mode="after")
    def _dates_ordered(self) -> "QueryDataInput":
        if self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date")
        return self


class CheckAvailabilityInput(BaseModel):
    """Parameters for checking dataset coverage over a date range.

    Mirrors the inputs on the Data Availability page. `location` is optional
    at this layer — the *local* availability check (which dates are loaded)
    is dataset-wide and doesn't need it. But the page's follow-up actions
    (probe upstream source, trigger a remote fetch) DO need location, so
    the tool accepts it here; it's passed through whenever a partial-
    coverage result leads into a remote-probe / fetch flow (wired in
    task #8).
    """

    source: str = Field(..., description=(
        "Dataset identifier — the registered dataset_subtype. Use resolve_dataset "
        "when unsure."
    ))
    start_date: date = Field(..., description=(
        "First day of the window to check. ISO YYYY-MM-DD."
    ))
    end_date: date = Field(..., description=(
        "Last day of the window to check. ISO YYYY-MM-DD. Must be on or after start_date."
    ))
    location: Optional[LocationInput] = Field(None, description=(
        "Optional location — required only when the follow-up action is a "
        "remote-source probe or a data fetch (both of which spatially bound "
        "their request). Omit for a pure local-coverage check."
    ))

    @model_validator(mode="after")
    def _dates_ordered(self) -> "CheckAvailabilityInput":
        if self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date")
        return self
