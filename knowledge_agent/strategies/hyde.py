"""HyDE (Hypothetical Document Embeddings) strategy.

The LLM generates a hypothetical answer to the query, then the *answer*
is embedded and used for vector search — often finding more relevant
chunks than the raw question embedding.

Domain coupling: the prompt asks the LLM to write in the corpus's voice,
so we splice in `tenant.domain.hint` (e.g. "DSSAT crop modelling and
agronomy" or "legal contracts and case law"). If the tenant doesn't
register a hint, falls back to "an authoritative reference for the
indexed corpus" — neutral.
"""

import logging
from typing import Any, Dict, List, Optional

from . import (
    RetrievalResult,
    RetrievalStrategy,
    load_tenant_domain,
    register_strategy,
    result_from_row,
)
from ..backends import get_embedding_backend, get_llm_backend
from ..store import VectorStore

logger = logging.getLogger(__name__)

# Parameterized template — `{domain_hint}` is filled in at query time
# from the tenant's domain config.
_HYDE_PROMPT = """You are an expert in {domain_hint}.
Write a short, factual paragraph that directly answers the following
question as if it appeared in an authoritative reference for that domain.
Do NOT say "I don't know" — write the best answer you can.

Question: {query}

Answer:"""


_DEFAULT_DOMAIN_HINT = "the indexed corpus"


@register_strategy("hyde")
class HyDEStrategy(RetrievalStrategy):
    """Generate a hypothetical answer via LLM, embed it, and search for
    the real chunks whose embeddings are closest to that answer."""

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
        domain = load_tenant_domain(tenant_id)
        domain_hint = domain.get("hint") or _DEFAULT_DOMAIN_HINT

        hyp_answer = self.llm.generate(
            _HYDE_PROMPT.format(domain_hint=domain_hint, query=query)
        )
        logger.info("HyDE hypothetical answer: %s", hyp_answer[:200])

        hyp_emb = self.embedding_service.embed_text(hyp_answer)
        rows = self.store.vector_search(
            hyp_emb, top_k=top_k, filters=filters, tenant_id=tenant_id,
        )
        return [result_from_row(r) for r in rows]
    # Inherits the base class's threaded retrieve_batch — HyDE is Tier-2
    # (per-query LLM call), so parallelization comes from the pool.
