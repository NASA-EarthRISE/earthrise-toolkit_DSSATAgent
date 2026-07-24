# Accessibility Compliance Report — Section 508 / WCAG 2.2 Level A & AA

**Application:** EarthRISEAgents / DSSAT Chat Agent (Django)
**Date:** 2026-07-24
**Standard tested:** WCAG 2.2, conformance target **Level A + AA** (the Section 508 baseline)
**Tooling:** [pa11y](https://github.com/pa11y/pa11y) v8.0.0 running **both** runners:
  - `htmlcs` — HTML_CodeSniffer, ruleset `WCAG2AA`
  - `axe` — axe-core 4.8

## How the audit was run

The app was run locally against a real PostGIS database (SQLite is not
supported — the migrations issue `CREATE SCHEMA`, which requires PostgreSQL).
A throwaway superuser session cookie was injected into pa11y so that
login-gated pages (almost the entire app, behind `LoginRequiredMiddleware`)
could be reached. Each page was scanned with both runners at once and the
union of issues recorded.

- **Pages scanned:** 32 top-level HTML routes.
- **Excluded from results:** `/management/feedback/messages/` — this route
  returns `application/json` (an API endpoint), not an HTML document. pa11y
  wrapped the JSON in a bare `<html>` shell and reported 4 false positives
  (missing `<title>`, missing `lang`). It is not a user-facing page and is
  excluded from the totals below.
- Detail/edit pages that require seed rows (e.g. `/dssat/soils/<id>/`) were
  not reachable because the dev database has no seed data; the templates they
  use are structurally covered by their list-page and create-page siblings,
  which were scanned.

## Result summary

| | Count |
|---|---|
| HTML pages scanned | 31 |
| Pages with **0** errors | 5 |
| Pages with errors | 26 |
| **Total Level A/AA errors** | **267** |

### Errors by WCAG success criterion

| Errors | Success criterion | Level |
|---:|---|:--:|
| 169 | **1.4.3 Contrast (Minimum)** | AA |
| 57  | **4.1.2 Name, Role, Value** (controls with no accessible name) | A |
| 36  | **1.3.1 Info and Relationships / 4.1.2** (unlabelled form fields) | A |
| 3   | **1.4.1 Use of Color** (links not distinguishable in body text) | A |
| 1   | **2.1.1 Keyboard** (scrollable region not focusable) | A |
| 1   | **2.4.1 Bypass Blocks** (no skip link / landmark bypass) | A |

### Errors by page

| Errors | Page |
|---:|---|
| 61 | `/knowledge/` |
| 26 | `/data/map/` |
| 20 | `/dssat/config/` |
| 20 | `/dssat/config/system/` |
| 19 | `/data/data-availability/` |
| 15 | `/dssat/soils/` |
| 14 | `/dssat/soils/create-from-texture/` |
| 9  | `/dssat/soils/create/` |
| 8  | `/dssat/treatments/` |
| 7  | `/accounts/profile/` |
| 6  | `/dssat/crops/` |
| 6  | `/knowledge/graphrag/` |
| 6  | `/knowledge/raptor/` |
| 5  | `/accounts/password-change/done/` |
| 5  | `/dssat/fields/` |
| 4  | `/accounts/password-change/` |
| 4  | `/accounts/users/` |
| 4  | `/dssat/codes/` |
| 4  | `/dssat/experiment/` |
| 4  | `/dssat/experiments/` |
| 4  | `/knowledge/config/` |
| 4  | `/knowledge/documents/` |
| 4  | `/knowledge/ontology/` |
| 4  | `/management/feedback/` |
| 2  | `/accounts/register/` |
| 2  | `/` (home) |
| 0  | `/accounts/login/`, `/accounts/password-reset/`, `/accounts/password-reset/done/`, `/accounts/password-reset/complete/`, `/data/` |

Most errors are a handful of **shared components** (the nav bar, form
controls, status pills) repeated across many pages — so a small number of
fixes clears a large share of the total.

---

## Findings & required fixes

### 1. Contrast — shared nav bar (affects ~23 pages) — WCAG 1.4.3 (AA)

The site header (`.explorer-nav`, background `#2e2e32`) contains three
components with failing contrast. Measured with the real computed styles in
Chrome:

| Component | File | Current | Ratio | Needs |
|---|---|---|---|---|
| `.nav-feedback-link` ("Send Feedback") | `earthrise_agents_base/static/earthrise_agents_base/css/feedback.css:157` | `color: var(--text-secondary)` = `#58585b` on `#2e2e32` | **1.91:1** | ≥ 4.5:1 |
| `.nav-brand` (product name) | `earthrise_agents_base/static/earthrise_agents_base/css/chat.css:1185` | gradient clipped to transparent text; falls back to `#3a3a3a` on `#2e2e32` | **1.19:1** | ≥ 4.5:1 |
| `.nav-user-caret` (▾ glyph) | `earthrise_agents_base/static/earthrise_agents_base/css/chat.css:1341` | `opacity: 0.7` on `rgba(255,255,255,0.85)` text | **fails** | ≥ 3:1 (glyph) |

> Note: `.nav-links > a` and `.nav-dropdown-toggle` use
> `rgba(255,255,255,0.6)` on the dark nav, which computes to **5.9:1** and
> **passes** — no change needed there.

**Fix:** raise the feedback-link color to a light value on the dark nav;
give `.nav-brand` a solid light color (or a light-on-dark gradient) instead
of the dark body-text fallback; raise the caret opacity.

### 2. Contrast — status pills / badges / step pills — WCAG 1.4.3 (AA)

Small pill/badge components use ~12–18% tinted backgrounds with a same-hue
text color; several fall under 4.5:1 for their small text:

| Component | File |
|---|---|
| `.status-pill.status-*` | `earthrise_agents_base/static/earthrise_agents_base/css/explorer.css:552` |
| `.step-pill` | `knowledge_agent/static/knowledge_agent/css/knowledge.css:222` |
| `.active-badge` | `knowledge_agent/static/knowledge_agent/css/knowledge.css:194` |
| `.entry-card-badge` / `.badge-*` | `earthrise_agents_base/static/earthrise_agents_base/css/home.css:50` |
| `.role-tag` | `earthrise_agents_base/static/earthrise_agents_base/css/management.css:141` |
| `.btn` / `.tab` variants | knowledge/explorer stylesheets |

**Fix:** darken the text tokens used inside these pills (or the pill text
color) so small (< 18.66px / < 14px bold) text reaches ≥ 4.5:1 on the tinted
background. The `/knowledge/` page alone accounts for 57 contrast hits
because its strategy table repeats these pills per row.

### 3. Form controls with no accessible name — WCAG 4.1.2 / 1.3.1 (A)

57 + 36 issues: `<select>`, `<input type="number|text|date|search">`, and
`<textarea>` elements that have neither an associated `<label for>`, an
`aria-label`, nor an `aria-labelledby`. Screen readers announce these as an
unnamed control. Representative locations:

| Page(s) | Controls |
|---|---|
| `/dssat/config/`, `/dssat/config/system/` | irrigation/harvest `data-*` selects & number inputs (16 each) |
| `/data/map/`, `/data/data-availability/` | month/year selects (`.mc-month`, `.mc-year`), query selects (`#query-variable`, `#area-aggregation`, …) |
| `/dssat/soils/` | filter inputs `#filter-search`, `#filter-source`, `#filter-country` |
| `/dssat/soils/create/`, `/dssat/soils/create-from-texture/` | `depth`, `clay_pct`, `silt_pct`, `organic_carbon` number inputs |
| `/dssat/crops/` | `#crop-search` |
| `/dssat/fields/`, `/dssat/treatments/` | `#fields-q`, `#treatments-q`, `#treatments-crop` |
| `/knowledge/` | `#active-strategy-select` |
| `/knowledge/graphrag/`, `/knowledge/raptor/` | `#graph-max-edges`, `#raptor-max-depth` |

**Fix:** add an `aria-label` (or a visible `<label for>`) to each control.
Placeholder text does **not** satisfy this criterion.

### 4. Use of color — links in body text — WCAG 1.4.1 (A)

3 issues: links that are distinguished from surrounding text by color alone
(no underline / non-color cue).

| Page | Link |
|---|---|
| `/accounts/register/` | "Sign in" link inside a sentence |
| `/data/map/`, `/data/data-availability/` | Leaflet attribution "Leaflet" link (third-party control) |

**Fix (ours):** add `text-decoration: underline` (or another non-color
indicator) to inline body links such as the register-page "Sign in" link.
The Leaflet attribution link is injected by the third-party map library.

### 5. Bypass blocks — WCAG 2.4.1 (A)

1 issue on `/data/map/`: the page has no skip link / landmark structure for
bypassing the repeated nav. (Other pages pass because their content sits in
detectable landmarks.)

**Fix:** ensure a "skip to main content" link and/or a `<main>` landmark is
present on the map page shell.

### 6. Keyboard access to scrollable region — WCAG 2.1.1 (A)

1 issue on `/data/map/`: `#query-results-content` is scrollable but not
keyboard-focusable, so a keyboard user cannot scroll it.

**Fix:** add `tabindex="0"` (and an accessible name / `role`) to the
scrollable results container.

---

## Notes / limitations

- Ollama (LLM inference) is **not** required for this audit — pa11y evaluates
  rendered HTML; the LLM health check fails gracefully at startup.
- Automated tools catch roughly 30–40% of WCAG issues. A full Section 508
  conformance claim also requires manual keyboard-only and screen-reader
  testing, which is out of scope for this automated pass.
- Raw machine-readable results are saved at `results_before.json` (per-page
  issue list from both runners).
