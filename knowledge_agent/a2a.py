"""
knowledge_agent A2A surface — skill table + agent card + URL patterns.

Single module holding the skill table, agent card, and URL patterns.
The A2A URL contract is mounted at ``/knowledge_agent/`` (the
app_label) — verified by ``knowledge_agent/tests/test_a2a_contract.py``.
"""

from __future__ import annotations

from typing import Any, Dict

from earthrise_agents_base.a2a import (
    SkillTable,
    build_agent_card,
    build_urlpatterns,
    register_agent,
)


skills = SkillTable()


@skills.skill(
    id="retrieve_knowledge",
    name="Retrieve Knowledge",
    description="Search the indexed corpus using configurable retrieval strategies.",
)
def _retrieve_knowledge(params: Dict[str, Any]) -> Any:
    from knowledge_agent.services import retrieve
    return retrieve(
        query=params.get("query", ""),
        strategy=params.get("strategy", "hybrid"),
        top_k=params.get("top_k", 5),
        filters=params.get("filters"),
    )


@skills.skill(
    id="list_strategies",
    name="List Strategies",
    description="List available retrieval strategies with readiness status.",
)
def _list_strategies(params: Dict[str, Any]) -> Any:
    from knowledge_agent.services import list_strategies
    return list_strategies()


@skills.skill(
    id="check_readiness",
    name="Check Readiness",
    description="Check preprocessing status for all strategies.",
)
def _check_readiness(params: Dict[str, Any]) -> Any:
    from knowledge_agent.services import check_readiness
    return check_readiness()


AGENT_CARD = build_agent_card(
    name="KnowledgeAgent",
    description=(
        "Tenant-scoped retrieval-augmented generation service over an indexed "
        "document corpus. Supports 12 retrieval strategies including vector, "
        "BM25, hybrid, HyDE, CRAG, RAG-Fusion, GraphRAG, RAPTOR, and "
        "ontology-based retrieval."
    ),
    version="2.0.0",
    skills=skills,
)


register_agent(name="knowledge_agent", card=AGENT_CARD, skills=skills)


# ---------------------------------------------------------------------------
# URL patterns
# ---------------------------------------------------------------------------

app_name = "knowledge_agent_a2a"
urlpatterns = build_urlpatterns(agent_name="knowledge_agent")
