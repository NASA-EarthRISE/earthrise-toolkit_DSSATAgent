"""Contextual retrieval strategy — hybrid search over context-enriched chunks."""

from typing import Any, Dict, List, Optional

from . import (
    RetrievalResult,
    RetrievalStrategy,
    register_strategy,
    result_from_row,
)
from ..backends import get_embedding_backend
from ..store import VectorStore


@register_strategy("contextual")
class ContextualStrategy(RetrievalStrategy):
    """Hybrid search against the contextual chunks table where each chunk
    has been prepended with LLM-generated context describing its position
    in the document (Anthropic's Contextual Retrieval technique)."""

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
        rows = self.store.hybrid_search(
            query, query_emb,
            top_k=top_k,
            table="knowledge_chunks_contextual",
            filters=filters,
            tenant_id=tenant_id,
        )
        return [result_from_row(r) for r in rows]

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
        out: List[List[RetrievalResult]] = []
        for q, emb in zip(queries, embeddings):
            rows = self.store.hybrid_search(
                q, emb,
                top_k=top_k,
                table="knowledge_chunks_contextual",
                filters=filters,
                tenant_id=tenant_id,
            )
            out.append([result_from_row(r) for r in rows])
        return out
