"""Basic cosine-similarity vector search strategy."""

from typing import Any, Dict, List, Optional

from . import (
    RetrievalResult,
    RetrievalStrategy,
    register_strategy,
    result_from_row,
)
from ..backends import get_embedding_backend
from ..store import VectorStore


@register_strategy("basic_vector")
class BasicVectorStrategy(RetrievalStrategy):
    """Embed the query and retrieve the top-k most similar chunks via
    pgvector cosine similarity. Tenant-scoped pre-filter."""

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
        rows = self.store.vector_search(
            query_emb, top_k=top_k, filters=filters, tenant_id=tenant_id,
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
        """Tier-1 override: embed all queries in one backend call, then
        search per query."""
        if not queries:
            return []
        embeddings = self.embedding_service.embed_batch(queries)
        out: List[List[RetrievalResult]] = []
        for emb in embeddings:
            rows = self.store.vector_search(
                emb, top_k=top_k, filters=filters, tenant_id=tenant_id,
            )
            out.append([result_from_row(r) for r in rows])
        return out
