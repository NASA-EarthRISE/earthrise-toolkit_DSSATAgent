"""
Retrieval strategy registry and base classes.

Each strategy implements a different RAG approach. Strategies are
registered via the `@register_strategy` decorator and looked up by name
at runtime.

Base-class contract:
- `retrieve()` takes a `tenant_id` keyword. Defaults to None, which the
  strategy resolves to `store.DEFAULT_TENANT_ID` ('default'). Callers
  that need explicit tenancy pass `tenant_id="acme"`.
- `retrieve_batch()` runs N `retrieve()` calls. Default impl uses `ThreadPoolExecutor` with
  `max_workers` from settings to run N independent `retrieve()` calls.
  Tier-1 strategies (vector/BM25/hybrid/parent_child/contextual/raptor/
  graphrag/ontology) can override for embed-once-search-N efficiency;
  Tier-2 strategies (hyde/crag/rag_fusion/adaptive) inherit the
  threaded default since they each need a per-query LLM call.
"""

import logging
import os
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


def _apply_subpath(url: str) -> str:
    """Prepend the deployment SUBPATH to a root-relative citation URL.

    Idempotent: returns the URL unchanged if it is already prefixed,
    absolute, or empty. Matches the same check tool_wrapper.py uses, so
    pipelines that touch the URL twice don't double-prefix.
    """
    if not url or not url.startswith("/"):
        return url
    subpath = (os.environ.get("SUBPATH") or "").strip("/")
    if not subpath:
        return url
    if url.startswith(f"/{subpath}/"):
        return url
    return f"/{subpath}{url}"

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Strategy → table dependency map
# ---------------------------------------------------------------------------

STRATEGY_DEPENDENCIES: Dict[str, Dict] = {
    "basic_vector":  {"tables": ["knowledge_chunks"], "steps": ["chunk_and_embed"]},
    "bm25":          {"tables": ["knowledge_chunks"], "steps": ["chunk_and_embed"]},
    "hybrid":        {"tables": ["knowledge_chunks"], "steps": ["chunk_and_embed"]},
    "hyde":          {"tables": ["knowledge_chunks"], "steps": ["chunk_and_embed"]},
    "crag":          {"tables": ["knowledge_chunks"], "steps": ["chunk_and_embed"]},
    "rag_fusion":    {"tables": ["knowledge_chunks"], "steps": ["chunk_and_embed"]},
    "contextual":    {"tables": ["knowledge_chunks_contextual"], "steps": ["chunk_and_embed", "build_contextual"]},
    "parent_child":  {"tables": ["knowledge_parent_chunks", "knowledge_child_chunks"], "steps": ["build_parent_child"]},
    "graphrag":      {"tables": ["knowledge_entities", "knowledge_relationships"], "steps": ["chunk_and_embed", "build_graph"]},
    "raptor":        {"tables": ["knowledge_raptor_nodes"], "steps": ["chunk_and_embed", "build_raptor"]},
    "ontology":      {"tables": ["knowledge_ontology"], "steps": ["chunk_and_embed", "build_ontology"]},
    "adaptive":      {"tables": ["knowledge_chunks"], "steps": ["chunk_and_embed"]},
}


def get_strategy_dependencies(name: str) -> Dict:
    return STRATEGY_DEPENDENCIES.get(name, {})


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class Citation:
    """Source citation for a retrieved chunk."""
    source_title: str = ""
    source_slug: str = ""
    page_number: Optional[int] = None
    section_title: Optional[str] = None
    url: str = ""


@dataclass
class RetrievalResult:
    """A single retrieval result returned by any strategy."""
    content: str
    score: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)
    citation: Optional[Citation] = None
    chunk_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "content": self.content,
            "score": self.score,
            "metadata": self.metadata,
        }
        if self.chunk_id:
            d["chunk_id"] = self.chunk_id
        if self.citation:
            d["citation"] = {
                "source_title": self.citation.source_title,
                "source_slug": self.citation.source_slug,
                "page_number": self.citation.page_number,
                "section_title": self.citation.section_title,
                "url": _apply_subpath(self.citation.url),
            }
        return d


# ---------------------------------------------------------------------------
# Strategy registry
# ---------------------------------------------------------------------------

STRATEGY_REGISTRY: Dict[str, type] = {}


def register_strategy(name: str):
    """Decorator to register a retrieval strategy class."""
    def decorator(cls):
        STRATEGY_REGISTRY[name] = cls
        cls.strategy_name = name
        return cls
    return decorator


def get_strategy(name: str, **kwargs):
    """Instantiate a registered strategy by name."""
    if name not in STRATEGY_REGISTRY:
        raise ValueError(
            f"Unknown strategy '{name}'. Available: {list(STRATEGY_REGISTRY.keys())}"
        )
    return STRATEGY_REGISTRY[name](**kwargs)


def _batch_max_workers() -> int:
    """Read the configured ThreadPoolExecutor size for retrieve_batch."""
    try:
        from django.conf import settings
        return int(getattr(settings, "KNOWLEDGE_AGENT", {}).get("batch_max_workers", 4))
    except Exception:
        return 4


# ---------------------------------------------------------------------------
# Base class
# ---------------------------------------------------------------------------

class RetrievalStrategy(ABC):
    """Abstract base class that every retrieval strategy must implement.

    Subclasses MUST implement `retrieve()`. They MAY override
    `retrieve_batch()` if they have an efficient batch path (e.g. Tier-1
    strategies share one embedding call across all queries).
    """

    strategy_name: str = ""

    @abstractmethod
    def retrieve(
        self,
        query: str,
        top_k: int = 5,
        filters: Optional[Dict[str, Any]] = None,
        *,
        tenant_id: Optional[str] = None,
    ) -> List[RetrievalResult]:
        """Return the top-k results for `query` within `tenant_id`.

        `tenant_id=None` resolves to `store.DEFAULT_TENANT_ID`. Callers
        that need multi-tenant isolation pass it explicitly.
        """
        ...

    def retrieve_batch(
        self,
        queries: List[str],
        top_k: int = 5,
        filters: Optional[Dict[str, Any]] = None,
        *,
        tenant_id: Optional[str] = None,
    ) -> List[List[RetrievalResult]]:
        """Default batch: parallel `retrieve()` calls via ThreadPoolExecutor.

        Tier-1 strategies override this to embed once and run N searches.
        Tier-2 LLM strategies (hyde, crag, rag_fusion, adaptive) inherit
        the threaded default — concurrency comes from this pool, not
        from the LLM backend.
        """
        if not queries:
            return []
        max_workers = min(_batch_max_workers(), len(queries))
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = [
                pool.submit(
                    self.retrieve, q,
                    top_k=top_k, filters=filters, tenant_id=tenant_id,
                )
                for q in queries
            ]
            return [f.result() for f in futures]

    @property
    def description(self) -> str:
        return self.__class__.__doc__ or self.strategy_name


# ---------------------------------------------------------------------------
# Tenant resolution helper (used by every strategy module)
# ---------------------------------------------------------------------------

def resolve_tenant(tenant_id: Optional[str]) -> str:
    """Resolve a tenant_id, defaulting via the same path store uses
    (settings.KNOWLEDGE_AGENT['default_tenant_id'])."""
    from ..store import _tenant_str
    return _tenant_str(tenant_id)


def load_tenant_domain(tenant_id: Optional[str]) -> Dict[str, Any]:
    """Load the domain dict for a tenant. Returns {} if tenant doesn't
    exist (strategies degrade gracefully — HyDE uses the default hint,
    GraphRAG uses empty entity_types which the prompt handles)."""
    from ..models import KnowledgeTenant
    tid = resolve_tenant(tenant_id)
    try:
        return KnowledgeTenant.objects.get(id=tid).domain or {}
    except KnowledgeTenant.DoesNotExist:
        return {}


def result_from_row(row: Dict[str, Any]) -> RetrievalResult:
    """Shape a store-layer dict into a RetrievalResult with Citation.

    Every strategy returns its results in this shape so chat orchestrator
    + tool wrapper handle them uniformly. Centralized here to keep the
    citation URL construction consistent across all 12 strategies.
    """
    meta = row.get("metadata") or {}
    citation = None
    if meta.get("source_slug"):
        page = meta.get("page_number")
        citation = Citation(
            source_title=meta.get("source_title", ""),
            source_slug=meta["source_slug"],
            page_number=page,
            section_title=meta.get("section_title"),
            url=f"/knowledge/documents/{meta['source_slug']}/file/"
                + (f"#page={page}" if page else ""),
        )
    return RetrievalResult(
        content=row.get("content", ""),
        score=row.get("score", 0.0),
        metadata=meta,
        chunk_id=row.get("id"),
        citation=citation,
    )


# ---------------------------------------------------------------------------
# Reciprocal Rank Fusion utility
# ---------------------------------------------------------------------------

def reciprocal_rank_fusion(
    *result_lists: List[RetrievalResult],
    k: int = 60,
    top_k: int = 5,
) -> List[RetrievalResult]:
    """Merge multiple ranked result lists using Reciprocal Rank Fusion.

    RRF score for a document d across lists L₁ … Lₙ:
        score(d) = Σ  1 / (k + rank_i(d))
    """
    scores: Dict[str, float] = {}
    result_map: Dict[str, RetrievalResult] = {}

    for results in result_lists:
        for rank, r in enumerate(results, start=1):
            key = r.chunk_id or r.content[:120]
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank)
            if key not in result_map:
                result_map[key] = r

    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:top_k]
    fused = []
    for key, score in ranked:
        result = result_map[key]
        result.score = score
        fused.append(result)
    return fused


# ---------------------------------------------------------------------------
# Auto-import all strategy modules so @register_strategy decorators fire.
# ---------------------------------------------------------------------------

from . import (  # noqa: E402, F401
    basic_vector,
    bm25,
    hybrid,
    hyde,
    rag_fusion,
    crag,
    adaptive,
    contextual,
    parent_child,
    graphrag,
    raptor,
    ontology,
)
