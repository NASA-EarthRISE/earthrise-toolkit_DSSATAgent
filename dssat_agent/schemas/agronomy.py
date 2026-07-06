"""
Composite agronomic-parameter models used inside ExperimentInput.

Field names follow LLM-facing conventions (`population`, `spacing`, `method`)
rather than DSSAT's 4-letter abbreviations (`ppop`, `plrs`, `plme`). The
run_experiment tool wrapper translates at the boundary before handing to
the existing `run_full_simulation(params: dict)` helper, which keeps the
downstream services intact.
"""

from __future__ import annotations

from datetime import date as _date
from typing import List, Optional

from pydantic import BaseModel, Field, model_validator

from dssat_agent.schemas.enums_static import (
    FertilizerApplicationMethod,
    FertilizerType,
    HarvestStrategy,
    IrrigationApplicationMethod,
    IrrigationStrategy,
    PlantingMethod,
)


class PlantingConfig(BaseModel):
    """Planting-related parameters.

    Only `date` is required. Population, spacing, method, and depth all fall
    back to per-crop defaults (from the user's CropDefault preference, then
    the CropModel row, then a hardcoded fallback) when omitted — this is
    what enables the "crop + location + planting_date" fast path.
    """

    date: _date = Field(..., description=(
        "Planting date, ISO YYYY-MM-DD. Must fall within the weather "
        "dataset's coverage."
    ))
    population: Optional[float] = Field(None, gt=0, le=500, description=(
        "Plant population in plants/m². Omit to use the crop's default. "
        "Typical maize 5–10; wheat 100–400; soybean 20–45."
    ))
    spacing: Optional[float] = Field(None, gt=0, le=300, description=(
        "Row spacing in centimeters (DSSAT 'plrs'). Omit to use the crop's "
        "default. Typical maize 75 cm, wheat 17 cm, soybean 38–76 cm."
    ))
    method: Optional[PlantingMethod] = Field(None, description=(
        "How the crop is planted. Omit for crop-typical default ('S' dry "
        "seed for most field crops, 'I' transplant for rice/cabbage)."
    ))
    depth_cm: Optional[float] = Field(None, gt=0, le=20, description=(
        "Seed-placement depth in cm. Omit to use the crop's DSSAT default."
    ))


class FertilizerInput(BaseModel):
    """Nutrient-amount payload, used inside FertilizerApplication."""

    date: _date = Field(..., description=(
        "Date the fertilizer is applied. Often equal to or close to the "
        "planting date for starter applications."
    ))
    depth_cm: float = Field(5.0, ge=0, le=50, description=(
        "Placement depth in cm. 0 for surface broadcast, 5–10 for banded."
    ))
    amount_kg_n_per_ha: float = Field(..., ge=0, le=1000, description=(
        "Nitrogen-equivalent applied in kg N/ha for this event. For non-N "
        "fertilizers, convert to N-equivalent — DSSAT derives P/K from the "
        "material code."
    ))


class FertilizerApplication(BaseModel):
    """One fertilizer event (material + placement + amount)."""

    type: FertilizerType = Field(..., description=(
        "Fertilizer material code. Urea (FE005) is the default for nitrogen "
        "fertilization."
    ))
    methodology: FertilizerApplicationMethod = Field(
        FertilizerApplicationMethod.BROADCAST_INCORPORATED,
        description="How the fertilizer is placed in or on the soil.",
    )
    input: FertilizerInput = Field(..., description=(
        "Date, depth, and amount for this specific application."
    ))


class IrrigationApplication(BaseModel):
    """One irrigation event (used when IrrigationStrategy=FIXED_SCHEDULE)."""

    date: _date = Field(..., description="Date of this irrigation event.")
    amount_mm: float = Field(..., gt=0, le=500, description=(
        "Water applied in millimeters for this event."
    ))
    method: IrrigationApplicationMethod = Field(
        IrrigationApplicationMethod.SPRINKLER,
        description="Irrigation delivery method.",
    )


class HarvestConfig(BaseModel):
    """Harvest timing."""

    strategy: HarvestStrategy = Field(HarvestStrategy.AUTO, description=(
        "'auto' harvests at physiological maturity; 'fixed' auto-picks a date "
        "from the crop's typical growth cycle; 'reported' requires a specific "
        "date."
    ))
    date: Optional[_date] = Field(None, description=(
        "Required when strategy='reported'; ignored otherwise."
    ))

    @model_validator(mode="after")
    def _date_required_for_reported(self) -> "HarvestConfig":
        if self.strategy == HarvestStrategy.REPORTED_DATE and self.date is None:
            raise ValueError("date is required when strategy='reported'")
        return self


class InitialConditions(BaseModel):
    """Soil state at simulation start."""

    initial_water: float = Field(0.5, ge=0, le=1, description=(
        "Initial plant-available soil water as a fraction of field capacity. "
        "0 = wilting point, 1 = saturation. 0.5 is a neutral default."
    ))
    initial_no3_kg_ha: int = Field(10, ge=0, le=500, description=(
        "Residual NO3-N in the root zone at start, in kg N/ha. Low-input "
        "systems: 5–15; over-fertilized or high-residue: 40–100."
    ))


class IrrigationConfig(BaseModel):
    """Combined irrigation strategy + application list.

    When `strategy=FIXED_SCHEDULE`, `applications` must be non-empty. For
    `strategy=AUTO`, supply optional `threshold_pct` / `efficiency_pct`.
    For `strategy=NONE`, the other fields are ignored.
    """

    strategy: IrrigationStrategy = Field(
        IrrigationStrategy.NONE,
        description="Overall irrigation approach. Default is rainfed (none).",
    )
    threshold_pct: Optional[float] = Field(None, ge=0, le=100, description=(
        "Automatic-irrigation soil-moisture threshold (% of field capacity) "
        "below which DSSAT triggers an event. Used only when strategy='automatic'."
    ))
    efficiency_pct: Optional[float] = Field(None, ge=0, le=100, description=(
        "Automatic-irrigation application efficiency (%). Used only when "
        "strategy='automatic'."
    ))
    applications: List[IrrigationApplication] = Field(
        default_factory=list,
        description=(
            "Fixed-schedule event list. Required non-empty when "
            "strategy='fixed'; ignored otherwise."
        ),
    )

    @model_validator(mode="after")
    def _applications_required_for_fixed(self) -> "IrrigationConfig":
        if self.strategy == IrrigationStrategy.FIXED_SCHEDULE and not self.applications:
            raise ValueError(
                "applications must be non-empty when strategy='fixed'"
            )
        return self
