"""RAPTOR strategy — searches across all levels of the summary tree."""

from typing import Any, Dict, List, Optional

from . import (
    Citation,
    RetrievalResult,
    RetrievalStrategy,
    register_strategy,
)
from ..backends import get_embedding_backend
from ..store import VectorStore


@register_strategy("raptor")
class RAPTORStrategy(RetrievalStrategy):
    """Search across all levels of the RAPTOR summary tree to return a
    mix of detailed leaf chunks and higher-level summaries."""

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
        rows = self.store.raptor_search(query_emb, top_k=top_k, tenant_id=tenant_id)
        return [self._to_result(r) for r in rows]

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
            rows = self.store.raptor_search(emb, top_k=top_k, tenant_id=tenant_id)
            out.append([self._to_result(r) for r in rows])
        return out

    @staticmethod
    def _to_result(row: Dict) -> RetrievalResult:
        meta = row.get("metadata") or {}
        meta["raptor_level"] = row.get("level", 0)
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
        return RetrievalResult(
            content=row["content"],
            score=row.get("score", 0.0),
            metadata=meta,
            chunk_id=row["id"],
            citation=citation,
        )
