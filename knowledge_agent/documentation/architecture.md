# Architecture

## Layer diagram

```
                ┌─────────────────────────────────────────┐
                │  Consumer (chat orchestrator, REST API) │
                └────────────────────┬────────────────────┘
                                     │
                       services.retrieve / retrieve_batch
                                     │
                ┌────────────────────▼─────────────────────┐
                │  RetrievalStrategy   (12 implementations) │
                │  basic_vector, bm25, hybrid, hyde, crag,  │
                │  rag_fusion, contextual, parent_child,    │
                │  raptor, graphrag, ontology, adaptive     │
                └─────┬──────────┬────────────┬─────────────┘
                      │          │            │
                      │     get_llm_backend  get_embedding_backend
                      │          │            │
                      │          ▼            ▼
                      │     LLMBackend    EmbeddingBackend
                      │     (Ollama)      (Ollama)
                      ▼
              ┌──────────────────────────────────────────┐
              │  VectorStore  (ORM facade)               │
              │  for_tenant(tid) → CosineDistance        │
              │  + compile_filter for $eq/$in/$or/...    │
              └────────────────────┬─────────────────────┘
                                   │
                          Django ORM querysets
                                   │
       ┌───────────────────────────▼─────────────────────────────┐
       │  Postgres (knowledge schema)                            │
       │  KnowledgeChunk, KnowledgeChunkContextual,              │
       │  KnowledgeParentChunk, KnowledgeChildChunk,             │
       │  KnowledgeEntity, KnowledgeRelationship,                │
       │  KnowledgeRaptorNode, KnowledgeOntology                 │
       │                                                          │
       │  All carry tenant FK → KnowledgeTenant                  │
       └─────────────────────────────────────────────────────────┘
```

## Three data-model layers

| Layer | Purpose | Tables |
|---|---|---|
| **Config** | Per-tenant domain vocabulary, default strategy, dirty-flag hashes | `KnowledgeTenant`, `KnowledgeSource`, `KnowledgeIngestionRun`, `KnowledgeConfig` (user prefs) |
| **Vectors + content** | Embeddings, full-text indexes, retrievable chunks | `KnowledgeChunk`, `KnowledgeChunkContextual`, `KnowledgeParentChunk`, `KnowledgeChildChunk` |
| **Structured derivatives** | Built during ingestion from the chunk corpus | `KnowledgeEntity`, `KnowledgeRelationship`, `KnowledgeRaptorNode`, `KnowledgeOntology`, `KnowledgeIngestionPartial` (per-run scratch rows used to assemble the ontology) |

Every row in every table carries `tenant_id` so queries pre-filter via
`Model.objects.for_tenant(tenant)` and only need to scan one tenant's
subset.

## PostgreSQL schema & regenerating migrations (gotcha)

All tables above except `KnowledgeConfig` live in a dedicated **`knowledge`
PostgreSQL schema**, not `public`. The schema is encoded into each model's
`Meta.db_table` by the `_schema_table()` helper (a double-quote trick that
emits `knowledge."<table>"`), so schema isolation is part of the models
themselves.

**The `knowledge` schema (and the `vector` / `pg_trgm` extensions) is created by
a migration's hand-authored `migrations.RunSQL("CREATE SCHEMA IF NOT EXISTS
knowledge; …")`, NOT by `makemigrations`.** `makemigrations` only emits
model-derived operations (`CreateModel`, `AddField`, indexes, …) — it will never
regenerate the `CREATE SCHEMA`/`CREATE EXTENSION` SQL.

> ⚠️ **If you ever delete and regenerate this app's migrations**, the fresh
> `0001_initial` will contain `CreateModel`s that target `knowledge."<table>"`
> but nothing that creates the schema, so `migrate` fails on a clean database
> with `schema "knowledge" does not exist`. You must **hand-add**, as the FIRST
> operation of the regenerated `0001_initial`:
>
> ```python
> migrations.RunSQL(
>     "CREATE SCHEMA IF NOT EXISTS knowledge; "
>     "CREATE EXTENSION IF NOT EXISTS vector; "
>     "CREATE EXTENSION IF NOT EXISTS pg_trgm;",
>     reverse_sql=migrations.RunSQL.noop,
> )
> ```
>
> Keep this in a migration (not only in the dev `postgres/init-extensions.sh`,
> which runs only for local Docker Compose) so schema creation stays portable to
> production/K8s, where migrations are the setup mechanism.

## Three integration entry points

| Entry point | When to use |
|---|---|
| `services.retrieve(query, ...)` | Direct Python call from a sister app (chat orchestrator) |
| `services.retrieve_batch(queries, ...)` | Multiple queries in one call (parallel embedding + per-query search) |
| `POST /knowledge_agent/` (JSON-RPC 2.0) | Remote A2A integration |

The chat orchestrator goes through `services` directly. Out-of-process
clients use the HTTP A2A endpoint.

## Why pre-filter vector search

For each retrieval call, the store does:

```python
KnowledgeChunk.objects.for_tenant(tid)         # tenant predicate
                       .filter(<user filters>)  # metadata predicates
                       .annotate(distance=CosineDistance("embedding", v))
                       .order_by("distance")[:top_k]
```

The `tenant_id` and metadata predicates are evaluated before the vector
distance, so for selective filters Postgres does an exact KNN scan over
the filtered subset — fast for small/medium tenants and correct without
HNSW over-fetch.

For very large single-tenant corpora, pgvector ≥ 0.7's iterative-scan
HNSW handles the same query plan with the index. No code change needed
on the consumer side.

## Backend abstraction

Strategies don't import Ollama directly. They call
`get_llm_backend()` and `get_embedding_backend()` (from
`knowledge_agent.backends`) which return module-wide singletons resolved
via a switch-case from `settings.KNOWLEDGE_AGENT['llm_backend']` /
`['embedding_backend']`. v1 ships one backend implementation each
(`OllamaLLMBackend`, `OllamaEmbeddingBackend`).

Adding a new backend = (a) implement the abstract class in
`knowledge_agent/backends/<name>.py`, (b) add a case to the switch in
`__init__.py`. No dynamic class loading from strings.
