---
name: data-availability
description: Check how much of a requested dataset window is already loaded locally.
tools:
  - check_availability
  - resolve_dataset
  - resolve_location
---

## Playbook

Use `check_availability` when the user asks "do we have data for X?",
"what's the coverage for Y?", or before a `query_data` call on a window
that might not be fully ingested yet. The tool summarizes local coverage —
first date, last date, record count — without fetching anything remote.

### Before calling check_availability

- Call `resolve_dataset(query=…)` if the dataset identifier is imprecise.
- If the user names a place and there's any chance you'll follow up with a
  remote-source probe or a fetch (partial/no local coverage), grab the
  coordinates first via `resolve_location` — `check_availability` accepts
  an optional `location` that the probe/fetch flow consumes.

### Calling check_availability

- `source` — resolved dataset identifier.
- `start_date`, `end_date` — ISO YYYY-MM-DD.
- `location` — optional JSON object (same shape as `query_data`'s location).
  Include when the user's question is scoped to a place AND you anticipate
  needing a fetch if coverage is partial. Omit for a pure local check.

### Handling responses

- `status: ok` — the `availability` payload summarizes local coverage.
  - **Full coverage** (local range fully contains the request window):
    tell the user the data is available and offer to run `query_data`.
  - **Partial coverage** (some but not all of the window is local):
    surface the gap to the user and ask whether to fetch the missing
    portion from the remote source. Task #8 turns this into an interrupt
    with a yes/no confirmation; until then, ask in plain text.
  - **No coverage**: explain that nothing is locally available and ask
    whether to trigger a remote fetch.
- `status: validation_error` — fix the bad field (dataset id, dates) and
  retry.
- `status: error` — report that the availability check failed; suggest
  trying a different dataset.

### What NOT to use this skill for

- Actually extracting the data — use `data-query` once you've confirmed
  coverage.
- Cross-dataset comparisons — iterate over datasets one at a time.
