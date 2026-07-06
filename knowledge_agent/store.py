"""
Facade over the Django ORM for knowledge_agent's data layer.

Every public method takes an optional `tenant_id` string. The default is
`"default"` — the auto-created tenant that single-tenant deployments and
existing callers transparently land in. Callers that need explicit tenant
scoping (e.g. multi-tenant production setups) pass `tenant_id="acme"`
through from the request context.

`VectorStore` is kept as a class (rather than module-level functions) for
backward compatibility with the existing dependency-injection pattern in
the strategies (`store=VectorStore()`). The constructor parameters are
accepted but ignored — Django's connection routing manages the database
now.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, List, Optional, Tuple

from pgvector.django import CosineDistance

from . import config
from .models import (
    KnowledgeChunk,
    KnowledgeChunkContextual,
    KnowledgeChildChunk,
    KnowledgeEntity,
    KnowledgeOntology,
    KnowledgeParentChunk,
    KnowledgeRaptorNode,
    KnowledgeRelationship,
)

logger = logging.getLogger(__name__)


# The default tenant_id used when a caller doesn't pass one. Matches the
# 'default' tenant auto-created by migration 0003 — single-tenant
# deployments never need to think about this.
DEFAULT_TENANT_ID = "default"


# Map the legacy `table` string argument that some strategies pass to
# `vector_search(table=...)` to a model class. The pre-ORM store keyed
# tables by their raw Postgres name; preserve that interface here.
_TABLE_TO_MODEL = {
    "knowledge_chunks":             KnowledgeChunk,
    "knowledge_chunks_contextual":  KnowledgeChunkContextual,
    "knowledge_parent_chunks":      KnowledgeParentChunk,
    "knowledge_child_chunks":       KnowledgeChildChunk,
}


def _tenant_str(tenant) -> str:
    """Resolve a tenant param to a tenant_id string.

    Accepts None (→ settings-driven default), a string, or a
    KnowledgeTenant instance. When None, looks up
    `settings.KNOWLEDGE_AGENT['default_tenant_id']` and falls back to
    DEFAULT_TENANT_ID for standalone scripts where Django isn't
    initialized. This is the single chokepoint for tenant defaulting —
    every store method that takes `tenant_id=None` flows through here.
    """
    if tenant is None:
        try:
            from django.conf import settings
            cfg = getattr(settings, "KNOWLEDGE_AGENT", {}) or {}
            return cfg.get("default_tenant_id", DEFAULT_TENANT_ID)
        except Exception:
            return DEFAULT_TENANT_ID
    if isinstance(tenant, str):
        return tenant
    return tenant.id  # KnowledgeTenant instance — read the PK


def _apply_metadata_filters(qs, filters: Optional[Dict[str, Any]]):
    """Compile a Chroma-style filter dict and apply it to a queryset.

    Filters support shorthand equality (`{"author": "John"}`) plus
    `$eq` / `$ne` / `$gt` / `$gte` / `$lt` / `$lte` / `$in` / `$nin` /
    `$exists` and the logical operators `$and` / `$or` / `$not`. See
    `knowledge_agent.filtering` for the full grammar.

    Tenant scoping is applied BEFORE this — the user-supplied filter
    can never override the tenant boundary because the queryset is
    already `for_tenant(tid).filter(...)` by the time we get here.
    """
    if not filters:
        return qs
    from .filtering import compile_filter
    return qs.filter(compile_filter(filters))


def _row(obj, *, score: float | None = None, extra: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """Shape an ORM instance into the dict format the existing callers expect."""
    out: Dict[str, Any] = {
        "id":       obj.id,
        "content":  getattr(obj, "content", None),
        "metadata": getattr(obj, "metadata", {}) or {},
    }
    if score is not None:
        out["score"] = score
    if extra:
        out.update(extra)
    return out


class VectorStore:
    """ORM-backed facade over the pgvector tables; all queries are tenant-scoped."""

    def __init__(self, conn_params: dict | None = None):
        # conn_params accepted but ignored — Django owns the connection.
        self._conn_params_legacy = conn_params
        self._dim = config.VECTOR_DIM
        # _schema retained as an attribute for any external integration
        # that reads it directly (none in-tree).
        self._schema = config.DB_SCHEMA

    # ------------------------------------------------------------------
    # Schema / table creation
    # ------------------------------------------------------------------

    def create_tables(self):
        """No-op on Django-managed deployments.

        `manage.py migrate` is the canonical schema-bootstrap entrypoint
        after the ORM port. This method is retained because the pre-ORM
        ingestion pipeline called it at `setup_tables()` time; that call
        is harmless now.
        """
        logger.debug(
            "VectorStore.create_tables() is a no-op since the ORM port — "
            "run `manage.py migrate knowledge_agent` instead."
        )

    def drop_tables(self):
        """Drop the eight data tables. Used by `pipeline.reset_tables()`.

        Direct DDL because Django doesn't expose a targeted-drop API and
        we want this to work even when the ORM state is out of sync with
        reality. Uses Django's connection — no psycopg2 dependency.
        """
        from django.db import connection

        tables = [
            "knowledge_ontology",
            "knowledge_raptor_nodes",
            "knowledge_relationships",
            "knowledge_entities",
            "knowledge_child_chunks",
            "knowledge_parent_chunks",
            "knowledge_chunks_contextual",
            "knowledge_chunks",
        ]
        with connection.cursor() as cur:
            for t in tables:
                cur.execute(f'DROP TABLE IF EXISTS "{self._schema}"."{t}" CASCADE')
        logger.info("All knowledge data tables dropped")

    # ------------------------------------------------------------------
    # Insert operations
    # ------------------------------------------------------------------

    def insert_chunk(
        self,
        content: str,
        embedding: List[float],
        metadata: Dict[str, Any] | None = None,
        chunk_id: str | None = None,
        *,
        tenant_id: str | None = None,
    ) -> str:
        cid = chunk_id or str(uuid.uuid4())
        KnowledgeChunk.objects.update_or_create(
            id=cid,
            defaults={
                "tenant_id": _tenant_str(tenant_id),
                "content": content,
                "embedding": embedding,
                "metadata": metadata or {},
            },
        )
        return cid

    def insert_chunks_bulk(
        self,
        rows: List[Tuple[str, str, List[float], Dict]],
        *,
        tenant_id: str | None = None,
    ):
        """Bulk insert (id, content, embedding, metadata) tuples."""
        tid = _tenant_str(tenant_id)
        objs = [
            KnowledgeChunk(
                id=cid,
                tenant_id=tid,
                content=content,
                embedding=emb,
                metadata=meta or {},
            )
            for cid, content, emb, meta in rows
        ]
        KnowledgeChunk.objects.bulk_create(objs, ignore_conflicts=True)

    def insert_contextual_chunk(
        self,
        base_chunk_id: str,
        content: str,
        context_prefix: str,
        embedding: List[float],
        metadata: Dict[str, Any] | None = None,
        *,
        tenant_id: str | None = None,
    ) -> str:
        cid = str(uuid.uuid4())
        KnowledgeChunkContextual.objects.create(
            id=cid,
            tenant_id=_tenant_str(tenant_id),
            base_chunk_id=base_chunk_id,
            content=content,
            context_prefix=context_prefix,
            embedding=embedding,
            metadata=metadata or {},
        )
        return cid

    def insert_parent_chunk(
        self,
        content: str,
        embedding: List[float],
        metadata: Dict[str, Any] | None = None,
        chunk_id: str | None = None,
        *,
        tenant_id: str | None = None,
    ) -> str:
        pid = chunk_id or str(uuid.uuid4())
        KnowledgeParentChunk.objects.update_or_create(
            id=pid,
            defaults={
                "tenant_id": _tenant_str(tenant_id),
                "content": content,
                "embedding": embedding,
                "metadata": metadata or {},
            },
        )
        return pid

    def insert_child_chunk(
        self,
        parent_id: str,
        content: str,
        embedding: List[float],
        metadata: Dict[str, Any] | None = None,
        *,
        tenant_id: str | None = None,
    ) -> str:
        cid = str(uuid.uuid4())
        KnowledgeChildChunk.objects.create(
            id=cid,
            tenant_id=_tenant_str(tenant_id),
            parent_id=parent_id,
            content=content,
            embedding=embedding,
            metadata=metadata or {},
        )
        return cid

    def insert_entity(
        self,
        name: str,
        entity_type: str,
        description: str = "",
        embedding: List[float] | None = None,
        metadata: Dict[str, Any] | None = None,
        *,
        tenant_id: str | None = None,
    ) -> str:
        """Atomically upsert an entity within a tenant. Returns the entity's ID.

        Safe under concurrent Celery workers: the (tenant, name,
        entity_type) unique constraint catches races. `transaction.atomic`
        wraps the get_or_create so an IntegrityError from one worker's
        losing insert race rolls back cleanly and Django re-fetches the
        winning row.
        """
        from django.db import transaction

        tid = _tenant_str(tenant_id)
        new_meta = dict(metadata or {})

        with transaction.atomic():
            obj, created = KnowledgeEntity.objects.select_for_update().get_or_create(
                tenant_id=tid,
                name=name,
                entity_type=entity_type,
                defaults={
                    "description": description,
                    "metadata": new_meta,
                    "embedding": embedding,
                },
            )
            if created:
                return obj.id

            # On update: merge metadata['aliases'] (union of sets), and
            # fill in description / embedding only when the existing row
            # has nothing better. Preserves alias contributions from
            # earlier chunks that may not have been re-extracted here.
            existing_meta = dict(obj.metadata or {})
            existing_aliases = set(existing_meta.get("aliases") or [])
            incoming_aliases = set(new_meta.get("aliases") or [])
            merged_aliases = existing_aliases | incoming_aliases
            if merged_aliases:
                existing_meta["aliases"] = sorted(merged_aliases)
            # Merge any other metadata keys (last write wins for non-alias keys)
            for k, v in new_meta.items():
                if k != "aliases":
                    existing_meta[k] = v

            dirty_fields = []
            if existing_meta != (obj.metadata or {}):
                obj.metadata = existing_meta
                dirty_fields.append("metadata")
            if description and not obj.description:
                obj.description = description
                dirty_fields.append("description")
            if embedding is not None and obj.embedding is None:
                obj.embedding = embedding
                dirty_fields.append("embedding")
            if dirty_fields:
                obj.save(update_fields=dirty_fields)
        return obj.id

    def merge_entity_aliases(self, entity_id: str, aliases: List[str]) -> None:
        """Union *aliases* into the entity's metadata['aliases'] list.

        Read-merge-write inside transaction.atomic + select_for_update so
        concurrent workers extracting the same entity from different
        chunks don't overwrite each other's contributions.
        """
        if not aliases:
            return
        from django.db import transaction

        with transaction.atomic():
            obj = (
                KnowledgeEntity.objects
                .select_for_update()
                .filter(pk=entity_id)
                .first()
            )
            if obj is None:
                return
            meta = dict(obj.metadata or {})
            existing = set(meta.get("aliases") or [])
            new = {a.strip() for a in aliases if isinstance(a, str) and a.strip()}
            merged = sorted(existing | new)
            if merged != sorted(existing):
                meta["aliases"] = merged
                obj.metadata = meta
                obj.save(update_fields=["metadata"])

    def insert_relationship(
        self,
        source_id: str,
        target_id: str,
        relationship: str,
        weight: float = 1.0,
        source_chunk_id: str | None = None,
        metadata: Dict[str, Any] | None = None,
        *,
        tenant_id: str | None = None,
    ) -> str:
        """Atomically upsert a relationship. Returns the row's ID.

        Safe under concurrent workers: the (tenant, source, target,
        relationship, source_chunk_id) unique constraint dedupes literal
        duplicate extractions from the same chunk while preserving
        provenance (same logical edge from different chunks = separate
        rows).
        """
        from django.db import transaction

        with transaction.atomic():
            obj, _created = KnowledgeRelationship.objects.get_or_create(
                tenant_id=_tenant_str(tenant_id),
                source_entity_id=source_id,
                target_entity_id=target_id,
                relationship=relationship,
                source_chunk_id=source_chunk_id,
                defaults={
                    "weight": weight,
                    "metadata": metadata or {},
                },
            )
        return obj.id

    def insert_raptor_node(
        self,
        content: str,
        embedding: List[float],
        level: int,
        parent_id: str | None = None,
        children_ids: List[str] | None = None,
        metadata: Dict[str, Any] | None = None,
        *,
        tenant_id: str | None = None,
    ) -> str:
        nid = str(uuid.uuid4())
        KnowledgeRaptorNode.objects.create(
            id=nid,
            tenant_id=_tenant_str(tenant_id),
            content=content,
            embedding=embedding,
            level=level,
            parent_id=parent_id,
            children_ids=children_ids or [],
            metadata=metadata or {},
        )
        return nid

    def insert_ontology(
        self,
        turtle_data: str,
        source_chunk_ids: List[str] | None = None,
        version: int = 1,
        *,
        tenant_id: str | None = None,
    ) -> str:
        """Replace the tenant's ontology with a freshly assembled version.

        The ontology is a single per-tenant document rebuilt in full on
        each ingest, so prior versions are deleted before the new row is
        written — this honors KnowledgeOntology's "latest version per
        tenant wins" contract and prevents stale/partial ontologies (e.g.
        from a failed run) from accumulating. Delete + create run in one
        transaction so a concurrent reader never observes zero rows.
        """
        from django.db import transaction

        tid = _tenant_str(tenant_id)
        oid = str(uuid.uuid4())
        with transaction.atomic():
            KnowledgeOntology.objects.filter(tenant_id=tid).delete()
            KnowledgeOntology.objects.create(
                id=oid,
                tenant_id=tid,
                version=version,
                turtle_data=turtle_data,
                source_chunk_ids=source_chunk_ids or [],
            )
        return oid

    # ------------------------------------------------------------------
    # Search operations
    # ------------------------------------------------------------------

    def vector_search(
        self,
        query_embedding: List[float],
        top_k: int = 5,
        table: str = "knowledge_chunks",
        filters: Dict[str, Any] | None = None,
        *,
        tenant_id: str | None = None,
    ) -> List[Dict]:
        """Cosine-similarity nearest-neighbor scoped to one tenant.

        Pre-filters by tenant + metadata via ORM, then exact KNN over
        the filtered subset — the default execution model. The HNSW
        index still accelerates large-tenant cases
        because pgvector falls back to the index when the WHERE clause
        is not selective enough.
        """
        model = _TABLE_TO_MODEL.get(table)
        if model is None:
            raise ValueError(
                f"vector_search: unknown table {table!r}. "
                f"Valid: {list(_TABLE_TO_MODEL)}"
            )
        tid = _tenant_str(tenant_id)
        qs = model.objects.for_tenant(tid)
        qs = _apply_metadata_filters(qs, filters)
        qs = qs.annotate(
            distance=CosineDistance("embedding", query_embedding),
        ).order_by("distance")[:top_k]

        results = []
        for obj in qs:
            score = 1.0 - float(obj.distance)
            row = _row(obj, score=score)
            # parent_id column for parent_child strategy callers
            if hasattr(obj, "parent_id"):
                row["parent_id"] = obj.parent_id
            results.append(row)
        return results

    def bm25_search(
        self,
        query: str,
        top_k: int = 5,
        table: str = "knowledge_chunks",
        filters: Dict[str, Any] | None = None,
        *,
        tenant_id: str | None = None,
    ) -> List[Dict]:
        """tsvector full-text search scoped to one tenant."""
        from django.contrib.postgres.search import SearchQuery, SearchRank
        from django.db.models import F

        model = _TABLE_TO_MODEL.get(table)
        if model is None:
            raise ValueError(
                f"bm25_search: unknown table {table!r}. "
                f"Valid: {list(_TABLE_TO_MODEL)}"
            )
        if not hasattr(model, "content_tsv"):
            raise ValueError(
                f"bm25_search: {table!r} does not have a tsvector column"
            )

        sq = SearchQuery(query, config="english")
        tid = _tenant_str(tenant_id)
        qs = model.objects.for_tenant(tid)
        qs = _apply_metadata_filters(qs, filters)
        qs = qs.annotate(
            rank=SearchRank(F("content_tsv"), sq),
        ).filter(content_tsv=sq).order_by("-rank")[:top_k]

        results = []
        for obj in qs:
            row = _row(obj, score=float(obj.rank))
            if hasattr(obj, "parent_id"):
                row["parent_id"] = obj.parent_id
            results.append(row)
        return results

    def hybrid_search(
        self,
        query: str,
        query_embedding: List[float],
        top_k: int = 5,
        table: str = "knowledge_chunks",
        filters: Dict[str, Any] | None = None,
        rrf_k: int = 60,
        *,
        tenant_id: str | None = None,
    ) -> List[Dict]:
        """Run vector + BM25 in parallel and fuse via Reciprocal Rank Fusion."""
        vec_results = self.vector_search(
            query_embedding, top_k=top_k * 2, table=table, filters=filters,
            tenant_id=tenant_id,
        )
        bm25_results = self.bm25_search(
            query, top_k=top_k * 2, table=table, filters=filters,
            tenant_id=tenant_id,
        )

        scores: Dict[str, float] = {}
        result_map: Dict[str, Dict] = {}
        for rank, r in enumerate(vec_results, 1):
            scores[r["id"]] = scores.get(r["id"], 0.0) + 1.0 / (rrf_k + rank)
            result_map.setdefault(r["id"], r)
        for rank, r in enumerate(bm25_results, 1):
            scores[r["id"]] = scores.get(r["id"], 0.0) + 1.0 / (rrf_k + rank)
            result_map.setdefault(r["id"], r)

        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:top_k]
        return [{**result_map[cid], "score": score} for cid, score in ranked]

    def graph_search(
        self,
        query_embedding: List[float],
        top_k: int = 5,
        hops: int = 2,
        *,
        tenant_id: str | None = None,
    ) -> List[Dict]:
        """Vector-match entities then traverse the relationship graph."""
        from django.db.models import Q

        tid = _tenant_str(tenant_id)
        entity_qs = (
            KnowledgeEntity.objects.for_tenant(tid)
            .exclude(embedding__isnull=True)
            .annotate(distance=CosineDistance("embedding", query_embedding))
            .order_by("distance")[:top_k]
        )
        entities = list(entity_qs)
        if not entities:
            return []

        entity_ids = [e.id for e in entities]
        visited: set = set(entity_ids)
        frontier: List[str] = list(entity_ids)
        related_chunks: List[str] = []

        for _ in range(hops):
            if not frontier:
                break
            rels = list(
                KnowledgeRelationship.objects.for_tenant(tid)
                .filter(Q(source_entity_id__in=frontier)
                        | Q(target_entity_id__in=frontier))
                .values("source_entity_id", "target_entity_id", "source_chunk_id")
            )
            next_frontier: List[str] = []
            for r in rels:
                for sid in (r["source_entity_id"], r["target_entity_id"]):
                    if sid and sid not in visited:
                        visited.add(sid)
                        next_frontier.append(sid)
                if r.get("source_chunk_id"):
                    related_chunks.append(r["source_chunk_id"])
            frontier = next_frontier

        if not related_chunks:
            return [
                {
                    "id":          e.id,
                    "name":        e.name,
                    "entity_type": e.entity_type,
                    "description": e.description,
                    "score":       1.0 - float(e.distance),
                }
                for e in entities
            ]

        chunks_qs = KnowledgeChunk.objects.for_tenant(tid).filter(id__in=related_chunks)
        return [_row(c) for c in chunks_qs]

    def raptor_search(
        self,
        query_embedding: List[float],
        top_k: int = 5,
        *,
        tenant_id: str | None = None,
    ) -> List[Dict]:
        """Cosine search across all RAPTOR tree levels for a tenant."""
        tid = _tenant_str(tenant_id)
        qs = (
            KnowledgeRaptorNode.objects.for_tenant(tid)
            .annotate(distance=CosineDistance("embedding", query_embedding))
            .order_by("distance")[:top_k]
        )
        return [
            {
                "id":       obj.id,
                "content":  obj.content,
                "level":    obj.level,
                "metadata": obj.metadata or {},
                "score":    1.0 - float(obj.distance),
            }
            for obj in qs
        ]

    # ------------------------------------------------------------------
    # Targeted lookups
    # ------------------------------------------------------------------

    def get_chunks_by_ids(
        self,
        chunk_ids: List[str],
        *,
        tenant_id: str | None = None,
    ) -> List[Dict]:
        """Fetch base chunks by ID list, scoped to one tenant."""
        if not chunk_ids:
            return []
        tid = _tenant_str(tenant_id)
        qs = KnowledgeChunk.objects.for_tenant(tid).filter(id__in=chunk_ids)
        return [_row(c) for c in qs]

    def get_parent_chunks_by_ids(
        self,
        parent_ids: List[str],
        *,
        tenant_id: str | None = None,
    ) -> List[Dict]:
        """Fetch parent chunks by ID list, scoped to one tenant."""
        if not parent_ids:
            return []
        tid = _tenant_str(tenant_id)
        qs = KnowledgeParentChunk.objects.for_tenant(tid).filter(id__in=parent_ids)
        return [_row(c) for c in qs]

    def get_latest_ontology(
        self,
        *,
        tenant_id: str | None = None,
    ) -> Optional[Tuple[str, List[str]]]:
        """Return (turtle_data, source_chunk_ids) for the most recent
        ontology row for a tenant, or None when none has been built."""
        tid = _tenant_str(tenant_id)
        row = (
            KnowledgeOntology.objects.for_tenant(tid)
            .order_by("-version", "-created_at")
            .first()
        )
        if row is None:
            return None
        return row.turtle_data, list(row.source_chunk_ids or [])

    def get_all_chunks(
        self,
        limit: int | None = None,
        offset: int = 0,
        *,
        tenant_id: str | None = None,
    ) -> List[Dict]:
        """Stream all chunks for a tenant. Used by ingestion pipeline."""
        tid = _tenant_str(tenant_id)
        qs = KnowledgeChunk.objects.for_tenant(tid).order_by("created_at")
        if limit is not None:
            qs = qs[offset:offset + limit]
        else:
            qs = qs[offset:]
        return [_row(c) for c in qs]

    # ------------------------------------------------------------------
    # Inspection — per-source and full-corpus views
    # Powers the document detail tabs + strategy inspect pages.
    # ------------------------------------------------------------------

    def get_contextual_chunks_for_source(
        self,
        source_slug: str,
        limit: int = 50,
        offset: int = 0,
        *,
        tenant_id: str | None = None,
    ) -> List[Dict]:
        """Paginated contextual-enriched chunks for one source.

        Returned shape matches what the explorer's contextual tab needs:
        each row has the original chunk's content (via the base_chunk FK)
        alongside the LLM-written context prefix and the enriched body.
        """
        tid = _tenant_str(tenant_id)
        qs = (
            KnowledgeChunkContextual.objects.for_tenant(tid)
            .filter(metadata__source_slug=source_slug)
            .select_related("base_chunk")
            .order_by("created_at")[offset:offset + limit]
        )
        return [
            {
                "id": c.id,
                "base_chunk_id": c.base_chunk_id,
                "base_content": c.base_chunk.content if c.base_chunk else None,
                "context_prefix": c.context_prefix or "",
                "enriched_content": c.content,
                "metadata": c.metadata or {},
            }
            for c in qs
        ]

    def get_parent_child_for_source(
        self,
        source_slug: str,
        *,
        tenant_id: str | None = None,
    ) -> List[Dict]:
        """Parent chunks for one source, each with its children inline.

        Two-pass: pull parents by metadata.source_slug, then pull all
        children whose parent_id is in that set. The shape is
        list-of-parents, each with `children: [...]` for the tree view.
        """
        tid = _tenant_str(tenant_id)
        parents = list(
            KnowledgeParentChunk.objects.for_tenant(tid)
            .filter(metadata__source_slug=source_slug)
            .order_by("id")
        )
        parent_ids = [p.id for p in parents]
        children_by_parent: Dict[str, List] = {pid: [] for pid in parent_ids}
        if parent_ids:
            for child in (
                KnowledgeChildChunk.objects.for_tenant(tid)
                .filter(parent_id__in=parent_ids)
                .order_by("parent_id", "id")
            ):
                children_by_parent.setdefault(child.parent_id, []).append({
                    "id":      child.id,
                    "content": child.content,
                    "metadata": child.metadata or {},
                })
        return [
            {
                "id":       p.id,
                "content":  p.content,
                "metadata": p.metadata or {},
                "children": children_by_parent.get(p.id, []),
            }
            for p in parents
        ]

    def get_graph_for_source(
        self,
        source_slug: str,
        *,
        tenant_id: str | None = None,
    ) -> Dict[str, List]:
        """Nodes + edges for the per-document GraphRAG subgraph.

        Edges = every KnowledgeRelationship whose source_chunk_id is one
        of this source's chunks. Nodes = the union of every endpoint
        entity in those edges (entities can come from many docs; this
        view shows them all, with metadata noting how many of this
        doc's chunks reference each).

        Returns shape suitable for cytoscape.js consumption:
            {
                "nodes": [{"data": {"id", "label", "entity_type",
                                    "description", "ref_count"}}, ...],
                "edges": [{"data": {"id", "source", "target", "label",
                                    "source_chunk_id"}}, ...],
            }
        """
        tid = _tenant_str(tenant_id)
        chunk_ids = list(
            KnowledgeChunk.objects.for_tenant(tid)
            .filter(metadata__source_slug=source_slug)
            .values_list("id", flat=True)
        )
        return self._build_graph_from_chunk_ids(chunk_ids, tenant_id=tid)

    def get_graph_full(
        self,
        *,
        tenant_id: str | None = None,
        max_edges: int = 5000,
    ) -> Dict[str, List]:
        """Nodes + edges for the entire tenant's GraphRAG graph.

        Hard-caps edges (default 5000) so the visualization stays usable
        on large corpora. When the cap kicks in we return the most
        recently created edges so the user sees recent ingestion output;
        a paged version can come later if needed.
        """
        tid = _tenant_str(tenant_id)
        from django.db.models import Q

        rel_qs = (
            KnowledgeRelationship.objects.for_tenant(tid)
            .order_by("-id")[:max_edges]
        )
        rels = list(rel_qs.values(
            "id", "source_entity_id", "target_entity_id",
            "relationship", "source_chunk_id",
        ))
        if not rels:
            return {"nodes": [], "edges": [], "truncated": False}

        entity_ids = {
            eid
            for r in rels
            for eid in (r["source_entity_id"], r["target_entity_id"])
            if eid
        }
        return self._shape_graph_response(
            rels, entity_ids, tenant_id=tid,
            truncated=(rel_qs.count() == max_edges),
        )

    def _build_graph_from_chunk_ids(
        self,
        chunk_ids: List[str],
        *,
        tenant_id: str,
    ) -> Dict[str, List]:
        """Helper: given a set of chunk_ids, return the subgraph of
        relationships emanating from them + their endpoint entities."""
        if not chunk_ids:
            return {"nodes": [], "edges": [], "truncated": False}

        rels = list(
            KnowledgeRelationship.objects.for_tenant(tenant_id)
            .filter(source_chunk_id__in=chunk_ids)
            .values(
                "id", "source_entity_id", "target_entity_id",
                "relationship", "source_chunk_id",
            )
        )
        entity_ids = {
            eid
            for r in rels
            for eid in (r["source_entity_id"], r["target_entity_id"])
            if eid
        }
        return self._shape_graph_response(
            rels, entity_ids, tenant_id=tenant_id, truncated=False,
        )

    def _shape_graph_response(
        self,
        rels: List[Dict],
        entity_ids: set,
        *,
        tenant_id: str,
        truncated: bool,
    ) -> Dict[str, List]:
        """Hydrate edge + node lists into cytoscape format."""
        entities_qs = (
            KnowledgeEntity.objects.for_tenant(tenant_id)
            .filter(id__in=entity_ids)
            .values("id", "name", "entity_type", "description")
        )
        # Count how many edges each entity participates in — useful for
        # node sizing in the visualization.
        ref_counts: Dict[str, int] = {}
        for r in rels:
            for eid in (r["source_entity_id"], r["target_entity_id"]):
                if eid:
                    ref_counts[eid] = ref_counts.get(eid, 0) + 1

        nodes = [
            {
                "data": {
                    "id":          e["id"],
                    "label":       e["name"],
                    "entity_type": e["entity_type"],
                    "description": e["description"],
                    "ref_count":   ref_counts.get(e["id"], 0),
                },
            }
            for e in entities_qs
        ]
        edges = [
            {
                "data": {
                    "id":              str(r["id"]),
                    "source":          r["source_entity_id"],
                    "target":          r["target_entity_id"],
                    "label":           r["relationship"],
                    "source_chunk_id": r["source_chunk_id"],
                },
            }
            for r in rels
            if r["source_entity_id"] and r["target_entity_id"]
        ]
        return {"nodes": nodes, "edges": edges, "truncated": truncated}

    def get_entity_sources(
        self,
        entity_id: str,
        *,
        tenant_id: str | None = None,
    ) -> List[Dict]:
        """For a given entity, return the list of source documents
        whose chunks contributed to relationships involving this entity.

        Powers the "click an entity node → which docs mention it" UX.
        Returns one row per distinct source_slug with a sample of
        contributing chunks.
        """
        from django.db.models import Q

        tid = _tenant_str(tenant_id)
        # Find all relationships involving this entity
        contributing_chunk_ids = list(
            KnowledgeRelationship.objects.for_tenant(tid)
            .filter(Q(source_entity_id=entity_id) | Q(target_entity_id=entity_id))
            .exclude(source_chunk_id__isnull=True)
            .values_list("source_chunk_id", flat=True)
            .distinct()
        )
        if not contributing_chunk_ids:
            return []

        # Group those chunks by source_slug + source_title
        from django.db.models.fields.json import KeyTextTransform
        from django.db.models import Count

        sources = (
            KnowledgeChunk.objects.for_tenant(tid)
            .filter(id__in=contributing_chunk_ids)
            .annotate(
                source_slug=KeyTextTransform("source_slug", "metadata"),
                source_title=KeyTextTransform("source_title", "metadata"),
            )
            .values("source_slug", "source_title")
            .annotate(chunk_count=Count("id"))
            .order_by("-chunk_count", "source_slug")
        )
        return list(sources)

    def get_raptor_subtree_for_source(
        self,
        source_slug: str,
        max_depth: int = 5,
        *,
        tenant_id: str | None = None,
    ) -> Dict:
        """Build the RAPTOR subtree containing this source's leaves and
        every ancestor summary node along the chain up to max_depth.

        Walk: find level-0 nodes whose metadata.source_slug matches,
        then look upward — any higher-level node whose children_ids
        intersect our running set joins the result. Repeat until we
        hit max_depth or no new ancestors are found.

        Returns a flat list keyed by node id with parent pointers so
        the frontend can render whatever tree shape it wants:
            {
                "nodes": [
                    {"id", "level", "content", "metadata",
                     "children_ids", "is_source_leaf"},
                    ...
                ],
                "source_slug": ..., "max_depth_reached": N,
            }
        """
        tid = _tenant_str(tenant_id)

        # Step 1: collect this source's leaves
        leaves = list(
            KnowledgeRaptorNode.objects.for_tenant(tid)
            .filter(level=0, metadata__source_slug=source_slug)
            .values("id", "level", "content", "metadata", "children_ids")
        )
        if not leaves:
            return {"nodes": [], "source_slug": source_slug, "max_depth_reached": 0}

        accumulator = {leaf["id"]: dict(leaf, is_source_leaf=True) for leaf in leaves}
        current_ids = set(accumulator.keys())
        depth = 0

        # Step 2: walk upward, depth-bounded
        while depth < max_depth:
            ancestors = list(
                KnowledgeRaptorNode.objects.for_tenant(tid)
                .filter(level=depth + 1)
                .filter(children_ids__overlap=list(current_ids))
                .values("id", "level", "content", "metadata", "children_ids")
            )
            if not ancestors:
                break
            new_ids = set()
            for a in ancestors:
                if a["id"] not in accumulator:
                    accumulator[a["id"]] = dict(a, is_source_leaf=False)
                    new_ids.add(a["id"])
            if not new_ids:
                break
            current_ids = new_ids
            depth += 1

        return {
            "nodes": list(accumulator.values()),
            "source_slug": source_slug,
            "max_depth_reached": depth,
        }

    def get_raptor_tree_full(
        self,
        max_depth: int = 5,
        *,
        tenant_id: str | None = None,
    ) -> Dict:
        """Whole-corpus RAPTOR tree, capped at max_depth levels.

        For a 60-chunk corpus the entire tree fits comfortably; for
        production-scale corpora the cap keeps the JSON payload sane.
        """
        tid = _tenant_str(tenant_id)
        nodes = list(
            KnowledgeRaptorNode.objects.for_tenant(tid)
            .filter(level__lt=max_depth)
            .values("id", "level", "content", "metadata",
                    "parent_id", "children_ids")
        )
        max_seen = max((n["level"] for n in nodes), default=0)
        return {
            "nodes": [dict(n, is_source_leaf=(n["level"] == 0)) for n in nodes],
            "max_depth_reached": max_seen,
        }

    def get_ontology_per_source(
        self,
        source_slug: str,
        *,
        tenant_id: str | None = None,
    ) -> Dict:
        """For a source: the corpus-wide schema (TBox) plus the
        individuals contributed by this source's chunks.

        The ontology is corpus-wide and can't be partitioned by chunk
        in the RDF graph itself — but `KnowledgeOntology.source_chunk_ids`
        tracks which chunks contributed, so we can show "these
        individuals were asserted because of chunks from this source."
        """
        tid = _tenant_str(tenant_id)

        from .models import KnowledgeTenant
        tenant = KnowledgeTenant.objects.filter(id=tid).first()
        domain = (tenant.domain if tenant else {}) or {}
        ontology_cfg = domain.get("ontology") or {}

        # Schema (TBox) — same for every document under this tenant
        schema = {
            "namespace": ontology_cfg.get("namespace", ""),
            "prefix":    ontology_cfg.get("prefix", ""),
            "classes":   domain.get("entity_types") or [],
            "object_properties": domain.get("relationship_types") or [],
            "data_properties":   ontology_cfg.get("data_properties") or [],
            "relationship_typing": ontology_cfg.get("relationship_typing") or {},
        }

        # Per-source individuals — derive from KnowledgeOntology.source_chunk_ids
        latest = (
            KnowledgeOntology.objects.for_tenant(tid)
            .order_by("-version", "-created_at")
            .first()
        )
        per_source_individuals: List[str] = []
        contributed = False
        if latest:
            this_source_chunks = set(
                KnowledgeChunk.objects.for_tenant(tid)
                .filter(metadata__source_slug=source_slug)
                .values_list("id", flat=True)
            )
            ontology_chunks = set(latest.source_chunk_ids or [])
            contributing = this_source_chunks & ontology_chunks
            contributed = bool(contributing)
            # Parse the turtle to find individuals that trace back to
            # this source. The rdflib parsing happens once and lets us
            # filter by chunk_ids that contributed.
            # For v1, we just report whether this source contributed
            # and how many of its chunks did. Full individual filtering
            # would require per-chunk triple lineage (a future
            # enhancement on the partials table).
            per_source_individuals = []  # not populated; full per-source filtering would need per-chunk triple lineage (see note above)
            return {
                "schema": schema,
                "ontology_built": True,
                "version": latest.version,
                "total_triples_in_graph": None,  # rdflib parse is expensive
                "source_contributed": contributed,
                "contributing_chunk_count": len(contributing),
                "source_chunk_total": len(this_source_chunks),
            }
        return {
            "schema": schema,
            "ontology_built": False,
        }

    def get_ontology_full(
        self,
        *,
        tenant_id: str | None = None,
    ) -> Dict:
        """Full ontology view: schema + parsed individuals + sample triples.

        Parses the Turtle blob once with rdflib so we can count and
        categorize the triples. For very large ontologies this could be
        slow — the inspect page can show a 'loading…' state.
        """
        tid = _tenant_str(tenant_id)

        from .models import KnowledgeTenant
        tenant = KnowledgeTenant.objects.filter(id=tid).first()
        domain = (tenant.domain if tenant else {}) or {}
        ontology_cfg = domain.get("ontology") or {}

        schema = {
            "namespace": ontology_cfg.get("namespace", ""),
            "prefix":    ontology_cfg.get("prefix", ""),
            "classes":   domain.get("entity_types") or [],
            "object_properties": domain.get("relationship_types") or [],
            "data_properties":   ontology_cfg.get("data_properties") or [],
            "relationship_typing": ontology_cfg.get("relationship_typing") or {},
        }

        latest = (
            KnowledgeOntology.objects.for_tenant(tid)
            .order_by("-version", "-created_at")
            .first()
        )
        if not latest:
            return {"schema": schema, "ontology_built": False}

        # Parse to count + categorize
        triples_by_class: Dict[str, List[str]] = {}
        total_triples = 0
        try:
            from rdflib import Graph, Namespace, RDF
            g = Graph()
            g.parse(data=latest.turtle_data, format="turtle")
            total_triples = len(g)
            ns_uri = ontology_cfg.get("namespace") or ""
            if ns_uri:
                ns = Namespace(ns_uri)
                # For each declared class, list its instances
                for cls_name in schema["classes"]:
                    cls_uri = ns[cls_name]
                    instances = sorted({
                        str(s).replace(ns_uri, "")
                        for s, p, o in g.triples((None, RDF.type, cls_uri))
                    })
                    if instances:
                        triples_by_class[cls_name] = instances
        except Exception as e:
            logger.warning("rdflib parse failed: %s", e)

        return {
            "schema":            schema,
            "ontology_built":    True,
            "version":           latest.version,
            "total_triples":     total_triples,
            "individuals":       triples_by_class,
            "contributing_chunks": len(latest.source_chunk_ids or []),
        }

    # ------------------------------------------------------------------
    # Strategy / tenant-level settings
    # ------------------------------------------------------------------
    # `active_strategy` is stored on `KnowledgeTenant.default_strategy`.
    # `active_task_id` is derived from a `KnowledgeIngestionRun` query.

    def get_default_strategy(
        self,
        *,
        tenant_id: str | None = None,
    ) -> Optional[str]:
        """Return the tenant's configured default strategy, or None if
        the tenant doesn't exist."""
        from .models import KnowledgeTenant
        tid = _tenant_str(tenant_id)
        try:
            return KnowledgeTenant.objects.get(id=tid).default_strategy
        except KnowledgeTenant.DoesNotExist:
            return None

    def set_default_strategy(
        self,
        strategy: str,
        *,
        tenant_id: str | None = None,
    ) -> None:
        """Update the tenant's default_strategy. Raises if tenant doesn't exist."""
        from .models import KnowledgeTenant
        tid = _tenant_str(tenant_id)
        # Use update() instead of save() to avoid triggering auto_now and
        # to fail loud if the tenant doesn't exist (rows-updated == 0).
        rows = KnowledgeTenant.objects.filter(id=tid).update(default_strategy=strategy)
        if rows == 0:
            raise ValueError(
                f"set_default_strategy: tenant {tid!r} does not exist. "
                f"Register it first via knowledge_agent.registry.register_tenant()."
            )

    # ------------------------------------------------------------------
    # Utility / introspection
    # ------------------------------------------------------------------

    def validate_dimension(self, embedding_service) -> None:
        """Compare the runtime embedding dimension against the configured
        VECTOR_DIM. Raises on mismatch to prevent silent data corruption."""
        runtime_dim = embedding_service.dimension
        db_dim = self._dim
        if db_dim != runtime_dim:
            raise ValueError(
                f"Embedding dimension mismatch: model produces {runtime_dim}-d "
                f"vectors but the configured VECTOR_DIM is {db_dim}-d. "
                f"Set VECTOR_DIM={runtime_dim} (matching the embedding model) "
                f"and re-run migrations."
            )
        logger.info(
            "Dimension validation passed: runtime=%d, db=%d", runtime_dim, db_dim,
        )

    def chunk_count(self, *, tenant_id: str | None = None) -> int:
        if tenant_id is None:
            # Cross-tenant count is the default behavior for ops dashboards.
            return KnowledgeChunk.objects.count()
        return KnowledgeChunk.objects.for_tenant(tenant_id).count()

    def table_row_counts(self, *, tenant_id: str | None = None) -> Dict[str, int]:
        """Return row counts for the data tables, keyed by their legacy
        table names. When `tenant_id` is None, counts span all tenants."""
        models_by_name = {
            "knowledge_chunks":            KnowledgeChunk,
            "knowledge_chunks_contextual": KnowledgeChunkContextual,
            "knowledge_parent_chunks":     KnowledgeParentChunk,
            "knowledge_child_chunks":      KnowledgeChildChunk,
            "knowledge_entities":          KnowledgeEntity,
            "knowledge_relationships":     KnowledgeRelationship,
            "knowledge_raptor_nodes":      KnowledgeRaptorNode,
            "knowledge_ontology":          KnowledgeOntology,
        }
        if tenant_id is None:
            return {name: model.objects.count() for name, model in models_by_name.items()}
        return {
            name: model.objects.for_tenant(tenant_id).count()
            for name, model in models_by_name.items()
        }

    def check_strategy_readiness(
        self,
        strategy_name: str,
        *,
        tenant_id: str | None = None,
    ) -> Dict[str, Any]:
        """Check whether a strategy's required tables have any data for a tenant."""
        from .strategies import STRATEGY_DEPENDENCIES

        deps = STRATEGY_DEPENDENCIES.get(strategy_name)
        if not deps:
            return {"ready": True, "strategy": strategy_name}

        counts = self.table_row_counts(tenant_id=tenant_id)
        missing = [t for t in deps["tables"] if counts.get(t, 0) == 0]
        return {
            "ready":           len(missing) == 0,
            "strategy":        strategy_name,
            "missing_tables":  missing,
            "required_steps":  deps["steps"] if missing else [],
            "table_counts":    {t: counts.get(t, 0) for t in deps["tables"]},
        }

    # ------------------------------------------------------------------
    # Source / document queries
    # ------------------------------------------------------------------

    def get_source_stats(self, *, tenant_id: str | None = None) -> List[Dict]:
        """Aggregate chunk counts per source slug. When tenant_id is None,
        spans all tenants (ops dashboard use case)."""
        from django.db.models.fields.json import KeyTextTransform
        from django.db.models import Count, Max, Min

        qs = KnowledgeChunk.objects.all()
        if tenant_id is not None:
            qs = qs.filter(tenant_id=_tenant_str(tenant_id))
        qs = (
            qs.annotate(
                source_slug=          KeyTextTransform("source_slug",           "metadata"),
                source_title=         KeyTextTransform("source_title",          "metadata"),
                source_category=      KeyTextTransform("source_category",       "metadata"),
                source_category_label=KeyTextTransform("source_category_label", "metadata"),
            )
            .values(
                "source_slug", "source_title", "source_category", "source_category_label",
            )
            .annotate(
                chunk_count=    Count("id"),
                first_ingested= Min("created_at"),
                last_ingested=  Max("created_at"),
            )
            .order_by("source_category", "source_slug")
        )
        return list(qs)

    def get_chunks_for_source(
        self,
        source_slug: str,
        limit: int = 50,
        offset: int = 0,
        *,
        tenant_id: str | None = None,
    ) -> List[Dict]:
        """Get chunks for a specific source with pagination, ordered by page."""
        from django.db.models.fields.json import KeyTextTransform
        from django.db.models.functions import Cast
        from django.db.models import IntegerField

        qs = KnowledgeChunk.objects.filter(metadata__source_slug=source_slug)
        if tenant_id is not None:
            qs = qs.filter(tenant_id=_tenant_str(tenant_id))
        qs = (
            qs.annotate(
                _page=Cast(
                    KeyTextTransform("page_number", "metadata"),
                    output_field=IntegerField(),
                ),
            )
            .order_by("_page", "created_at")[offset:offset + limit]
        )
        return [
            {"id": c.id, "content": c.content, "metadata": c.metadata or {},
             "created_at": c.created_at}
            for c in qs
        ]

    def count_chunks_for_source(
        self,
        source_slug: str,
        *,
        tenant_id: str | None = None,
    ) -> int:
        qs = KnowledgeChunk.objects.filter(metadata__source_slug=source_slug)
        if tenant_id is not None:
            qs = qs.filter(tenant_id=_tenant_str(tenant_id))
        return qs.count()

    def get_derived_counts_for_source(
        self,
        source_slug: str,
        *,
        tenant_id: str | None = None,
    ) -> Dict[str, int]:
        """Count contextual and parent/child chunks for a source."""
        def _scoped(model):
            qs = model.objects.filter(metadata__source_slug=source_slug)
            if tenant_id is not None:
                qs = qs.filter(tenant_id=_tenant_str(tenant_id))
            return qs.count()

        return {
            "contextual": _scoped(KnowledgeChunkContextual),
            "parents":    _scoped(KnowledgeParentChunk),
            "children":   _scoped(KnowledgeChildChunk),
        }

    def get_chunk_by_id(
        self,
        chunk_id: str,
        *,
        tenant_id: str | None = None,
    ) -> Optional[Dict]:
        qs = KnowledgeChunk.objects.filter(id=chunk_id)
        if tenant_id is not None:
            qs = qs.filter(tenant_id=_tenant_str(tenant_id))
        c = qs.first()
        if c is None:
            return None
        return {
            "id":         c.id,
            "content":    c.content,
            "metadata":   c.metadata or {},
            "created_at": c.created_at,
        }
