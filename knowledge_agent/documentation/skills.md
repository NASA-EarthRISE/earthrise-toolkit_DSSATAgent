# A2A skills

Every skill exposed at `/knowledge_agent/` via JSON-RPC 2.0. Ids
come from `knowledge_agent.a2a.skills` (the `SkillTable`);
implementations delegate to `knowledge_agent.services`. Each
skill is also directly callable from Python — see
[`api_reference.md`](api_reference.md).

## Agent card

- **Name:** `KnowledgeAgent`
- **Version:** `2.0.0`
- **URL contract:** `/knowledge_agent/`
- **Card:** `GET /knowledge_agent/.well-known/agent-card.json`
- **RPC:** `POST /knowledge_agent/`

## Skill dispatch envelope

Every RPC call looks like this:

```json
POST /knowledge_agent/
{
  "jsonrpc": "2.0",
  "id": "t1",
  "method": "message/send",
  "params": {"message": {"parts": [
    {"type": "data", "data": {"skill": "<skill_id>", "params": {...}}}
  ]}}
}
```

Framing, error codes, and NaN sanitization are handled by the
framework (see
[`../../earthrise_agents_base/documentation/api_reference.md`](../../earthrise_agents_base/documentation/api_reference.md)).

## Tenant resolution

None of these skills take a `tenant_id` param today — every A2A
call is resolved against `settings.KNOWLEDGE_AGENT['default_tenant_id']`.
This matches the current single-tenant deployment model. To
support multi-tenant A2A, add `tenant_id` to the skill params and
forward it into `services.retrieve(..., tenant_id=...)`.

---

## `retrieve_knowledge` — Retrieve Knowledge

Search the indexed corpus using a configurable retrieval strategy.
Backed by `knowledge_agent.services.retrieve`.

**Input params:**

| Name | Required | Type | Default | Notes |
|---|---|---|---|---|
| `query` | yes | str | — | The user question. |
| `strategy` | no | str | `"hybrid"` | One of the 12 registered strategies. See [`strategies.md`](strategies.md). |
| `top_k` | no | int | `5` | Number of ranked hits to return. |
| `filters` | no | dict | `null` | Chroma-style filter grammar. See [`filters.md`](filters.md). |

**Output:**

```json
{
  "results": [
    {
      "document_id": "...",
      "chunk_id": "...",
      "content": "...",
      "score": 0.823,
      "metadata": {"...": "..."}
    }
  ],
  "strategy": "hybrid",
  "count": 5
}
```

Errors: `{"error": "<message>"}`.

**Example call:**

```python
from knowledge_agent.services import retrieve
retrieve(
    query="What are the input requirements for the CERES-Maize model?",
    strategy="hybrid",
    top_k=5,
)
```

---

## `list_strategies` — List Strategies

List available retrieval strategies with per-tenant readiness
status. Backed by `knowledge_agent.services.list_strategies`.

**Input params:** *(none)*

**Output:**

```json
{
  "strategies": [
    {
      "name": "basic_vector",
      "description": "Cosine-distance search over chunk embeddings.",
      "ready": true,
      "required_tables": ["knowledge_chunks"]
    },
    {
      "name": "graphrag",
      "description": "Graph-augmented retrieval over entity + relationship tables.",
      "ready": false,
      "required_tables": ["knowledge_entities", "knowledge_relationships"]
    }
  ]
}
```

`ready` reflects whether the strategy's required ingestion outputs
exist for the resolved tenant.

**Example call:**

```python
from knowledge_agent.services import list_strategies
list_strategies()
```

---

## `check_readiness` — Check Readiness

Report row counts across all knowledge tables so operators can
verify what's been ingested. Backed by
`knowledge_agent.services.check_readiness`.

**Input params:** *(none)*

**Output:**

```json
{
  "table_counts": {
    "knowledge_chunks": 1544,
    "knowledge_chunks_contextual": 1544,
    "knowledge_parent_chunks": 128,
    "knowledge_child_chunks": 1544,
    "knowledge_entities": 872,
    "knowledge_relationships": 1401,
    "knowledge_raptor_nodes": 61,
    "knowledge_ontology": 1
  }
}
```

**Example call:**

```python
from knowledge_agent.services import check_readiness
check_readiness()
```

---

## Related docs

- [`api_reference.md`](api_reference.md) — full Python API surface
  (including `retrieve_batch`, `get_active_strategy`,
  `set_active_strategy`, and the `registry` module).
- [`strategies.md`](strategies.md) — the 12 retrieval strategies.
- [`filters.md`](filters.md) — the `filters` grammar accepted by
  `retrieve_knowledge`.
- [`../../earthrise_agents_base/documentation/api_reference.md`](../../earthrise_agents_base/documentation/api_reference.md)
  — framework primitives (`SkillTable`, `register_agent`,
  `build_urlpatterns`) that produce this A2A surface.
