"""RAG Fusion strategy.

LLM generates 3-5 query variants → retrieve for each → fuse via RRF.

DSSAT phrasing stripped — the rewrite task is corpus-agnostic; knowing
the domain doesn't materially help the LLM generate query variants.
"""

import logging
from typing import Any, Dict, List, Optional

from . import (
    RetrievalResult,
    RetrievalStrategy,
    reciprocal_rank_fusion,
    register_strategy,
    result_from_row,
)
from ..backends import get_embedding_backend, get_llm_backend
from ..store import VectorStore

logger = logging.getLogger(__name__)

_GENERATE_QUERIES_PROMPT = """Given a user question, generate {n} different
versions of the question that could help find relevant information. Each
version should approach the topic from a slightly different angle or use
different terminology.

Original question: {query}

Respond with ONLY the alternative questions, one per line (no numbering,
no bullets):"""


@register_strategy("rag_fusion")
class RAGFusionStrategy(RetrievalStrategy):
    """Generate multiple query variants via LLM, retrieve for each,
    and fuse results with Reciprocal Rank Fusion."""

    def __init__(
        self,
        store: VectorStore | None = None,
        embedding_service=None,
        llm=None,
        n_queries: int = 4,
        **kwargs,
    ):
        self.store = store or VectorStore()
        self.embedding_service = embedding_service or get_embedding_backend()
        self.llm = llm or get_llm_backend()
        self.n_queries = n_queries

    def retrieve(
        self,
        query: str,
        top_k: int = 5,
        filters: Optional[Dict[str, Any]] = None,
        *,
        tenant_id: Optional[str] = None,
    ) -> List[RetrievalResult]:
        raw = self.llm.generate(
            _GENERATE_QUERIES_PROMPT.format(query=query, n=self.n_queries)
        )
        alt_queries = [q.strip() for q in raw.strip().split("\n") if q.strip()]
        all_queries = [query] + alt_queries[:self.n_queries]
        logger.info("RAG Fusion queries: %s", all_queries)

        # Embed all variants at once — single backend call instead of N.
        all_embs = self.embedding_service.embed_batch(all_queries)

        all_result_lists = []
        for q, q_emb in zip(all_queries, all_embs):
            rows = self.store.hybrid_search(
                q, q_emb, top_k=top_k, filters=filters, tenant_id=tenant_id,
            )
            all_result_lists.append([result_from_row(r) for r in rows])

        return reciprocal_rank_fusion(*all_result_lists, top_k=top_k)
    # Inherits base class threaded retrieve_batch — RAG-Fusion is Tier-2
    # (each batch query triggers another LLM call to generate variants).
