# Development Roadmap

Consolidated, code-verified backlog for EarthRISEAgents. Every item below was
checked against the current codebase; already-shipped and obsolete items were
dropped, and partially-done items were rescoped to the work that actually
remains.

> **This document supersedes:**
> - `knowledge_agent/documentation/future_improvements.md`
> - `dssat_agent/documentation/ROADMAP_V2.md`
>
> Extraction / repo-split work is **not** duplicated here — it remains owned by
> `documentation/MODULARIZATION_PLAN.md` §4. This roadmap only cross-references it.

Priority tags carried from the source docs: **P0** (correctness/robustness),
**P1**, **P2** (optional/nice-to-have).

---

## Platform (`earthrise_agents_base`)

### Finish the legacy orchestration teardown (partial — cleanup remaining)
The `plan_execute` orchestration mode and the intent-router are already gone:
the `ORCHESTRATION_MODE` / `USE_INTENT_ROUTING` flags, `_plan_node`,
`_execute_node`, `_route_from_orchestrator`, `StructuredExtractor`, and
`classify_intent` no longer exist, and `_build_graph()` is a pure ReAct graph.
What remains is dead scaffolding to delete once the ReAct path is confirmed
stable in production:
- Remove the vestigial `ChatIntent` enum (`earthrise_agents_base/agent/chat_agent.py`)
  — `_intake_node` already hardcodes `ChatIntent.TASK`.
- Drop the orphaned `ExecutionPlan` and `SubagentTask` Django models
  (`earthrise_agents_base/models.py`), with a migration to remove their tables.
- The `plan_tasks` / `plan_results` fields on `ChatAgentState` are **not dead** —
  the ReAct `respond` path still repurposes them: `_synthesize_results` builds a
  `shim_state` populating `plan_tasks`/`plan_results` to compose the final
  narrative (`chat_agent.py` ~L1634, ~L1661, ~L1737). Removing them requires first
  refactoring that synthesis shim; until then, only the stale
  `'plan_execute' mode` comments should be cleaned up.

*Gated on:* operator confidence in the ReAct path across all four sub-agent
flows (DSSAT run + query, Data query + availability, Knowledge search) plus the
wizard interrupt flow.

---

## knowledge_agent

### 1. Layout-aware fallback for mangled manual-style PDFs (P0, partial)
Reading-order column detection (`_extract_text_in_reading_order`) and text
normalization already ship and handle most multi-column layouts. What remains is
a **heavier fallback for the documents that still come out as character-level
garbage** (drop-caps, large-caps headings, tightly-set manuals): add cheap
corruption detection (reuse the junk-signal from item 2) and route only the
detected-bad documents through an OCR / "marker"-style / pdfplumber-layout-mode
path. Today corrupt PDFs are simply caught and skipped, losing that content.
*Affected:* `knowledge_agent/ingestion/pdf_parser.py`; parse stage in
`knowledge_agent/tasks.py` (`ingest_pdf_task` / `build_parent_child_pdf_task`).

### 2. Junk-chunk + front-matter filtering (P0)
No quality gate exists between chunking and `store.insert_chunks_bulk`.
- Add a **junk filter** in the chunking stage that drops/flags chunks dominated
  by repeated characters or dot-leaders, or below a minimum real-word count
  (this doubles as the corruption signal for item 1).
- **Skip or down-weight front-matter** — TOC, copyright/citation, and
  reference-only pages — so a small corpus isn't dominated by title pages.
*Affected:* chunking in `knowledge_agent/ingestion/`; gate applied before
`store.insert_chunks_bulk` in `knowledge_agent/tasks.py`.

### 3. LLM-call timeouts + fail-loud on wholesale failure (P0)
Two robustness gaps, both still open:
- **Client-side request timeout** on the Ollama embedding/LLM backends so a
  wedged daemon fails fast and the task can retry instead of blocking
  indefinitely. Only the `/api/tags` health check is time-bounded today; the
  actual `embed_*` / `invoke` calls are not.
  *Affected:* `knowledge_agent/backends/ollama.py`.
- **Fail loudly on zero results:** if a phase-2 branch produces zero successful
  results from N>0 inputs (e.g. all RAPTOR leaves return `None`), mark the branch
  and the run **failed** — or preserve the dirty flag so the next ingest re-runs
  it — instead of finalizing `status="completed"` with an empty derived table.
  *Affected:* `build_raptor_leaf_task` / `build_raptor_summary_task` /
  `build_raptor_level_task` and `finalize_ingestion_run` in
  `knowledge_agent/tasks.py`.

### 4. RAPTOR summarizer grounding (P1)
The summarizer prompt currently constrains only formatting (no preamble), not
faithfulness, and there is no post-hoc grounding check.
- Tighten `_SUMMARIZE_PROMPT`: "summarize only what is present; do not introduce
  metrics, methods, or claims not in the provided text; prefer omission over
  speculation."
- Optionally route the **summary step only** to a larger / instruction-tuned
  model (low call volume, small cost delta).
- Optionally add a lightweight lexical grounding check (summary claims must have
  support in the children) before persisting.
*Affected:* `_SUMMARIZE_PROMPT` / `_DEFAULT_DOMAIN_HINT` in
`knowledge_agent/ingestion/raptor_builder.py`; `build_raptor_summary_task` in
`knowledge_agent/tasks.py`. The same grounding discipline should extend to
`build_contextual_chunk_task` and ontology triple extraction (see Cross-cutting).

### 5. Surface the corpus sampling limit (P1, partial)
`MAX_DOCS_PER_CATEGORY` is already env-configurable
(`KNOWLEDGE_MAX_DOCS_PER_CATEGORY`, default 0 = unlimited) and enforced in the
scanner and ingest task. The remaining work is to make it **visible and
deliberate**: surface the effective value in `check_knowledge` output and expose
it as an explicit `--max-docs` argument on the `ingest_knowledge` management
command, so a capped run is an obvious choice rather than a silent default.
*Affected:* `knowledge_agent/management/commands/check_knowledge.py` and
`ingest_knowledge.py`.

### 6. Incremental / queryable ontology storage (P1, partial)
The atomic write fix is done (`insert_ontology` deletes prior versions inside a
transaction before writing). Still pending: the ontology is a **single
serialized Turtle blob per tenant**, fully parsed into `rdflib` on every read
and rebuilt from scratch on every ingest — fine at ~95 KB, degrading as the
corpus grows. Move to a store that supports incremental assembly and query
without a full parse (candidate stores: normalized triple table, Oxigraph,
owlready2, HDT).
*Affected:* ontology storage/read path in `knowledge_agent/store.py`;
`assemble_ontology_task` in `knowledge_agent/tasks.py`.

### 7. Render isolated entities in the GraphRAG viz (P2, optional)
`get_graph_full` derives nodes solely from relationship endpoints, so entities
with no relationships are never rendered (correct, but surprising when comparing
against `check_knowledge` entity counts). Optionally add a toggle to also render
isolated entities as disconnected nodes for count parity.
*Affected:* `get_graph_full` in `knowledge_agent/store.py`.

---

## dssat_agent

Both CDE-expansion features below follow the existing V1 seeder / CDE-parser
pattern (`codes_service.py` for DETAIL.CDE, `seed_crop_models` for GRSTAGE.CDE)
and are **additive** — neither should touch the core DSSAT run path. Shared
implementation pattern: (1) parse the CDE via a `codes_service._parse_*`
function, (2) seed via an idempotent, re-runnable management command, (3) expose
via a `/dssat/api/<resource>/` endpoint for the wizard and chat skills,
(4) validate via `validation.py` helpers, (5) persist into experiment inputs via
`_capture_reference_files`.

### A. Economic analysis (ECONOMIC.CDE)
DSSAT ships crop-specific economic parameters (input costs, output prices, labor
rates, uncertainty distributions). The file is vendored but unparsed. Work:
- New `DSSATEconomicCode` model seeded by a `seed_economic_codes` command.
- Per-(crop, region) economic profile on experiments (or a `DSSATConfig` default).
- Batch sensitivity analysis: NPV ranges, breakeven price curves, cost/yield
  scatter charts.
- An "Economics" panel in the experiment explorer (single run) + a batch-level
  economic summary for ensembles.

*Gated on:* whether economic output is a primary deliverable — low priority for
research deployments, central if the app pivots toward extension advisory work.

### B. Pest / disease management (PEST.CDE)
DSSAT ships pest/disease codes (nematode species, earworm, defoliation %,
photosynthesis reduction %, etc.). Vendored but unparsed. Work:
- New `DSSATPestCode` model seeded from PEST.CDE.
- Pest-management event schedule in the experiment wizard (mirrors the existing
  fertilizer/irrigation event tables).
- `PestEvent` handling via a new `_build_pest` in `experiment_service.py` and a
  matching `pest` field on `CropDefault`.
- Pest-aware outputs in the run explorer (pest-pressure timeline, yield-loss
  attribution).

*Gated on:* user demand for pest-scenario simulations (current runs assume a
pest-free baseline).

### C. Unify experiment parameter naming (remove the adapter layer)
The chat tool boundary validates against the Pydantic `ExperimentInput` schema
(LLM-friendly names: `crop`, `planting.date`, `fertilizer[].type`, …) and then
`experiment_to_legacy_params` translates every field into the legacy flat dict
(`crop_code`, `planting_date`, `fertilizer[].fmcd`, …) that
`build_experiment` → `run_experiment` still consume. The adapter localizes the
new vocabulary to the tool boundary while the tool-wrapper contract stabilizes.
Work, when picked up:
1. Promote the Pydantic field names to the canonical vocabulary throughout
   `dssat_agent/services/`.
2. Migrate `CropDefault`, `DSSATConfig`, and JSONField-stored configs to the new
   names (with a key-renaming data migration).
3. Update the wizard payload + explorer API endpoints to accept the new names
   (permissive accept-both validators for one release cycle).
4. Delete `experiment_to_legacy_params` once all consumers use the Pydantic
   schema directly.

*Gated on:* ~4 weeks of stability in the tool-wrapper contract; while new fields
and renames are still landing, the adapter keeps churn away from the downstream
pipeline.
*Entry points:* Pydantic schemas `dssat_agent/schemas/experiment.py` &
`agronomy.py`; adapter `dssat_agent/services/tool_wrapper.py::experiment_to_legacy_params`;
legacy consumer `dssat_agent/services/workflow.py::build_experiment`.

---

## data_agent

No standalone backlog items originate from the source docs. The only pending
`data_agent` work is the set of A2A skills needed to decouple `dssat_agent`
(`sample_raster_at_point`, `is_point_in_coverage`, `get_raster_legend`) — tracked
under Extraction / modularization below, not duplicated here.

---

## Extraction / modularization

Owned in full by `documentation/MODULARIZATION_PLAN.md` §4 (not restated here).
Current state of that plan:
- **§4.1 — Decouple `dssat_agent` from `data_agent`** (the dominant blocker):
  still **pending** — 35 direct import sites bypass the existing A2A abstraction.
- **§4.2 — Make `accounts` a plugin:** **done** (2026-07).
- **§4.3 — Package `earthrise_agents_base` + shared schemas as a versioned
  dependency:** pending (in-repo imports fine until extraction).
- **§4.4 — Formalize the sub-agent → framework envelope seam:** pending.

These are future work and **not blockers for monolith publishing**.

---

## Cross-cutting

### Grounding discipline across all LLM steps
The anti-hallucination tightening in knowledge_agent item 4 is not summarizer-
specific: the same "assert only what's supported by the source" discipline
should be applied to `build_contextual_chunk_task` and to ontology triple
extraction, so no LLM step in the ingest pipeline introduces ungrounded claims.

### Fail-fast + fail-loud as a general pattern
The timeout + zero-result-validation work in knowledge_agent item 3 is a model
for any Celery fan-out/chord pipeline in the project: a "completed" run should
never contain an empty derived table when its inputs were non-empty, and every
external-service client should be time-bounded.

---

## Post-release tech debt (refactor candidates)

Not blockers for publishing; tracked here so they aren't lost. These are large
single-responsibility modules that have accreted enough scope to warrant
splitting, plus mechanical consolidations.

- **God-files to split** (by descending size): `dssat_agent/services/workflow.py`
  (~2430 LOC), `earthrise_agents_base/agent/chat_agent.py` (~2005),
  `dssat_agent/services/wizard.py` (~1368), `dssat_agent/explorer/views_api.py`
  (~1467), `dssat_agent/services/experiment_service.py` (~1226),
  `knowledge_agent/store.py` (~1438), `data_agent/etl/etl_pipeline.py` (~4309).
  Each is a cohesive-but-oversized unit; split along the internal seams already
  present (e.g. ETL: download / merge / load / query handlers).
- **ETL TODOs** (`data_agent/etl/etl_pipeline.py`): granule-merge conflict
  detection (~L975), per-dataset `format_params`/overwrite/coordinate-attribute
  overrides (~L1000/L1118/L1817), dynamic aggregation op (~L1212), partial-file
  cleanup tracking (~L1417), clearer errors on missing encodings/attributes DB
  config (~L1715/L1746). All real robustness work needing ETL domain judgment.
- **Test coverage gaps:** `earthrise_agents_base` (views/tasks untested) and the
  large `dssat_agent/services/` surface beyond the wizard/DAP path.
