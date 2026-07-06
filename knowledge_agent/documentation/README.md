# knowledge_agent — developer documentation

Tenant-scoped RAG (retrieval-augmented generation) over an indexed
document corpus. Ships 12 retrieval strategies, three registration
paths, a pluggable backend layer, and Chroma-compatible filter operators.

## Quick links

| Topic | File |
|---|---|
| Top-level concepts and component map | [`architecture.md`](architecture.md) |
| Registering tenants + sources (settings, YAML, programmatic) | [`registration.md`](registration.md) |
| Running ingestion and inspecting state | [`ingestion.md`](ingestion.md) |
| Available retrieval strategies | [`strategies.md`](strategies.md) |
| Embedding + LLM backend interfaces | [`backends.md`](backends.md) |
| Chroma-style filter operator grammar | [`filters.md`](filters.md) |
| How domain config drives LLM prompts | [`prompts.md`](prompts.md) |
| Auto-generated SPARQL templates | [`sparql_templates.md`](sparql_templates.md) |
| Two-tier error formatting | [`errors.md`](errors.md) |
| Public Python API | [`api_reference.md`](api_reference.md) |
| Prioritized backlog of quality/robustness gaps | [`documentation/DEVELOPMENT_ROADMAP.md`](../../documentation/DEVELOPMENT_ROADMAP.md) |

## Naming convention

- `documentation/` (this directory) — developer-facing docs about the
  agent itself. **Don't put ingested content here.**
- `documents/` (`dssat_chat_agent/data/documents/`) — actual files the
  knowledge agent indexes. Per-tenant subdirectory layout. Owned by
  the product shell (the only integration point with knowledge_agent).

## Minimum deployment requirements

- PostgreSQL 12+ with the `vector` and `pg_trgm` extensions
- Django 5+
- Ollama daemon reachable at `KNOWLEDGE_AGENT['ollama']['base_url']`
  (default `http://localhost:11434`)
- A configured tenant — see [`registration.md`](registration.md)

## Bootstrapping a fresh deployment

```bash
python manage.py migrate knowledge_agent
python manage.py check_knowledge                       # confirm tenant exists
python manage.py ingest_knowledge --tenant=<tenant_id> # populate the corpus
```

That's the whole flow.

### Management commands

| Command | Purpose |
|---|---|
| `ingest_knowledge` | Primary Celery-backed ingestion driver (per-tenant/all-tenants, per-step). See [`ingestion.md`](ingestion.md). |
| `check_knowledge` | Report tenant config, table row counts, and readiness. |
| `seed_knowledge_config` | Seed the system-level `KnowledgeConfig` defaults (e.g. `default_strategy=hybrid`). |
| `ingest_documents` | Legacy `IngestionPipeline` driver with per-phase flags (`--all`, `--parse`, `--embed`, `--graph`, …). Run via `manage.py`, not as a standalone script. |
