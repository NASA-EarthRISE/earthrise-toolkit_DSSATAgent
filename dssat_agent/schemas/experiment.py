"""
Top-level input schema for the DSSAT experiment runner.

`ExperimentInput` is a discriminated union over the four experiment types.
The LLM's tool schema renders as `oneOf` with `discriminator.propertyName =
experiment_type`, so picking a variant surfaces exactly that variant's
extra fields as required.

Dynamic catalog fields (crop, cultivar, weather_dataset) are plain `str`
here. The tool wrapper builds the JSON schema with enums injected at
invocation time via `chat.schemas.tool_schema.build_tool_schema`. Soil
is never enumified (thousands of rows) — it's validated post-hoc via
`soil_profile_exists()` in the model validator below.
"""

from __future__ import annotations

from typing import Annotated, List, Literal, Optional, Union

from pydantic import BaseModel, Field, field_validator, model_validator

from data_agent.schemas.location import LocationInput
from dssat_agent.schemas.agronomy import (
    FertilizerApplication,
    HarvestConfig,
    InitialConditions,
    IrrigationConfig,
    PlantingConfig,
)
from dssat_agent.schemas.enums_static import ExperimentType, SamplingMethod


class _ExperimentBase(BaseModel):
    """Fields shared by every experiment variant."""

    crop: str = Field(..., description=(
        "DSSAT 2-letter crop code (e.g. 'MZ' for maize, 'WH' for wheat). The "
        "tool schema injects the current enum at runtime — the LLM cannot "
        "emit a crop outside the registered list."
    ))
    cultivar: Optional[str] = Field(None, description=(
        "Cultivar code for the chosen crop. Valid values depend on `crop`; "
        "call list_cultivars(crop) to see available options. Omit to use the "
        "crop's configured default cultivar."
    ))
    location: LocationInput = Field(..., description=(
        "Where to run the simulation — a JSON object (not a string). Pass "
        'a GPS point {"kind":"point","lat":..,"lon":..} or an admin area '
        '{"kind":"admin_area","country":"US","state":"Alabama","county":"Cullman"}.'
    ))
    soil_profile: Optional[str] = Field(None, description=(
        "Soil profile ID from the user's library or a public/shared catalog. "
        "Thousands of profiles exist — do not guess. Call resolve_soil_profile "
        "or list_soil_profiles to obtain a valid ID. Omit to auto-pick a "
        "generic profile based on location."
    ))
    weather_dataset: Optional[str] = Field(None, description=(
        "Weather source identifier (e.g. 'power', 'chirps_era5'). Omit to use "
        "the system default (NASA POWER)."
    ))
    planting: PlantingConfig = Field(..., description=(
        "Planting parameters as a JSON object. Only `date` is required; "
        "everything else falls back to per-crop defaults. Example: "
        '{"date":"2024-03-01"}  or  '
        '{"date":"2024-03-01","population":7.0,"spacing":75.0,"method":"S"}.'
    ))
    fertilizer: List[FertilizerApplication] = Field(
        default_factory=list,
        description="Fertilizer event list. Empty = no fertilization.",
    )
    irrigation: IrrigationConfig = Field(
        default_factory=IrrigationConfig,
        description="Irrigation strategy and (for fixed schedule) event list.",
    )
    harvest: HarvestConfig = Field(
        default_factory=HarvestConfig,
        description="Harvest timing strategy.",
    )
    initial_conditions: InitialConditions = Field(
        default_factory=InitialConditions,
        description="Soil state at simulation start.",
    )

    additional_instructions: Optional[str] = Field(None, description=(
        "Free-form notes the user provided about what they want emphasized in "
        "the response (e.g. 'focus on nitrogen stress', 'compare to last year'). "
        "Passed through to response synthesis."
    ))

    @field_validator("crop", mode="before")
    @classmethod
    def _uppercase_crop(cls, v):
        return v.upper() if isinstance(v, str) else v

    @field_validator("crop", mode="after")
    @classmethod
    def _crop_must_be_registered(cls, v: str) -> str:
        """Reject unregistered crop codes at the field level so the error
        `loc` is `('crop',)` — makes suggestions map cleanly in the React
        envelope."""
        # Lazy import keeps Django ORM out of module-load time.
        from dssat_agent.schemas.enums_dynamic import build_crop_enum

        valid = build_crop_enum()
        if valid and v not in valid:
            raise ValueError(
                f"crop '{v}' is not a registered DSSAT crop code"
            )
        return v

    @field_validator("cultivar", mode="after")
    @classmethod
    def _cultivar_must_match_crop(cls, v, info):
        """Cross-field check: cultivar must be registered for the already-
        validated crop. Runs after `crop` (declaration order)."""
        if not v:
            return v
        crop = (info.data or {}).get("crop")
        if not crop:
            # crop failed earlier; skip this check to avoid cascading noise.
            return v
        from dssat_agent.schemas.enums_dynamic import build_cultivar_enum

        valid = build_cultivar_enum(crop)
        if valid and v not in valid:
            raise ValueError(
                f"cultivar '{v}' is not registered for crop '{crop}'"
            )
        return v


class SingleExperiment(_ExperimentBase):
    """One treatment at one location — the default experiment type."""

    experiment_type: Literal[ExperimentType.SINGLE] = Field(
        ExperimentType.SINGLE,
        description=(
            "Discriminator — 'single' runs one simulation at the given "
            "location with the given parameters. This is the default when "
            "experiment_type is not specified."
        ),
    )
    num_years: int = Field(1, ge=1, le=10, description=(
        "Number of consecutive simulated years starting from planting_date."
    ))


class EnsembleExperiment(_ExperimentBase):
    """Base treatment plus alternative treatments compared side-by-side."""

    experiment_type: Literal[ExperimentType.ENSEMBLE] = Field(
        ExperimentType.ENSEMBLE,
        description=(
            "Discriminator — 'ensemble' runs the base treatment plus each "
            "entry in `treatments`, displaying them side by side. Use for "
            "what-if comparisons (different planting dates, nitrogen rates, "
            "cultivars)."
        ),
    )
    treatments: List[dict] = Field(..., min_length=1, description=(
        "List of treatment-override dicts. Each dict overrides fields of the "
        "base experiment (e.g. {'planting': {'date': '2024-04-15'}} or "
        "{'fertilizer': [...custom applications...]}). Results for each "
        "treatment render in the comparison table."
    ))


class MonteCarloExperiment(_ExperimentBase):
    """Spatial uncertainty: many sampled locations, quantile-aggregated output."""

    experiment_type: Literal[ExperimentType.MONTE_CARLO] = Field(
        ExperimentType.MONTE_CARLO,
        description=(
            "Discriminator — 'monte_carlo' samples many locations within the "
            "specified region/buffer and aggregates yield/stress/biomass with "
            "quantile reporting (p10/p50/p90). Use for regional-scale "
            "what-is-the-expected-yield questions."
        ),
    )
    n_locations: int = Field(30, ge=2, le=500, description=(
        "Number of spatial samples to draw. Higher values tighten quantile "
        "estimates but increase runtime. Default 30 is a reasonable balance."
    ))
    n_weather_realizations: int = Field(1, ge=1, le=500, description=(
        "Number of stochastic weather realizations per location. Default 1 "
        "(use recorded weather)."
    ))
    sampling_method: SamplingMethod = Field(
        SamplingMethod.LATIN_HYPERCUBE,
        description=(
            "Spatial sampling strategy within the region. Latin hypercube is "
            "the default."
        ),
    )


class BatchExperiment(_ExperimentBase):
    """Parameter sweep: cartesian product of variations."""

    experiment_type: Literal[ExperimentType.BATCH] = Field(
        ExperimentType.BATCH,
        description=(
            "Discriminator — 'batch' runs the cartesian product of "
            "`variations`, producing one simulation per combination. Use for "
            "large sensitivity analyses (e.g. 10 cultivars × 5 planting dates)."
        ),
    )
    variations: List[dict] = Field(..., min_length=1, description=(
        "Sweep axes. Each dict specifies one variable and its values, e.g. "
        "{'cultivar': ['PI0001','PI0002']} or {'planting.date': "
        "['2024-03-01','2024-04-01','2024-05-01']}. All combinations are run."
    ))


ExperimentInput = Annotated[
    Union[SingleExperiment, EnsembleExperiment, MonteCarloExperiment, BatchExperiment],
    Field(
        discriminator="experiment_type",
        description=(
            "DSSAT experiment specification. Choose experiment_type first "
            "('single' by default). The selected variant surfaces its own "
            "required fields (e.g. monte_carlo requires n_locations)."
        ),
    ),
]


def default_experiment_type() -> ExperimentType:
    """Default used when the LLM omits `experiment_type`."""
    return ExperimentType.SINGLE
