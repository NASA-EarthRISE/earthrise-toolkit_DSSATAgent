# Python API reference

The public Python API lives in `knowledge_agent.services` and
`knowledge_agent.registry`. Everything else is internal and may
change between versions.

## `services` — retrieval

### `retrieve(query, strategy='hybrid', top_k=5, filters=None, *, tenant_id=None) -> dict`

Single-query retrieval. Returns:

```python
{
    "results": [{...}, {...}, ...],   # ordered hits
    "strategy": "hybrid",
    "count": 5,
}
```

On failure: `{"error": "<message>"}`.

### `retrieve_batch(queries, strategy='hybrid', top_k=5, filters=None, *, tenant_id=None) -> dict`

Multi-query retrieval. Returns:

```python
{
    "strategy": "hybrid",
    "queries": ["q1", "q2", "q3"],
    "count": 9,                # total hits across all queries
    "results": [[...], [...], [...]],   # parallel to queries
}
```

For Tier-1 strategies the embedding service is called once for all
queries; for Tier-2 a `ThreadPoolExecutor` (sized via
`KNOWLEDGE_AGENT['batch_max_workers']`) parallelizes the per-query LLM
calls. See [`strategies.md`](strategies.md) for the tier list.

### `list_strategies(*, tenant_id=None) -> dict`

```python
{
    "strategies": [
        {"name": "basic_vector", "description": "...", "ready": True,
         "required_tables": ["knowledge_chunks"]},
        ...
    ],
}
```

`ready` is per-tenant — depends on whether the strategy's required
ingestion outputs exist for that tenant.

### `check_readiness(*, tenant_id=None) -> dict`

```python
{
    "table_counts": {
        "knowledge_chunks": 1544,
        "knowledge_entities": 0,
        ...
    }
}
```

### `get_active_strategy(user=None, *, tenant_id=None) -> str`

Returns the resolved strategy name for `(tenant, user)`. Resolution
chain: user pref → tenant default → `'hybrid'`.

### `set_active_strategy(strategy, *, tenant_id=None) -> dict`

Updates `KnowledgeTenant.default_strategy`.

## `registry` — tenants + sources

### `register_tenant(tenant_id, *, display_name='', default_strategy='hybrid', domain=None) -> None`

Idempotent upsert. Computes per-pipeline hashes and sets dirty flags if
the domain config changed. Raises `pydantic.ValidationError` on bad
input.

### `register_source(tenant_id, path, *, label='', category='', agent_label='default') -> None`

Idempotent upsert. Raises `ValueError` if `tenant_id` is not
registered. Raises on `path` not being an existing directory.

### `load_registered_config() -> None`

Loads tenant + source definitions from
`settings.KNOWLEDGE_AGENT['tenants']` and the optional
`['config_file']`. Called automatically from
`KnowledgeAgentConfig.ready()` — you rarely call this directly.

### `compute_domain_hashes(domain) -> dict`

Returns `{raptor_config_hash, graphrag_config_hash, ontology_config_hash}`
for change detection. Used internally by `register_tenant`.

## `backends` — embedding + LLM access

### `get_embedding_backend() -> EmbeddingBackend`

Returns the module-wide singleton. First call constructs based on
`settings.KNOWLEDGE_AGENT['embedding_backend']`.

### `get_llm_backend() -> LLMBackend`

Same pattern for LLM.

### `validate_backends() -> None`

Construct + health-check both backends. Hard-fail on missing config,
soft-warn on health check failure. Called from `AppConfig.ready()`.

### `reset_backends() -> None`

Drops the cached singletons. For tests and reconfiguration.

## `store` — direct access (advanced)

`VectorStore` is the ORM facade used by every strategy. It's exposed
for callers that need lower-level operations not covered by `services`
(e.g. direct chunk insert from a non-standard ingestion path). Every
method takes an optional `tenant_id` kwarg. See `store.py` docstrings
for the full list.
