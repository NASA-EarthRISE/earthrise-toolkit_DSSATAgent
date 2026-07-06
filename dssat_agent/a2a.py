"""
dssat_agent (DSSATAgent) A2A surface — skill table + agent card + URLs.

Consolidated into a single thin module matching the pattern used by
``data_agent/a2a.py`` and ``knowledge_agent/a2a.py``. Every skill is a
thin wrapper that delegates to the single dispatcher in
``dssat_agent.services.executor.dispatch_skill`` — no handler logic is
duplicated here. The canonical skill IDs are the keys of
``executor.SKILL_HANDLERS``.
"""

from __future__ import annotations

from typing import Any, Dict

from earthrise_agents_base.a2a import (
    SkillTable,
    build_agent_card,
    build_urlpatterns,
    register_agent,
)


def _dispatch(skill_id: str, params: Dict[str, Any]) -> Any:
    """Route a skill to the single executor dispatcher."""
    from dssat_agent.services.executor import dispatch_skill
    return dispatch_skill(skill_id, params)


skills = SkillTable()


# ---------------------------------------------------------------------------
# Group 1: Crop Reference
# ---------------------------------------------------------------------------

@skills.skill(
    id="list_crops",
    name="List Crops",
    description="List all 18 supported crop types with codes, names, models, and cultivar counts.",
)
def _list_crops(params: Dict[str, Any]) -> Any:
    return _dispatch("list_crops", params)


@skills.skill(
    id="list_cultivars",
    name="List Cultivars",
    description="List all cultivars for a given crop code.",
)
def _list_cultivars(params: Dict[str, Any]) -> Any:
    return _dispatch("list_cultivars", params)


@skills.skill(
    id="get_cultivar_details",
    name="Get Cultivar Details",
    description="Get full cultivar and ecotype parameters for a specific cultivar.",
)
def _get_cultivar_details(params: Dict[str, Any]) -> Any:
    return _dispatch("get_cultivar_details", params)


@skills.skill(
    id="calculate_weather_dates",
    name="Calculate Weather Dates",
    description="Compute the weather date range required for a crop given its planting date.",
)
def _calculate_weather_dates(params: Dict[str, Any]) -> Any:
    return _dispatch("calculate_weather_dates", params)


# ---------------------------------------------------------------------------
# Group 2: Soil Management
# ---------------------------------------------------------------------------

@skills.skill(
    id="list_soils",
    name="List Soil Profiles",
    description="List stored soil profiles with optional filtering.",
)
def _list_soils(params: Dict[str, Any]) -> Any:
    return _dispatch("list_soils", params)


@skills.skill(
    id="get_soil_profile",
    name="Get Soil Profile",
    description="Get a full soil profile with all layers.",
)
def _get_soil_profile(params: Dict[str, Any]) -> Any:
    return _dispatch("get_soil_profile", params)


@skills.skill(
    id="create_soil_profile",
    name="Create Soil Profile",
    description="Create a new soil profile (without layers).",
)
def _create_soil_profile(params: Dict[str, Any]) -> Any:
    return _dispatch("create_soil_profile", params)


@skills.skill(
    id="create_soil_from_texture",
    name="Create Soil from Texture",
    description="Create a soil profile with hydraulic properties auto-estimated from clay/silt percentages using Rosetta pedotransfer.",
)
def _create_soil_from_texture(params: Dict[str, Any]) -> Any:
    return _dispatch("create_soil_from_texture", params)


@skills.skill(
    id="add_soil_layer",
    name="Add Soil Layer",
    description="Add a layer to an existing soil profile.",
)
def _add_soil_layer(params: Dict[str, Any]) -> Any:
    return _dispatch("add_soil_layer", params)


@skills.skill(
    id="update_soil_layer",
    name="Update Soil Layer",
    description="Update parameters of an existing soil layer.",
)
def _update_soil_layer(params: Dict[str, Any]) -> Any:
    return _dispatch("update_soil_layer", params)


@skills.skill(
    id="remove_soil_layer",
    name="Remove Soil Layer",
    description="Remove a layer from a soil profile.",
)
def _remove_soil_layer(params: Dict[str, Any]) -> Any:
    return _dispatch("remove_soil_layer", params)


@skills.skill(
    id="estimate_soil_properties",
    name="Estimate Soil Properties",
    description="Estimate hydraulic properties from texture (no persistence).",
)
def _estimate_soil_properties(params: Dict[str, Any]) -> Any:
    return _dispatch("estimate_soil_properties", params)


# ---------------------------------------------------------------------------
# Group 3: Experiment Management
# ---------------------------------------------------------------------------

@skills.skill(
    id="run_simulation",
    name="Run Simulation",
    description=(
        "Run a DSSAT crop simulation. Accepts full experiment definition with "
        "crop, cultivar, soil, weather, planting, and optional management sections. "
        "Returns summary statistics and output tables, or missing_data status if "
        "weather is incomplete."
    ),
)
def _run_simulation(params: Dict[str, Any]) -> Any:
    return _dispatch("run_simulation", params)


@skills.skill(
    id="run_ensemble",
    name="Run Ensemble Simulation",
    description=(
        "Run multiple DSSAT treatments in a single batch execution. "
        "Supports spatial ensembles (varying weather/soil), management "
        "comparisons (varying planting/fertilizer/irrigation), and "
        "sensitivity analysis. All parameters can appear in 'base' "
        "(shared defaults) or per 'treatments' entry (override). "
        "Treatment-level values replace base-level values."
    ),
    tags=["dssat", "ensemble", "spatial", "batch"],
)
def _run_ensemble(params: Dict[str, Any]) -> Any:
    return _dispatch("run_ensemble", params)


@skills.skill(
    id="run_monte_carlo",
    name="Run Monte Carlo Simulation",
    description=(
        "Run a Monte Carlo spatial uncertainty analysis. Samples weather "
        "grid points from PostGIS rasters across a region, runs DSSATBatch "
        "with each sample as a treatment, and returns quantile-based "
        "yield statistics (P5-P95, mean, std). Supports three spatial "
        "modes: bounding box, center+radius, and admin boundary."
    ),
    tags=["dssat", "monte_carlo", "spatial", "uncertainty"],
)
def _run_monte_carlo(params: Dict[str, Any]) -> Any:
    return _dispatch("run_monte_carlo", params)


@skills.skill(
    id="validate_experiment",
    name="Validate Experiment",
    description="Validate experiment parameters without running the simulation.",
)
def _validate_experiment(params: Dict[str, Any]) -> Any:
    return _dispatch("validate_experiment", params)


@skills.skill(
    id="list_admin_units",
    name="List Admin Units",
    description="List available administrative boundary units for spatial grid selection.",
)
def _list_admin_units(params: Dict[str, Any]) -> Any:
    return _dispatch("list_admin_units", params)


@skills.skill(
    id="get_admin_geojson",
    name="Get Admin Boundaries GeoJSON",
    description="Return GeoJSON geometry for administrative boundary units.",
)
def _get_admin_geojson(params: Dict[str, Any]) -> Any:
    return _dispatch("get_admin_geojson", params)


# ---------------------------------------------------------------------------
# Group 4: File Retrieval
# ---------------------------------------------------------------------------

@skills.skill(
    id="get_soil_sol_file",
    name="Get Soil .SOL File",
    description="Retrieve pre-generated DSSAT .SOL file content for a stored soil profile.",
)
def _get_soil_sol_file(params: Dict[str, Any]) -> Any:
    return _dispatch("get_soil_sol_file", params)


@skills.skill(
    id="get_crop_cul_file",
    name="Get Crop .CUL File",
    description="Retrieve pre-generated DSSAT .CUL file content for a crop type.",
)
def _get_crop_cul_file(params: Dict[str, Any]) -> Any:
    return _dispatch("get_crop_cul_file", params)


@skills.skill(
    id="get_crop_eco_file",
    name="Get Crop .ECO File",
    description="Retrieve pre-generated DSSAT .ECO file content for a crop type.",
)
def _get_crop_eco_file(params: Dict[str, Any]) -> Any:
    return _dispatch("get_crop_eco_file", params)


@skills.skill(
    id="get_simulation_files",
    name="List Simulation Files",
    description="List captured DSSAT input and output files for a completed simulation.",
)
def _get_simulation_files(params: Dict[str, Any]) -> Any:
    return _dispatch("get_simulation_files", params)


@skills.skill(
    id="get_simulation_file",
    name="Get Simulation File",
    description="Retrieve content of a specific DSSAT file from a simulation run.",
)
def _get_simulation_file(params: Dict[str, Any]) -> Any:
    return _dispatch("get_simulation_file", params)


# ---------------------------------------------------------------------------
# Group 5: Experiment History
# ---------------------------------------------------------------------------

@skills.skill(
    id="get_experiment",
    name="Get Experiment",
    description="Retrieve full experiment session details including related simulation results.",
)
def _get_experiment(params: Dict[str, Any]) -> Any:
    return _dispatch("get_experiment", params)


@skills.skill(
    id="list_experiments",
    name="List Experiments",
    description="List experiment sessions with optional filtering and pagination.",
)
def _list_experiments(params: Dict[str, Any]) -> Any:
    return _dispatch("list_experiments", params)


# ---------------------------------------------------------------------------
# Group 6: Reference Data
# ---------------------------------------------------------------------------

@skills.skill(
    id="list_codes",
    name="List Valid Codes",
    description=(
        "List valid DSSAT codes for a category: fertilizer_materials, "
        "application_methods, planting_methods, planting_distributions, "
        "drainage_types, soil_textures, soil_colors, evaporation_methods, "
        "som_methods, harvest_components, harvest_sizes."
    ),
)
def _list_codes(params: Dict[str, Any]) -> Any:
    return _dispatch("list_codes", params)


# ---------------------------------------------------------------------------
# Group 7: High-level Workflow
# ---------------------------------------------------------------------------

@skills.skill(
    id="prepare_weather",
    name="Prepare Weather",
    description="Resolve and prepare the weather data bundle required for an experiment.",
)
def _prepare_weather(params: Dict[str, Any]) -> Any:
    return _dispatch("prepare_weather", params)


@skills.skill(
    id="build_experiment",
    name="Build Experiment",
    description="Assemble a fully-resolved experiment definition from raw and wizard parameters.",
)
def _build_experiment(params: Dict[str, Any]) -> Any:
    return _dispatch("build_experiment", params)


@skills.skill(
    id="run_experiment",
    name="Run Experiment",
    description="Run a pre-built experiment definition through the DSSAT runner.",
)
def _run_experiment(params: Dict[str, Any]) -> Any:
    return _dispatch("run_experiment", params)


@skills.skill(
    id="run_full_simulation",
    name="Run Full Simulation",
    description="Prepare weather, build, and run a complete simulation in one step.",
)
def _run_full_simulation(params: Dict[str, Any]) -> Any:
    return _dispatch("run_full_simulation", params)


# ---------------------------------------------------------------------------
# Group 8: Batch (multi-location)
# ---------------------------------------------------------------------------

@skills.skill(
    id="run_batch",
    name="Run Batch Experiment",
    description="Create and asynchronously dispatch a multi-location batch experiment.",
)
def _run_batch(params: Dict[str, Any]) -> Any:
    return _dispatch("run_batch", params)


@skills.skill(
    id="get_batch",
    name="Get Batch Experiment",
    description="Get batch experiment status, aggregate results, and per-location children.",
)
def _get_batch(params: Dict[str, Any]) -> Any:
    return _dispatch("get_batch", params)


AGENT_CARD = build_agent_card(
    name="DSSATAgent - DSSAT Crop Modeling",
    description=(
        "Builds and runs DSSAT crop simulations using DSSATTools. "
        "Supports 18 crop types with full experiment parameterization, "
        "soil profile management, pedotransfer estimation, and validated "
        "simulation execution. Returns summary statistics and detailed "
        "output tables (plant growth, soil water, nitrogen, weather)."
    ),
    version="1.0.0",
    skills=skills,
    capabilities={"streaming": False},
)


register_agent(name="dssat_agent", card=AGENT_CARD, skills=skills)


# ---------------------------------------------------------------------------
# URL patterns
# ---------------------------------------------------------------------------

app_name = "dssat_agent_a2a"
urlpatterns = build_urlpatterns(agent_name="dssat_agent")
