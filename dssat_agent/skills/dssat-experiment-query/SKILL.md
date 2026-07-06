---
name: dssat-experiment-query
description: Pull additional details, charts, variables, or stress summaries from an already-run DSSAT experiment.
tools:
  - query_experiment
---

## Playbook

Use `query_experiment` for follow-up questions about a simulation that
already completed in this conversation. Typical prompts:

- "Show me the LAI chart."
- "What was the nitrogen stress?"
- "Can I see the soil water content?"
- "Give me the harvest index."
- "What's the daily water stress over the season?"

Do NOT call `run_experiment` to answer these — the data already lives in
the stored experiment record.

### Where experiment_id comes from

`experiment_id` is in conversation context, set by `run_experiment` when a
simulation completes. Always pull it from context — do NOT ask the user.
If multiple experiments have been run and the user is ambiguous about
which one they mean, use the most recent.

### Picking a query_type

- `variables` — specific output values. Set `variables` to a list of
  short-codes (e.g. `["lai", "n_uptake", "harvest_index"]`).
- `chart` — render a time-series figure. Set `chart_type` to one of:
  `lai`, `soil_water`, `water_stress`, `nitrogen_stress`,
  `phosphorus_stress`, `potassium_stress`, `et_components`,
  `soil_nitrogen`, `temperature`.
- `stress` — aggregate stress analysis across all factors.
- `summary` — the complete simulation summary.

When the user's phrasing doesn't cleanly map, prefer:
- "show me the X chart" → `chart`, chart_type=X
- "what was X?" (single variable) → `variables`
- "how was the stress?" (no specific factor) → `stress`
- "give me the summary again" → `summary`

### Handling responses

- `status: ok` — the payload carries chart artifacts, variable values, or
  summary text. Weave them into a short narrative reply; keep any
  structured artifacts available for the UI to render.
- `status: validation_error` — the most common cause is a missing
  `variables` list (for `query_type=variables`) or `chart_type` (for
  `query_type=chart`). Add the missing field and retry.
- `status: error` — report that the query couldn't run and offer to run
  a new simulation or pick a different query_type.
