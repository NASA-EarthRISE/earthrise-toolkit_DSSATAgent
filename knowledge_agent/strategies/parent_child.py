"""Parent-child retrieval strategy.

Searches against fine-grained *child* chunks for precision, then returns
the broader *parent* chunks to give the LLM more context.
"""

from typing import Any, Dict, List, Optional

from . import (
    Citation,
    RetrievalResult,
    RetrievalStrategy,
    register_strategy,
)
from ..backends import get_embedding_backend
from ..store import VectorStore


@register_strategy("parent_child")
class ParentChildStrategy(RetrievalStrategy):
    """Search small child chunks → return their larger parent chunks for
    richer context. Balances retrieval precision with response quality."""

    def __init__(
        self,
        store: VectorStore | None = None,
        embedding_service=None,
        **kwargs,
    ):
        self.store = store or VectorStore()
        self.embedding_service = embedding_service or get_embedding_backend()

    def retrieve(
        self,
        query: str,
        top_k: int = 5,
        filters: Optional[Dict[str, Any]] = None,
        *,
        tenant_id: Optional[str] = None,
    ) -> List[RetrievalResult]:
        query_emb = self.embedding_service.embed_text(query)
        return self._search(query_emb, top_k, filters, tenant_id)

    def retrieve_batch(
        self,
        queries: List[str],
        top_k: int = 5,
        filters: Optional[Dict[str, Any]] = None,
        *,
        tenant_id: Optional[str] = None,
    ) -> List[List[RetrievalResult]]:
        if not queries:
            return []
        embeddings = self.embedding_service.embed_batch(queries)
        return [
            self._search(emb, top_k, filters, tenant_id)
            for emb in embeddings
        ]

    def _search(
        self,
        query_emb: List[float],
        top_k: int,
        filters: Optional[Dict[str, Any]],
        tenant_id: Optional[str],
    ) -> List[RetrievalResult]:
        child_rows = self.store.vector_search(
            query_emb,
            top_k=top_k * 2,
            table="knowledge_child_chunks",
            filters=filters,
            tenant_id=tenant_id,
        )

        # Collect unique parent IDs preserving order
        seen_parents: dict = {}
        for row in child_rows:
            meta = row.get("metadata") or {}
            pid = meta.get("parent_id") or row.get("parent_id")
            if pid and pid not in seen_parents:
                seen_parents[pid] = row.get("score", 0.0)

        if not seen_parents:
            return []

        parent_ids = list(seen_parents.keys())[:top_k]
        parent_rows = self.store.get_parent_chunks_by_ids(
            parent_ids, tenant_id=tenant_id,
        )

        results = []
        for pr in (parent_rows or []):
            meta = dict(pr.get("metadata") or {})
            citation = None
            if meta.get("source_slug"):
                citation = Citation(
                    source_title=meta.get("source_title", ""),
                    source_slug=meta["source_slug"],
                    page_number=meta.get("page_number"),
                    section_title=meta.get("section_title"),
                    url=f"/knowledge/documents/{meta['source_slug']}/file/"
                        + (f"#page={meta['page_number']}" if meta.get("page_number") else ""),
                )
            results.append(RetrievalResult(
                content=pr["content"],
                score=seen_parents.get(pr["id"], 0.0),
                metadata=meta,
                chunk_id=pr["id"],
                citation=citation,
            ))
        return results
