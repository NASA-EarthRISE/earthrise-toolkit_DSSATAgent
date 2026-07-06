---
name: dssat-results
description: DSSAT Agent result formatting instructions for composing into chat results.
composable: true
compose_into: results
---

## Results

When presenting crop simulation results, write a **narrative analysis** tailored to the user's original question — not a generic data dump.

**CRITICAL RULES:**
1. ONLY use values and labels provided in the key_results data. Do NOT rename, reinterpret, or guess meanings.
2. Do NOT perform any mathematical operations or unit conversions.
3. Do NOT invent or fabricate any values not present in the data.
4. Do NOT reference internal DSSAT file names, codes, or technical details (e.g. no crop codes, model version numbers, file names).
5. Do NOT list all output variables. The detailed tables and charts are displayed separately below your response.
6. Do NOT use bullet points or numbered lists to present the results. Write in flowing paragraphs.
7. Do NOT make up agronomic advice or assessments. Use ONLY the `_insights` field from the data for yield assessment, stress description, and suggestions. Paraphrase the insights naturally but do NOT contradict them or add your own.

### Single Simulation Results

Start by confirming what was simulated (crop, location, planting date) using the user's original request.

Weave in the key numbers naturally: yield, days to flowering, days to maturity, and precipitation. Use the exact values from the data.

Then paraphrase the `_insights` field — this contains the pre-computed yield assessment, stress analysis, and suggestion. Rewrite it conversationally but do NOT change the meaning or add claims not in the insights.

End with: "Detailed results, charts, and additional output variables are available in the expandable sections below."

### Ensemble / Sensitivity Results

**Answer the user's question first.** The user asked a comparison or optimization question — answer it directly.

Available context (read from key_results):
- ``treatment_count`` — how many treatments ran.
- ``Mean / Min / Max yield (kg/ha)``, ``Yield std dev (kg/ha)``, ``Mean days to maturity`` — aggregate stats.
- ``Best treatment`` / ``Best yield (kg/ha)``, ``Worst treatment`` / ``Worst yield (kg/ha)``.
- ``Sweep`` — for sensitivity runs, the swept variable and range (e.g. "fertilizer amount 10-30 (5 step(s)) × 3-5 applications × 3 gap-day step(s)").
- ``_insights`` — pre-computed best-vs-worst-vs-mean comparison sentences (paraphrase, do not contradict).

Write a 2-3 paragraph response, in this shape:

1. **Lead with the answer.** One sentence stating the best treatment, its yield, and the swept variable's value if applicable. Example: "The best yield (727 kg/ha) came from Treatment 23 — 90 kg N/ha applied in 3 doses 20 days apart."

2. **Frame the spread.** State the yield range from worst (X kg/ha, label) to best (Y kg/ha, label), the mean as a baseline, and either the absolute or % spread between min and max. Mention how the worst compares to the mean (often this is where stress shows up).

3. **Explain the pattern (sensitivity only).** If a sweep variable is present, describe the trend — does yield rise with the swept variable? Plateau? Have a peak? Use the best treatment's parameter values to anchor the description.

Do NOT enumerate every treatment. Do NOT list raw treatment numbers without their parameters. Do NOT invent stress claims that aren't in `_insights`.

End with: "Treatment comparison charts and detailed per-treatment results are available in the expandable sections below."

### Monte Carlo Results

**Frame results as a regional assessment** answering the user's spatial/risk question.

Present the yield distribution using the quantile data:
- State the median (P50) yield as the "expected" yield.
- State the range: P10 to P90 as the "likely range" (80% of scenarios fall here).
- State P5 as the worst-case and P95 as the best-case.
- Include mean and standard deviation if available.

Present these quantiles in a compact markdown table:

| Statistic | Yield (kg/ha) |
|-----------|---------------|
| Best case (P95) | ... |
| Upper quartile (P75) | ... |
| Median (P50) | ... |
| Lower quartile (P25) | ... |
| Worst case (P5) | ... |

Then paraphrase the `_insights` field for the regional assessment.

End with: "Spatial distribution charts and per-location details are available in the expandable sections below."

### General Guidelines

**If the user provided additional_instructions**, honor those for what to highlight.

**Tone**: Conversational and informative. Write in paragraphs, not lists. Answer the user's actual question — do not give a generic simulation report.
