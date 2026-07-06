"""
Step machine for the chat-based DSSAT experiment wizard.

Mirrors the panels of the UI wizard at /dssat/experiment/, but presents
each one as a `needs_input` envelope the orchestrator turns into a
LangGraph interrupt. The user's reply is merged into the accumulating
`ExperimentWizardInput` state on the next invocation.

Steps in canonical order:

    basics            crop, location, planting_date
    fork              run with defaults OR continue customizing
    planting          population, row spacing, planting method
    soil              soil_id (or auto-select)
    weather           weather source (or auto-select)
    management        fertilizer / irrigation / harvest
    initial_conditions  starting soil water + residue
    review            controls (sdate, num_years, num_reps) + final confirm

Each step is "complete" when EITHER the relevant fields are populated
OR the matching `<step>_use_defaults` flag was set (the user's way of
saying "skip — use sensible defaults"). The wizard advances through
the list and only calls `run_experiment_tool` after the review step
is confirmed (or after fork=True for the fast path).

Domain-specific defaults live in this file; chat orchestrator code
must not import these constants.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any, Dict, List, Optional

from dssat_agent.schemas import ExperimentWizardInput

logger = logging.getLogger(__name__)


# Tree-step machine: each ``experiment_type`` walks its own ordered list
# of step names. Step keys are unique across types so the per-step
# completeness check + prompt can be looked up by key without knowing the
# parent tree.
SINGLE_STEPS: List[str] = [
    "experiment_type",
    "basics",
    "cultivar",
    "fork",
    "planting",
    "soil",
    "weather",
    "management",
    "initial_conditions",
    "review",
]

ENSEMBLE_STEPS: List[str] = [
    "experiment_type",
    "basics",
    "cultivar",
    "soil",
    "weather",
    "protocols_loop",
    "review",
]

SENSITIVITY_STEPS: List[str] = [
    "experiment_type",
    "basics",
    "cultivar",
    "soil",
    "weather",
    "sweep_category",
    "sweep_axes",
    "planting",            # baseline planting
    "management",          # baseline management
    "initial_conditions",  # baseline IC
    "review",
]

MONTE_CARLO_STEPS: List[str] = [
    "experiment_type",
    "spatial_geometry",
    "spatial_density",
    "crop_only",
    "cultivar",
    "soil",
    "weather",
    "auto_planting_toggle",
    "planting",            # template planting
    "management",          # template management
    "initial_conditions",  # template IC
    "review",
]

BATCH_STEPS: List[str] = [
    "experiment_type",
    "locations_loop",
    "protocols_loop",
    "review",
]

WIZARD_TREES: Dict[str, List[str]] = {
    "single": SINGLE_STEPS,
    "ensemble": ENSEMBLE_STEPS,
    "sensitivity": SENSITIVITY_STEPS,
    "monte_carlo": MONTE_CARLO_STEPS,
    "batch": BATCH_STEPS,
}

# All five types are now first-class in the chat wizard. The SPA wizard at
# /dssat/wizard/ remains an alternative for users who prefer the visual
# UX but the chat-driven path is feature-complete for every type.
CHAT_SUPPORTED_TYPES = set(WIZARD_TREES.keys())

# Back-compat alias — keep the old name pointed at the single-type tree
# so any external imports of WIZARD_STEPS keep working.
WIZARD_STEPS: List[str] = SINGLE_STEPS


# ---------------------------------------------------------------------------
# Sensible defaults (filled in when the user opts to skip a step)
# ---------------------------------------------------------------------------

DEFAULT_PLANT_POPULATION = 7.0  # plants/m² (typical maize)
DEFAULT_ROW_SPACING = 75.0  # cm
DEFAULT_PLANTING_METHOD = "S"  # dry seed
DEFAULT_IRRIGATION_STRATEGY = "rainfed"
DEFAULT_HARVEST_STRATEGY = "auto"
DEFAULT_INITIAL_WATER = "field_capacity"
DEFAULT_RESIDUE_KG_HA = 0.0
DEFAULT_NUM_YEARS = 1
DEFAULT_NUM_REPS = 1


# ---------------------------------------------------------------------------
# Step completion checks
# ---------------------------------------------------------------------------

def _basics_complete(p: ExperimentWizardInput) -> bool:
    """Mandatory for any experiment: crop + location + a planting year.

    The user can satisfy "year" two ways:
      - set ``planting_date`` directly (its year is used), or
      - set ``year`` and let the wizard resolve the day from the
        in-house planting-date raster.

    Everything else (cultivar, soil, weather, management, IC) takes a
    sensible default if the user opts out — see the per-step
    ``*_use_defaults`` flags.
    """
    if not (p.crop and p.location):
        return False
    return p.year is not None or p.planting_date is not None


def _cultivar_complete(p: ExperimentWizardInput) -> bool:
    """Cultivar step closed when either a code is set or the user opted
    into auto-selection."""
    return bool(p.cultivar_code or p.cultivar_use_defaults)


def _fork_complete(p: ExperimentWizardInput) -> bool:
    return p.shortcircuit is not None


def _planting_complete(p: ExperimentWizardInput) -> bool:
    if p.planting_use_defaults:
        return True
    # planting_date belongs to this step.
    # All four (date + population + spacing + method) must be set when
    # the user opts to specify rather than use defaults.
    return (
        p.planting_date is not None
        and p.plant_population is not None
        and p.row_spacing is not None
        and bool(p.planting_method)
    )


def _soil_complete(p: ExperimentWizardInput) -> bool:
    return bool(p.soil_use_defaults or p.soil_id)


def _weather_complete(p: ExperimentWizardInput) -> bool:
    return bool(p.weather_use_defaults or p.weather_source)


def _management_complete(p: ExperimentWizardInput) -> bool:
    if p.management_use_defaults:
        return True
    # Need at least the strategy decisions; fertilizer list can be empty
    # but must have been explicitly set (None vs []).
    return (
        p.fertilizer is not None
        and bool(p.irrigation_strategy)
        and bool(p.harvest_strategy)
    )


def _initial_complete(p: ExperimentWizardInput) -> bool:
    if p.initial_use_defaults:
        return True
    return bool(p.initial_water) and p.residue_kg_ha is not None


def _review_complete(p: ExperimentWizardInput) -> bool:
    return bool(p.review_confirmed)


def _experiment_type_complete(p: ExperimentWizardInput) -> bool:
    """The user must pick a type before any other step makes sense.

    'single' continues through the rest of the chat wizard. Anything else
    short-circuits to a redirect (handled in the prompt — the rest of the
    flow is skipped because the redirect envelope tells the LLM not to
    re-call this tool).
    """
    return bool(p.experiment_type)


# ---------------------------------------------------------------------------
# Completion checks for the advanced experiment types
# ---------------------------------------------------------------------------

def _protocols_loop_complete(p: ExperimentWizardInput) -> bool:
    """The protocols loop is closed when the user explicitly says
    ``protocols_complete=True`` and at least one protocol has been
    captured. Each protocol must have a planting_date — that's the
    minimum we need to materialise a Treatment row."""
    if not p.protocols_complete:
        return False
    if not p.protocols:
        return False
    return all(pr.planting_date is not None for pr in p.protocols)


def _locations_loop_complete(p: ExperimentWizardInput) -> bool:
    """Batch only — closed when user signals ``locations_complete=True``
    and at least one location is captured."""
    return bool(p.locations_complete and (p.locations or []))


def _sweep_category_complete(p: ExperimentWizardInput) -> bool:
    return bool(p.sensitivity and p.sensitivity.category in
                ('planting', 'cultivar', 'management'))


def _sweep_axes_complete(p: ExperimentWizardInput) -> bool:
    """Each sweep category requires its own minimum set of axis values."""
    if not (p.sensitivity and p.sensitivity.category):
        return False
    s = p.sensitivity
    if s.category == 'planting':
        return (s.date_central_doy is not None
                and s.date_steps is not None
                and s.date_steps >= 1)
    if s.category == 'cultivar':
        return bool(s.cultivars)
    if s.category == 'management':
        return bool(s.practice and s.amount_steps and s.gap_steps)
    return False


def _spatial_geometry_complete(p: ExperimentWizardInput) -> bool:
    if not (p.spatial and p.spatial.mode):
        return False
    sp = p.spatial
    if sp.mode == 'circle':
        return (sp.center_lat is not None and sp.center_lon is not None
                and sp.radius_km is not None)
    if sp.mode == 'bbox':
        return (sp.min_lat is not None and sp.max_lat is not None
                and sp.min_lon is not None and sp.max_lon is not None)
    if sp.mode == 'admin':
        return bool(sp.admin_name and sp.admin_level)
    return False


def _spatial_density_complete(p: ExperimentWizardInput) -> bool:
    if not (p.spatial and p.spatial.sampling_strategy):
        return False
    sp = p.spatial
    if sp.sampling_strategy == 'systematic':
        return sp.grid_spacing is not None
    if sp.sampling_strategy == 'random':
        return sp.n_points is not None
    return False


def _crop_only_complete(p: ExperimentWizardInput) -> bool:
    """MC has no per-experiment location (the spatial config provides
    them); we only need the crop here."""
    return bool(p.crop)


def _auto_planting_toggle_complete(p: ExperimentWizardInput) -> bool:
    """User explicitly answered yes/no to in-situ planting auto-fill."""
    return p.auto_planting is not None


_STEP_CHECKS = {
    "experiment_type":      _experiment_type_complete,
    "basics":               _basics_complete,
    "cultivar":             _cultivar_complete,
    "fork":                 _fork_complete,
    "planting":             _planting_complete,
    "soil":                 _soil_complete,
    "weather":              _weather_complete,
    "management":           _management_complete,
    "initial_conditions":   _initial_complete,
    "review":               _review_complete,
    # tree-step extensions
    "protocols_loop":       _protocols_loop_complete,
    "locations_loop":       _locations_loop_complete,
    "sweep_category":       _sweep_category_complete,
    "sweep_axes":           _sweep_axes_complete,
    "spatial_geometry":     _spatial_geometry_complete,
    "spatial_density":      _spatial_density_complete,
    "crop_only":            _crop_only_complete,
    "auto_planting_toggle": _auto_planting_toggle_complete,
}


def _steps_for(p: ExperimentWizardInput) -> List[str]:
    """Pick the right step tree for the user's chosen experiment_type.

    Until the user has picked a type, the tree is just
    ``['experiment_type']`` so we re-prompt. After that we follow the
    type-specific list.
    """
    et = (p.experiment_type or '').strip().lower()
    if et in WIZARD_TREES:
        return WIZARD_TREES[et]
    return ['experiment_type']


def next_pending_step(p: ExperimentWizardInput) -> Optional[str]:
    """Return the first step in the active tree that's not satisfied, or
    None when every step in that tree is complete.
    """
    for step in _steps_for(p):
        if not _STEP_CHECKS[step](p):
            return step
    return None


# ---------------------------------------------------------------------------
# Per-step prompts
# ---------------------------------------------------------------------------

def _envelope(prompt: str, schema_keys: List[str], known: Dict[str, Any],
              current_step: str) -> Dict[str, Any]:
    """Standard `needs_input` envelope shape for every step."""
    return {
        "status": "needs_input",
        "prompt": prompt,
        "schema": {
            "type": "object",
            "properties": {k: {"type": "any"} for k in schema_keys},
            "required": [],
        },
        "known": known,
        "next_tool": "experiment_wizard_step",
        "current_step": current_step,
    }


def _prompt_basics(p: ExperimentWizardInput) -> Dict[str, Any]:
    missing = []
    if not p.crop:
        missing.append("- **crop** (DSSAT 2-letter code or common name like 'maize')")
    if p.location is None:
        missing.append("- **location** (lat/lon, OR country + state + county)")
    if p.year is None and p.planting_date is None:
        missing.append(
            "- **year** (4-digit, e.g. 2025) — or a full **planting_date** "
            "(ISO YYYY-MM-DD) if you know the exact day"
        )
    body = (
        "To set up an experiment I need:\n\n"
        + "\n".join(missing)
        + "\n\nIf you only know the season's year, give me that — I'll look "
        "up the typical planting day for your location. Pass a full "
        "planting_date if you'd rather pin it explicitly."
    )
    return _envelope(body, ["crop", "location", "year", "planting_date"],
                     _wizard_known_dict(p), "basics")


def _prompt_cultivar(p: ExperimentWizardInput) -> Dict[str, Any]:
    body = (
        "**Cultivar.**\n\n"
        f"- Reply with a `cultivar_code` for {p.crop or 'your crop'} (call "
        "`list_cultivars` for valid codes), or\n"
        "- Reply 'auto' / 'defaults' and I'll pick the first registered "
        "cultivar for the crop."
    )
    return _envelope(body, ["cultivar_code", "cultivar_use_defaults",
                            "dssat_model"],
                     _wizard_known_dict(p), "cultivar")


def _prompt_fork(p: ExperimentWizardInput) -> Dict[str, Any]:
    body = (
        f"Got it: **{p.crop}** at {_format_location(p.location)}, planted "
        f"**{p.planting_date.isoformat()}**.\n\n"
        "Two options from here:\n\n"
        "- **Run with defaults** — I'll auto-pick soil, weather, "
        "planting density, fertilizer (none), irrigation (rainfed), "
        "harvest (at maturity), and standard initial conditions. "
        "Fastest path; results in ~30s.\n"
        "- **Customize** — I'll walk you through each parameter so you "
        "can tune planting, soil, fertilizer, irrigation, etc.\n\n"
        "Reply 'run' to use defaults, or 'customize' to step through."
    )
    return _envelope(body, ["shortcircuit"], _wizard_known_dict(p), "fork")


def _prompt_planting(p: ExperimentWizardInput) -> Dict[str, Any]:
    body = (
        "**Planting details.**\n\n"
        f"- plant_population: plants/m² (default {DEFAULT_PLANT_POPULATION})\n"
        f"- row_spacing: cm (default {DEFAULT_ROW_SPACING})\n"
        "- planting_method: 'S' (dry seed, default), 'T' (transplant), "
        "or 'P' (pre-germinated)\n\n"
        "Provide values, or reply 'defaults' to accept all defaults."
    )
    return _envelope(
        body,
        ["plant_population", "row_spacing", "planting_method", "planting_use_defaults"],
        _wizard_known_dict(p), "planting",
    )


def _prompt_soil(p: ExperimentWizardInput) -> Dict[str, Any]:
    body = (
        "**Soil profile.**\n\n"
        "- Reply with a specific DSSAT `soil_id` if you have one (you can "
        "call `resolve_soil_profile` to look one up by location or texture).\n"
        "- Or reply 'auto' / 'defaults' and I'll pick the closest match for "
        "your location."
    )
    return _envelope(body, ["soil_id", "soil_use_defaults"],
                     _wizard_known_dict(p), "soil")


def _prompt_weather(p: ExperimentWizardInput) -> Dict[str, Any]:
    body = (
        "**Weather data source.**\n\n"
        "- 'nasa_power' — NASA POWER reanalysis (global, free, default)\n"
        "- 'cli' — DSSAT climate file (if you have one set up)\n\n"
        "Reply with a source name, or 'auto' to let me choose."
    )
    return _envelope(body, ["weather_source", "weather_use_defaults"],
                     _wizard_known_dict(p), "weather")


def _prompt_management(p: ExperimentWizardInput) -> Dict[str, Any]:
    body = (
        "**Management.**\n\n"
        "- **fertilizer**: list of applications, each with "
        "`days_after_planting`, `n_kg_ha`, `p_kg_ha`, `k_kg_ha`. "
        "Pass an empty list `[]` for unfertilized.\n"
        "- **irrigation_strategy**: 'rainfed' (default), 'auto', or "
        "'scheduled'.\n"
        "- **harvest_strategy**: 'auto' (default — at maturity), "
        "'on_maturity', or 'on_date' (then provide `harvest_date`).\n\n"
        "Provide values, or reply 'defaults' for unfertilized + rainfed + "
        "auto-harvest."
    )
    return _envelope(
        body,
        ["fertilizer", "irrigation_strategy", "harvest_strategy",
         "harvest_date", "management_use_defaults"],
        _wizard_known_dict(p), "management",
    )


def _prompt_initial(p: ExperimentWizardInput) -> Dict[str, Any]:
    body = (
        "**Initial soil conditions.**\n\n"
        "- **initial_water**: 'field_capacity' (default), 'wilting_point', "
        "or 'half'.\n"
        f"- **residue_kg_ha**: surface crop residue at start, kg/ha "
        f"(default {DEFAULT_RESIDUE_KG_HA}).\n\n"
        "Provide values, or reply 'defaults'."
    )
    return _envelope(
        body, ["initial_water", "residue_kg_ha", "initial_use_defaults"],
        _wizard_known_dict(p), "initial_conditions",
    )


def _prompt_review(p: ExperimentWizardInput) -> Dict[str, Any]:
    summary_lines = [
        "**Review.** Final settings before running:",
        "",
        f"- Crop: **{p.crop}**",
        f"- Location: {_format_location(p.location)}",
        f"- Planting: **{p.planting_date.isoformat()}**",
    ]
    if not p.planting_use_defaults:
        summary_lines.append(
            f"- Plant population / spacing / method: "
            f"{p.plant_population} pl/m² / {p.row_spacing} cm / {p.planting_method}"
        )
    else:
        summary_lines.append("- Planting: defaults")
    summary_lines.append(
        f"- Soil: {p.soil_id or 'auto-selected'}"
    )
    summary_lines.append(
        f"- Weather: {p.weather_source or 'auto-selected'}"
    )
    if p.management_use_defaults:
        summary_lines.append("- Management: unfertilized + rainfed + auto-harvest")
    else:
        fert = p.fertilizer or []
        summary_lines.append(
            f"- Fertilizer: {len(fert)} application(s)"
            + ("" if fert else " (unfertilized)")
        )
        summary_lines.append(f"- Irrigation: {p.irrigation_strategy}")
        harvest = p.harvest_strategy
        if p.harvest_date:
            harvest = f"{harvest} on {p.harvest_date.isoformat()}"
        summary_lines.append(f"- Harvest: {harvest}")
    if p.initial_use_defaults:
        summary_lines.append("- Initial conditions: defaults")
    else:
        summary_lines.append(
            f"- Initial conditions: water={p.initial_water}, "
            f"residue={p.residue_kg_ha} kg/ha"
        )
    summary_lines.append(
        f"- Controls: {p.num_years or DEFAULT_NUM_YEARS} year(s), "
        f"{p.num_reps or DEFAULT_NUM_REPS} rep(s), "
        f"sdate={(p.simulation_start_date.isoformat() if p.simulation_start_date else 'auto (~30d before planting)')}"
    )
    summary_lines.append(
        "\nReply 'run' / 'confirm' to execute, or 'cancel' to exit."
    )
    return _envelope(
        "\n".join(summary_lines),
        ["num_years", "num_reps", "simulation_start_date", "review_confirmed"],
        _wizard_known_dict(p), "review",
    )


def _prompt_experiment_type(p: ExperimentWizardInput) -> Dict[str, Any]:
    """First step in the chat wizard — pick one of the five types. Every
    type walks its own per-type step tree from here; the visual SPA at
    ``/dssat/wizard/`` is an alternative entry point, but the chat
    wizard can drive all types on its own.
    """
    body = (
        "What kind of experiment are you setting up?\n\n"
        "- **single** — one DSSAT run at one location\n"
        "- **ensemble** — multiple custom protocols at one location\n"
        "- **sensitivity** — sweep one variable category Cartesian-style\n"
        "- **monte_carlo** — one shared protocol over many spatial grid "
        "points\n"
        "- **batch** — matrix of (field, protocol) pairs\n\n"
        "Reply with one of those names. The visual wizard at "
        "`/dssat/wizard/` is an alternative if you'd rather click through "
        "the same configuration; it shares the same backend state."
    )
    return _envelope(body, ["experiment_type"], _wizard_known_dict(p),
                     "experiment_type")


# ---------------------------------------------------------------------------
# Per-step prompts — advanced experiment types
# ---------------------------------------------------------------------------

def _prompt_protocols_loop(p: ExperimentWizardInput) -> Dict[str, Any]:
    """Loop: ask the user to add another protocol or close the loop."""
    n = len(p.protocols or [])
    if n == 0:
        body = (
            "**Protocols.**\n\n"
            "Define the first treatment protocol — minimally a "
            "`planting_date`. Optional per-protocol overrides: "
            "`name`, `cultivar_code`, `plant_population`, `row_spacing`, "
            "`fertilizer`, `irrigation_strategy`, `harvest_strategy`.\n\n"
            "Provide an entry by appending it to the `protocols` array. "
            "Reply 'done' (set `protocols_complete: true`) when you've "
            "added every protocol."
        )
    else:
        body = (
            f"You have **{n}** protocol{'s' if n != 1 else ''} configured.\n\n"
            "Add another by appending to `protocols`, or set "
            "`protocols_complete: true` to move on to review."
        )
    return _envelope(
        body, ["protocols", "protocols_complete"],
        _wizard_known_dict(p), "protocols_loop",
    )


def _prompt_locations_loop(p: ExperimentWizardInput) -> Dict[str, Any]:
    n = len(p.locations or [])
    if n == 0:
        body = (
            "**Batch locations.**\n\n"
            "Add at least one location to the `locations` array. Each "
            "entry follows the same shape as a single-experiment "
            "`location` object (point with lat/lon, or admin_area with "
            "country/state/county). Reply 'done' (set "
            "`locations_complete: true`) when you've added every location."
        )
    else:
        body = (
            f"You have **{n}** location{'s' if n != 1 else ''} configured.\n\n"
            "Add more or set `locations_complete: true` to move on to "
            "the protocols loop."
        )
    return _envelope(
        body, ["locations", "locations_complete"],
        _wizard_known_dict(p), "locations_loop",
    )


def _prompt_sweep_category(p: ExperimentWizardInput) -> Dict[str, Any]:
    body = (
        "**Sensitivity sweep.**\n\n"
        "Pick which axis to vary across treatments — only one category "
        "per run, Cartesian within that category:\n\n"
        "- **planting** — sweep planting date / population / row spacing\n"
        "- **cultivar** — multi-select a list of cultivars\n"
        "- **management** — sweep one practice's amount × applications "
        "× days-between\n\n"
        "Reply with the category (and optionally the axis values; the "
        "next prompt will collect what's missing)."
    )
    return _envelope(
        body, ["sensitivity"], _wizard_known_dict(p), "sweep_category",
    )


def _prompt_sweep_axes(p: ExperimentWizardInput) -> Dict[str, Any]:
    cat = (p.sensitivity.category if p.sensitivity else None) or '?'
    if cat == 'planting':
        body = (
            "**Planting sweep axes** (Cartesian — final treatment count "
            "is the product of step counts):\n\n"
            "- `date_central_doy` (1-366), `date_offset_days`, "
            "`date_steps`\n"
            "- `pop_min`, `pop_max`, `pop_steps`\n"
            "- `rs_min`, `rs_max`, `rs_steps`\n\n"
            "Set steps=1 on any axis to leave it fixed at the baseline."
        )
    elif cat == 'cultivar':
        body = (
            "**Cultivar sweep.** Provide a `cultivars` list — every "
            "entry becomes one treatment."
        )
    elif cat == 'management':
        body = (
            "**Management sweep axes** (Cartesian within the chosen "
            "practice):\n\n"
            "- `practice`: 'fertilizer', 'irrigation', 'tillage', "
            "'chemical', or 'residue'\n"
            "- `amount_min`, `amount_max`, `amount_steps`\n"
            "- `apps_min`, `apps_max`\n"
            "- `gap_days_min`, `gap_days_max`, `gap_steps`"
        )
    else:
        body = "Pick a sweep category first."
    return _envelope(body, ["sensitivity"], _wizard_known_dict(p), "sweep_axes")


def _prompt_spatial_geometry(p: ExperimentWizardInput) -> Dict[str, Any]:
    body = (
        "**Monte Carlo spatial geometry** — define the *region* the grid "
        "samples cover.\n\n"
        "Emit a top-level `spatial` object — **not** inside `location`. "
        "`location.kind` only accepts `\"point\"` or `\"admin_area\"`; "
        "the geometry shape (`circle` / `bbox` / `admin`) goes in "
        "`spatial.mode`.\n\n"
        "Pick exactly one shape and copy the matching JSON template:\n\n"
        "**1. Circle around a center point** — when the user said "
        "\"X km around lat/lon\" or \"a circle around <place>\":\n"
        "```json\n"
        "{\n"
        "  \"spatial\": {\n"
        "    \"mode\": \"circle\",\n"
        "    \"center_lat\": 33.55,\n"
        "    \"center_lon\": -86.97,\n"
        "    \"radius_km\": 25\n"
        "  }\n"
        "}\n"
        "```\n\n"
        "**2. Bounding box** — when the user gave four corners:\n"
        "```json\n"
        "{\n"
        "  \"spatial\": {\n"
        "    \"mode\": \"bbox\",\n"
        "    \"min_lat\": 33.0, \"max_lat\": 34.0,\n"
        "    \"min_lon\": -87.5, \"max_lon\": -86.5\n"
        "  }\n"
        "}\n"
        "```\n\n"
        "**3. Named admin area** — when the user named a state or county:\n"
        "```json\n"
        "{\n"
        "  \"spatial\": {\n"
        "    \"mode\": \"admin\",\n"
        "    \"admin_name\": \"Alabama\",\n"
        "    \"admin_level\": \"admin1\"\n"
        "  }\n"
        "}\n"
        "```\n\n"
        "Common mistakes: putting `\"kind\": \"circle\"` inside `location` "
        "(invalid — only `point` / `admin_area` allowed there); putting "
        "`center_lat` / `center_lon` / `radius_km` in `location` (they "
        "belong on `spatial`)."
    )
    return _envelope(
        body, ["spatial"], _wizard_known_dict(p), "spatial_geometry",
    )


def _prompt_spatial_density(p: ExperimentWizardInput) -> Dict[str, Any]:
    body = (
        "**Monte Carlo sampling density** — how to draw sample points "
        "from the region you just defined.\n\n"
        "Add the density fields to the **same `spatial` object** "
        "(merge with `mode` / geometry fields already set). Pick one "
        "strategy:\n\n"
        "**1. Systematic** (deterministic lattice — preferred when the "
        "user said \"systematic\" or \"grid\"):\n"
        "```json\n"
        "{\n"
        "  \"spatial\": {\n"
        "    \"mode\": \"circle\", \"...geometry...\": \"keep prior fields\",\n"
        "    \"sampling_strategy\": \"systematic\",\n"
        "    \"grid_spacing\": 0.1\n"
        "  }\n"
        "}\n"
        "```\n"
        "`grid_spacing` is in decimal degrees (~11 km per 0.1° at the "
        "equator).\n\n"
        "**2. Random** (uniform sampling):\n"
        "```json\n"
        "{\n"
        "  \"spatial\": {\n"
        "    \"mode\": \"circle\", \"...geometry...\": \"keep prior fields\",\n"
        "    \"sampling_strategy\": \"random\",\n"
        "    \"n_points\": 30\n"
        "  }\n"
        "}\n"
        "```\n"
        "`n_points` is 1-99 (DSSAT FILEX limit caps total grid points "
        "at 99 either way)."
    )
    return _envelope(
        body, ["spatial"], _wizard_known_dict(p), "spatial_density",
    )


def _prompt_crop_only(p: ExperimentWizardInput) -> Dict[str, Any]:
    body = (
        "**Crop.**\n\n"
        "DSSAT 2-letter code (e.g. 'MZ' for maize, 'WH' for wheat). "
        "Optionally set `cultivar_code` too — otherwise we'll pick the "
        "default cultivar for the crop at run time."
    )
    return _envelope(body, ["crop"], _wizard_known_dict(p), "crop_only")


def _prompt_auto_planting_toggle(p: ExperimentWizardInput) -> Dict[str, Any]:
    body = (
        "**Auto-fill planting date per grid point?**\n\n"
        "- `auto_planting: true` — resolve a per-field planting date "
        "from the in-situ raster (in-house SE-US 5km source). The "
        "template's `planting_date` is used as a fallback at uncovered "
        "points.\n"
        "- `auto_planting: false` — every grid point uses the "
        "template's fixed planting_date."
    )
    return _envelope(
        body, ["auto_planting"], _wizard_known_dict(p), "auto_planting_toggle",
    )


_STEP_PROMPTS = {
    "experiment_type":      _prompt_experiment_type,
    "basics":               _prompt_basics,
    "cultivar":             _prompt_cultivar,
    "fork":                 _prompt_fork,
    "planting":             _prompt_planting,
    "soil":                 _prompt_soil,
    "weather":              _prompt_weather,
    "management":           _prompt_management,
    "initial_conditions":   _prompt_initial,
    "review":               _prompt_review,
    # tree-step extensions
    "protocols_loop":       _prompt_protocols_loop,
    "locations_loop":       _prompt_locations_loop,
    "sweep_category":       _prompt_sweep_category,
    "sweep_axes":           _prompt_sweep_axes,
    "spatial_geometry":     _prompt_spatial_geometry,
    "spatial_density":      _prompt_spatial_density,
    "crop_only":            _prompt_crop_only,
    "auto_planting_toggle": _prompt_auto_planting_toggle,
}


# ---------------------------------------------------------------------------
# Run-args builder + entry point
# ---------------------------------------------------------------------------

def _build_run_args(p: ExperimentWizardInput) -> Dict[str, Any]:
    """Translate accumulated wizard state into the dict shape that
    ``run_experiment_tool`` accepts.

    The chat wizard now supports all five experiment types; the per-type
    helpers below build the right wizard_params shape. ``run_experiment_tool``
    forwards into ``workflow.run_full_simulation`` which dispatches per
    ``experiment_type`` from there.
    """
    et = (p.experiment_type or 'single').strip().lower()
    if et == 'ensemble':
        return _build_ensemble_args(p)
    if et == 'sensitivity':
        return _build_sensitivity_args(p)
    if et == 'monte_carlo':
        return _build_monte_carlo_args(p)
    if et == 'batch':
        return _build_batch_args(p)
    return _build_single_args(p)


def _build_single_args(p: ExperimentWizardInput) -> Dict[str, Any]:
    # Resolve planting date — explicit > in-situ raster lookup > year fallback.
    pdate_iso: Optional[str] = None
    if p.planting_date:
        pdate_iso = p.planting_date.isoformat()
    elif p.year is not None and p.location is not None:
        loc = p.location.model_dump() if hasattr(p.location, 'model_dump') else dict(p.location)
        if loc.get('kind') == 'point' and loc.get('lat') is not None:
            try:
                from dssat_agent.services.insitu_lookup import lookup_planting_date
                r = lookup_planting_date(
                    lat=float(loc['lat']), lon=float(loc['lon']),
                    year=int(p.year), source='auto',
                )
                if r.get('planting_date'):
                    pdate_iso = r['planting_date']
            except Exception:
                pass
        if pdate_iso is None:
            pdate_iso = f"{int(p.year)}-04-30"

    args: Dict[str, Any] = {
        "experiment_type": "single",
        "crop": p.crop,
        "location": p.location.model_dump() if p.location else None,
        "planting": {
            "date": pdate_iso,
            "population": p.plant_population if p.plant_population is not None else DEFAULT_PLANT_POPULATION,
            "row_spacing": p.row_spacing if p.row_spacing is not None else DEFAULT_ROW_SPACING,
            "method": p.planting_method or DEFAULT_PLANTING_METHOD,
        },
    }
    if p.cultivar_code:
        args["cultivar"] = p.cultivar_code
    if p.dssat_model:
        args["dssat_model"] = p.dssat_model
    if p.soil_id:
        # Schema field is `soil_profile`; user-facing wizard field is `soil_id`.
        args["soil_profile"] = p.soil_id
    if p.weather_source:
        # Schema field is `weather_dataset`; user-facing wizard field is `weather_source`.
        args["weather_dataset"] = p.weather_source
    if p.fertilizer is not None:
        args["fertilizer"] = [f.model_dump() for f in p.fertilizer]
    args["irrigation_strategy"] = p.irrigation_strategy or DEFAULT_IRRIGATION_STRATEGY
    args["harvest_strategy"] = p.harvest_strategy or DEFAULT_HARVEST_STRATEGY
    if p.harvest_date:
        args["harvest_date"] = p.harvest_date.isoformat()
    args["initial_water"] = p.initial_water or DEFAULT_INITIAL_WATER
    args["residue_kg_ha"] = (
        p.residue_kg_ha if p.residue_kg_ha is not None else DEFAULT_RESIDUE_KG_HA
    )
    args["num_years"] = p.num_years or DEFAULT_NUM_YEARS
    args["num_reps"] = p.num_reps or DEFAULT_NUM_REPS
    if p.simulation_start_date:
        args["simulation_start_date"] = p.simulation_start_date.isoformat()
    elif p.planting_date:
        args["simulation_start_date"] = (
            (p.planting_date - timedelta(days=30)).isoformat()
        )
    return args


def _protocol_to_treatment(prot, p: ExperimentWizardInput) -> Dict[str, Any]:
    """Convert a single ProtocolInput into the merge-shape
    ``run_ensemble`` consumes. ``ensemble_service._merge_treatment`` walks
    the keys in ``ALL_PARAM_KEYS`` and shallow-merges; planting overrides
    must therefore live in a nested ``planting`` dict (with DSSAT keys)
    rather than flat ``planting_date``/``plant_population`` so the merge
    picks them up."""
    pdate = prot.planting_date or p.planting_date
    out: Dict[str, Any] = {
        "name": prot.name or f"T{(p.protocols or []).index(prot) + 1}",
        "crop_code": (prot.crop or p.crop or '').upper() or None,
    }
    if prot.cultivar_code:
        out["cultivar_code"] = prot.cultivar_code
    planting: Dict[str, Any] = {}
    if pdate:
        planting["pdate"] = pdate.isoformat()
    if prot.plant_population is not None:
        planting["ppop"] = prot.plant_population
    if prot.row_spacing is not None:
        planting["plrs"] = prot.row_spacing
    if prot.planting_method:
        planting["plme"] = prot.planting_method
    if planting:
        out["planting"] = planting
    if prot.fertilizer is not None:
        out["fertilizer"] = [f.model_dump() for f in prot.fertilizer]
    if prot.irrigation_strategy:
        out["irrigation_strategy"] = prot.irrigation_strategy
    if prot.harvest_strategy:
        out["harvest_strategy"] = prot.harvest_strategy
    if prot.harvest_date:
        out["harvest_date"] = prot.harvest_date.isoformat()
    return out


def _build_ensemble_args(p: ExperimentWizardInput) -> Dict[str, Any]:
    base = _build_single_args(p)
    base.pop('experiment_type', None)
    treatments = [_protocol_to_treatment(pr, p) for pr in (p.protocols or [])]
    return {
        "experiment_type": "ensemble",
        "base": base,
        "treatments": treatments,
    }


def _build_sensitivity_args(p: ExperimentWizardInput) -> Dict[str, Any]:
    """Generate the per-treatment list from the sweep config + baseline."""
    base = _build_single_args(p)
    base.pop('experiment_type', None)
    treatments = _materialise_sensitivity_sweep(p)
    return {
        "experiment_type": "sensitivity",
        "base": base,
        "treatments": treatments,
        "sensitivity": (p.sensitivity.model_dump() if p.sensitivity else {}),
    }


def _materialise_sensitivity_sweep(p: ExperimentWizardInput) -> List[Dict[str, Any]]:
    """Cartesian-within-category generator. Mirrors the SPA's
    ``sensitivity_axes.js:generate(...)`` with the same shape."""
    s = p.sensitivity
    if not s or not s.category:
        return []
    base_treatment = _protocol_to_treatment(
        # Synthetic protocol from the wizard's basics — gives the
        # generator a proper jumping-off point.
        type('P', (), {
            'name': 'baseline',
            'crop': p.crop, 'cultivar_code': None,
            'planting_date': p.planting_date,
            'plant_population': p.plant_population,
            'row_spacing': p.row_spacing,
            'planting_method': p.planting_method,
            'fertilizer': p.fertilizer,
            'irrigation_strategy': p.irrigation_strategy,
            'harvest_strategy': p.harvest_strategy,
            'harvest_date': p.harvest_date,
        })(),
        p,
    )
    if s.category == 'cultivar':
        return [
            {**base_treatment, 'cultivar_code': cv,
             'name': f"T{i + 1}: {cv}"}
            for i, cv in enumerate(s.cultivars or [])
        ]
    if s.category == 'planting':
        # date axis (DOY-anchored); pop / rs ranges
        doys = _doy_range(s.date_central_doy, s.date_offset_days, s.date_steps)
        pops = _linrange(s.pop_min, s.pop_max, s.pop_steps)
        rss = _linrange(s.rs_min, s.rs_max, s.rs_steps)
        out: List[Dict[str, Any]] = []
        i = 0
        for d in (doys or [None]):
            for pop in (pops or [None]):
                for rs in (rss or [None]):
                    i += 1
                    t = dict(base_treatment)
                    if d is not None and p.planting_date is not None:
                        # Re-anchor to the wizard's year using the doy.
                        from datetime import date as _date
                        t['planting_date'] = (
                            _date(p.planting_date.year, 1, 1)
                            + timedelta(days=int(d) - 1)
                        ).isoformat()
                    if pop is not None:
                        t['plant_population'] = pop
                    if rs is not None:
                        t['row_spacing'] = rs
                    t['name'] = f"T{i}: DOY={d} pop={pop} rs={rs}"
                    out.append(t)
        return out
    if s.category == 'management':
        amts = _linrange(s.amount_min, s.amount_max, s.amount_steps)
        apps = list(range(s.apps_min or 1, (s.apps_max or 1) + 1))
        gaps = _linrange(s.gap_days_min, s.gap_days_max, s.gap_steps)
        out = []
        i = 0
        for amt in (amts or [None]):
            for napps in (apps or [1]):
                for gap in (gaps or [None]):
                    i += 1
                    t = dict(base_treatment)
                    events = []
                    for j in range(int(napps)):
                        ev = {'days_after_planting': int((gap or 0) * j)}
                        if amt is not None and s.practice == 'fertilizer':
                            ev['n_kg_ha'] = float(amt)
                        events.append(ev)
                    if s.practice == 'fertilizer':
                        t['fertilizer'] = events
                    t['name'] = f"T{i}: {s.practice} amt={amt} n={napps} gap={gap}"
                    out.append(t)
        return out
    return []


def _linrange(lo, hi, steps):
    if lo is None or hi is None or not steps or steps < 1:
        return []
    if steps == 1:
        return [(lo + hi) / 2]
    step = (hi - lo) / (steps - 1)
    return [round(lo + i * step, 4) for i in range(int(steps))]


def _doy_range(central, offset, steps):
    if central is None or not steps or steps < 1:
        return [central] if central is not None else []
    if steps == 1:
        return [central]
    out = []
    total = (offset or 0) * 2
    for i in range(int(steps)):
        v = -((offset or 0)) + (i * total) / (int(steps) - 1)
        v = int(max(1, min(366, round(int(central) + v))))
        out.append(v)
    return out


def _build_monte_carlo_args(p: ExperimentWizardInput) -> Dict[str, Any]:
    sp = p.spatial or None
    sp_dump: Dict[str, Any] = sp.model_dump() if sp else {}
    args: Dict[str, Any] = {
        "experiment_type": "monte_carlo",
        "crop_code": (p.crop or '').upper() or None,
    }
    # Spatial fields flattened into the run params, matching the legacy
    # MC service expectations.
    if sp_dump.get('mode'):       args['spatial_mode']       = sp_dump['mode']
    if sp_dump.get('center_lat') is not None and sp_dump.get('center_lon') is not None:
        args['center'] = {'lat': sp_dump['center_lat'], 'lon': sp_dump['center_lon']}
    if sp_dump.get('radius_km') is not None:
        args['radius_km'] = sp_dump['radius_km']
    if sp_dump.get('min_lat') is not None:
        args['bbox'] = {
            'min_lat': sp_dump['min_lat'], 'max_lat': sp_dump['max_lat'],
            'min_lon': sp_dump['min_lon'], 'max_lon': sp_dump['max_lon'],
        }
    if sp_dump.get('admin_name'):
        args['admin_name']  = sp_dump['admin_name']
        args['admin_level'] = sp_dump.get('admin_level', 'admin1')
    if sp_dump.get('grid_spacing') is not None:
        args['grid_spacing'] = sp_dump['grid_spacing']
    if sp_dump.get('n_points') is not None:
        args['n_points'] = sp_dump['n_points']
        args['nens']     = sp_dump['n_points']
    if sp_dump.get('sampling_strategy'):
        args['sampling_strategy'] = sp_dump['sampling_strategy']

    # Template treatment: borrow the basics' planting / mgmt as the
    # shared protocol every grid point will run.
    if p.planting_date:
        args['planting_date'] = p.planting_date.isoformat()
    if p.fertilizer is not None:
        args['fertilizer'] = [f.model_dump() for f in p.fertilizer]
    args['irrigation_strategy'] = p.irrigation_strategy or DEFAULT_IRRIGATION_STRATEGY
    args['harvest_strategy']    = p.harvest_strategy or DEFAULT_HARVEST_STRATEGY
    if p.soil_id:        args['soil_id']        = p.soil_id
    if p.weather_source: args['weather_source'] = p.weather_source

    # Auto-planting toggle — when True the MC pipeline resolves planting
    # date per grid point from the in-situ raster instead of using the
    # template's fixed date for every point.
    if p.auto_planting is not None:
        args['auto_planting'] = bool(p.auto_planting)
    return args


def _build_batch_args(p: ExperimentWizardInput) -> Dict[str, Any]:
    """Build the legacy chat-batch shape (location_selection_mode +
    template_params). The run pipeline's ``_handle_run_batch`` handles
    this branch via ``_build_batch_defaults`` in ``workflow.py``."""
    template = _build_single_args(p)
    template.pop('experiment_type', None)
    # Convert wizard locations to the legacy "points" list (lat/lon/name).
    points: List[Dict[str, Any]] = []
    for i, loc in enumerate(p.locations or []):
        d = loc.model_dump() if hasattr(loc, 'model_dump') else dict(loc or {})
        if d.get('kind') == 'point':
            points.append({
                'name': f"({d.get('lat')}, {d.get('lon')})",
                'lat': d.get('lat'),
                'lon': d.get('lon'),
            })
        # admin_area locations are deferred — run pipeline expects a flat
        # admin_parent for those, which doesn't compose cleanly with a
        # heterogeneous list. Document and skip.
    return {
        "experiment_type": "batch",
        "sub_experiment_type": "single",
        "location_selection_mode": "points",
        "batch_locations": points,
        "template_params": template,
    }


def _wizard_to_legacy_basics(p: ExperimentWizardInput) -> Dict[str, Any]:
    """Distill an ``ExperimentWizardInput`` into the flat-key params dict
    that ``run_full_simulation`` reads at the top level (for location
    resolution + weather prep, before delegating to ``build_experiment``).

    Mirrors the subset of ``experiment_to_legacy_params`` that single
    needs, but pulled directly off the wizard model so non-single chat
    flows don't have to round-trip through the (single-shaped)
    experiment adapter."""
    out: Dict[str, Any] = {}
    if p.experiment_type:
        out['experiment_type'] = p.experiment_type.strip().lower()
    if p.crop:
        out['crop_code'] = p.crop.upper()
    if p.cultivar_code:
        out['cultivar_code'] = p.cultivar_code
    if p.dssat_model:
        out['dssat_model'] = p.dssat_model
    if p.soil_id:
        out['soil_id'] = p.soil_id
    if p.weather_source:
        out['weather_source'] = p.weather_source

    # Location → flat lat/lon for run_full_simulation's pre-build phase.
    loc = p.location
    if loc is not None:
        d = loc.model_dump() if hasattr(loc, 'model_dump') else dict(loc)
        if d.get('kind') == 'point':
            if d.get('lat') is not None:
                out['latitude'] = d['lat']
            if d.get('lon') is not None:
                out['longitude'] = d['lon']
        else:
            parts = [d.get('county'), d.get('state'), d.get('country')]
            out['location_name'] = ', '.join(x for x in parts if x)
            if d.get('country'):
                out['country'] = d['country']
    # MC's "spatial" config doesn't have ONE location, but the run
    # pipeline's pre-build phase still needs lat/lon for weather prep.
    # Use the spatial center / first bbox corner as the representative.
    if p.spatial is not None and 'latitude' not in out:
        sp = p.spatial.model_dump() if hasattr(p.spatial, 'model_dump') else dict(p.spatial)
        if sp.get('center_lat') is not None:
            out['latitude'] = sp['center_lat']
            out['longitude'] = sp['center_lon']
        elif sp.get('min_lat') is not None:
            out['latitude'] = (sp['min_lat'] + sp['max_lat']) / 2
            out['longitude'] = (sp['min_lon'] + sp['max_lon']) / 2

    # Batch's "locations" list — use the first as the representative for
    # the pre-build phase (run_full_simulation skips weather prep for
    # batch anyway, but lat/lon flow into legacy summary helpers).
    if not out.get('latitude') and p.locations:
        first = p.locations[0]
        d = first.model_dump() if hasattr(first, 'model_dump') else dict(first)
        if d.get('kind') == 'point':
            out['latitude'] = d.get('lat')
            out['longitude'] = d.get('lon')

    if p.planting_date:
        out['planting_date'] = p.planting_date.isoformat()
    elif p.year is not None and out.get('latitude') is not None:
        # Resolve from in-house in-situ raster (DSSAT planting-date lookup);
        # fall back to year-04-30 if the point is outside coverage.
        try:
            from dssat_agent.services.insitu_lookup import lookup_planting_date
            r = lookup_planting_date(
                lat=float(out['latitude']),
                lon=float(out['longitude']),
                year=int(p.year),
                source='auto',
            )
            if r.get('planting_date'):
                out['planting_date'] = r['planting_date']
        except Exception:
            pass
        if 'planting_date' not in out:
            out['planting_date'] = f"{int(p.year)}-04-30"
    if p.plant_population is not None:
        out['plant_population'] = p.plant_population
    if p.row_spacing is not None:
        out['row_spacing'] = p.row_spacing
    if p.num_years is not None:
        out['num_years'] = p.num_years

    return out


def _execute(params: ExperimentWizardInput, user: Any) -> Dict[str, Any]:
    """Run the wizard's collected state through DSSAT.

    Single takes the schema-validated tool path (``run_experiment_tool``)
    so the LLM-shape normalizer + soil-existence check fire. Non-single
    types skip that adapter — its underlying ``ExperimentInput`` schema
    only models single's shape, and re-modelling ensemble/sensitivity/MC/
    batch through it would just paper over the run pipeline's actual
    shape — and call ``run_full_simulation`` directly with the chat-side
    ``_build_run_args`` output as ``wizard_params``.
    """
    et = (params.experiment_type or 'single').strip().lower()
    if et == 'single':
        run_args = _build_run_args(params)
        from dssat_agent.services.tool_wrapper import run_experiment_tool
        return run_experiment_tool(run_args, user=user)

    from dssat_agent.services.workflow import run_full_simulation
    legacy_params = _wizard_to_legacy_basics(params)
    wizard_params = _build_run_args(params)
    user_id = getattr(user, 'id', None) if user else None
    # Positional ``params`` so test assertions on ``call_args.args[0]`` work
    # uniformly with the single path's call shape.
    return run_full_simulation(
        legacy_params,
        wizard_params=wizard_params,
        user_id=user_id,
    )


def advance_wizard(
    params: ExperimentWizardInput, *, user: Any = None,
) -> Dict[str, Any]:
    """Walk the step machine until either the simulation runs or the
    next step needs the user. The caller supplies the accumulated state;
    this function returns either a needs_input envelope or the
    completed-run result.
    """
    # All five experiment types are first-class in the chat wizard now.
    # Each one walks its own per-type step tree (see ``WIZARD_TREES``).
    et = (params.experiment_type or '').strip().lower()
    if et and et not in CHAT_SUPPORTED_TYPES:
        # Unknown type — re-prompt at the experiment_type step.
        params.experiment_type = None

    # Fast path: user took the shortcircuit fork → skip to run.
    if params.shortcircuit is True and _basics_complete(params):
        return _execute(params, user)

    next_step = next_pending_step(params)
    if next_step is None:
        # All steps satisfied → run.
        return _execute(params, user)

    # Skip the fork prompt entirely when the user explicitly answered False
    # to it — they want to customize, so we proceed to planting.
    handler = _STEP_PROMPTS.get(next_step)
    if handler is None:
        logger.warning("[wizard] no prompt handler for step %r", next_step)
        return {
            "status": "error",
            "error": f"Wizard step '{next_step}' has no prompt handler.",
        }
    return handler(params)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _format_location(loc: Any) -> str:
    if loc is None:
        return "?"
    try:
        d = loc.model_dump() if hasattr(loc, "model_dump") else dict(loc)
    except Exception:
        return str(loc)
    if d.get("kind") == "point":
        return f"{d.get('lat')}, {d.get('lon')}"
    parts = [d.get(k) for k in ("county", "state", "country") if d.get(k)]
    return ", ".join(parts) if parts else str(d)


def _wizard_known_dict(p: ExperimentWizardInput) -> Dict[str, Any]:
    """Compact dict of already-collected wizard fields. Returned in
    every needs_input envelope so the LLM can echo them back next turn.

    Must include EVERY field the wizard accepts that the LLM should
    carry forward. Missing fields here means the LLM, following the
    SKILL.md instruction to "preserve everything in known", will silently
    forget that field on the next call, the wizard re-prompts for it,
    and the user gets stuck in a loop. Past omissions: ``experiment_type``,
    ``cultivar_code``, ``year``, ``protocols``, ``spatial``, ``locations``.
    """
    known: Dict[str, Any] = {}
    # Scalar / primitive fields — direct copy when set.
    for field in (
        "experiment_type", "crop", "cultivar_code", "dssat_model", "year",
        "shortcircuit",
        "soil_id", "weather_source",
        "plant_population", "row_spacing", "planting_method",
        "irrigation_strategy", "harvest_strategy",
        "initial_water", "residue_kg_ha",
        "num_years", "num_reps",
        "auto_planting",
        # use-defaults flags so the LLM remembers which steps the user
        # already accepted with defaults.
        "planting_use_defaults", "soil_use_defaults",
        "weather_use_defaults", "management_use_defaults",
        "initial_use_defaults", "cultivar_use_defaults",
        # protocol-/sweep-/batch-loop completion flags so the LLM
        # doesn't re-trigger those loops after the user closed them.
        "protocols_complete", "locations_complete",
        "review_confirmed",
    ):
        v = getattr(p, field, None)
        if v is not None:
            known[field] = v
    # Nested / model fields — dump to dict so the LLM can re-emit.
    if p.location is not None:
        known["location"] = p.location.model_dump()
    if p.planting_date is not None:
        known["planting_date"] = p.planting_date.isoformat()
    if p.harvest_date is not None:
        known["harvest_date"] = p.harvest_date.isoformat()
    if p.simulation_start_date is not None:
        known["simulation_start_date"] = p.simulation_start_date.isoformat()
    if p.fertilizer is not None:
        known["fertilizer"] = [f.model_dump() for f in p.fertilizer]
    if getattr(p, "protocols", None):
        known["protocols"] = [pr.model_dump() for pr in p.protocols]
    if getattr(p, "sensitivity", None) is not None:
        known["sensitivity"] = p.sensitivity.model_dump()
    if getattr(p, "spatial", None) is not None:
        known["spatial"] = p.spatial.model_dump()
    if getattr(p, "locations", None):
        known["locations"] = [loc.model_dump() for loc in p.locations]
    if getattr(p, "pairs", None):
        known["pairs"] = [pp.model_dump() for pp in p.pairs]
    return known
