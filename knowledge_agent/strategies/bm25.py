"""BM25 full-text search strategy using PostgreSQL tsvector/tsquery."""

from typing import Any, Dict, List, Optional

from . import (
    RetrievalResult,
    RetrievalStrategy,
    register_strategy,
    result_from_row,
)
from ..store import VectorStore


@register_strategy("bm25")
class BM25Strategy(RetrievalStrategy):
    """Lexical search via PostgreSQL ts_rank_cd. No embeddings."""

    def __init__(self, store: VectorStore | None = None, **kwargs):
        self.store = store or VectorStore()

    def retrieve(
        self,
        query: str,
        top_k: int = 5,
        filters: Optional[Dict[str, Any]] = None,
        *,
        tenant_id: Optional[str] = None,
    ) -> List[RetrievalResult]:
        rows = self.store.bm25_search(
            query, top_k=top_k, filters=filters, tenant_id=tenant_id,
        )
        return [result_from_row(r) for r in rows]

    # No batch override needed — bm25 is DB-bound; the base class's
    # ThreadPoolExecutor parallelism is already the best we can do
    # without aggregating tsqueries server-side.
