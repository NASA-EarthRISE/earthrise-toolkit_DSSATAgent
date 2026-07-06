"""
Static enums surfaced to the LLM in DSSAT experiment tool schemas.

Values mirror the DSSAT `CODE_VARS` tables from the DSSATTools vendored copy.
Where the official table exposes >20 codes we curate a common-subset and rely
on the Codes page (linked from the dssat-experiment-run skill body) to let
users look up less-common codes. Post-validation in each tool's wrapper
accepts any valid `CODE_VARS` member via `validate_code_var()` —  the enum
here is a generation guide, not the complete allowlist.
"""

from __future__ import annotations

from enum import Enum


class ExperimentType(str, Enum):
    """Which simulation runner to use.

    SINGLE     — one treatment at one location. Default when unspecified.
    ENSEMBLE   — base treatment plus N alternative treatments (nitrogen sweep,
                 planting-date comparisons, cultivar comparisons, etc.).
    MONTE_CARLO — spatial uncertainty: many sampled locations within a region
                  or buffer, aggregated with quantile reporting.
    BATCH      — many distinct experiments (different locations / cultivars /
                 planting dates) executed in parallel.
    """

    SINGLE = "single"
    ENSEMBLE = "ensemble"
    MONTE_CARLO = "monte_carlo"
    BATCH = "batch"


class PlantingMethod(str, Enum):
    """DSSAT `plme` code. See Codes page → Planting Method for full list.

    Most field-crop experiments use S (dry seed) or I (transplant).
    """

    DRY_SEED = "S"
    TRANSPLANT = "I"
    PRESPROUTED_SEED = "P"
    NURSERY_SEED = "N"
    BROADCAST = "B"
    HILL = "H"
    ROW = "R"
    VEGETATIVE = "V"
    SEED_PIECE = "T"
    CUTTINGS = "C"


class FertilizerType(str, Enum):
    """DSSAT `fmcd` code — fertilizer material. Curated common subset.

    FE005 = Urea (most common nitrogen source). For other materials consult
    the Codes page → Fertilizer Material.
    """

    UREA = "FE005"
    AMMONIUM_NITRATE = "FE001"
    AMMONIUM_SULFATE = "FE002"
    ANHYDROUS_AMMONIA = "FE004"
    DAP = "FE010"
    MAP = "FE013"
    KCL = "FE014"
    POTASSIUM_NITRATE = "FE015"
    POTASSIUM_SULFATE = "FE016"
    UAN = "FE017"
    TSP = "FE012"
    CALCIUM_NITRATE = "FE008"
    NOT_SPECIFIED = "IB001"


class FertilizerApplicationMethod(str, Enum):
    """DSSAT `facd` code — how the fertilizer is placed."""

    BROADCAST_NOT_INCORPORATED = "AP001"
    BROADCAST_INCORPORATED = "AP002"
    BAND_AT_PLANTING = "AP003"
    BAND_ON_SURFACE = "AP004"
    INCORPORATED_VIA_IRRIGATION = "AP005"
    FOLIAR = "AP006"
    DEEP_BAND = "AP007"
    SIDE_DRESS = "AP008"
    FERTIGATION = "AP009"


class IrrigationStrategy(str, Enum):
    """High-level irrigation strategy. Maps to DSSAT's management options.

    NONE — rainfed (no irrigation applied).
    AUTO — automatic irrigation to maintain a soil-moisture threshold.
    FIXED_SCHEDULE — user-specified dates/amounts (list of IrrigationApplication).
    """

    NONE = "none"
    AUTO = "automatic"
    FIXED_SCHEDULE = "fixed"


class IrrigationApplicationMethod(str, Enum):
    """DSSAT `iame` code — how each irrigation event is delivered."""

    FURROW = "IR001"
    ALTERNATING_FURROW = "IR002"
    FLOOD = "IR003"
    SPRINKLER = "IR004"
    DRIP = "IR005"
    SUBSURFACE = "IR006"
    BASIN = "IR007"
    BORDERED = "IR008"
    BED = "IR009"
    PUDDLE = "IR010"
    DEFAULT = "IB001"


class HarvestStrategy(str, Enum):
    """DSSAT `harvs` management option.

    AUTO          — harvest at physiological maturity (harvs='M').
    FIXED_DATE    — use the crop's typical growth cycle length from planting
                    to auto-pick a fixed date (harvs='A').
    REPORTED_DATE — harvest on the date supplied (harvs='R'); requires
                    `HarvestConfig.date` to be set.
    """

    AUTO = "auto"
    FIXED_DATE = "fixed"
    REPORTED_DATE = "reported"


class SamplingMethod(str, Enum):
    """Monte Carlo spatial sampling method."""

    LATIN_HYPERCUBE = "latin_hypercube"
    RANDOM = "random"
    SOBOL = "sobol"
