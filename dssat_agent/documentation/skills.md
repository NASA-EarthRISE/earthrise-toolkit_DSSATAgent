# Chat tool registry

This document describes `dssat_agent`'s **chat tools** — entries in
`dssat_agent.tools.TOOL_REGISTRY` that the chat orchestrator discovers
generically; each entry describes one tool the LLM can call. (`dssat_agent`
*also* publishes a separate, larger **A2A skill surface** in `dssat_agent/a2a.py`
mounted at `/dssat_agent/` — see [`README.md`](README.md); this file covers only
the chat-tool registry.)

## `TOOL_REGISTRY` entry shape

Every entry in `dssat_agent.tools.TOOL_REGISTRY` is a dict with
four keys:

| Key | Type | Purpose |
|---|---|---|
| `description` | `() -> str` | Returns the one-paragraph description the LLM sees in its tool schema. |
| `schema_builder` | `() -> dict` | Returns the JSON schema for the tool's parameters. Called per request so dynamic enums (crop list, weather-dataset list) stay fresh. |
| `func` | `(args: dict, user=None) -> dict` | Handler. Returns an envelope dict with a `status` field (`ok`, `needs_input`, `error`, ...). Contract in `chat.schemas.envelope`. |
| `owner_agent` | `str` | Always `"dssat_agent"`. |

Ordering matters — `run_experiment` is listed first so llama3.1
picks it when the user provides full inputs. Reorder entries in
`TOOL_REGISTRY` to change LLM tie-breaking behaviour.

---

## `run_experiment`

**Purpose:** Fast path — run a DSSAT crop simulation end-to-end
when the user has stated the crop, a specific location, and a
planting date in the same message. Assembles weather, soil,
cultivar, and management inputs; writes a FileX; invokes the
DSSAT binary; captures inputs/outputs; and persists a
`StoredExperiment` row. Partial inputs should route through
`experiment_wizard_step`; follow-up questions on an existing
experiment should route through `query_experiment`.

**Handler:** `dssat_agent.services.run_experiment_tool`

**Schema builder:** builds from
`dssat_agent.schemas.SingleExperiment` with two dynamic enums —
`crop` (from `dssat_agent.schemas.build_crop_enum`) and
`weather_dataset` (from `data_agent.schemas.build_dataset_enum`).

**Key input params:**

| Name | Required | Type | Notes |
|---|---|---|---|
| `crop` | yes | str | 2-letter DSSAT code (`MZ`, `WH`, `SB`, ...). |
| `location` | yes | object | `{kind: "point", lat, lon}` or `{kind: "admin_area", country, state, county}`. |
| `planting.date` | yes | ISO date | `YYYY-MM-DD`. Bare `MM-DD` is filled to `<current_year - 1>-MM-DD`. |
| `cultivar` | no | str | Defaults to a per-crop pick. |
| `soil` | no | str | Soil profile id; defaults from reference raster + `SOIL.SOL`. |
| `weather_dataset` | no | str | Registered `data_agent` source id. Auto-picked if omitted. |
| `fertilizer`, `irrigation`, `harvest` | no | object | Optional management overrides. |

**Output envelope:**

```json
{
  "status": "ok",
  "experiment_id": "<uuid>",
  "results": {"yield": 6421.0, "biomass": 15832.0, "..."},
  "summary": "...",
  "chart_urls": ["..."]
}
```

Errors surface as `{"status": "error", "message": "..."}` or, on
validation failure, `{"status": "needs_input", "prompt": "..."}`.

**Example call (from the chat agent):**

```python
run_experiment_tool({
    "crop": "MZ",
    "location": {"kind": "admin_area", "country": "USA",
                 "state": "Alabama", "county": "Cullman"},
    "planting": {"date": "2024-03-01"},
})
```

---

## `experiment_wizard_step`

**Purpose:** Guided-setup wizard for a DSSAT experiment. Call
this instead of `run_experiment` whenever the user hasn't
supplied all three of crop, specific location, and planting date.
Also call it when the user asks to be walked through setup
("guide me", "walk me through it", "step by step"). Pass only
the fields the user actually provided — the wizard prompts for
anything missing, one field at a time, via `needs_input`
envelopes that pause the graph. Once the three core inputs are
collected it delegates to `run_experiment` with sensible defaults
for everything else.

**Handler:** `dssat_agent.services.experiment_wizard_step_tool`

**Schema builder:** built from
`dssat_agent.schemas.ExperimentWizardInput` with the same `crop`
enum provider as `run_experiment`.

**Key input params:** superset of `run_experiment`, but all
fields optional. Additional wizard-only fields track state (e.g.
which prompt was last shown).

**Output envelope:**

```json
{
  "status": "needs_input",
  "prompt": "Which crop would you like to simulate?",
  "field": "crop",
  "wizard_state": {"known": {"location": {...}}}
}
```

Once all core inputs are known, the wizard emits an `ok` envelope
identical to `run_experiment`.

**Example call:**

```python
experiment_wizard_step_tool({
    "location": {"kind": "point", "lat": 34.1, "lon": -86.8},
})
# → asks for crop + planting date
```

---

## `query_experiment`

**Purpose:** Follow-up query against an already-run experiment.
Requires the `experiment_id` from conversation memory (set when
an experiment completes) plus a `query_type`. The chat agent must
**not** ask the user for the id — it's in context.

**Handler:** `dssat_agent.services.query_experiment_tool`

**Schema builder:** built from
`dssat_agent.schemas.QueryExperimentInput`.

**Input params:**

| Name | Required | Type | Notes |
|---|---|---|---|
| `experiment_id` | yes | str | UUID of a `StoredExperiment` row. |
| `query_type` | yes | enum | `variables`, `chart`, `stress`, `summary`. |
| `variables` | when `query_type=variables` | list[str] | DSSAT variable codes to return. |
| `chart_type` | when `query_type=chart` | enum | `ExperimentChartType` — one of `lai`, `soil_water`, `water_stress`, `nitrogen_stress`, `phosphorus_stress`, `potassium_stress`, `et_components`, `soil_nitrogen`, `temperature` (lowercase). |

**Output envelope (shape varies by `query_type`):**

```json
{"status": "ok", "query_type": "variables", "values": {"HWAM": 6421.0}}
{"status": "ok", "query_type": "chart", "chart_url": "..."}
{"status": "ok", "query_type": "stress", "summary": {...}}
{"status": "ok", "query_type": "summary", "text": "..."}
```

**Example call:**

```python
query_experiment_tool({
    "experiment_id": "abc-123",
    "query_type": "variables",
    "variables": ["HWAM", "GNAM"],
})
```

---

## Adding or removing a tool

Add or delete an entry in `TOOL_REGISTRY` — that's the only
change needed. The chat orchestrator's `_build_tool_registry()`
imports the dict generically (no hardcoded tool names), so the
new tool appears in the LLM's schema on the next request. See
[`../../earthrise_agents_base/documentation/writing_a_subagent.md`](../../earthrise_agents_base/documentation/writing_a_subagent.md)
for the framework-level conventions.

## Related docs

- [`configuration.md`](configuration.md) — the settings and env
  vars these tools read.
- [`dependencies.md`](dependencies.md) — how these tools call
  into `data_agent` for weather + reference raster queries.
- `dssat_agent/skills/` — SKILL.md content packs the chat agent
  loads to shape LLM behaviour around these tools.
