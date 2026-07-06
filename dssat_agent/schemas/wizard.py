"""
Input schema for the DSSAT experiment wizard tool.

Every field is optional — the wizard accumulates state across multiple
`needs_input`/resume cycles. The step machine walks per ``experiment_type``:

  * **single**       basics → fork → planting → soil → weather → management
                     → initial_conditions → review
  * **ensemble**     basics → soil → weather → protocols_loop → review
  * **sensitivity**  basics → soil → weather → sweep_category → sweep_axes
                     → baseline_planting → baseline_management
                     → baseline_initial → review
  * **monte_carlo**  spatial geometry → density → crop → soil → weather
                     → auto_planting_toggle → template_planting
                     → template_management → template_initial → review
  * **batch**        locations_loop → protocols_loop → matrix → review

The LLM re-invokes ``experiment_wizard_step`` each turn with everything
it has been able to extract; the tool returns ``needs_input`` until each
step's required fields are satisfied, then advances.
"""

from __future__ import annotations

from datetime import date as _date
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from data_agent.schemas.location import LocationInput


class FertilizerEvent(BaseModel):
    """One fertilizer application."""
    days_after_planting: int = Field(0, ge=0, description=(
        "Days after planting when this application is made (0 = at-plant)."
    ))
    n_kg_ha: float = Field(0.0, ge=0, description="Nitrogen, kg/ha.")
    p_kg_ha: float = Field(0.0, ge=0, description="Phosphorus (P2O5), kg/ha.")
    k_kg_ha: float = Field(0.0, ge=0, description="Potassium (K2O), kg/ha.")
    depth_cm: float = Field(5.0, ge=0, description="Application depth, cm.")


class ProtocolInput(BaseModel):
    """A single treatment protocol used by ensemble / sensitivity / batch.

    A loose container — the LLM populates whatever fields the user
    described for this protocol. Empty fields fall back to the wizard's
    top-level basics (crop, location) at run-args build time.
    """
    name: Optional[str] = Field(None, description="Display name, e.g. 'High N'")
    crop: Optional[str] = Field(None, description=(
        "DSSAT 2-letter crop code, defaults to the wizard's top-level crop."
    ))
    cultivar_code: Optional[str] = Field(None, description="DSSAT cultivar code.")
    planting_date: Optional[_date] = Field(None, description=(
        "Optional per-protocol planting date — falls back to top-level."
    ))
    plant_population: Optional[float] = Field(None, ge=0)
    row_spacing: Optional[float] = Field(None, ge=0)
    planting_method: Optional[str] = Field(None)
    fertilizer: Optional[List[FertilizerEvent]] = Field(None)
    irrigation_strategy: Optional[str] = Field(None)
    harvest_strategy: Optional[str] = Field(None)
    harvest_date: Optional[_date] = Field(None)


class SpatialConfig(BaseModel):
    """Monte Carlo region + density (the **`spatial`** field on the wizard).

    Distinct from the top-level ``location`` field, which only accepts
    ``kind="point"`` or ``kind="admin_area"``. Region shape goes here
    on ``mode``; never put ``"circle" / "bbox" / "admin"`` into
    ``location.kind``.

    ``mode`` is the discriminator. Geometry fields are valid only for
    the matching mode; others are ignored. Density fields
    (``sampling_strategy`` / ``grid_spacing`` / ``n_points``) apply to
    every mode.
    """
    mode: Optional[str] = Field(None, description=(
        "Region shape — discriminator for SpatialConfig (NOT for "
        "`location`). One of: "
        "'circle' (set center_lat/center_lon/radius_km), "
        "'bbox' (set min_lat/max_lat/min_lon/max_lon), or "
        "'admin' (set admin_name/admin_level)."
    ))
    # circle
    center_lat: Optional[float] = Field(None, ge=-90, le=90, description=(
        "Center latitude — used only when mode='circle'."
    ))
    center_lon: Optional[float] = Field(None, ge=-180, le=180, description=(
        "Center longitude — used only when mode='circle'."
    ))
    radius_km: Optional[float] = Field(None, ge=0, description=(
        "Circle radius in kilometers — used only when mode='circle'."
    ))
    # bbox
    min_lat: Optional[float] = Field(None, ge=-90, le=90, description=(
        "Bbox south edge — used only when mode='bbox'."
    ))
    max_lat: Optional[float] = Field(None, ge=-90, le=90, description=(
        "Bbox north edge — used only when mode='bbox'."
    ))
    min_lon: Optional[float] = Field(None, ge=-180, le=180, description=(
        "Bbox west edge — used only when mode='bbox'."
    ))
    max_lon: Optional[float] = Field(None, ge=-180, le=180, description=(
        "Bbox east edge — used only when mode='bbox'."
    ))
    # admin
    admin_name: Optional[str] = Field(None, description=(
        "Named admin polygon (e.g. 'Alabama', 'Cullman') — used only "
        "when mode='admin'."
    ))
    admin_level: Optional[str] = Field(None, description=(
        "'admin1' (state/province) or 'admin2' (county/district) — "
        "used only when mode='admin'."
    ))
    # density (apply to every mode)
    grid_spacing: Optional[float] = Field(None, gt=0, description=(
        "Lattice spacing in decimal degrees — pair with "
        "sampling_strategy='systematic'."
    ))
    n_points: Optional[int] = Field(None, ge=1, le=99, description=(
        "Number of random samples (1-99) — pair with "
        "sampling_strategy='random'."
    ))
    sampling_strategy: Optional[str] = Field(None, description=(
        "'systematic' (deterministic lattice via grid_spacing) or "
        "'random' (uniform sampling via n_points)."
    ))


class SensitivitySweep(BaseModel):
    """Sweep config — one category, Cartesian within that category."""
    category: Optional[str] = Field(None, description=(
        "'planting', 'cultivar', or 'management'."
    ))
    # planting axes
    date_central_doy: Optional[int] = Field(None, ge=1, le=366)
    date_offset_days: Optional[int] = Field(None, ge=0)
    date_steps: Optional[int] = Field(None, ge=1)
    pop_min: Optional[float] = Field(None, ge=0)
    pop_max: Optional[float] = Field(None, ge=0)
    pop_steps: Optional[int] = Field(None, ge=1)
    rs_min: Optional[float] = Field(None, ge=0)
    rs_max: Optional[float] = Field(None, ge=0)
    rs_steps: Optional[int] = Field(None, ge=1)
    # cultivar axis
    cultivars: Optional[List[str]] = Field(None, description=(
        "List of cultivar codes to sweep (cultivar category only)."
    ))
    # management axis
    practice: Optional[str] = Field(None, description=(
        "'fertilizer', 'irrigation', 'tillage', 'chemical', or 'residue'."
    ))
    amount_min: Optional[float] = Field(None)
    amount_max: Optional[float] = Field(None)
    amount_steps: Optional[int] = Field(None, ge=1)
    apps_min: Optional[int] = Field(None, ge=1)
    apps_max: Optional[int] = Field(None, ge=1)
    gap_days_min: Optional[int] = Field(None, ge=1)
    gap_days_max: Optional[int] = Field(None, ge=1)
    gap_steps: Optional[int] = Field(None, ge=1)


class BatchPair(BaseModel):
    """One (location_idx, protocol_idx) cell in a batch matrix."""
    location_idx: int = Field(..., ge=0)
    protocol_idx: int = Field(..., ge=0)


class ExperimentWizardInput(BaseModel):
    """Accumulating wizard state.

    The LLM re-invokes `experiment_wizard_step` each turn with every field
    it has been able to extract so far from the conversation. The tool
    returns `needs_input` until each step's required fields are collected,
    then advances to the next step.
    """

    # ---- Step: experiment type ----
    experiment_type: Optional[str] = Field(None, description=(
        "Type of experiment to set up. One of: 'single' (one DSSAT run at "
        "one location), 'ensemble' (multiple custom protocols at one "
        "location), 'sensitivity' (sweep one variable category Cartesian-"
        "style), 'monte_carlo' (one shared protocol over many spatial grid "
        "points), or 'batch' (matrix of (field, protocol) pairs). The chat "
        "wizard fully supports 'single' — for the others, the user is "
        "directed to the visual wizard at /dssat/wizard/ which shares the "
        "same WizardDraft state model."
    ))

    # ---- Step: basics ----
    crop: Optional[str] = Field(None, description=(
        "DSSAT 2-letter crop code (e.g. MZ for maize, WH for wheat). If the "
        "user named a crop by common name, map it to the DSSAT code; if "
        "unsure, call `resolve_crops` first."
    ))
    location: Optional[LocationInput] = Field(None, description=(
        "Where to run the simulation — a JSON object (point or admin_area). "
        "Same shape as for `run_experiment`."
    ))
    planting_date: Optional[_date] = Field(None, description=(
        "Planting date, ISO YYYY-MM-DD. Optional — if you only know the "
        "season's year, set ``year`` and the wizard will resolve the actual "
        "planting day from the in-house planting-date raster (southeast US "
        "coverage; falls back to a per-crop default elsewhere)."
    ))
    year: Optional[int] = Field(None, ge=1980, le=2050, description=(
        "Planting year (4-digit). Mandatory unless ``planting_date`` is set. "
        "When ``planting_date`` is missing, the wizard looks up the typical "
        "DOY for the location and combines it with this year."
    ))

    # ---- Step: fork ----
    shortcircuit: Optional[bool] = Field(None, description=(
        "Only set after the user has chosen the fork. True = run "
        "immediately with defaults for every non-core parameter. False = "
        "continue into the full guided wizard. Do NOT set this until the "
        "user has answered the shortcircuit question."
    ))

    # ---- Step: planting ----
    plant_population: Optional[float] = Field(None, ge=0, description=(
        "Plant population, plants/m² (typical maize ~7.0)."
    ))
    row_spacing: Optional[float] = Field(None, ge=0, description=(
        "Row spacing, cm (typical maize ~75)."
    ))
    planting_method: Optional[str] = Field(None, description=(
        "DSSAT planting method code: 'S' (dry seed, default), 'T' "
        "(transplanted), 'P' (pre-germinated)."
    ))

    # ---- Step: cultivar ----
    cultivar_code: Optional[str] = Field(None, description=(
        "DSSAT cultivar code (e.g. 'IB0011' for DEKALBXL45 maize). Optional — "
        "leave None and set ``cultivar_use_defaults=True`` to auto-pick the "
        "first registered cultivar for the chosen crop. Call list_cultivars(crop) "
        "to see options."
    ))
    cultivar_use_defaults: Optional[bool] = Field(None, description=(
        "Set True to auto-select the first registered cultivar for the chosen "
        "crop. Mutually exclusive with ``cultivar_code``."
    ))
    dssat_model: Optional[str] = Field(None, description=(
        "Optional DSSAT model variant (e.g. 'MZCER' vs 'MZIXM' for maize). "
        "Most crops have a single default — only set this when the user "
        "explicitly named a model variant."
    ))

    # ---- Step: soil ----
    soil_id: Optional[str] = Field(None, description=(
        "DSSAT soil profile ID. Leave None to auto-select by location."
    ))

    # ---- Step: weather ----
    weather_source: Optional[str] = Field(None, description=(
        "Weather data source. Common values: 'nasa_power', 'cli'. Leave "
        "None to auto-select."
    ))

    # ---- Step: management ----
    fertilizer: Optional[List[FertilizerEvent]] = Field(None, description=(
        "List of fertilizer applications. Empty list = unfertilized."
    ))
    irrigation_strategy: Optional[str] = Field(None, description=(
        "'rainfed' (no irrigation), 'auto' (automatic when soil water "
        "drops), or 'scheduled' (only with explicit events — chat wizard "
        "treats scheduled as auto for now)."
    ))
    harvest_strategy: Optional[str] = Field(None, description=(
        "'auto' (DSSAT picks at maturity), 'on_maturity' (force at "
        "physiological maturity), or 'on_date'."
    ))
    harvest_date: Optional[_date] = Field(None, description=(
        "Required when harvest_strategy = 'on_date'."
    ))

    # ---- Step: initial conditions ----
    initial_water: Optional[str] = Field(None, description=(
        "Initial soil water: 'field_capacity', 'wilting_point', or 'half' "
        "(midpoint). Defaults to field_capacity."
    ))
    residue_kg_ha: Optional[float] = Field(None, ge=0, description=(
        "Surface crop residue at start, kg/ha. Default 0."
    ))

    # ---- Step: review ----
    num_years: Optional[int] = Field(None, ge=1, le=50, description=(
        "Number of simulation years. Default 1."
    ))
    num_reps: Optional[int] = Field(None, ge=1, le=100, description=(
        "Number of replicates. Default 1."
    ))
    simulation_start_date: Optional[_date] = Field(None, description=(
        "Simulation start date (sdate) — usually a few weeks before "
        "planting. Default = 30 days before planting_date."
    ))
    review_confirmed: Optional[bool] = Field(None, description=(
        "Set True after user reviews and confirms. Triggers run."
    ))

    # ---- Per-step defaults flags ----
    # When the user replies with "use defaults" / "auto" / "skip" to a
    # step prompt, the LLM sets the matching flag — the wizard then
    # advances without requiring concrete values for that step.
    planting_use_defaults: Optional[bool] = Field(None, description=(
        "Set True to accept the default planting parameters."
    ))
    soil_use_defaults: Optional[bool] = Field(None, description=(
        "Set True to let the system auto-select a soil profile."
    ))
    weather_use_defaults: Optional[bool] = Field(None, description=(
        "Set True to let the system auto-select a weather source."
    ))
    management_use_defaults: Optional[bool] = Field(None, description=(
        "Set True to use unfertilized + rainfed + auto-harvest defaults."
    ))
    initial_use_defaults: Optional[bool] = Field(None, description=(
        "Set True to accept default initial soil water + 0 residue."
    ))

    # ---- Multi-treatment (ensemble / sensitivity / batch) ----
    protocols: Optional[List[ProtocolInput]] = Field(None, description=(
        "List of treatment protocols. For ensemble: N independent "
        "protocols all run at one location. For batch: paired with "
        "locations via the matrix. Empty / None means none configured "
        "yet — the chat asks for the first one when entering the loop."
    ))
    protocols_complete: Optional[bool] = Field(None, description=(
        "Set True when the user signals 'no more protocols' to close "
        "the protocols loop."
    ))

    # ---- Sensitivity sweep ----
    sensitivity: Optional[SensitivitySweep] = Field(None, description=(
        "Sweep configuration for the 'sensitivity' experiment_type."
    ))

    # ---- Monte Carlo spatial ----
    spatial: Optional[SpatialConfig] = Field(None, description=(
        "Spatial geometry + density for the 'monte_carlo' experiment_type."
    ))
    auto_planting: Optional[bool] = Field(None, description=(
        "Monte Carlo only: True = resolve planting date per grid point "
        "from the in-situ raster; False = use the template's fixed "
        "planting_date for every point."
    ))

    # ---- Batch ----
    locations: Optional[List[LocationInput]] = Field(None, description=(
        "Batch only: list of locations (points or admin areas). "
        "Each becomes one batch child."
    ))
    locations_complete: Optional[bool] = Field(None, description=(
        "Set True to close the locations loop in batch mode."
    ))
    pairs: Optional[List[BatchPair]] = Field(None, description=(
        "Batch only: explicit (location_idx, protocol_idx) cells. "
        "If empty / None, the wizard pairs every protocol with every "
        "location (full Cartesian)."
    ))
