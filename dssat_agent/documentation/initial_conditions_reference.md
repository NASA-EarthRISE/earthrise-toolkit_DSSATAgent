# Initial Conditions (IC) parameter reference

Human-readable glossary of the DSSAT FileX `*INITIAL CONDITIONS` parameters the
wizard collects and the pipeline writes. Developer reference — the agent-facing
descriptions live in the skills; this is for people reading the code.

| Param | Meaning | Units / notes |
|-------|---------|---------------|
| `ICDAT` | Initial conditions date | `YYDDD` encoded date, DSSAT `"%y%j"` (e.g. `20061` = year 2020, day-of-year 061) |
| `ICRT`  | Initial root weight | kg/ha |
| `ICND`  | Initial nodule weight | kg/ha (`-99` = not applicable/missing) |
| `ICRN`  | Rhizobia number | for legumes |
| `ICRE`  | Rhizobia effectiveness | — |
| `ICWD`  | Initial water-table depth | cm (`-99` = not present) |
| `ICRES` | Initial crop-residue weight | kg/ha |
| `ICREN` | Residue nitrogen concentration | % |
| `ICREP` | Residue phosphorus concentration | % |
| `ICRIP` | Residue incorporation percentage | % |
| `ICRID` | Residue incorporation depth | cm |
| `ICNAME`| Name/label for this IC set | — |
| `SH2O`  | Initial soil-water content | cm³/cm³ (volumetric) |
| `SNH4`  | Initial ammonium concentration | mg N / kg soil |
| `SNO3`  | Initial nitrate concentration | mg N / kg soil |
| `ICBL`  | Soil-layer base depth | cm |

`SH2O`, `SNH4`, `SNO3`, and `ICBL` are **per soil layer** (one row per layer);
the remaining parameters are per IC set.

See also [`evaluations/dssat_45_vs_48_parity.md`](../../evaluations/dssat_45_vs_48_parity.md)
for how IC values affect output parity.
