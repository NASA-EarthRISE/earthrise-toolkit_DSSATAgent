# Retrieval strategies

12 strategies registered by name in `STRATEGY_REGISTRY`. Choose by
passing `strategy="<name>"` to `services.retrieve()` or
`services.retrieve_batch()`.

## Strategy reference

| Name | Approach | Ingestion deps | Tier | Good for |
|---|---|---|---|---|
| `basic_vector` | Cosine similarity over chunk embeddings | `chunk_and_embed` | 1 | Straightforward factual lookups |
| `bm25` | tsvector full-text rank (no embeddings) | `chunk_and_embed` (the table; not embeddings) | 1 | Keyword-heavy queries, named entities |
| `hybrid` | Vector + BM25 fused via Reciprocal Rank Fusion | `chunk_and_embed` | 1 | General default; mixes semantic + lexical |
| `parent_child` | Vector-search child chunks → return parent chunks | `build_parent_child` | 1 | Need detailed answer with broad context |
| `contextual` | Hybrid search over LLM-context-enriched chunks | `build_contextual` | 1 | Ambiguous terms needing document context |
| `raptor` | Vector search across hierarchical tree (leaves + cluster summaries) | `build_raptor` | 1 | Broad "summarize the corpus on X" |
| `graphrag` | Vector-match entities → graph-traverse relationships → return linked chunks | `build_graph` | 1 | Structural questions about entities |
| `ontology` | LLM-vocabulary-synthesized SPARQL → rdflib query → linked chunks (vector fallback) | `build_ontology` | 1 | Formal structured questions over the OWL ontology |
| `hyde` | LLM writes hypothetical answer → embed answer → vector search | `chunk_and_embed` (+ LLM backend) | 2 | "How does X work?" / conceptual questions |
| `crag` | Hybrid search → LLM grades relevance → rewrite query and re-search if poor | `chunk_and_embed` (+ LLM) | 2 | Self-corrective fallback for poor initial hits |
| `rag_fusion` | LLM generates N query variants → hybrid-search each → fuse via RRF | `chunk_and_embed` (+ LLM) | 2 | Vocabulary-mismatched queries; multi-angle retrieval |
| `adaptive` | LLM classifies the query and routes to one of basic_vector/hybrid/hyde/parent_child/contextual | depends on chosen sub-strategy (+ LLM) | 2 | Default for mixed-shape query workloads |

## Tier-1 vs Tier-2

- **Tier 1**: no per-query LLM call. `retrieve_batch` overrides the base
  class to embed once and search N times — much faster than the threaded
  fallback. Strategies: `basic_vector`, `bm25`, `hybrid`,
  `parent_child`, `contextual`, `raptor`, `graphrag`, `ontology`.
- **Tier 2**: per-query LLM call. `retrieve_batch` uses the base class's
  `ThreadPoolExecutor` (size: `settings.KNOWLEDGE_AGENT['batch_max_workers']`,
  default 4) to parallelize across queries. Strategies: `hyde`, `crag`,
  `rag_fusion`, `adaptive`.

## Tenant-scoped reads, regardless of tier

Every strategy threads `tenant_id` through to the store layer, so
`for_tenant(tid)` runs before any vector / graph / SPARQL work.
Cross-tenant leaks at the strategy layer are impossible by construction.

## When LLM backends are required

`hyde`, `crag`, `rag_fusion`, `adaptive` all require an LLM backend
(see [`backends.md`](backends.md)). If `KNOWLEDGE_AGENT['llm_backend']`
isn't configured, app startup fails with `ImproperlyConfigured`. With a
configured-but-unreachable Ollama, the strategies fail at retrieve time
with a clean error — the two-tier formatter (`errors.md`) shows admins
the underlying detail and users a safe minimal message.
