"""
Public service API for the knowledge_agent.

Provides clean Python-callable functions that can be imported directly
by other Django apps (e.g. chat orchestrator) without going through A2A HTTP.
"""

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Lazy singletons — initialized on first use
_store = None
_embedding_service = None


def _get_store():
    """Get or create the VectorStore singleton."""
    global _store
    if _store is None:
        from knowledge_agent.store import VectorStore
        _store = VectorStore()
    return _store


def _get_embedding_service():
    """Get or create the EmbeddingService singleton."""
    global _embedding_service
    if _embedding_service is None:
        from knowledge_agent.embeddings import EmbeddingService
        from knowledge_agent.config import (
            EMBEDDING_MODEL, EMBEDDING_OLLAMA_URL, VECTOR_DIM,
        )
        _embedding_service = EmbeddingService(
            model=EMBEDDING_MODEL,
            base_url=EMBEDDING_OLLAMA_URL,
        )
    return _embedding_service


def retrieve(
    query: str,
    strategy: str = "hybrid",
    top_k: int = 5,
    filters: Optional[Dict[str, Any]] = None,
    *,
    tenant_id: Optional[str] = None,
) -> Dict:
    """Retrieve relevant knowledge chunks for one query within a tenant.

    `tenant_id` defaults to `store.DEFAULT_TENANT_ID` ('default'),
    matching single-tenant deployments without callers having to know
    about tenancy.
    """
    from knowledge_agent.strategies import get_strategy, STRATEGY_REGISTRY

    if strategy not in STRATEGY_REGISTRY:
        return {"error": f"Unknown strategy: {strategy}"}

    store = _get_store()
    embedding_service = _get_embedding_service()

    try:
        strat = get_strategy(
            strategy,
            store=store,
            embedding_service=embedding_service,
        )
        results = strat.retrieve(
            query, top_k=top_k, filters=filters, tenant_id=tenant_id,
        )
        return {
            "results": [r.to_dict() for r in results],
            "strategy": strategy,
            "count": len(results),
        }
    except Exception as e:
        logger.error("Retrieval error (strategy=%s): %s", strategy, e)
        return {"error": str(e)}


def retrieve_batch(
    queries: List[str],
    strategy: str = "hybrid",
    top_k: int = 5,
    filters: Optional[Dict[str, Any]] = None,
    *,
    tenant_id: Optional[str] = None,
) -> Dict:
    """Retrieve for N queries in one call, returning parallel result lists.

    Shape mirrors ChromaDB's batch query API: a `results` list whose
    i-th element is the hits for `queries[i]`, plus a `sources_markdown`
    union for deduplication. Tier-1 strategies override `retrieve_batch`
    to embed once and search N times; Tier-2 (HyDE/CRAG/RAG-Fusion/
    adaptive) inherit the base class's ThreadPoolExecutor parallelism.
    """
    from knowledge_agent.strategies import get_strategy, STRATEGY_REGISTRY

    if strategy not in STRATEGY_REGISTRY:
        return {"error": f"Unknown strategy: {strategy}"}

    if not queries:
        return {
            "strategy": strategy,
            "queries": [],
            "count": 0,
            "results": [],
        }

    store = _get_store()
    embedding_service = _get_embedding_service()

    try:
        strat = get_strategy(
            strategy,
            store=store,
            embedding_service=embedding_service,
        )
        per_query_results = strat.retrieve_batch(
            queries, top_k=top_k, filters=filters, tenant_id=tenant_id,
        )
        return {
            "strategy": strategy,
            "queries": queries,
            "count": sum(len(rs) for rs in per_query_results),
            "results": [
                [r.to_dict() for r in rs] for rs in per_query_results
            ],
        }
    except Exception as e:
        logger.error(
            "Batch retrieval error (strategy=%s, n=%d): %s",
            strategy, len(queries), e,
        )
        return {"error": str(e)}


def list_strategies(*, tenant_id: Optional[str] = None) -> Dict:
    """List available retrieval strategies with readiness status.

    Readiness is per-tenant — a strategy whose required tables are
    populated for tenant A may be 'not ready' for tenant B that hasn't
    ingested anything yet.
    """
    from knowledge_agent.strategies import STRATEGY_REGISTRY, STRATEGY_DEPENDENCIES

    store = _get_store()
    row_counts = store.table_row_counts(tenant_id=tenant_id)

    strategies = []
    for name, cls in STRATEGY_REGISTRY.items():
        deps = STRATEGY_DEPENDENCIES.get(name, {})
        required_tables = deps.get("tables", [])
        ready = all(row_counts.get(t, 0) > 0 for t in required_tables)

        strategies.append({
            "name": name,
            "description": cls.__doc__ or name,
            "ready": ready,
            "required_tables": required_tables,
        })

    return {"strategies": strategies}


def check_readiness(*, tenant_id: Optional[str] = None) -> Dict:
    """Check preprocessing status, optionally scoped to one tenant."""
    store = _get_store()
    return {"table_counts": store.table_row_counts(tenant_id=tenant_id)}


def ingest(steps: Optional[List[str]] = None, progress_callback=None) -> Dict:
    """
    Run ingestion pipeline steps.

    Args:
        steps: List of step names to run (default: all).
               Valid: setup_tables, download, parse, chunk_and_embed,
                      build_parent_child, build_contextual, build_graph,
                      build_ontology, build_raptor
        progress_callback: Optional callable(step_name, completed_count, total_count).

    Returns:
        dict with completed steps.
    """
    from knowledge_agent.ingestion.pipeline import IngestionPipeline

    ALL_STEPS = [
        "setup_tables", "download", "parse", "chunk_and_embed",
        "build_parent_child", "build_contextual", "build_graph",
        "build_ontology", "build_raptor",
    ]

    pipeline = IngestionPipeline()
    run_steps = ALL_STEPS if steps is None else steps
    total = len(run_steps)
    completed = []

    for i, step in enumerate(run_steps):
        method = getattr(pipeline, step, None)
        if method and callable(method):
            if progress_callback:
                progress_callback(step, i, total)
            method()
            completed.append(step)
        else:
            logger.warning("Unknown ingestion step: %s", step)

    if progress_callback:
        progress_callback("done", total, total)

    return {"status": "completed", "steps": completed}


# ---------------------------------------------------------------------------
# Document / source queries
# ---------------------------------------------------------------------------

def get_document_sources() -> Dict:
    """Get document sources with their ingestion stats (from DB metadata)."""
    store = _get_store()
    try:
        source_stats = store.get_source_stats()
    except Exception:
        source_stats = []

    sources = []
    for stats in source_stats:
        sources.append({
            "slug": stats.get("source_slug") or "unknown",
            "title": stats.get("source_title") or stats.get("source_slug") or "Unknown",
            "category": stats.get("source_category") or "uncategorized",
            "category_label": stats.get("source_category_label") or "Uncategorized",
            "chunk_count": stats.get("chunk_count", 0),
            "first_ingested": str(stats["first_ingested"]) if stats.get("first_ingested") else None,
            "last_ingested": str(stats["last_ingested"]) if stats.get("last_ingested") else None,
        })

    return {"sources": sources}


def get_chunks(source_slug: str, page: int = 1, per_page: int = 50) -> Dict:
    """Get paginated chunks for a source slug."""
    store = _get_store()
    offset = (page - 1) * per_page
    total = store.count_chunks_for_source(source_slug)
    chunks = store.get_chunks_for_source(source_slug, limit=per_page, offset=offset)

    # Serialise datetimes
    for c in chunks:
        if c.get("created_at"):
            c["created_at"] = str(c["created_at"])
        if c.get("metadata") and isinstance(c["metadata"], str):
            import json
            c["metadata"] = json.loads(c["metadata"])

    return {
        "chunks": chunks,
        "total": total,
        "page": page,
        "per_page": per_page,
        "total_pages": max(1, (total + per_page - 1) // per_page),
    }


def get_source_file_path(source_slug: str, *, tenant_id: str | None = None) -> Optional[str]:
    """Return the absolute path to the PDF for a source slug, or None.

    The tenant_id is required for the scanner to see this tenant's
    registered KnowledgeSource directories. Defaults to the configured
    tenant from settings, matching the rest of the read path.
    """
    from knowledge_agent.ingestion.downloader import DocumentScanner
    from knowledge_agent.store import _tenant_str
    scanner = DocumentScanner(tenant_id=_tenant_str(tenant_id))
    sf = scanner.get_file_by_slug(source_slug)
    return str(sf.path) if sf else None


def get_source_detail(source_slug: str) -> Dict:
    """Get detailed info for a single source including derived chunk counts."""
    store = _get_store()
    base_count = store.count_chunks_for_source(source_slug)
    derived = store.get_derived_counts_for_source(source_slug)
    return {
        "slug": source_slug,
        "base_chunks": base_count,
        **derived,
    }


# ---------------------------------------------------------------------------
# Active strategy management
# ---------------------------------------------------------------------------

def get_active_strategy(user=None, *, tenant_id: str | None = None) -> str:
    """Return the retrieval strategy to use, given a tenant + user.

    Resolution chain:
      1. User preference: KnowledgeConfig(tenant, user, key='preferred_strategy')
      2. Tenant default:  KnowledgeTenant(id=tenant).default_strategy
      3. Hardcoded 'hybrid'

    `tenant_id` defaults to the configured DEFAULT_TENANT_ID, which keeps
    single-tenant deployments working without passing the kwarg.
    """
    from knowledge_agent.store import _tenant_str
    tid = _tenant_str(tenant_id)

    if user is not None and getattr(user, 'is_authenticated', False):
        try:
            from knowledge_agent.models import KnowledgeConfig
            row = KnowledgeConfig.objects.get(
                tenant_id=tid, user=user, key='preferred_strategy',
            )
            if row.value:
                return row.value
        except Exception:
            pass

    store = _get_store()
    try:
        val = store.get_default_strategy(tenant_id=tid)
        if val:
            return val
    except Exception:
        pass
    return "hybrid"


def set_active_strategy(strategy: str, *, tenant_id: str | None = None) -> Dict:
    """Set the tenant's default retrieval strategy."""
    from knowledge_agent.strategies import STRATEGY_REGISTRY
    from knowledge_agent.store import _tenant_str

    if strategy not in STRATEGY_REGISTRY:
        return {"error": f"Unknown strategy: {strategy}"}

    tid = _tenant_str(tenant_id)
    store = _get_store()
    try:
        store.set_default_strategy(strategy, tenant_id=tid)
    except ValueError as e:
        return {"error": str(e)}
    return {"active_strategy": strategy, "tenant_id": tid}


# ---------------------------------------------------------------------------
# Source registration  (called by subagents at startup)
# ---------------------------------------------------------------------------

# In-memory registry: agent_label → list of directory paths
_registered_sources: Dict[str, List[Dict]] = {}


def register_source_directory(
    agent_label: str,
    path: str,
    label: str | None = None,
    category: str | None = None,
) -> None:
    """
    Register a local directory of documents for ingestion.

    Called by subagents (e.g. dssat_agent) in their AppConfig.ready()
    so the knowledge agent knows where to find their docs.

    Args:
        agent_label: The registering agent (e.g. "dssat_agent").
        path:        Absolute path to a directory containing PDFs.
        label:       Human-readable label (defaults to directory name).
        category:    URL-safe category slug (defaults to slugified label).
    """
    import os
    from knowledge_agent.ingestion.downloader import slugify

    dir_name = os.path.basename(path.rstrip("/"))
    label = label or dir_name
    category = category or slugify(label)

    _registered_sources.setdefault(agent_label, []).append({
        "path": path,
        "label": label,
        "category": category,
    })
    logger.info(
        "Source registered by '%s': %s → category '%s'",
        agent_label, path, category,
    )


def get_registered_sources() -> Dict[str, List[Dict]]:
    """Return all registered source directories, keyed by agent label."""
    return dict(_registered_sources)


def get_all_source_directories() -> List[Dict]:
    """Return a flat list of all registered source directories."""
    all_dirs = []
    for dirs in _registered_sources.values():
        all_dirs.extend(dirs)
    return all_dirs
