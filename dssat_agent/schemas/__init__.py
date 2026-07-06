"""
Pydantic schemas owned by the dssat_agent subagent.

- `enums_static` — ExperimentType, PlantingMethod, FertilizerType,
  FertilizerApplicationMethod, IrrigationStrategy, IrrigationApplicationMethod,
  HarvestStrategy, SamplingMethod.
- `enums_dynamic` — build_crop_enum(), build_cultivar_enum(crop), resolver
  helpers for the soil catalog.
- `agronomy` — PlantingConfig, FertilizerInput, FertilizerApplication,
  IrrigationApplication, HarvestConfig, InitialConditions.
- `experiment` — ExperimentInput (discriminated union over single / ensemble /
  monte_carlo / batch).

Location types are provided by `data_agent.schemas.location` (reused, not
duplicated — DSSAT experiments and data queries share the same LocationInput).
"""

from dssat_agent.schemas.enums_static import (
    ExperimentType,
    PlantingMethod,
    FertilizerType,
    FertilizerApplicationMethod,
    IrrigationStrategy,
    IrrigationApplicationMethod,
    HarvestStrategy,
    SamplingMethod,
)
from dssat_agent.schemas.enums_dynamic import (
    build_crop_enum,
    build_cultivar_enum,
    list_soil_profiles_page,
    resolve_soil_profile,
)
from dssat_agent.schemas.agronomy import (
    PlantingConfig,
    FertilizerInput,
    FertilizerApplication,
    IrrigationApplication,
    HarvestConfig,
    InitialConditions,
)
from dssat_agent.schemas.experiment import (
    SingleExperiment,
    EnsembleExperiment,
    MonteCarloExperiment,
    BatchExperiment,
    ExperimentInput,
)
from dssat_agent.schemas.query_experiment import (
    QueryExperimentInput,
    ExperimentQueryType,
    ExperimentChartType,
)
from dssat_agent.schemas.wizard import (
    ExperimentWizardInput,
    FertilizerEvent,
    ProtocolInput,
    SpatialConfig,
    SensitivitySweep,
    BatchPair,
)

__all__ = [
    # Static enums
    "ExperimentType",
    "PlantingMethod",
    "FertilizerType",
    "FertilizerApplicationMethod",
    "IrrigationStrategy",
    "IrrigationApplicationMethod",
    "HarvestStrategy",
    "SamplingMethod",
    # Dynamic enum factories and soil resolvers
    "build_crop_enum",
    "build_cultivar_enum",
    "list_soil_profiles_page",
    "resolve_soil_profile",
    # Composite models
    "PlantingConfig",
    "FertilizerInput",
    "FertilizerApplication",
    "IrrigationApplication",
    "HarvestConfig",
    "InitialConditions",
    # Experiment variants
    "SingleExperiment",
    "EnsembleExperiment",
    "MonteCarloExperiment",
    "BatchExperiment",
    "ExperimentInput",
    # Experiment query
    "QueryExperimentInput",
    "ExperimentQueryType",
    "ExperimentChartType",
    # Experiment wizard
    "ExperimentWizardInput",
    "ProtocolInput",
    "SpatialConfig",
    "SensitivitySweep",
    "BatchPair",
    "FertilizerEvent",
]
