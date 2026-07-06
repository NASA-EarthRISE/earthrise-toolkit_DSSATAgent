"""
Input schema for the knowledge_agent retrieval tool.

Note: `strategy` is optional and typically omitted — the service resolves
the effective strategy via `get_active_strategy(user)` (user preference →
system default). Specify `strategy` only when the question shape clearly
warrants a non-default (e.g., factual lookup → 'bm25', comparative
synthesis → 'rag_fusion').
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class KnowledgeStrategy(str, Enum):
    """The 12 retrieval strategies exposed by knowledge_agent.

    Leave `strategy` unset to use the system/user default — the
    `knowledge_retrieve` tool resolves it.
    """

    HYBRID = "hybrid"
    BASIC_VECTOR = "basic_vector"
    BM25 = "bm25"
    HYDE = "hyde"
    RAG_FUSION = "rag_fusion"
    GRAPHRAG = "graphrag"
    RAPTOR = "raptor"
    CRAG = "crag"
    CONTEXTUAL = "contextual"
    PARENT_CHILD = "parent_child"
    ONTOLOGY = "ontology"
    ADAPTIVE = "adaptive"


class RetrieveInput(BaseModel):
    """Parameters for `knowledge_retrieve` (RAG over the indexed corpus)."""

    query: str = Field(..., min_length=3, description=(
        "The user's question, rewritten if helpful for clarity. Must be a "
        "natural-language query — this is NOT a keyword search."
    ))
    strategy: Optional[KnowledgeStrategy] = Field(None, description=(
        "Override the default retrieval strategy. Leave unset to use the "
        "resolved default (user preference → tenant default → 'hybrid'). "
        "Choose a specific strategy only when the question shape clearly "
        "warrants it — see the knowledge-search skill body for guidance."
    ))
    top_k: int = Field(5, ge=1, le=50, description=(
        "Number of chunks to retrieve. Default 5 is suitable for most questions; "
        "raise to 10–15 for broad 'summarize what the docs say about X' asks."
    ))
    filters: Optional[Dict[str, Any]] = Field(None, description=(
        "Optional metadata filters. Chroma-style operator dict, e.g. "
        "{'source_category': 'manuals'} for equality, "
        "{'page_number': {'$gte': 10, '$lt': 50}} for ranges, or "
        "{'$or': [{'category': 'a'}, {'category': 'b'}]} for logical combinations. "
        "Usually omitted."
    ))
