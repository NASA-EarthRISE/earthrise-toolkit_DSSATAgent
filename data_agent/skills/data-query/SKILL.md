---
name: data-query
description: Extract time-series data from a raster dataset at a location and date range.
tools:
  - query_data
  - resolve_dataset
  - resolve_location
---

## Playbook

Use `query_data` when the user wants values (tmax, tmin, rainfall, solar
radiation, NDVI, etc.) from a gridded dataset over a specific location and
date window — the same kind of output the Map Explorer page produces for a
"show me the time series" action.

### Before calling query_data

1. **Resolve the dataset identifier.** If the user names the dataset only
   partially or ambiguously, call `resolve_dataset(query="<what they said>")`
   first. Do NOT guess. The resolver returns candidate identifiers — if
   only one matches, use it; if several match and the choice is ambiguous,
   surface the candidate list to the user and ask which one.

2. **Resolve the location.** If the user gave coordinates, use them
   directly (`kind=point`). If they gave an admin area name, you can
   either:
   - Pass `location={"kind":"admin_area","country":..,"state":..,"county":..}`
     and let `query_data` resolve the centroid internally, OR
   - Call `resolve_location(name="<their text>")` to inspect the match
     first. Use the resolver when the admin name is ambiguous ("Springfield"
     could be Alabama or Missouri).

### Calling query_data

- `source` — the dataset identifier you just resolved (e.g. `power`,
  `chirps_chirts_era5`).
- `variables` — array of short-codes the dataset supports. Common
  weather: `["tmax", "tmin", "rain", "srad"]`. If uncertain about supported
  variables for a dataset, call `resolve_dataset` to see its `variables`
  field.
- `location` — a JSON object, never a free-text label:
  `{"kind":"point","lat":..,"lon":..}` or
  `{"kind":"admin_area","country":"..","state":"..","county":".."}`.
- `start_date`, `end_date` — ISO YYYY-MM-DD; end must be on or after start.

### Handling tool responses

- `status: ok` — the `records` array has one entry per day with the
  requested variables. Summarize for the user: window covered,
  record count, any obvious patterns (unusually wet month, extreme
  temperatures). Offer to generate a chart if relevant (task #10 wires
  this up).
- `status: needs_input` — the resolver couldn't match a dataset or
  location. Surface the prompt to the user and wait for their reply.
- `status: validation_error` — fix the offending field (use
  `suggestions` / `valid_values`) and re-call.
- `status: error` — tell the user the query couldn't run; suggest
  checking `check_availability` for the requested window.

### What NOT to use this skill for

- Fetching data that isn't in the catalog: there is no "fetch from web"
  path in this skill. Use `data-availability` first and if coverage is
  partial, the availability tool will prompt the user to initiate a
  remote fetch (task #8).
- Multi-year climatologies or long-range averages — those belong in
  the downstream simulation/modeling workflow, not here.
