"""GraphRAG strategy — entity-based knowledge graph traversal."""

from typing import Any, Dict, List, Optional

from . import (
    Citation,
    RetrievalResult,
    RetrievalStrategy,
    register_strategy,
)
from ..backends import get_embedding_backend
from ..store import VectorStore


@register_strategy("graphrag")
class GraphRAGStrategy(RetrievalStrategy):
    """Embed the query, find the most similar entities in the knowledge
    graph, then traverse relationships 1-2 hops to collect context from
    linked chunks."""

    def __init__(
        self,
        store: VectorStore | None = None,
        embedding_service=None,
        hops: int = 2,
        **kwargs,
    ):
        self.store = store or VectorStore()
        self.embedding_service = embedding_service or get_embedding_backend()
        self.hops = hops

    def retrieve(
        self,
        query: str,
        top_k: int = 5,
        filters: Optional[Dict[str, Any]] = None,
        *,
        tenant_id: Optional[str] = None,
    ) -> List[RetrievalResult]:
        query_emb = self.embedding_service.embed_text(query)
        rows = self.store.graph_search(
            query_emb, top_k=top_k, hops=self.hops, tenant_id=tenant_id,
        )
        return [self._to_result(r) for r in rows][:top_k]

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
        out = []
        for emb in embeddings:
            rows = self.store.graph_search(
                emb, top_k=top_k, hops=self.hops, tenant_id=tenant_id,
            )
            out.append([self._to_result(r) for r in rows][:top_k])
        return out

    @staticmethod
    def _to_result(row: Dict) -> RetrievalResult:
        meta = row.get("metadata") or {}
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
        # Graph results may include entity or chunk rows
        content = row.get("content") or row.get("description") or ""
        return RetrievalResult(
            content=content,
            score=row.get("score", 0.0),
            metadata=meta,
            chunk_id=row.get("id"),
            citation=citation,
        )
