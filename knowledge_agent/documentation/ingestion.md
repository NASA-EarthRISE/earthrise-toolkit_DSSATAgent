# Ingestion

## Trigger

Ingestion is always explicit — no `run_ingestion` flag on sources, no
auto-scheduling. Run the management command:

```bash
# Smart-run for one tenant. Parallelizes via Celery by default.
python manage.py ingest_knowledge --tenant=dssat

# Force-run every pipeline regardless of dirty flags.
python manage.py ingest_knowledge --tenant=dssat --force

# Limit to a single source row.
python manage.py ingest_knowledge --tenant=dssat --source=42

# Run only specific pipelines.
python manage.py ingest_knowledge --tenant=dssat \
    --steps=chunk_and_embed,build_raptor

# All tenants in one shot.
python manage.py ingest_knowledge --all-tenants

# Inline / synchronous execution — same task code, no broker.
python manage.py ingest_knowledge --tenant=dssat --sync

# Dispatch and return immediately. Check progress with check_knowledge.
python manage.py ingest_knowledge --tenant=dssat --no-wait
```

## Parallel pipeline DAG

PR 2.5 orchestrates ingestion as a Celery canvas: per-PDF tasks for the
chunking phase, then 4-way fan-out for the downstream LLM-heavy
pipelines, each of which is itself a per-chunk chord.

```
Phase 1: per-PDF group
  ┌─ ingest_pdf_task × N
  ├─ build_parent_child_pdf_task × N
  └─ (chord callback: start_phase_2)
                  ▼
Phase 2: 4-way fan-out
  ┌─ chord(build_contextual_chunk_task × C)(noop)
  ├─ chord(build_graph_chunk_task × C)(noop)
  ├─ chord(extract_ontology_triples_task × C)(assemble_ontology)
  └─ chord(build_raptor_leaf_task × C)(build_raptor_level_task → recursive)
                  ▼
finalize_ingestion_run
  - mark KnowledgeIngestionRun.status = completed
  - clear raptor_dirty / graphrag_dirty / ontology_dirty
  - stamp KnowledgeSource.last_ingested_at on every tenant source
```

## Available pipeline steps

| Step | Output table(s) | Re-run trigger |
|---|---|---|
| `chunk_and_embed` | `KnowledgeChunk` | Every run (idempotent on chunk_id) |
| `build_parent_child` | `KnowledgeParentChunk` + `KnowledgeChildChunk` | Every run |
| `build_contextual` | `KnowledgeChunkContextual` | Every run |
| `build_graph` | `KnowledgeEntity` + `KnowledgeRelationship` | `graphrag_dirty` flag or empty entity table |
| `build_raptor` | `KnowledgeRaptorNode` | `raptor_dirty` flag or empty raptor table |
| `build_ontology` | `KnowledgeOntology` | `ontology_dirty` flag or empty ontology table |

## OWL ontology strategy — future improvements

The ontology is stored as a **single serialized Turtle document** per tenant
(`KnowledgeOntology.turtle_data`, a Postgres `text` column) and is rebuilt in
full on every ingest. `insert_ontology` (`store.py`) now atomically deletes the
prior version before writing the new one, so exactly one row per tenant is
retained. Two known scalability limits remain — fine at the current
sample size (~60 chunks → ~95 KB), worth revisiting before the full corpus:

1. **Runtime-efficient storage instead of a single Turtle blob.** The whole
   document is parsed into an `rdflib.Graph` in memory on every read, and
   Postgres TOASTs the value once it grows past ~2 KB (hard ceiling ~1 GB per
   `text` field). Read latency and memory scale with total document size.
   Candidate replacements, in order of fit:
   - a **normalized per-triple table** in Postgres — `(tenant, subject,
     predicate, object, is_literal, source_chunk_id)` indexed on
     `(tenant, subject)` / `(tenant, predicate)`; enables indexed SQL lookups
     with no blob parse (reuses existing infra; loses native SPARQL/reasoning);
   - an **embedded triplestore** — [Oxigraph](https://github.com/oxigraph/oxigraph)
     (`pyoxigraph`, MIT/Apache-2.0, in-process) for full SPARQL 1.1 without
     loading the graph into RAM;
   - **[owlready2](https://owlready2.readthedocs.io)** (LGPL-3.0, SQLite-backed
     quadstore) only if formal OWL reasoning/inference is required;
   - **HDT** (compressed binary RDF with random access) if a single-artifact
     blob is preferred but read cost must drop.

2. **Incremental assembly instead of full rebuild.** `assemble_ontology_task`
   re-reads every chunk's triples and re-serializes the entire graph on each
   ingest — O(all triples) per run. A per-triple / named-graph-per-chunk store
   (see #1) would allow updating only the changed chunks' triples — O(changed).

## Concurrency model

| Layer | Knob | Default |
|---|---|---|
| Per Celery worker | `CELERY_WORKER_CONCURRENCY` (in `chat_agent/settings.py`) | 4 |
| Celery worker count | Docker compose `celery` service replicas | 1 |
| Ollama parallel generates | `OLLAMA_NUM_PARALLEL` env var on the Ollama daemon | depends on Ollama version |
| Phase-1 PDFs in flight | `len(sources) × MAX_DOCS_PER_CATEGORY` (dispatched as a single group) | unbounded |
| Phase-2 per-chunk tasks in flight | `4 × chunk_count` (4 branches × C chunks each) | unbounded |

**The actual concurrency ceiling is** `workers × CELERY_WORKER_CONCURRENCY` (i.e. how many in-flight Celery tasks the worker pool can execute), **capped by** `OLLAMA_NUM_PARALLEL` (since most tasks are LLM/embed calls and Ollama queues anything beyond its parallel limit server-side).

For a single-worker, concurrency=4 setup: at most 4 simultaneous LLM/embed calls regardless of how many tasks the canvas dispatched. Increase worker concurrency or scale workers horizontally to go faster — Ollama must keep up.

## Concurrent-write safety

Every Postgres write touched by per-chunk tasks goes through atomic
upsert helpers in `store.py`. Specifically:

- `store.insert_entity` — `transaction.atomic + get_or_create` against the
  `(tenant, name, entity_type)` unique constraint. Concurrent workers
  inserting the same entity name race cleanly; the loser catches
  `IntegrityError` (handled internally by Django) and gets the winner's row.
- `store.insert_relationship` — same pattern against the
  `(tenant, source_entity, target_entity, relationship, source_chunk_id)`
  unique constraint (`uq_rel_tenant_endpoints_rel_chunk`, defined in
  `migrations/0001_initial.py`).
- `store.insert_chunks_bulk` — `bulk_create(ignore_conflicts=True)`.
- `store.insert_contextual_chunk`, `insert_raptor_node`,
  `insert_parent_chunk`, `insert_child_chunk` — fresh UUIDs, no
  natural-key conflict.
- `store.insert_ontology` — `transaction.atomic` delete-then-create; the
  tenant's prior ontology row(s) are removed and the new one written in one
  transaction, so a reader never sees zero rows and stale versions never
  accumulate.

No in-memory caches are shared across workers (the pre-PR-2.5 GraphRAG
`_entity_cache` was dropped — DB-side dedup is the only consistent
mechanism in a distributed-worker setup).

## Inspecting state

```bash
python manage.py check_knowledge               # all tenants
python manage.py check_knowledge --tenant=dssat # one tenant
```

Shows per tenant:
- Dirty flags (and which command would clear them)
- Row counts per data table
- Registered sources + `last_ingested_at`
- Last 3 ingestion runs with status and any error message

## Ingestion run history

Every invocation creates one `KnowledgeIngestionRun` row:

- `tenant`, `source` (null if multi-source)
- `status`: `pending` → `running` → `completed` / `failed`
- `started_at`, `completed_at`
- `chunks_created`: total chunks for the tenant at completion
- `pipelines_run`: list of step names included in this run
- `error_message`: full traceback on failure

```python
from knowledge_agent.models import KnowledgeIngestionRun
KnowledgeIngestionRun.objects.filter(tenant_id="dssat").order_by("-created_at")[:10]
```

## Troubleshooting

### "task_id dispatched but nothing happens"

Celery worker isn't running for the `knowledge_agent` queue. Check:
```bash
docker compose ps celery
docker compose logs celery --tail=50
```

You should see workers listening on `-Q chat_agent,data_agent,dssat_agent,knowledge_agent`.

### "ingestion is slower than expected"

Almost always Ollama queueing. Inspect:
```bash
curl http://host.docker.internal:11434/api/ps   # active generate sessions
```

If you see fewer concurrent sessions than `CELERY_WORKER_CONCURRENCY`,
your Ollama deployment is the bottleneck. Increase `OLLAMA_NUM_PARALLEL`
or run Ollama on a faster machine.

### "I want to debug a per-chunk task without Celery in the loop"

Use `--sync`. The exact same task code runs in-process synchronously,
so you can put `pdb` breakpoints inside any `@shared_task` function
and they'll fire.

### Backward-compat: the old `run_ingestion_task`

Removed in PR 2.5. The pre-PR-2.5 `run_ingestion_task` /
`run_single_step_task` are no longer in `tasks.py`. Any external code
(e.g. an explorer view button that dispatched ingestion via
`run_ingestion_task.apply_async`) now needs to dispatch
`ingest_tenant_task.apply_async(args=(tenant_id, ...))` instead.
