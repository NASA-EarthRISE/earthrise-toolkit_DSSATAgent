---
name: dssat-query
description: Instructions for handling follow-up queries about existing DSSAT experiments.
composable: true
compose_into: capabilities
---

## Experiment Queries

When a user asks a follow-up question about a previously run simulation (e.g. "show me the LAI chart",
"what was the nitrogen stress?", "can I see soil water content?"), use the `query_experiment` skill
with the `experiment_id` from conversation context.

**query_type options:**
- `variables` — retrieve specific output variable values. Pass a `variables` list (e.g. `["lai", "n_uptake", "harvest_index"]`).
- `chart` — generate a specific chart. Pass `chart_type` (e.g. `"lai"`, `"soil_water"`, `"water_stress"`, `"nitrogen_stress"`, `"phosphorus_stress"`, `"potassium_stress"`, `"temperature"`).
- `stress` — full stress analysis across all factors.
- `summary` — complete simulation summary.

**Important:** The `experiment_id` should come from the conversation context stored after the initial simulation run. Do not ask the user for it.
