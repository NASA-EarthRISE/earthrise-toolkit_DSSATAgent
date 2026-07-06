# DSSAT Output & Parity — how simulation runs were validated

This note documents how the app's generated DSSAT runs were checked against a
reference stakeholder bundle, and captures the durable facts needed to read and
compare DSSAT output. It is an *evaluation* record (how we tested), not a
how-to for the agents.

Reference comparison: a stakeholder DSSAT **4.5** experiment (`NSNS0000.MZX`,
full SSURGO soil library, cultivar `IB0322`) vs. our pipeline's generated
**4.8** run (`CHAT2401.MZX`, minimal per-run files, default placeholder
cultivar). The two are **not apples-to-apples** — see caveats below.

## 1. Minimal-scope files vs. full libraries

DSSATTools (and therefore our pipeline) writes **per-run, minimal-scope** input
files: a single-profile `SOIL.SOL`, a single-cultivar `.CUL`, and a per-run
`.WTH`, rather than shipping the full multi-thousand-profile soil library and
complete `MZCER045.CUL`. At execution time the two approaches are **equivalent**
— DSSAT only reads the profile/cultivar the experiment references. A reference
bundle looking "richer" (full libraries, institution/location metadata in
`*EXP.DETAILS`) does not mean our run is missing anything functionally.

Sections we emit into the FileX are **conditional on wizard input**: `*INITIAL
CONDITIONS`, `*FERTILIZERS`, and `*IRRIGATION` appear when the wizard collects
them (initial-conditions step, fertilizer/irrigation events) and are omitted for
a bare run.

## 2. Simulation-control options — what each changes

The `*SIMULATION CONTROLS` block toggles process modules. The values our
pipeline sets are DSSATTools/4.8 defaults and are **not wrong**, but they differ
from a 4.5 stakeholder file. Key switches: `WATER`, `NITRO`, `SYMBI` (N
fixation), `TILL`, `CO2`, `INFIL`, `PHOTO` (photosynthesis method), `MESOM`,
`MESEV`, `MESOL`, and the management switches `IRRIG`, `FERTI`, `RESID`, `HARVS`
— the last four should mirror the FileX events (they are auto-derived to `R`
when the run supplies scheduled events). When comparing two runs, diff this
block first: most output differences trace back to a control toggle, not a bug.

## 3. Where simulation-control values come from (two-layer model)

`_build_simulation_controls()` merges **two** layers, wizard-wins:

1. **System Config defaults** — installation-level `DSSATConfig` rows
   (`user=NULL`), read via `get_config('sim_water', 'Y')`. Seeded by
   `manage.py seed_dssat_config`; editable by admins in the UI.
2. **Wizard per-experiment overrides** — `Treatment.simulation_controls` set
   during the wizard.

Merge rule: `opts.get('water', get_config('sim_water', 'Y'))` — a per-experiment
override wins; otherwise the system default applies; only then the hardcoded
fallback. So changing a default in System Config affects all future runs that
don't override it.

## 4. Version & cultivar caveats (why yields differ)

- **DSSAT version.** The reference used 4.5.1 (filenames suffixed `045`); we run
  4.8.2 (suffixed `048`). Results across major versions **cannot be expected to
  match** — this is expected, not a defect.
- **Placeholder cultivar (footgun).** DSSATTools' default cultivar `999991`
  ("MINIMA") is a deliberately "tiny" maize variety used as a known-good
  fallback when no cultivar is chosen (`P1=5, G2=248, G3=5`). It yields
  order-of-magnitude lower grain than a real cultivar (e.g. `IB0322`,
  `P1≈277, P5≈674`). **Do not compare MINIMA runs against real-world yields** —
  select a real cultivar first.

## 5. Why our runs emit ~25 output files (not ~7)

Our generated FileX row carries a 14th column `FMOPT=A` (DSSAT 4.8 "format
option = ASCII (all)"), which dumps every internal balance/process file DSSAT
can produce (`ET.OUT`, `SoilWat.OUT`, etc.). A 4.5 reference (13-column row,
ending `OPOUT=Y`) folds much of that data into `Overview.OUT` / `PlantGro.OUT`
instead of emitting separate files. The larger file count is a formatting
choice, not extra computation.

---
*Source: consolidated from validation notes taken during development
(`NSNS0000` reference bundle). The historical "action items" list from those
notes has been omitted — those fixes (initial-conditions wizard step,
auto-derived management modes, removal of MINIMA as a service default) have
since landed.*
