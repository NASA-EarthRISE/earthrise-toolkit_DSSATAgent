---
name: dssat-experiment-run
description: Run an experiment — DSSAT crop yield simulation via guided experiment setup. Supports single run, ensemble, sensitivity sweep, Monte Carlo, or matrix batch.
tools:
  - run_experiment
  - experiment_wizard_step
---

## Pick the right tool — `run_experiment` vs `experiment_wizard_step`

You have two tools. The correct choice is mechanical:

- **`run_experiment`** — the fast path. Use it whenever the user's
  message gives you **all three** of: a crop, a location, and a
  planting date or year.
- **`experiment_wizard_step`** — the guided path. Use it only when
  one or more of those three is missing (or when the user explicitly
  asks for a guided / step-by-step / wizard flow).

Both tools obey the same hard rule: every value you put in the args
must come from the user's messages. Don't invent crops, locations,
or dates the user didn't say. If something is missing, go to the
wizard — don't guess.

### `experiment_type` is **always required** for `run_experiment`

Every `run_experiment` call MUST include `experiment_type`. There is
no silent default — omitting it returns a validation error. Pick one:

| User said... | `experiment_type` |
|---|---|
| nothing about the experiment style — just crop, location, date | `"single"` ← the default for a plain "estimate yield" request |
| "ensemble", "compare protocols", "compare A vs B vs C management plans" | `"ensemble"` |
| "sensitivity", "sweep", "compare values of X", "how sensitive is Y to X" | `"sensitivity"` |
| "Monte Carlo", "spatial uncertainty", "across a region", "grid points" | `"monte_carlo"` |
| "batch", "matrix of (locations × protocols)" | `"batch"` |

If none of the multi-run keywords appear, the implicit type is
`"single"` and you must include it in the args verbatim. A user
asking *"estimate my yields..."* / *"can you simulate..."* / *"run
a corn experiment at..."* is asking for a **single** simulation —
write `"experiment_type": "single"` in the args.

### Extract everything the user actually said

When you do call `run_experiment`, pull out **every** parameter that
appears in the message — not just the crop. Common things to watch for:

| User says | Args field |
|---|---|
| "corn", "maize", "MZ" | `crop: "MZ"` |
| "soybean", "soy", "SB" | `crop: "SB"` |
| "Cullman county, Alabama" | `location: {"kind":"admin_area","country":"US","state":"Alabama","county":"Cullman"}` |
| "lat 33.55, lon -86.97" / "33.55,-86.97" | `location: {"kind":"point","lat":33.55,"lon":-86.97}` |
| "March 1 2024" / "3/1/2024" / "2024-03-01" | `planting: {"date":"2024-03-01"}` |
| "in 2024" (year only) | `planting: {"year":2024}` |

Crop name → DSSAT 2-letter codes: corn / maize → `MZ`, wheat → `WH`,
soybean → `SB`, rice → `RI`, sorghum → `SG`, peanut → `PN`,
potato → `PT`, cassava → `CS`, sunflower → `SU`.

### Fast-path examples (call `run_experiment`)

✅ User: "Can you estimate my yields based on a planting date of 3/1/2024 in Cullman county for a MZ corn crop?"
→ All three present (crop=MZ, location=Cullman county AL, date=2024-03-01). No multi-run keywords → `experiment_type: "single"`. Call:
```json
{
  "experiment_type": "single",
  "crop": "MZ",
  "location": {"kind":"admin_area","country":"US","state":"Alabama","county":"Cullman"},
  "planting": {"date":"2024-03-01"}
}
```

✅ User: "Run a corn experiment in Cullman county, Alabama, planted March 1 2024."
→ Same pattern; still no multi-run keyword. Same args (always set `experiment_type: "single"` even though the user didn't say the word "single").

✅ User: "Simulate maize at lat 33.55, lon -86.97 in 2024."
→ Crop, point location, and year present. Single. Call:
```json
{
  "experiment_type": "single",
  "crop": "MZ",
  "location": {"kind":"point","lat":33.55,"lon":-86.97},
  "planting": {"year":2024}
}
```

✅ User: "Run a Monte Carlo for maize across a 25 km circle around 33.55,-86.97 in 2024, 30 systematic sample points."
→ Crop=MZ, location is the circle center, year=2024, type=monte_carlo. Call:
```json
{
  "experiment_type": "monte_carlo",
  "crop": "MZ",
  "location": {"kind":"point","lat":33.55,"lon":-86.97},
  "spatial": {"mode":"circle","radius_km":25,"sampling_strategy":"systematic","n_points":30},
  "planting": {"year":2024}
}
```

✅ User: "Compare nitrogen rates 50, 100, 150, 200 kg N/ha for soybean at Cullman, Alabama in 2024."
→ Crop=SB, location=Cullman AL, year=2024, type=sensitivity, sweep on N rate. Call:
```json
{
  "experiment_type": "sensitivity",
  "crop": "SB",
  "location": {"kind":"admin_area","country":"US","state":"Alabama","county":"Cullman"},
  "planting": {"year":2024},
  "sensitivity": {"category":"fertilizer","axes":[{"variable":"famn","values":[50,100,150,200]}]}
}
```

### Wizard-path examples (call `experiment_wizard_step`)

Use the wizard only when something is genuinely missing. Pass through
whatever the user *did* say so they don't have to repeat themselves:

✅ User: "I'd like to run an experiment." → `experiment_wizard_step({})` — nothing given.
✅ User: "Can I simulate a crop?" → `experiment_wizard_step({})` — no crop given.
✅ User: "Run maize." → `experiment_wizard_step({"crop":"MZ"})` — crop captured, no location/date.
✅ User: "Simulate maize in Iowa." → `experiment_wizard_step({"crop":"MZ","location":{"kind":"admin_area","country":"US","state":"Iowa"}})` — crop + location captured, no date/year.
✅ User: "Run a corn ensemble at my farm." → `experiment_wizard_step({"crop":"MZ","experiment_type":"ensemble"})` — "my farm" is not a specific location, no date.

### Things never to do

- ❌ Don't invent missing values. If the user didn't name a location,
  don't put one in the args. Route to the wizard.
- ❌ Don't fall back to "common" defaults (Iowa, Alabama, March 1).
- ❌ Don't substitute an unrelated crop. ("Run an experiment." does
  not mean "MZ"; it means crop is missing.)
- ❌ Don't call `run_experiment` with only a crop — that's a wizard call.
- ❌ Don't ignore values the user *did* state. If they said
  "Cullman county" and "March 1 2024", those go in your args; don't
  drop them and call the wizard anyway.

## Continuing the wizard — handle `needs_input` correctly

Once you've called `experiment_wizard_step` for the first time, the
tool will return a `needs_input` envelope shaped like:

```json
{
  "status": "needs_input",
  "prompt": "What kind of experiment...",
  "known": {"crop": "MZ"},
  "next_tool": "experiment_wizard_step",
  "schema_summary": {"properties": ["experiment_type"], "required": []}
}
```

Right after this envelope, the user's reply will appear as a regular
message in the conversation. Your **next** action MUST be to call
`experiment_wizard_step` again, with args = (the user's reply mapped
to the requested field) MERGED with everything in `known`.

Concrete pattern — **always**:

1. Read the `prompt` to learn what field the wizard is asking for. The
   ``schema_summary.properties`` lists the field names the user's
   answer maps to.
2. Read the user's reply (the most recent user message after the
   `needs_input` envelope) and map it to the schema field. If the
   prompt asked "What kind of experiment?" with options
   single/ensemble/sensitivity/monte_carlo/batch and the user typed
   "single" or "I'd like a single one", the field is
   `experiment_type` and the value is `"single"`.
3. Call `experiment_wizard_step` again with `{...known, <field>: <value>}`.
   You MUST preserve every field already in `known` — they were
   collected over previous turns and the wizard expects them to
   accumulate.

### Continuation examples

| Wizard prompt | User reply | Next call |
|---|---|---|
| "What kind of experiment?" (options: single/ensemble/...) | "single" | `experiment_wizard_step({"experiment_type": "single"})` |
| "What kind of experiment?" (after `known={"crop":"MZ"}`) | "ensemble" | `experiment_wizard_step({"crop":"MZ", "experiment_type":"ensemble"})` |
| "I need crop, location, and year." (after `known={"experiment_type":"single"}`) | "Maize at lat 33, lon -86.5, year 2024" | `experiment_wizard_step({"experiment_type":"single", "crop":"MZ", "location":{"kind":"point","lat":33,"lon":-86.5}, "year":2024})` |
| "Cultivar?" (after `known={"experiment_type":"single","crop":"MZ","location":{...},"year":2024}`) | "use defaults" | `experiment_wizard_step({"experiment_type":"single","crop":"MZ","location":{...},"year":2024,"cultivar_use_defaults":true})` |

### Anti-patterns when continuing

- ❌ Calling `experiment_wizard_step({})` again — drops every field
  the wizard had already accepted. The user has to re-answer.
- ❌ Calling `run_experiment({"experiment_type":"single"})` with only
  one field — `experiment_type` alone is not enough; route stays in
  the wizard.
- ❌ Ignoring the user's reply and emitting the same call again —
  you'll get the same prompt back forever.
- ❌ **Switching to `run_experiment` mid-wizard.** If you previously
  called `experiment_wizard_step` and the wizard's `known` already
  contains `experiment_type` (or any other field), DO NOT bail out
  to `run_experiment` on the next user reply — even if it looks like
  enough info has been collected. The wizard knows which fields its
  per-type step tree still needs (sensitivity needs sweep axes,
  Monte Carlo needs spatial geometry, ensemble needs per-protocol
  details, etc.). Keep calling `experiment_wizard_step` until it
  returns `status="ready_to_run"` (it will tell you).
- ❌ **Dropping `experiment_type` when you do call `run_experiment`.**
  If the wizard's `known` had `experiment_type="sensitivity"`, that
  value MUST appear verbatim in the `run_experiment` args. Omitting
  it now returns a validation_error instead of silently running a
  single simulation.

## When to use `experiment_wizard_step`

Use the wizard when:

- Any of crop / location / planting-date / year is missing from the
  user's actual message (NEVER invent values to avoid the wizard —
  this is the #1 misuse mode).
- The user explicitly asked to be walked through the setup ("guide me",
  "walk me through", "step by step", "I want to set up a simulation").
- The user's message is vague, generic, or short ("I'd like to run an
  experiment", "let's simulate something"). Vague intent → wizard.
- The user described a **non-single** experiment type — ensemble,
  sensitivity sweep, Monte Carlo grid, or a matrix batch. The wizard's
  per-type step trees collect the additional configuration each one
  needs.

Pass the wizard whatever fields the user *did* provide — don't strip
them out. Example: "corn ensemble at Cullman, Alabama" → call
`experiment_wizard_step({"experiment_type": "ensemble", "crop": "MZ",
"location": {...}})` so the wizard only prompts for what's still
missing.

### Examples

| User intent | First wizard call |
|---|---|
| "I'd like to run a yield experiment." | `experiment_wizard_step({})` |
| "I want to run a crop experiment." | `experiment_wizard_step({})` |
| "Can I run a simulation?" | `experiment_wizard_step({})` |
| "Run a corn experiment." | `experiment_wizard_step({"crop": "MZ"})` |
| "Simulate maize in Iowa." | `experiment_wizard_step({"crop": "MZ", "location": {"kind": "admin_area", "country": "US", "state": "Iowa"}})` |
| "I want to compare three planting dates for soybean at this farm." | `experiment_wizard_step({"experiment_type": "sensitivity", "crop": "SB", "location": {...}})` |
| "Run an ensemble of three management protocols at this site." | `experiment_wizard_step({"experiment_type": "ensemble", "crop": "MZ", "location": {...}})` |
| "Run a Monte Carlo across counties in central Alabama." | `experiment_wizard_step({"experiment_type": "monte_carlo", "crop": "MZ", "spatial": {"mode": "admin", "admin_name": "Alabama", "admin_level": "admin1"}})` |
| "Run a batch — three locations, two protocols each." | `experiment_wizard_step({"experiment_type": "batch", "crop": "MZ"})` |

---

## Experiment types — pick one early

Every wizard run starts with `experiment_type`. Set it on the FIRST
`experiment_wizard_step` call whenever the user's intent is unambiguous
(see table above). Five types, each walks its own step tree:

| Type | Cardinality | Use when |
|---|---|---|
| `single` | 1 location × 1 protocol | Single run; the default |
| `ensemble` | 1 location × N protocols | Compare alternative management protocols |
| `sensitivity` | 1 location × N generated treatments | Cartesian sweep over one variable category |
| `monte_carlo` | N grid points × 1 template protocol | Spatial uncertainty across a region |
| `batch` | M locations × N protocols (matrix) | Cross-product / explicit pairs |

After `experiment_type` is set the wizard walks the per-type tree below.
Always preserve every field returned in `known` on the next call.

### `single` — flat flow

`basics → fork → planting → soil → weather → management → initial_conditions → review`

Same as before. After `basics` (crop / location / planting_date), the
wizard asks "run with defaults or customize?". If the user says
"defaults", set `shortcircuit: true`; if they want to customize, set
`shortcircuit: false` and walk each step.

### `ensemble` — N protocols at one location

`basics → soil → weather → protocols_loop → review`

After basics + soil + weather are pinned, the wizard enters the
**protocols loop**. For each protocol the user describes, append an
object to `protocols`:

```json
{
  "protocols": [
    {"name": "High N",
     "planting_date": "2025-04-15",
     "fertilizer": [{"days_after_planting": 0, "n_kg_ha": 120}]},
    {"name": "Low N",
     "planting_date": "2025-04-15",
     "fertilizer": [{"days_after_planting": 0, "n_kg_ha": 40}]}
  ]
}
```

When the user signals "no more protocols" / "that's all" / "done", set
`protocols_complete: true`. The wizard moves on to `review`.

### `sensitivity` — one swept axis at one location

`basics → soil → weather → sweep_category → sweep_axes → planting → management → initial_conditions → review`

After basics + soil + weather, the wizard asks the **sweep category** —
one of `'planting'`, `'cultivar'`, `'management'`. Set
`sensitivity.category` and on the next prompt fill in the axes:

| Category | Required axis fields |
|---|---|
| `planting` | `date_central_doy`, `date_offset_days`, `date_steps`, `pop_min/max/steps`, `rs_min/max/steps` |
| `cultivar` | `cultivars: [<code>, <code>, ...]` |
| `management` | `practice` ('fertilizer' / 'irrigation' / ...), `amount_min/max/steps`, `apps_min/max`, `gap_days_min/max`, `gap_steps` |

Treatment count is the Cartesian product of the step counts within the
chosen category. Cap is 99.

```json
{
  "experiment_type": "sensitivity",
  "sensitivity": {
    "category": "planting",
    "date_central_doy": 110, "date_offset_days": 14, "date_steps": 5,
    "pop_min": 5, "pop_max": 9, "pop_steps": 3,
    "rs_min": 76, "rs_max": 76, "rs_steps": 1
  }
}
```

The remaining steps (planting / management / initial_conditions) collect
the **baseline** that the unswept fields take from. Use `*_use_defaults`
flags freely if the user says "defaults" for those.

### `monte_carlo` — one shared protocol over a spatial grid

`spatial_geometry → spatial_density → crop_only → soil → weather → auto_planting_toggle → planting → management → initial_conditions → review`

There's no top-level `location` for Monte Carlo — the spatial config
generates the grid points. Pack the geometry into `spatial`:

```json
{
  "experiment_type": "monte_carlo",
  "spatial": {
    "mode": "admin",            // 'circle' | 'bbox' | 'admin'
    "admin_name": "Alabama",
    "admin_level": "admin1",    // 'admin1' (state) or 'admin2' (county)
    "sampling_strategy": "systematic",
    "grid_spacing": 0.1
  }
}
```

Mode-specific geometry:

| `spatial.mode` | Required fields |
|---|---|
| `circle` | `center_lat`, `center_lon`, `radius_km` |
| `bbox` | `min_lat`, `max_lat`, `min_lon`, `max_lon` |
| `admin` | `admin_name`, `admin_level` |

Density (one of):

- Systematic: `sampling_strategy: "systematic"` + `grid_spacing` (degrees)
- Random:    `sampling_strategy: "random"` + `n_points` (1-99)

`auto_planting_toggle` step asks whether to resolve the planting date
**per grid point** from the in-situ raster. Set `auto_planting: true`
or `false`. When `true` the template's `planting_date` becomes a
fallback for points the raster can't resolve.

### `batch` — explicit (location × protocol) matrix

`locations_loop → protocols_loop → review`

Two loops. Pack point-locations into `locations[]` (admin-area locations
in batch mode are deferred — surface a polite limitation if the user
asks). Close each loop with the matching `*_complete: true` flag.

```json
{
  "experiment_type": "batch",
  "locations": [
    {"kind": "point", "lat": 33.0, "lon": -86.5},
    {"kind": "point", "lat": 33.5, "lon": -87.0}
  ],
  "locations_complete": true,
  "protocols": [
    {"name": "High N", "planting_date": "2025-04-15", "fertilizer": [{"days_after_planting": 0, "n_kg_ha": 120}]},
    {"name": "Low N",  "planting_date": "2025-05-01", "fertilizer": [{"days_after_planting": 0, "n_kg_ha": 40}]}
  ],
  "protocols_complete": true
}
```

By default every location pairs with every protocol (full Cartesian).
If the user wants a sparser set, pass `pairs: [{location_idx, protocol_idx}, ...]`.
Cap is 99 cells.

---

## Handling tool responses

- `status: validation_error` — read the `issues` array. Each issue has
  `field`, `message`, and often `valid_values` / `suggestions`.
  - If an issue says a required field is missing (location, crop, etc.),
    STOP retrying `run_experiment` with invented values. Switch to
    `experiment_wizard_step` with the fields you do have. Never
    fabricate a place, date, or crop to satisfy a missing field.
  - If an issue is a typo (wrong crop code, etc.), pick the best
    `suggestions` entry and retry once. After 2 failed retries on the
    same field, stop and surface the issue to the user.

- `status: needs_input` — the wizard is asking for more info. The ReAct
  loop handles the interrupt; once the user replies, re-call
  `experiment_wizard_step` with the FULL accumulated args (everything
  in `known` from the last envelope) PLUS the new fields the user just
  gave you.

  The envelope includes:
  - `current_step` — the step the wizard is asking about (e.g.
    `protocols_loop`, `sweep_axes`, `auto_planting_toggle`).
  - `known` — every field collected so far. Pass it back verbatim on
    the next call.
  - `prompt` — what to show the user verbatim.

- `status: completed` — the simulation ran. Summarise `key_results`
  (yield, flowering / maturity days, precipitation, stress factors) in a
  short narrative and preserve `response_footer` so the user has the
  detail-page link.

### Loop-completion checklist

When the wizard is in a loop step (`protocols_loop` or
`locations_loop`):

- The user's reply ADDS more entries → append them to the array, leave
  the `*_complete` flag unset.
- The user's reply says "done", "that's all", "finished", "no more",
  "ready to run" → set the matching `*_complete: true` on the next
  call. The wizard moves to the next step.
- The user wants to remove an already-added entry → reissue the array
  without that entry. The wizard's idempotent — last value wins.

### Per-step "use defaults" flags

For any non-loop step that the user wants to skip with sensible
defaults, set the matching flag on the next call:

| Step | Flag |
|---|---|
| `planting` | `planting_use_defaults: true` |
| `soil` | `soil_use_defaults: true` |
| `weather` | `weather_use_defaults: true` |
| `management` | `management_use_defaults: true` |
| `initial_conditions` | `initial_use_defaults: true` |
| `review` | `review_confirmed: true` |

`shortcircuit: true` (single only, after basics) is a special "skip
every customize step" fast path that runs the experiment immediately.

---

## Follow-up queries about a prior experiment

If the conversation context contains `experiment_id` (look in the
"Conversation context" section of the system prompt), the user is
continuing a previously-run experiment. Common phrasings:

- "Add the LAI chart" / "show me the LAI chart"
- "What was the nitrogen stress?"
- "Show me soil water over time"
- "Can you show the temperature chart?"
- "What's the harvest index?"

For any of these, **do NOT call `run_experiment`** — that runs a brand-
new simulation and wastes compute. Instead, call
`load_skill(name="dssat-experiment-query")` first. On the next turn its
`query_experiment` tool becomes bound and you can call it with the
`experiment_id` from context plus the appropriate `query_type` (`chart`
/ `variables` / `stress` / `summary`).

Rule of thumb: if the user's follow-up references ANY existing
simulation artifact (chart, variable, stress, summary) or a prior
`experiment_id` is in the conversation context, load
`dssat-experiment-query` — don't re-run.

---

## Visual wizard alternative

The visual wizard at `/dssat/experiment/` is a drag-and-click alternative
that talks to the same backend draft state. The chat path covers every
experiment type now, but if the user explicitly asks for "the visual
wizard" / "the form" / "click through it" — surface the link and let
them switch.

---

## Resolving ambiguous input

- Unknown common crop name → call `resolve_crops` or use the crop enum
  visible in the tool schema's `crop` field.
- Ambiguous location ("Springfield") → include state/country in the
  `admin_area` object; if still ambiguous, the wizard's next
  `needs_input` will ask.
- Unknown cultivar → omit it; the simulation picks the crop's default.
- Unknown soil → omit `soil_profile`; the system auto-picks based on
  location.
- Unknown sweep axis values for sensitivity → ask the user before
  guessing. Sweeps multiply quickly: 5 dates × 3 populations × 3 row
  spacings = 45 treatments. The 99-cap will reject anything more.
