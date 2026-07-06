"""CRAG (Corrective RAG) strategy.

Retrieve → LLM grades relevance → if results are poor, rewrite the query
and re-retrieve for better results.

DSSAT phrasing stripped from the rewrite prompt (decorative — the LLM
doesn't need to know the corpus topic to rewrite a query). The grade
prompt was already generic.
"""

import logging
from typing import Any, Dict, List, Optional

from . import (
    RetrievalResult,
    RetrievalStrategy,
    register_strategy,
    result_from_row,
)
from ..backends import get_embedding_backend, get_llm_backend
from ..store import VectorStore

logger = logging.getLogger(__name__)

_GRADE_PROMPT = """You are grading the relevance of a retrieved document to a user question.

Question: {query}
Document: {document}

Is this document relevant to answering the question?
Respond with ONLY "yes" or "no":"""

_REWRITE_PROMPT = """The original query did not return good results.
Rewrite it to be more specific and likely to match relevant documentation.

Original query: {query}

Rewritten query (respond with ONLY the rewritten query):"""


@register_strategy("crag")
class CRAGStrategy(RetrievalStrategy):
    """Corrective RAG: retrieve, grade relevance via LLM, and if results
    are poor, rewrite the query and re-retrieve."""

    def __init__(
        self,
        store: VectorStore | None = None,
        embedding_service=None,
        llm=None,
        **kwargs,
    ):
        self.store = store or VectorStore()
        self.embedding_service = embedding_service or get_embedding_backend()
        self.llm = llm or get_llm_backend()

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
            query, query_emb, top_k=top_k, filters=filters, tenant_id=tenant_id,
        )
        results = [result_from_row(r) for r in rows]

        good, bad = self._grade_results(query, results)
        logger.info(
            "CRAG grading: %d good, %d bad out of %d",
            len(good), len(bad), len(results),
        )

        if len(good) < top_k // 2:
            rewritten = self.llm.generate(_REWRITE_PROMPT.format(query=query)).strip()
            logger.info("CRAG rewritten query: %s", rewritten)

            rewritten_emb = self.embedding_service.embed_text(rewritten)
            new_rows = self.store.hybrid_search(
                rewritten, rewritten_emb,
                top_k=top_k, filters=filters, tenant_id=tenant_id,
            )
            new_results = [result_from_row(r) for r in new_rows]

            seen = {r.chunk_id for r in good}
            combined = list(good)
            for r in new_results:
                if r.chunk_id not in seen:
                    combined.append(r)
                    seen.add(r.chunk_id)
            return combined[:top_k]

        return good[:top_k] if good else results[:top_k]

    def _grade_results(
        self, query: str, results: List[RetrievalResult],
    ) -> tuple[List[RetrievalResult], List[RetrievalResult]]:
        good, bad = [], []
        for r in results:
            grade = self.llm.generate(
                _GRADE_PROMPT.format(query=query, document=r.content[:500])
            ).strip().lower()
            if "yes" in grade:
                good.append(r)
            else:
                bad.append(r)
        return good, bad
    # Inherits base class threaded retrieve_batch — CRAG is Tier-2.
