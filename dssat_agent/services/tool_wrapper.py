"""
Single-entry-point tool adapter for DSSAT experiment execution.

`run_experiment_tool()` is the tool exposed to the ReAct loop. It:
  1. Returns a validation_error when `experiment_type` is omitted —
     never silently defaults. The wizard establishes the type at
     Step 0; bailing into ``run_experiment`` without carrying the type
     forward would drop the user's choice and silently run a single
     simulation, so the LLM is forced to either re-include the type
     or switch to ``experiment_wizard_step`` to resume the flow.
  2. Validates the raw payload against `ExperimentInput` (the discriminated
     union Pydantic model).
  3. On ValidationError, returns a ReAct envelope enriched with valid-value
     lists and fuzzy suggestions from the live DB catalogs. Adds a `hint`
     directing the LLM to `experiment_wizard_step` when core inputs (crop,
     location, planting_date) are absent.
  4. On a valid model, verifies `soil_profile` exists (not an enum — thousands
     of rows) and returns a targeted validation envelope with resolver hints
     when it doesn't.
  5. Translates the validated Pydantic structure into the legacy flat-dict
     format that `run_full_simulation()` already accepts, preserving all
     existing downstream logic (defaults resolution, weather fetch, artifact
     assembly, response footer).

Runtime cost: the enum builders hit Postgres via small indexed queries
(~5–15 ms total). Per-request rebuild is the intended design — no caching
layer needed initially.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from pydantic import TypeAdapter, ValidationError

from earthrise_agents_base.schemas import validation_error_to_react
from data_agent.schemas import build_dataset_enum
from dssat_agent.schemas import (
    ExperimentInput,
    ExperimentType,
    HarvestStrategy,
    IrrigationStrategy,
    build_crop_enum,
    build_cultivar_enum,
    resolve_soil_profile,
)
from dssat_agent.schemas.enums_dynamic import soil_profile_exists

logger = logging.getLogger(__name__)


_EXPERIMENT_ADAPTER = TypeAdapter(ExperimentInput)


# Common crop names → DSSAT 2-letter codes. Used to translate LLM outputs
# that pass `crop: "corn"` instead of `crop: "MZ"`. The real list is in
# `build_crop_enum()`; this map only covers the most common casual
# phrasings the LLM emits.
_COMMON_CROP_ALIASES: Dict[str, str] = {
    "corn": "MZ", "maize": "MZ",
    "wheat": "WH",
    "soybean": "SB", "soy": "SB", "soya": "SB",
    "rice": "RI",
    "sorghum": "SG",
    "peanut": "PN", "groundnut": "PN",
    "potato": "PT",
    "sugarcane": "SC", "sugar cane": "SC", "cane": "SC",
    "cassava": "CS",
    "sunflower": "SU",
    "tomato": "TM",
    "cotton": "CO",
    "barley": "BA",
    "millet": "ML",
    "bean": "BN", "drybean": "BN", "dry bean": "BN",
    "cabbage": "CB",
    "chickpea": "CH",
    "cowpea": "CP",
    "faba bean": "FB", "faba": "FB",
    "green bean": "GB",
    "bell pepper": "PR", "pepper": "PR",
    "pigeon pea": "PP",
    "quinoa": "QU",
    "safflower": "SF",
    "taro": "TR",
    "tanier": "TN",
    "sweet corn": "SW", "sweetcorn": "SW",
}


def _normalize_crop(raw_value: Any) -> Any:
    """Translate common crop names to DSSAT 2-letter codes; leave codes alone."""
    if not isinstance(raw_value, str):
        return raw_value
    v = raw_value.strip()
    if len(v) == 2:
        return v.upper()
    return _COMMON_CROP_ALIASES.get(v.lower(), v)


def _normalize_location_value(value: Any) -> Any:
    """Translate various LLM shapes into a LocationInput-compatible dict.

    Accepts:
      - Already-valid dicts with `kind` → returned as-is.
      - Dicts missing `kind` → infer (lat/lon → 'point'; country/state/county → 'admin_area').
      - Bare strings → resolve via `data_agent.location.resolve_location`,
        convert to a point if lat/lon resolved; otherwise return an
        admin_area best-effort with country='US' and county=cleaned string.
    """
    if isinstance(value, dict):
        if "kind" in value:
            return value
        if "lat" in value and "lon" in value:
            return {**value, "kind": "point"}
        if any(k in value for k in ("country", "state", "county")):
            return {**value, "kind": "admin_area"}
        return value

    if isinstance(value, str) and value.strip():
        from data_agent.location import resolve_location as _resolve

        resolved = _resolve(value)
        if resolved.get("lat") is not None and resolved.get("lon") is not None:
            return {
                "kind": "point",
                "lat": resolved["lat"],
                "lon": resolved["lon"],
            }
        # Fallback: treat as admin_area best-effort.
        cleaned = value.replace("County", "").replace("county", "").strip().strip(",").strip()
        return {
            "kind": "admin_area",
            "country": "US",
            "county": cleaned,
        }
    return value


def _normalize_llm_shape(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Mutate `raw` in place to coerce common LLM-emitted shape variants
    into the canonical schema-expected shape. Handles:

      - top-level `date` → `planting_date`
      - nested `planting: {date: ...}` → flat `planting_date`
      - bare-string `location` → dict (point or admin_area)
      - dict `location` missing `kind` → inferred `kind`
      - common-name `crop` → DSSAT 2-letter code
    """
    # Date — LLM sometimes uses a generic `date` field.
    if raw.get("date") and not raw.get("planting_date"):
        raw["planting_date"] = raw.pop("date")

    # Nested planting → flat planting_date.
    planting_obj = raw.pop("planting", None)
    if isinstance(planting_obj, dict) and planting_obj.get("date") and not raw.get("planting_date"):
        raw["planting_date"] = planting_obj["date"]

    # Location shape.
    if "location" in raw:
        raw["location"] = _normalize_location_value(raw["location"])

    # Crop common-name translation.
    if "crop" in raw:
        raw["crop"] = _normalize_crop(raw["crop"])

    return raw


def experiment_wizard_step_tool(
    raw: Dict[str, Any], *, user: Any = None,
) -> Dict[str, Any]:
    """Multi-turn wizard for DSSAT experiment setup.

    Mirrors the panels of the UI experiment wizard. Step machine lives
    in `dssat_agent.services.wizard`; this wrapper validates the LLM's
    raw arguments, normalizes shape variants, and delegates.

    Shortcircuit path: if the user takes the fast fork after step 1
    (`shortcircuit=True`), we skip directly to `run_experiment_tool`
    with defaults filled in for every non-core parameter. Otherwise the
    machine walks `basics → fork → planting → soil → weather →
    management → initial_conditions → review` and only runs once
    review is confirmed.
    """
    from dssat_agent.schemas import ExperimentWizardInput
    from dssat_agent.services.wizard import advance_wizard

    raw = dict(raw or {})

    # Diagnostic: log the raw dict so we can see what shape the LLM chose.
    import json as _json
    try:
        logger.info(
            "[experiment_wizard_step] raw args (keys=%s):\n%s",
            sorted(raw.keys()),
            _json.dumps(raw, default=str, indent=2)[:800],
        )
    except Exception:
        logger.info("[experiment_wizard_step] raw args keys=%s", sorted(raw.keys()))

    # Normalize common LLM shape variants (see _normalize_llm_shape).
    _normalize_llm_shape(raw)

    # Partial-date inference ("March 1" → "2024-03-01" using prior year).
    if raw.get("planting_date"):
        probe = {"planting": {"date": raw["planting_date"]}}
        _infer_planting_year(probe)
        raw["planting_date"] = probe["planting"]["date"]

    try:
        params = ExperimentWizardInput.model_validate(raw)
    except ValidationError as ve:
        return validation_error_to_react(ve)

    return advance_wizard(params, user=user)


def _wizard_known_dict(params: Any) -> Dict[str, Any]:
    """Compact dict of already-collected wizard fields, for `known`."""
    known: Dict[str, Any] = {}
    if params.crop:
        known["crop"] = params.crop
    if params.location is not None:
        known["location"] = params.location.model_dump()
    if params.planting_date is not None:
        known["planting_date"] = params.planting_date.isoformat()
    if params.shortcircuit is not None:
        known["shortcircuit"] = params.shortcircuit
    return known


def _location_label(loc: Any) -> str:
    """Short label for a LocationInput, used in wizard prompts."""
    if loc is None:
        return "(unknown location)"
    if loc.kind == "point":
        return f"({loc.lat:.3f}, {loc.lon:.3f})"
    parts = [loc.county, loc.state, loc.country]
    return ", ".join(p for p in parts if p) or "(admin area)"


def query_experiment_tool(raw: Dict[str, Any], *, user: Any = None) -> Dict[str, Any]:
    """Validated entry point for the dssat-experiment-query skill's tool.

    Thin wrapper around `dssat_agent.services.api.query_experiment` — just
    adds Pydantic validation and shapes the response into the ReAct envelope.
    """
    from dssat_agent.schemas import QueryExperimentInput

    try:
        params = QueryExperimentInput.model_validate(raw or {})
    except ValidationError as ve:
        return validation_error_to_react(ve)

    from dssat_agent.services import query_experiment

    kwargs: Dict[str, Any] = {
        "experiment_id": params.experiment_id,
        "query_type": params.query_type.value,
    }
    if params.variables:
        kwargs["variables"] = list(params.variables)
    if params.chart_type:
        kwargs["chart_type"] = params.chart_type.value

    result = query_experiment(**kwargs) or {}
    if isinstance(result, dict) and result.get("error"):
        return {"status": "error", "error": result["error"], "details": kwargs}

    if not isinstance(result, dict):
        result = {"payload": result}
    result.setdefault("status", "ok")
    return result


# Surfaced to the chat agent's tool registry so the orchestrator stays
# domain-agnostic. Any DSSAT-specific copy belongs here, alongside the
# function it describes.
RUN_EXPERIMENT_TOOL_DESCRIPTION = (
    "Run a DSSAT crop simulation. The fast path when the user has stated "
    "a crop, a specific location, and a planting date in the same message. "
    "Examples: \"Run a corn experiment in Cullman county Alabama planted "
    "March 1 2024\", \"Simulate maize in Iowa on 2024-04-15\". Partial "
    "inputs fit better in `experiment_wizard_step`, which prompts the "
    "user for what's missing. Follow-up questions about a prior "
    "experiment (charts, variables, stress — any time an `experiment_id` "
    "is already in the conversation context) are better served by "
    "loading `dssat-experiment-query`.\n"
    "\n"
    "Args: `crop` (2-letter DSSAT code, e.g. MZ/WH/SB), `location` JSON "
    "object (either {kind: 'point', lat, lon} OR {kind: 'admin_area', "
    "country, state, county}), `planting` JSON object with `date` in ISO "
    "YYYY-MM-DD. Optional fields (cultivar, soil, weather source, "
    "fertilizer, irrigation, harvest) default to per-crop values."
)


QUERY_EXPERIMENT_TOOL_DESCRIPTION = (
    "Follow-up query against an already-run experiment. Supply the "
    "experiment_id from conversation memory (set when an experiment "
    "completes) plus a query_type: 'variables' returns specific output "
    "values, 'chart' renders a time-series chart, 'stress' aggregates "
    "stress factors, 'summary' returns the full simulation summary. Do "
    "NOT ask the user for the experiment_id — it's in conversation context."
)


EXPERIMENT_WIZARD_STEP_TOOL_DESCRIPTION = (
    "Guided-setup wizard for a DSSAT experiment. Call this FIRST — instead "
    "of `run_experiment` — whenever the user asks to run a simulation but "
    "HASN'T explicitly provided all three of crop, a specific location, "
    "and a planting date. Also call this when the user asks to be walked "
    "through setup ('guide me', 'walk me through it', 'step by step'). "
    "Pass ONLY the fields the user actually provided — empty args are "
    "fine. DO NOT invent a crop, location, or date; the wizard will ask "
    "the user for anything missing. The wizard returns `needs_input` "
    "envelopes that pause the graph until the user answers, then runs "
    "the simulation with defaults once the three core inputs are collected."
)


def _infer_planting_year(raw: Dict[str, Any]) -> None:
    """If `raw['planting']['date']` is a month/day without year, prepend
    `current_year - 1` so the simulation targets a season we likely have
    weather data for. Mutates `raw` in place; no-op when the date already
    carries a year or can be parsed cleanly.
    """
    import re
    from datetime import datetime

    planting = raw.get("planting")
    if not isinstance(planting, dict):
        return
    raw_date = planting.get("date")
    if not isinstance(raw_date, str) or not raw_date:
        return
    # Already a full YYYY-MM-DD — leave it.
    if re.match(r"^\d{4}-\d{2}-\d{2}$", raw_date.strip()):
        return
    # Try "March 1" / "Mar 1" / "3/1" / "3-1" / "01/03" patterns → infer year.
    year = datetime.now().year - 1
    candidates = [
        f"{raw_date} {year}", f"{raw_date}/{year}", f"{raw_date}-{year}",
    ]
    for cand in candidates:
        for fmt in ("%B %d %Y", "%b %d %Y", "%m/%d/%Y", "%m-%d-%Y", "%d/%m/%Y"):
            try:
                dt = datetime.strptime(cand, fmt)
                planting["date"] = dt.date().isoformat()
                planting["_year_inferred"] = year
                logger.info(
                    "[run_experiment] inferred planting year=%d from partial "
                    "date %r → %s", year, raw_date, planting["date"],
                )
                return
            except ValueError:
                continue


def run_experiment_tool(raw: Dict[str, Any], *, user: Any = None) -> Dict[str, Any]:
    """Run a DSSAT experiment from a tool-call payload.

    Args:
        raw: The tool's JSON payload, as the LLM produced it.
        user: Optional Django User (passed through as `user_id` for
              config/default resolution).

    Returns:
        A dict compatible with the ReAct envelope contract. Possible shapes:
        - {"status": "validation_error", "issues": [...], "hint": "..."}
        - {"status": "completed", "results": ..., "experiment_id": ..., ...}
        - {"status": "error", "error": "..."}
        - {"status": "failed", ...}  (simulation ran but returned no yield)
        - {"status": "missing_data", ...}
    """
    raw = dict(raw or {})
    # Don't silently default ``experiment_type`` to "single" — that
    # erased the wizard's experiment_type=sensitivity once the LLM
    # bailed mid-flow into ``run_experiment``. Force the LLM to pass
    # it through; a missing one comes back as a validation error that
    # routes the LLM to ``experiment_wizard_step`` (which still holds
    # the prior wizard ``known`` state).
    if not raw.get("experiment_type"):
        valid = ", ".join(t.value for t in ExperimentType)
        return {
            "status": "validation_error",
            "error": "experiment_type is required.",
            "issues": [{
                "field": "experiment_type",
                "message": (
                    "Pick one of: " + valid + ". Default to \"single\" "
                    "when the user gave a plain yield-estimate request "
                    "with no multi-run keywords (no \"ensemble\", "
                    "\"sensitivity\", \"sweep\", \"Monte Carlo\", "
                    "\"batch\", \"compare protocols\", \"across a "
                    "region\"). Use the multi-run value when those "
                    "keywords appeared. If a wizard turn already "
                    "established the type (it's in `known`), pass "
                    "that value through verbatim."
                ),
            }],
            "hint": (
                "Re-call run_experiment with the same args plus "
                "\"experiment_type\": \"single\" (for a plain yield "
                "estimate) or the multi-run value the user named."
            ),
        }
    # Log the raw payload before any normalization so we can diagnose
    # cases like "LLM invented values vs user provided them" after the
    # fact. Mirrors the experiment_wizard_step logging above; together
    # they're the audit trail for tool-call inputs.
    import json as _json
    try:
        logger.info(
            "[run_experiment] raw args (chat_id=%s, keys=%s):\n%s",
            raw.get("chat_id"),
            sorted(raw.keys()),
            _json.dumps(raw, default=str, indent=2)[:1500],
        )
    except Exception:
        logger.info(
            "[run_experiment] raw args keys=%s (chat_id=%s)",
            sorted(raw.keys()), raw.get("chat_id"),
        )
    # Normalize common LLM shape variants (bare-string location, common
    # crop names, flat `date`, nested `planting.date`) before Pydantic
    # validates — otherwise fields get silently dropped.
    # run_experiment expects nested `planting.date`, so re-wrap if the
    # normalizer flattened it.
    _normalize_llm_shape(raw)
    if raw.get("planting_date") and not raw.get("planting"):
        raw["planting"] = {"date": raw.pop("planting_date")}
    _infer_planting_year(raw)

    try:
        exp = _EXPERIMENT_ADAPTER.validate_python(raw)
    except ValidationError as ve:
        return _envelope_for_validation_error(ve, raw)

    # Soil existence check (not enumified — thousands of rows)
    if exp.soil_profile and not soil_profile_exists(exp.soil_profile):
        matches = resolve_soil_profile(exp.soil_profile, limit=5)
        return {
            "status": "validation_error",
            "error": "Soil profile not found.",
            "issues": [
                {
                    "field": "soil_profile",
                    "message": (
                        f"soil_profile '{exp.soil_profile}' does not exist in "
                        "the catalog."
                    ),
                    "received": exp.soil_profile,
                    "suggestions": [m["soil_id"] for m in matches],
                }
            ],
            "hint": (
                "Call `resolve_soil_profile(query, country=...)` to find valid "
                "soil IDs, or omit `soil_profile` entirely to let the system "
                "auto-pick a generic profile based on location."
            ),
        }

    legacy = experiment_to_legacy_params(exp, user=user)

    # Lazy import to avoid a circular import at module load time.
    from dssat_agent.services.workflow import run_full_simulation

    return run_full_simulation(
        legacy,
        user_id=getattr(user, "id", None),
        chat_id=raw.get("chat_id"),
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _envelope_for_validation_error(
    ve: ValidationError, raw: Dict[str, Any]
) -> Dict[str, Any]:
    """Build a ReAct envelope with enum suggestions and core-triple hint."""
    field_values: Dict[str, List[str]] = {
        "crop": build_crop_enum(),
        "weather_dataset": build_dataset_enum(),
    }
    if isinstance(raw.get("crop"), str):
        field_values["cultivar"] = build_cultivar_enum(raw["crop"])

    missing = _missing_core_triple(raw)
    hint: Optional[str] = None
    if missing:
        hint = (
            "Core inputs missing: "
            + ", ".join(missing)
            + ". Call `experiment_wizard_step` to start the guided setup, or "
            "re-call with crop + location + planting_date all provided."
        )

    return validation_error_to_react(ve, field_values=field_values, hint=hint)


def _missing_core_triple(raw: Dict[str, Any]) -> List[str]:
    """Return human-readable labels for any missing core triple fields.

    Defensive: LLMs occasionally emit malformed values for nested objects
    (e.g. a bare string instead of a dict). Treat any non-dict as absent
    rather than crashing.
    """
    missing: List[str] = []
    if not raw.get("crop"):
        missing.append("crop")

    loc = raw.get("location")
    if not isinstance(loc, dict) or not loc:
        missing.append("location")
    else:
        kind = loc.get("kind")
        if kind == "point":
            if loc.get("lat") is None or loc.get("lon") is None:
                missing.append("location (lat/lon)")
        elif kind == "admin_area":
            if not loc.get("country"):
                missing.append("location (country)")
        else:
            missing.append("location (kind)")

    planting = raw.get("planting")
    if not isinstance(planting, dict) or not planting.get("date"):
        missing.append("planting_date")

    return missing


def experiment_to_legacy_params(exp: Any, *, user: Any = None) -> Dict[str, Any]:
    """Translate a validated `ExperimentInput` into the legacy flat-dict format.

    The existing `run_full_simulation` → `build_experiment` → `run_experiment`
    pipeline accepts a flat dict with keys like `crop_code`, `latitude`,
    `planting_date`, `plant_population`, `row_spacing`, `fertilizer`,
    `irrigation`, `harvest`, `initial_conditions`. This helper produces that
    shape without touching the downstream code.
    """
    legacy: Dict[str, Any] = {
        "experiment_type": exp.experiment_type.value,
        "crop_code": exp.crop,
    }
    if exp.cultivar:
        legacy["cultivar_code"] = exp.cultivar
    if exp.soil_profile:
        legacy["soil_id"] = exp.soil_profile
    if exp.weather_dataset:
        legacy["weather_source"] = exp.weather_dataset
    if getattr(exp, "additional_instructions", None):
        legacy["additional_instructions"] = exp.additional_instructions
    if user is not None and hasattr(user, "id"):
        legacy["user_id"] = user.id

    # Location — flatten to top-level keys `run_full_simulation` expects.
    loc = exp.location
    if loc.kind == "point":
        legacy["latitude"] = loc.lat
        legacy["longitude"] = loc.lon
        if loc.elevation_m is not None:
            legacy["elevation"] = loc.elevation_m
    else:  # admin_area
        parts = [loc.county, loc.state, loc.country]
        legacy["location_name"] = ", ".join(p for p in parts if p)
        legacy["country"] = loc.country
        if loc.state:
            legacy["state"] = loc.state
        if loc.county:
            legacy["county"] = loc.county

    # Planting — top-level keys match downstream conventions. Only set
    # population/spacing when the LLM/user actually provided them; omission
    # lets run_full_simulation apply per-crop defaults.
    p = exp.planting
    legacy["planting_date"] = p.date.isoformat()
    if p.population is not None:
        legacy["plant_population"] = p.population
    if p.spacing is not None:
        legacy["row_spacing"] = p.spacing
    planting_extras: Dict[str, Any] = {}
    if p.method:
        planting_extras["plme"] = p.method.value
    if p.depth_cm is not None:
        planting_extras["pldp"] = p.depth_cm
    if planting_extras:
        nested: Dict[str, Any] = {"date": p.date.isoformat()}
        if p.population is not None:
            nested["population"] = p.population
        if p.spacing is not None:
            nested["row_spacing"] = p.spacing
        nested.update(planting_extras)
        legacy["planting"] = nested

    # Fertilizer applications -> DSSAT event dicts.
    if exp.fertilizer:
        legacy["fertilizer"] = [
            {
                "fdate": fa.input.date.isoformat(),
                "fmcd": fa.type.value,
                "facd": fa.methodology.value,
                "fdep": fa.input.depth_cm,
                "famn": fa.input.amount_kg_n_per_ha,
            }
            for fa in exp.fertilizer
        ]

    # Irrigation strategy + events.
    irr = exp.irrigation
    if irr.strategy == IrrigationStrategy.AUTO:
        auto_cfg: Dict[str, Any] = {"method": "automatic", "automatic": True}
        if irr.threshold_pct is not None:
            auto_cfg["threshold"] = irr.threshold_pct
        if irr.efficiency_pct is not None:
            auto_cfg["efficiency"] = irr.efficiency_pct
        legacy["irrigation"] = auto_cfg
    elif irr.strategy == IrrigationStrategy.FIXED_SCHEDULE:
        legacy["irrigation"] = {
            "method": "fixed",
            "events": [
                {
                    "idate": app.date.isoformat(),
                    "irval": app.amount_mm,
                    "irop": app.method.value,
                }
                for app in irr.applications
            ],
        }
    # strategy=NONE → don't set, downstream default is rainfed.

    # Harvest strategy.
    h = exp.harvest
    if h.strategy == HarvestStrategy.AUTO:
        legacy["harvest"] = {"strategy": "auto"}
    elif h.strategy == HarvestStrategy.FIXED_DATE:
        legacy["harvest"] = {"strategy": "fixed"}
    elif h.strategy == HarvestStrategy.REPORTED_DATE:
        harvest: Dict[str, Any] = {"strategy": "reported"}
        if h.date:
            harvest["date"] = h.date.isoformat()
        legacy["harvest"] = harvest

    # Initial conditions.
    ic = exp.initial_conditions
    legacy["initial_conditions"] = {
        "initial_water": ic.initial_water,
        "initial_no3": ic.initial_no3_kg_ha,
    }

    # Experiment-type-specific extras.
    et = exp.experiment_type
    if et == ExperimentType.SINGLE:
        legacy["num_years"] = exp.num_years
    elif et == ExperimentType.ENSEMBLE:
        legacy["treatments"] = exp.treatments
    elif et == ExperimentType.MONTE_CARLO:
        legacy["n_locations"] = exp.n_locations
        legacy["n_weather_realizations"] = exp.n_weather_realizations
        legacy["sampling_method"] = exp.sampling_method.value
    elif et == ExperimentType.BATCH:
        legacy["variations"] = exp.variations

    return legacy
