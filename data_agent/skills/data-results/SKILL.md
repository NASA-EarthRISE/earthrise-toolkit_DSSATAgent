---
name: data-results
description: Synthesis guidance for responses that surface data-agent tool output.
composable: true
compose_into: results
---

## Results

When presenting time-series or availability results from the data agent:

- Lead with a 1–2 sentence summary of the window covered (dataset,
  location, date range, record count).
- If the user asked a specific question ("was it dry?", "how much rain?"),
  answer it directly using aggregates over the returned records before
  listing the raw numbers.
- Never invent values — only report figures the tool returned. If a
  record is missing for a requested day, say so.
- Mention the weather/data source by name once (e.g. "NASA POWER",
  "CHIRPS + CHIRTS merge") so the user knows provenance.
- Surface notable gaps in coverage. If the availability check said the
  window is partially covered, explicitly note what's missing and offer
  to fetch the remote data (the tool returns a `needs_input` envelope
  when a remote fetch would be required — let the user decide).
- Offer to produce a chart or a different visualization when the
  response mostly relies on numeric tables.

Units matter: always attach units to extracted variables (`tmax` in °C,
`rain` in mm, `srad` in MJ/m²/day, etc.). If the user's question was
unit-less, pick the native unit of the dataset.
