"""Ontology strategy — domain-agnostic SPARQL queries against an OWL/RDF graph.

The strategy iterates over a small set of parameterized SPARQL templates
(in `ontology_templates.py`), formatting each one with the tenant's
ontology vocabulary at query time. Tenants never write SPARQL — they
declare entity_types / relationship_types / relationship_typing in their
DomainConfig and the strategy synthesizes the queries.

If the tenant has no ontology block in its domain config, only the
domain-agnostic `general_concept` template runs. If the tenant has no
ontology rows in the database, the strategy falls back to plain vector
search and logs a warning (Q9: admins see "ontology not built", users
see a minimal message — wired through tool_wrapper's two-tier formatter).
"""

import logging
from typing import Any, Dict, List, Optional

from . import (
    Citation,
    RetrievalResult,
    RetrievalStrategy,
    load_tenant_domain,
    register_strategy,
    resolve_tenant,
    result_from_row,
)
from ..backends import get_embedding_backend
from ..store import VectorStore
from .ontology_templates import TEMPLATES

logger = logging.getLogger(__name__)


@register_strategy("ontology")
class OntologyStrategy(RetrievalStrategy):
    """Synthesize SPARQL queries from the tenant's ontology vocabulary,
    run them against the in-memory rdflib graph built during ingestion,
    fetch back-linked source chunks for citation, and fall back to vector
    search when the ontology returns nothing."""

    def __init__(
        self,
        store: VectorStore | None = None,
        embedding_service=None,
        **kwargs,
    ):
        self.store = store or VectorStore()
        self.embedding_service = embedding_service or get_embedding_backend()
        # Per-tenant graph cache. Populated lazily on first query for each
        # tenant; cleared by the destructive re-ingest flow in chunk 5.
        self._graph_cache: Dict[str, Any] = {}
        self._chunk_ids_cache: Dict[str, List[str]] = {}

    # ------------------------------------------------------------------
    # Graph loading (per tenant)
    # ------------------------------------------------------------------

    def _load_graph(self, tenant_id: str):
        """Lazy-load the latest ontology turtle for this tenant into rdflib.

        Returns (graph, source_chunk_ids) or (None, []) if rdflib isn't
        installed or the tenant has no ontology rows yet.
        """
        if tenant_id in self._graph_cache:
            return self._graph_cache[tenant_id], self._chunk_ids_cache[tenant_id]
        try:
            from rdflib import Graph
        except ImportError:
            logger.error("rdflib not installed — ontology strategy unavailable")
            return None, []

        latest = self.store.get_latest_ontology(tenant_id=tenant_id)
        if latest is None:
            logger.warning(
                "No ontology found for tenant %r — strategy will fall back "
                "to vector search. Run `manage.py ingest_knowledge "
                "--tenant=%s` to build it.",
                tenant_id, tenant_id,
            )
            self._graph_cache[tenant_id] = Graph()
            self._chunk_ids_cache[tenant_id] = []
            return self._graph_cache[tenant_id], []

        turtle_data, source_chunk_ids = latest
        graph = Graph()
        graph.parse(data=turtle_data, format="turtle")
        self._graph_cache[tenant_id] = graph
        self._chunk_ids_cache[tenant_id] = source_chunk_ids
        logger.info(
            "Loaded ontology for tenant %r: %d triples", tenant_id, len(graph),
        )
        return graph, source_chunk_ids

    # ------------------------------------------------------------------
    # SPARQL synthesis (per tenant vocabulary)
    # ------------------------------------------------------------------

    def _compile_queries(self, tenant_id: str, query_lower: str) -> List[str]:
        """Synthesize the SPARQL strings to run for this tenant + query.

        Iterates the tenant's domain vocabulary, plugging each entry
        into the appropriate parameterized template. Always emits the
        domain-agnostic `general_concept` template, even when the tenant
        has no ontology block.
        """
        domain = load_tenant_domain(tenant_id)
        ontology = domain.get("ontology") or {}
        prefix = ontology.get("prefix", "ex")
        namespace = ontology.get("namespace", "http://example.com/ontology#")
        entity_types = domain.get("entity_types") or []
        relationship_types = domain.get("relationship_types") or []
        relationship_typing = ontology.get("relationship_typing") or {}
        data_properties = ontology.get("data_properties") or []

        sparql_strings: List[str] = []

        # general_concept always runs
        sparql_strings.append(TEMPLATES["general_concept"].format(
            prefix=prefix, namespace=namespace, query_lower=query_lower,
        ))

        # find_class_instances — once per entity type
        for cls in entity_types:
            sparql_strings.append(TEMPLATES["find_class_instances"].format(
                prefix=prefix, namespace=namespace,
                class_name=cls, query_lower=query_lower,
            ))

        # find_typed_relation — once per typed relationship
        for rel_name, typing in relationship_typing.items():
            if isinstance(typing, dict):
                dom, rng = typing.get("domain"), typing.get("range")
            elif isinstance(typing, (list, tuple)) and len(typing) == 2:
                dom, rng = typing
            else:
                continue
            if dom is None or rng is None:
                continue
            sparql_strings.append(TEMPLATES["find_typed_relation"].format(
                prefix=prefix, namespace=namespace,
                relation=rel_name, domain_class=dom, range_class=rng,
                query_lower=query_lower,
            ))

        # find_untyped_relation — for relationships without typing info
        for rel_name in relationship_types:
            if rel_name in relationship_typing:
                continue
            sparql_strings.append(TEMPLATES["find_untyped_relation"].format(
                prefix=prefix, namespace=namespace,
                relation=rel_name, query_lower=query_lower,
            ))

        # find_with_data_property — once per data property
        for prop_name in data_properties:
            sparql_strings.append(TEMPLATES["find_with_data_property"].format(
                prefix=prefix, namespace=namespace,
                property_name=prop_name, query_lower=query_lower,
            ))

        return sparql_strings

    # ------------------------------------------------------------------
    # Retrieval
    # ------------------------------------------------------------------

    def retrieve(
        self,
        query: str,
        top_k: int = 5,
        filters: Optional[Dict[str, Any]] = None,
        *,
        tenant_id: Optional[str] = None,
    ) -> List[RetrievalResult]:
        tid = resolve_tenant(tenant_id)
        graph, source_chunk_ids = self._load_graph(tid)
        if graph is None:
            return self._fallback_vector(query, top_k, filters, tid)

        query_lower = query.lower()
        results: List[RetrievalResult] = []

        for sparql in self._compile_queries(tid, query_lower):
            try:
                qres = graph.query(sparql)
                for row in qres:
                    content = " | ".join(str(v) for v in row)
                    results.append(RetrievalResult(
                        content=content,
                        score=1.0,
                        metadata={"ontology_query": "synthesized"},
                    ))
            except Exception as e:
                logger.debug("SPARQL query failed: %s", e)

        # If the ontology returned anything, attach back-linked source
        # chunks for citation. Otherwise fall back to vector search.
        if results and source_chunk_ids:
            chunk_ids = source_chunk_ids[:top_k]
            chunks = self.store.get_chunks_by_ids(chunk_ids, tenant_id=tid)
            for c in (chunks or []):
                row = dict(c)
                row["score"] = 0.8
                results.append(result_from_row(row))

        if not results:
            return self._fallback_vector(query, top_k, filters, tid)

        return results[:top_k]

    def _fallback_vector(
        self,
        query: str,
        top_k: int,
        filters: Optional[Dict[str, Any]],
        tenant_id: str,
    ) -> List[RetrievalResult]:
        """Vector-search fallback when no ontology matches (or no ontology
        is configured for the tenant yet)."""
        query_emb = self.embedding_service.embed_text(query)
        rows = self.store.vector_search(
            query_emb, top_k=top_k, filters=filters, tenant_id=tenant_id,
        )
        return [result_from_row(r) for r in rows]
