"""
Input schema for the DSSAT experiment query tool.

Targets an already-run experiment by UUID and asks for a specific slice
(variables, a chart, stress summary, or the full summary). The LLM gets
`experiment_id` from conversation memory (set by `run_experiment` when a
simulation completes).
"""

from __future__ import annotations

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field, model_validator


class ExperimentQueryType(str, Enum):
    VARIABLES = "variables"
    CHART = "chart"
    STRESS = "stress"
    SUMMARY = "summary"


class ExperimentChartType(str, Enum):
    LAI = "lai"
    SOIL_WATER = "soil_water"
    WATER_STRESS = "water_stress"
    NITROGEN_STRESS = "nitrogen_stress"
    PHOSPHORUS_STRESS = "phosphorus_stress"
    POTASSIUM_STRESS = "potassium_stress"
    ET_COMPONENTS = "et_components"
    SOIL_NITROGEN = "soil_nitrogen"
    TEMPERATURE = "temperature"


class QueryExperimentInput(BaseModel):
    """Parameters for querying an existing DSSAT experiment."""

    experiment_id: str = Field(..., min_length=8, description=(
        "UUID of the experiment to query. Read this from conversation "
        "memory (it's set when an experiment completes) — do NOT ask the "
        "user for it."
    ))
    query_type: ExperimentQueryType = Field(..., description=(
        "What kind of output to return. 'variables' pulls specific values; "
        "'chart' renders a time-series figure; 'stress' aggregates stress "
        "factors; 'summary' returns the complete simulation summary."
    ))
    variables: Optional[List[str]] = Field(None, description=(
        "Required when query_type='variables'. Short-codes for the "
        "variables to pull (e.g. ['lai', 'n_uptake', 'harvest_index']). "
        "Ignored for other query types."
    ))
    chart_type: Optional[ExperimentChartType] = Field(None, description=(
        "Required when query_type='chart'. Which chart to render."
    ))

    @model_validator(mode="after")
    def _required_when(self) -> "QueryExperimentInput":
        if self.query_type == ExperimentQueryType.VARIABLES and not self.variables:
            raise ValueError("`variables` is required when query_type='variables'")
        if self.query_type == ExperimentQueryType.CHART and not self.chart_type:
            raise ValueError("`chart_type` is required when query_type='chart'")
        return self
