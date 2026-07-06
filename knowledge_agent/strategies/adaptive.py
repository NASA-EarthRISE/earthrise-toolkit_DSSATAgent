"""Adaptive strategy — classifies query complexity and routes to the
most appropriate retrieval strategy automatically.

DSSAT phrasing stripped — routing is corpus-agnostic; the LLM picks a
strategy based on query *shape* (factual vs comparative vs conceptual),
not on knowing the corpus topic.
"""

import logging
from typing import Any, Dict, List, Optional

from . import (
    STRATEGY_REGISTRY,
    RetrievalResult,
    RetrievalStrategy,
    register_strategy,
)
from ..backends import get_embedding_backend, get_llm_backend
from ..store import VectorStore

logger = logging.getLogger(__name__)

_CLASSIFY_PROMPT = """Given a user's question, decide which retrieval
strategy will produce the best results.

Available strategies:
- "basic_vector": Simple semantic search. Good for straightforward factual lookups.
- "hybrid": Combined semantic + keyword search. Good for queries mixing technical terms and concepts.
- "hyde": Hypothetical document generation then search. Good for complex "how does X work?" questions.
- "parent_child": Fine search, broad context. Good for questions needing detailed explanations.
- "contextual": Context-enriched search. Good for ambiguous terms that need document context.

User query: {query}

Respond with ONLY the strategy name (one of: basic_vector, hybrid, hyde, parent_child, contextual):"""


@register_strategy("adaptive")
class AdaptiveStrategy(RetrievalStrategy):
    """LLM classifies the query and routes to the chosen strategy."""

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
        raw = self.llm.generate(_CLASSIFY_PROMPT.format(query=query)).strip().lower()
        chosen = raw.split()[0].strip('"\'.,')

        valid = {"basic_vector", "hybrid", "hyde", "parent_child", "contextual"}
        if chosen not in valid:
            chosen = "hybrid"

        logger.info("Adaptive strategy chose: %s", chosen)

        strategy_cls = STRATEGY_REGISTRY.get(chosen) or STRATEGY_REGISTRY["hybrid"]
        delegate = strategy_cls(
            store=self.store,
            embedding_service=self.embedding_service,
            llm=self.llm,
        )
        results = delegate.retrieve(
            query, top_k=top_k, filters=filters, tenant_id=tenant_id,
        )

        for r in results:
            r.metadata["adaptive_strategy"] = chosen
        return results
    # Inherits base class threaded retrieve_batch — Tier-2 (per-query LLM
    # routing call). The delegate strategy may itself be Tier-1, but the
    # routing decision is per-query.
