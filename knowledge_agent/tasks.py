"""
Celery task suite for the knowledge_agent's ingestion pipeline.

Instead of one monolithic `run_ingestion_task`, the suite exposes
fine-grained per-PDF and per-chunk tasks plus the orchestrator canvas
(group/chord/chain) that wires them into a DAG. This lets a
multi-worker Celery deployment fan out ingestion work in parallel:

    Phase 1 (per-PDF):
        chunk_and_embed × N   ┐    group  → chord callback fires
        build_parent_child × N ┘            when all PDFs done
                              │
                              ▼
    Phase 2 (per-chunk, 4-way fan-out, all in parallel):
        contextual chord (1 task per chunk)
        graphrag chord   (1 task per chunk)  ← atomic upserts, drops cache
        raptor recursion (chord per tree level)
        ontology chord   (parallel extract + serial assemble)
                              │
                              ▼
    finalize_ingestion_run    — clears dirty flags, stamps
                                KnowledgeSource.last_ingested_at,
                                marks KnowledgeIngestionRun complete

Run synchronously for tests / debugging by setting
    settings.CELERY_TASK_ALWAYS_EAGER = True
which makes every .apply_async() execute in-process. Same code path
either way.
"""

from __future__ import annotations

import logging
import traceback
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from celery import chord, group, shared_task

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Per-PDF tasks (Phase 1)
# ---------------------------------------------------------------------------

@shared_task(queue="knowledge_agent", bind=True, time_limit=3600)
def ingest_pdf_task(self, tenant_id: str, source_id: int, pdf_path: str) -> int:
    """Parse → chunk → embed one PDF for a tenant.

    Returns the number of chunks created. Idempotent on chunk_id (bulk
    insert uses ON CONFLICT DO NOTHING).
    """
    from .backends import get_embedding_backend
    from .ingestion.chunker import DocumentChunker
    from .ingestion.downloader import slugify
    from .ingestion.pdf_parser import PDFParser
    from .store import VectorStore

    try:
        parser = PDFParser()
        chunker = DocumentChunker()
        embedding = get_embedding_backend()
        store = VectorStore()

        doc = parser.parse(pdf_path)
        slug = _filename_to_slug(doc.filename)

        from .models import KnowledgeSource
        src = KnowledgeSource.objects.get(id=source_id)
        category = src.category or slugify(src.label or src.path)
        category_label = src.label or src.path

        chunks = chunker.chunk_fixed(
            doc, source_slug=slug, source_title=doc.title,
        )

        # Apply the per-doc chunk cap if set (KNOWLEDGE_MAX_CHUNKS_PER_DOC).
        # Lets a smoke test run end-to-end on a tiny corpus.
        from . import config as ka_config
        if ka_config.MAX_CHUNKS_PER_DOC > 0:
            chunks = chunks[:ka_config.MAX_CHUNKS_PER_DOC]

        for c in chunks:
            c.metadata["source_category"] = category
            c.metadata["source_category_label"] = category_label

        # Embed in batches of 32 (already efficient at the Ollama API level)
        batch_size = 32
        for i in range(0, len(chunks), batch_size):
            batch = chunks[i:i + batch_size]
            texts = [c.content for c in batch]
            embeddings = embedding.embed_batch(texts)
            rows = [
                (c.id, c.content.replace("\x00", ""), emb, c.metadata)
                for c, emb in zip(batch, embeddings)
            ]
            store.insert_chunks_bulk(rows, tenant_id=tenant_id)

        logger.info("ingest_pdf_task: %s → %d chunks", pdf_path, len(chunks))
        return len(chunks)
    except Exception:
        # Don't re-raise — one corrupt PDF (e.g. mupdf "cycle in page
        # tree") would abort the entire phase-1 chord, which in turn
        # prevents start_phase_2 from firing. Log + return 0 so the
        # chord sees a successful task and continues.
        logger.exception("ingest_pdf_task failed (skipping) for %s", pdf_path)
        return 0


@shared_task(queue="knowledge_agent", bind=True, time_limit=3600)
def build_parent_child_pdf_task(self, tenant_id: str, source_id: int, pdf_path: str) -> int:
    """Build parent/child chunks for one PDF. Returns parent count."""
    from .backends import get_embedding_backend
    from .ingestion.chunker import DocumentChunker
    from .ingestion.downloader import slugify
    from .ingestion.pdf_parser import PDFParser
    from .store import VectorStore

    try:
        parser = PDFParser()
        chunker = DocumentChunker()
        embedding = get_embedding_backend()
        store = VectorStore()

        doc = parser.parse(pdf_path)
        slug = _filename_to_slug(doc.filename)

        from .models import KnowledgeSource
        src = KnowledgeSource.objects.get(id=source_id)
        category = src.category or slugify(src.label or src.path)
        category_label = src.label or src.path

        parents, children = chunker.chunk_parent_child(
            doc, source_slug=slug, source_title=doc.title,
        )

        # Apply the per-doc chunk cap if set. For parent/child we keep
        # parents up to the cap, then drop children that reference
        # discarded parents — keeps the FK relationship intact.
        from . import config as ka_config
        if ka_config.MAX_CHUNKS_PER_DOC > 0:
            parents = parents[:ka_config.MAX_CHUNKS_PER_DOC]
            kept = {p.id for p in parents}
            children = [c for c in children if c.parent_id in kept]

        for p in parents:
            p.metadata["source_category"] = category
            p.metadata["source_category_label"] = category_label
        for c in children:
            c.metadata["source_category"] = category
            c.metadata["source_category_label"] = category_label

        stored_parent_ids = set()
        for p in parents:
            try:
                emb = embedding.embed_text(p.content)
                store.insert_parent_chunk(
                    p.content, emb, p.metadata,
                    chunk_id=p.id, tenant_id=tenant_id,
                )
                stored_parent_ids.add(p.id)
            except Exception as e:
                logger.error(
                    "Failed parent chunk %s (%d chars): %s",
                    p.id[:8], len(p.content), e,
                )

        for c in children:
            if c.parent_id not in stored_parent_ids:
                continue
            try:
                emb = embedding.embed_text(c.content)
                store.insert_child_chunk(
                    c.parent_id, c.content, emb, c.metadata,
                    tenant_id=tenant_id,
                )
            except Exception as e:
                logger.error("Failed child chunk %s: %s", c.id[:8], e)

        logger.info(
            "build_parent_child_pdf_task: %s → %d parents",
            pdf_path, len(stored_parent_ids),
        )
        return len(stored_parent_ids)
    except Exception:
        # Same rationale as ingest_pdf_task — never abort the phase-1
        # chord because of one bad PDF.
        logger.exception("build_parent_child_pdf_task failed (skipping) for %s", pdf_path)
        return 0


# ---------------------------------------------------------------------------
# Per-chunk tasks (Phase 2 — 4-way fan-out)
# ---------------------------------------------------------------------------

@shared_task(queue="knowledge_agent", bind=True, time_limit=300)
def build_contextual_chunk_task(self, tenant_id: str, chunk_id: str) -> Optional[str]:
    """Enrich one chunk with LLM-written context prefix and insert."""
    from .backends import get_embedding_backend, get_llm_backend
    from .ingestion._llm_output import strip_llm_preamble
    from .ingestion.pipeline import _CONTEXTUAL_PROMPT
    from .store import VectorStore

    store = VectorStore()
    chunk = store.get_chunk_by_id(chunk_id, tenant_id=tenant_id)
    if not chunk:
        return None

    try:
        llm = get_llm_backend()
        emb = get_embedding_backend()
        meta = chunk["metadata"] or {}

        raw_context = llm.generate(_CONTEXTUAL_PROMPT.format(
            source_title=meta.get("source_title", ""),
            section_title=meta.get("section_title", ""),
            content=chunk["content"][:1500],
        ))
        context = strip_llm_preamble(raw_context)
        enriched = (
            f"Context:\n{context}\n\n"
            f"Source:\n{chunk['content']}"
        )
        enriched_emb = emb.embed_text(enriched)

        store.insert_contextual_chunk(
            base_chunk_id=chunk_id,
            content=enriched,
            context_prefix=context,
            embedding=enriched_emb,
            metadata=meta,
            tenant_id=tenant_id,
        )
        return chunk_id
    except Exception:
        logger.exception("build_contextual_chunk_task failed for %s", chunk_id)
        # Don't re-raise — one chunk's failure shouldn't abort the whole chord
        return None


@shared_task(queue="knowledge_agent", bind=True, time_limit=300,
             autoretry_for=(Exception,), retry_backoff=True, max_retries=2)
def build_graph_chunk_task(self, tenant_id: str, chunk_id: str) -> Optional[Dict]:
    """Extract entities + relationships from one chunk via LLM.

    Concurrent-safe: relies on the (tenant, name, entity_type) unique
    constraint to dedupe entities under concurrent workers. Each
    upsert is wrapped in transaction.atomic (handled inside
    store.insert_entity / store.insert_relationship).
    """
    import json
    import re

    from .backends import get_embedding_backend, get_llm_backend
    from .ingestion._acronyms import canonicalize_name, find_acronym_pairs, format_acronym_hints
    from .ingestion.graph_builder import _EXTRACT_PROMPT
    from .store import VectorStore
    from .strategies import load_tenant_domain

    store = VectorStore()
    chunk = store.get_chunk_by_id(chunk_id, tenant_id=tenant_id)
    if not chunk:
        return None

    domain = load_tenant_domain(tenant_id)
    entity_types = domain.get("entity_types") or []
    relationship_types = domain.get("relationship_types") or []

    llm = get_llm_backend()
    embedding = get_embedding_backend()

    acronym_pairs = find_acronym_pairs(chunk["content"])
    hints = format_acronym_hints(acronym_pairs)
    hints_block = (
        f"\nKnown acronym definitions in this chunk:\n{hints}\n"
        if hints else ""
    )

    prompt = _EXTRACT_PROMPT.format(
        entity_types=", ".join(entity_types) or "(unspecified)",
        relationship_types=", ".join(relationship_types) or "(unspecified)",
        acronym_hints_block=hints_block,
        text=chunk["content"][:2000],
    )
    try:
        raw = llm.generate(prompt)
    except Exception:
        logger.exception("graph LLM call failed for %s", chunk_id)
        return None

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", raw)
        if not match:
            return None
        try:
            data = json.loads(match.group())
        except json.JSONDecodeError:
            return None

    entities_inserted = 0
    relationships_inserted = 0
    entity_name_to_id: Dict[str, str] = {}

    for ent in data.get("entities", []) or []:
        raw_name = (ent.get("name") or "").strip()
        etype = (ent.get("type") or "").strip()
        desc = ent.get("description") or ""
        llm_aliases = ent.get("aliases") or []
        if not raw_name or not etype:
            continue

        canonical = canonicalize_name(raw_name, acronym_pairs)
        aliases = {a.strip() for a in llm_aliases if isinstance(a, str) and a.strip()}
        if canonical != raw_name:
            aliases.add(raw_name)
        for acr, full in acronym_pairs.items():
            if full.lower() == canonical.lower():
                aliases.add(acr)

        name = canonical
        try:
            emb = embedding.embed_text(f"{etype}: {name}. {desc}")
            eid = store.insert_entity(
                name=name, entity_type=etype, description=desc,
                embedding=emb,
                metadata={"aliases": sorted(aliases)} if aliases else None,
                tenant_id=tenant_id,
            )
            if aliases:
                store.merge_entity_aliases(eid, sorted(aliases))
            entity_name_to_id[name.lower()] = eid
            for alias in aliases:
                entity_name_to_id[alias.lower()] = eid
            entities_inserted += 1
        except Exception as e:
            logger.warning("entity upsert failed (%s, %s): %s", name, etype, e)

    for rel in data.get("relationships", []) or []:
        src_raw = (rel.get("source") or "").strip()
        tgt_raw = (rel.get("target") or "").strip()
        rel_type = (rel.get("relationship") or "").strip()
        if not src_raw or not tgt_raw or not rel_type:
            continue
        src_name = canonicalize_name(src_raw, acronym_pairs).lower()
        tgt_name = canonicalize_name(tgt_raw, acronym_pairs).lower()
        src_id = entity_name_to_id.get(src_name) or entity_name_to_id.get(src_raw.lower())
        tgt_id = entity_name_to_id.get(tgt_name) or entity_name_to_id.get(tgt_raw.lower())
        if not (src_id and tgt_id):
            continue
        try:
            store.insert_relationship(
                source_id=src_id, target_id=tgt_id,
                relationship=rel_type, source_chunk_id=chunk_id,
                tenant_id=tenant_id,
            )
            relationships_inserted += 1
        except Exception as e:
            logger.warning(
                "relationship upsert failed (%s → %s, %s): %s",
                src_raw, tgt_raw, rel_type, e,
            )

    return {"entities": entities_inserted, "relationships": relationships_inserted}


@shared_task(queue="knowledge_agent", bind=True, time_limit=300)
def extract_ontology_triples_task(self, tenant_id: str, chunk_id: str, run_id: int) -> str:
    """LLM-classify one chunk into ontology individuals.

    Persists the extracted triples to KnowledgeIngestionPartial so the
    assemble step can survive Celery's CELERY_RESULT_EXPIRES window
    (which would otherwise lose per-task payloads during a long pipeline).
    Returns only the chunk_id so chord member-counting still works.
    """
    import json
    import re

    from .backends import get_llm_backend
    from .ingestion._acronyms import canonicalize_name, find_acronym_pairs, format_acronym_hints
    from .ingestion.ontology_builder import _CLASSIFY_PROMPT
    from .models import KnowledgeIngestionPartial
    from .store import VectorStore
    from .strategies import load_tenant_domain

    store = VectorStore()
    chunk = store.get_chunk_by_id(chunk_id, tenant_id=tenant_id)
    if not chunk:
        return chunk_id

    domain = load_tenant_domain(tenant_id)
    entity_types = domain.get("entity_types") or []
    relationship_types = domain.get("relationship_types") or []
    data_properties = (domain.get("ontology") or {}).get("data_properties") or []

    if not entity_types:
        return chunk_id

    acronym_pairs = find_acronym_pairs(chunk["content"])
    hints = format_acronym_hints(acronym_pairs)
    hints_block = (
        f"\nKnown acronym definitions in this chunk:\n{hints}\n"
        if hints else ""
    )

    llm = get_llm_backend()
    prompt = _CLASSIFY_PROMPT.format(
        classes=", ".join(entity_types),
        relationships=", ".join(relationship_types) or "(unspecified)",
        acronym_hints_block=hints_block,
        text=chunk["content"][:2000],
    )
    try:
        raw = llm.generate(prompt)
    except Exception:
        logger.exception("ontology LLM call failed for %s", chunk_id)
        return chunk_id

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", raw)
        if not match:
            return chunk_id
        try:
            data = json.loads(match.group())
        except json.JSONDecodeError:
            return chunk_id

    # Build triple list. Each tuple becomes [s, p, o] in JSON (no tuple
    # type in JSON, so we use lists). The "LBL:" prefix on objects marks
    # rdfs:label literals for the assembler.
    triples: List[List[str]] = []
    for ind in data.get("individuals", []) or []:
        raw_name = (ind.get("name") or "").strip()
        cls_name = (ind.get("class") or "").strip()
        llm_aliases = ind.get("aliases") or []
        if not raw_name or cls_name not in entity_types:
            continue

        canonical = canonicalize_name(raw_name, acronym_pairs)
        aliases = {a.strip() for a in llm_aliases if isinstance(a, str) and a.strip()}
        if canonical != raw_name:
            aliases.add(raw_name)
        for acr, full in acronym_pairs.items():
            if full.lower() == canonical.lower():
                aliases.add(acr)

        safe_name = canonical.replace(" ", "_").replace("/", "_")
        triples.append([safe_name, "rdf:type", cls_name])
        triples.append([safe_name, "rdfs:label", f"LIT:{canonical}"])
        for alias in sorted(aliases):
            triples.append([safe_name, "rdfs:label", f"LIT:{alias}"])

        props = ind.get("properties") or {}
        for prop_name in data_properties:
            if prop_name in props and props[prop_name]:
                triples.append([safe_name, prop_name, f"LIT:{props[prop_name]}"])

        for rel in ind.get("relationships") or []:
            prop = rel.get("property") or ""
            target_raw = (rel.get("target") or "").strip()
            if prop and target_raw and prop in relationship_types:
                target = canonicalize_name(target_raw, acronym_pairs)
                safe_target = target.replace(" ", "_").replace("/", "_")
                triples.append([safe_name, prop, safe_target])

    # Persist to the durable partial-results table so the assemble step
    # doesn't depend on Celery's result backend retaining this payload.
    KnowledgeIngestionPartial.objects.create(
        tenant_id=tenant_id,
        run_id=run_id,
        branch="ontology",
        chunk_id=chunk_id,
        payload={"triples": triples},
    )
    return chunk_id


@shared_task(queue="knowledge_agent", bind=True, time_limit=300)
def build_raptor_leaf_task(self, tenant_id: str, chunk_id: str) -> Optional[Dict]:
    """Insert one RAPTOR leaf (level 0) and return (node_id, embedding)
    for the next-level clustering."""
    from .backends import get_embedding_backend
    from .store import VectorStore

    store = VectorStore()
    chunk = store.get_chunk_by_id(chunk_id, tenant_id=tenant_id)
    if not chunk:
        return None

    try:
        emb = get_embedding_backend().embed_text(chunk["content"])
        nid = store.insert_raptor_node(
            content=chunk["content"],
            embedding=emb,
            level=0,
            metadata=chunk["metadata"],
            tenant_id=tenant_id,
        )
        return {"node_id": nid, "embedding": emb}
    except Exception:
        logger.exception("build_raptor_leaf_task failed for %s", chunk_id)
        return None


@shared_task(queue="knowledge_agent", bind=True, time_limit=300)
def build_raptor_summary_task(self, tenant_id: str, child_node_ids: List[str], level: int) -> Optional[Dict]:
    """Summarize one cluster of child RAPTOR nodes, embed the summary,
    insert as a level-`level` parent. Returns (node_id, embedding)."""
    from .backends import get_embedding_backend, get_llm_backend
    from .ingestion._llm_output import strip_llm_preamble
    from .ingestion.raptor_builder import _DEFAULT_DOMAIN_HINT, _SUMMARIZE_PROMPT
    from .models import KnowledgeRaptorNode
    from .store import VectorStore
    from .strategies import load_tenant_domain

    store = VectorStore()
    domain_hint = (load_tenant_domain(tenant_id).get("hint")) or _DEFAULT_DOMAIN_HINT

    # Pull the child nodes' content for the summary prompt
    children = (
        KnowledgeRaptorNode.objects.for_tenant(tenant_id)
        .filter(id__in=child_node_ids)
        .values_list("content", flat=True)
    )
    combined = "\n---\n".join((c or "")[:500] for c in list(children)[:5])

    try:
        raw_summary = get_llm_backend().generate(_SUMMARIZE_PROMPT.format(
            domain_hint=domain_hint, texts=combined,
        ))
        summary = strip_llm_preamble(raw_summary)
        summary_emb = get_embedding_backend().embed_text(summary)
        nid = store.insert_raptor_node(
            content=summary,
            embedding=summary_emb,
            level=level,
            children_ids=child_node_ids,
            metadata={"cluster_size": len(child_node_ids)},
            tenant_id=tenant_id,
        )
        return {"node_id": nid, "embedding": summary_emb}
    except Exception:
        logger.exception("build_raptor_summary_task failed at level %d", level)
        return None


# ---------------------------------------------------------------------------
# Aggregation callbacks
# ---------------------------------------------------------------------------

@shared_task(queue="knowledge_agent", bind=True)
def assemble_ontology_task(self, _chunk_ids: List[str], tenant_id: str, run_id: int) -> int:
    """Aggregate per-chunk triples from KnowledgeIngestionPartial into
    a single rdflib graph, serialize to turtle, insert, then truncate
    the partial rows.

    Decoupled from Celery's result backend: even if every per-chunk
    task's Celery payload expired from Redis, the triples are durable
    in Postgres until this step succeeds.
    """
    from django.db import transaction

    from .models import KnowledgeIngestionPartial
    from .store import VectorStore
    from .strategies import load_tenant_domain

    try:
        from rdflib import Graph, Literal, Namespace, OWL, RDF, RDFS
    except ImportError:
        logger.error("rdflib not installed — cannot assemble ontology")
        return 0

    domain = load_tenant_domain(tenant_id)
    ontology = domain.get("ontology") or {}
    namespace = ontology.get("namespace", "http://example.com/ontology#")
    prefix = ontology.get("prefix", "ex")
    entity_types = domain.get("entity_types") or []
    relationship_types = domain.get("relationship_types") or []
    relationship_typing = ontology.get("relationship_typing") or {}
    data_properties = ontology.get("data_properties") or []

    ns = Namespace(namespace)
    g = Graph()
    g.bind(prefix, ns)
    g.bind("owl", OWL)

    # TBox: classes
    for cls in entity_types:
        cls_uri = ns[cls]
        g.add((cls_uri, RDF.type, OWL.Class))
        g.add((cls_uri, RDFS.label, Literal(cls)))

    # TBox: object properties (with optional typing)
    for rel in relationship_types:
        prop_uri = ns[rel]
        g.add((prop_uri, RDF.type, OWL.ObjectProperty))
        typing = relationship_typing.get(rel)
        if isinstance(typing, dict):
            dom, rng = typing.get("domain"), typing.get("range")
        elif isinstance(typing, (list, tuple)) and len(typing) == 2:
            dom, rng = typing
        else:
            dom, rng = None, None
        if dom and rng:
            g.add((prop_uri, RDFS.domain, ns[dom]))
            g.add((prop_uri, RDFS.range, ns[rng]))

    # TBox: data properties
    for prop in data_properties:
        g.add((ns[prop], RDF.type, OWL.DatatypeProperty))

    # ABox: triples from the durable partial-results table
    # Prefixed-name predicates resolve to their canonical RDF URIs
    # instead of being treated as tenant-namespace properties.
    prefixed_predicates = {
        "rdf:type": RDF.type,
        "rdfs:label": RDFS.label,
    }
    source_chunk_ids: List[str] = []
    partials = KnowledgeIngestionPartial.objects.filter(
        tenant_id=tenant_id, run_id=run_id, branch="ontology",
    ).values("chunk_id", "payload")

    for row in partials:
        chunk_id = row["chunk_id"]
        if chunk_id:
            source_chunk_ids.append(chunk_id)
        triples = (row["payload"] or {}).get("triples") or []
        for triple in triples:
            if not (isinstance(triple, (list, tuple)) and len(triple) == 3):
                continue
            s, p, o = triple
            try:
                s_uri = ns[s]
                pred = prefixed_predicates.get(p)
                if pred is not None:
                    if isinstance(o, str) and o.startswith("LIT:"):
                        g.add((s_uri, pred, Literal(o[4:])))
                    else:
                        g.add((s_uri, pred, ns[o]))
                elif isinstance(o, str) and o.startswith("LIT:"):
                    g.add((s_uri, ns[p], Literal(o[4:])))
                else:
                    g.add((s_uri, ns[p], ns[o]))
            except Exception as e:
                logger.debug("triple skip (%s, %s, %s): %s", s, p, o, e)

    turtle = g.serialize(format="turtle")
    logger.info(
        "assemble_ontology_task: tenant=%s run=%d %d triples from %d partial rows",
        tenant_id, run_id, len(g), len(source_chunk_ids),
    )

    # Insert ontology + truncate partials atomically — either both happen
    # or neither, so a retry can rerun from a clean state.
    with transaction.atomic():
        VectorStore().insert_ontology(
            turtle_data=turtle,
            source_chunk_ids=source_chunk_ids,
            version=1,
            tenant_id=tenant_id,
        )
        # Reclaim the scratch space — these rows have served their purpose.
        deleted, _ = KnowledgeIngestionPartial.objects.filter(
            tenant_id=tenant_id, run_id=run_id, branch="ontology",
        ).delete()
        logger.info(
            "assemble_ontology_task: deleted %d partial rows", deleted,
        )
    return len(g)


@shared_task(queue="knowledge_agent", bind=True)
def finalize_ingestion_run(self, _phase_2_results, tenant_id: str, run_id: int) -> Dict:
    """Final callback after all of phase 2 completes.

    - Marks KnowledgeIngestionRun as completed
    - Clears the tenant's dirty flags
    - Stamps KnowledgeSource.last_ingested_at for every source on the tenant
    """
    from .models import KnowledgeIngestionRun, KnowledgeSource, KnowledgeTenant
    from .store import VectorStore

    now = datetime.now(timezone.utc)

    try:
        run = KnowledgeIngestionRun.objects.get(id=run_id)
        run.status = "completed"
        run.completed_at = now
        run.chunks_created = VectorStore().chunk_count(tenant_id=tenant_id)
        run.save()

        # Clear all three dirty flags — this run touched every pipeline.
        tenant = KnowledgeTenant.objects.get(id=tenant_id)
        tenant.raptor_dirty = False
        tenant.graphrag_dirty = False
        tenant.ontology_dirty = False
        tenant.save()

        # Stamp source timestamps. Per Q6 — this lives here so the
        # bookkeeping happens regardless of whether the dispatcher
        # waited synchronously or fired and forgot.
        KnowledgeSource.objects.filter(tenant=tenant).update(last_ingested_at=now)

        logger.info(
            "Ingestion completed: tenant=%s run=%d chunks=%d",
            tenant_id, run_id, run.chunks_created,
        )
        return {"status": "completed", "chunks": run.chunks_created}
    except Exception:
        logger.exception("finalize_ingestion_run failed")
        # Surface as failure on the run row even if we crash here.
        try:
            run = KnowledgeIngestionRun.objects.get(id=run_id)
            run.status = "failed"
            run.completed_at = now
            run.error_message = traceback.format_exc()
            run.save()
        except Exception:
            pass
        raise


# A trivial no-op callback for chords whose results we just want to
# aggregate-and-discard. Celery requires a callback signature for chords.
@shared_task(queue="knowledge_agent")
def noop_chord_callback(_results) -> str:
    return "ok"


# ---------------------------------------------------------------------------
# Phase orchestrators
# ---------------------------------------------------------------------------

@shared_task(queue="knowledge_agent", bind=True)
def start_phase_2(self, _phase_1_results, tenant_id: str, run_id: int, steps: List[str]) -> str:
    """Phase 1 chord callback. Looks up the chunks that phase 1
    populated, then fans out 4-way for {contextual, graph, raptor,
    ontology} — whichever are listed in `steps`. Each branch is itself
    a chord. The final finalize callback fires when ALL branches done.
    """
    from .models import KnowledgeChunk

    chunk_ids = list(
        KnowledgeChunk.objects.for_tenant(tenant_id).values_list("id", flat=True)
    )
    if not chunk_ids:
        # Nothing to do — finalize immediately.
        finalize_ingestion_run.apply_async(args=(None, tenant_id, run_id))
        return "no_chunks"

    # Build chord SIGNATURES (not invocations). Using the `body=` kwarg
    # form keeps each chord as an unsent signature so the outer chord
    # can wrap them; the chord-of-chords pattern dispatches everything
    # in one go via the outer .apply_async() at the bottom.
    branches = []

    if "build_contextual" in steps:
        branches.append(chord(
            group(build_contextual_chunk_task.s(tenant_id, cid) for cid in chunk_ids),
            body=noop_chord_callback.s(),
        ))
    if "build_graph" in steps:
        branches.append(chord(
            group(build_graph_chunk_task.s(tenant_id, cid) for cid in chunk_ids),
            body=noop_chord_callback.s(),
        ))
    if "build_ontology" in steps:
        # Pass run_id so per-chunk tasks can persist their triples to
        # KnowledgeIngestionPartial — the assemble step then reads from
        # there, avoiding the CELERY_RESULT_EXPIRES race.
        branches.append(chord(
            group(
                extract_ontology_triples_task.s(tenant_id, cid, run_id)
                for cid in chunk_ids
            ),
            body=assemble_ontology_task.s(tenant_id, run_id),
        ))
    if "build_raptor" in steps:
        branches.append(chord(
            group(build_raptor_leaf_task.s(tenant_id, cid) for cid in chunk_ids),
            body=build_raptor_level_task.s(tenant_id, level=1),
        ))

    if not branches:
        finalize_ingestion_run.apply_async(args=(None, tenant_id, run_id))
        return "no_phase_2_branches"

    # Chord-of-chords: outer chord waits for all 4 inner chords to
    # complete (each with its own per-chunk group), then fires
    # finalize_ingestion_run.
    chord(
        group(branches),
        body=finalize_ingestion_run.s(tenant_id, run_id),
    ).apply_async()
    return "phase_2_dispatched"


@shared_task(queue="knowledge_agent", bind=True)
def build_raptor_level_task(self, child_results: List[Dict], tenant_id: str, level: int) -> str:
    """Recursive RAPTOR level orchestrator.

    Receives the (node_id, embedding) results from the previous level's
    chord. Runs k-means clustering, spawns cluster-summary tasks for
    the next level. Recursion terminates when the cluster count falls
    below the threshold.
    """
    import numpy as np
    from sklearn.cluster import KMeans

    MAX_CLUSTER_SIZE = 10
    MIN_NODES_TO_STOP = 5

    # Filter out None results from failed leaf tasks
    valid = [r for r in (child_results or []) if r and r.get("node_id")]
    if len(valid) < MIN_NODES_TO_STOP:
        return f"raptor_complete_at_level_{level - 1}_n={len(valid)}"

    n = len(valid)
    n_clusters = max(1, n // MAX_CLUSTER_SIZE)
    if n_clusters >= n:
        return f"raptor_complete_at_level_{level - 1}_no_clustering"

    node_ids = [r["node_id"] for r in valid]
    embeddings = np.array([r["embedding"] for r in valid])

    kmeans = KMeans(n_clusters=n_clusters, random_state=42, n_init="auto")
    labels = kmeans.fit_predict(embeddings)

    # Bucket child nodes by cluster
    clusters: Dict[int, List[str]] = {}
    for nid, label in zip(node_ids, labels):
        clusters.setdefault(int(label), []).append(nid)

    # Spawn summary tasks; chord callback recurses into the next level
    summary_tasks = [
        build_raptor_summary_task.s(tenant_id, list(cluster_ids), level)
        for cluster_ids in clusters.values()
    ]
    chord(group(summary_tasks))(
        build_raptor_level_task.s(tenant_id, level + 1)
    )
    return f"raptor_level_{level}_dispatched_clusters={len(clusters)}"


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

@shared_task(queue="knowledge_agent", bind=True)
def ingest_tenant_task(
    self,
    tenant_id: str,
    source_ids: Optional[List[int]] = None,
    steps: Optional[List[str]] = None,
    run_id: Optional[int] = None,
) -> str:
    """Top-level orchestrator. Builds the per-PDF group → chord callback
    → 4-way fan-out → finalize DAG and dispatches it.

    The `run_id` argument lets the management command create the
    KnowledgeIngestionRun row first (so the user sees it in
    `check_knowledge` immediately) and pass it through.
    """
    from .ingestion.downloader import DocumentScanner
    from .models import KnowledgeIngestionRun, KnowledgeSource, KnowledgeTenant

    DEFAULT_STEPS = [
        "chunk_and_embed", "build_parent_child",
        "build_contextual", "build_graph", "build_raptor", "build_ontology",
    ]
    steps = steps or DEFAULT_STEPS

    sources_qs = KnowledgeSource.objects.filter(tenant_id=tenant_id)
    if source_ids:
        sources_qs = sources_qs.filter(id__in=source_ids)

    if run_id is None:
        run = KnowledgeIngestionRun.objects.create(
            tenant_id=tenant_id,
            status="running",
            started_at=datetime.now(timezone.utc),
            pipelines_run=steps,
        )
        run_id = run.id

    # Discover PDFs per source (scanner handles the directory walk)
    scanner = DocumentScanner(tenant_id=tenant_id)
    scanner.scan()
    all_pdfs_by_source: Dict[int, List[str]] = {}
    for src in sources_qs:
        from pathlib import Path
        src_path = Path(src.path)
        if not src_path.is_dir():
            continue
        all_pdfs_by_source[src.id] = [str(p) for p in sorted(src_path.rglob("*.pdf"))]

    # Honor MAX_DOCS_PER_CATEGORY (same as the scanner does for sequential)
    from . import config as ka_config
    if ka_config.MAX_DOCS_PER_CATEGORY > 0:
        for sid in all_pdfs_by_source:
            all_pdfs_by_source[sid] = all_pdfs_by_source[sid][:ka_config.MAX_DOCS_PER_CATEGORY]

    # Phase 1: per-PDF tasks for chunk_and_embed + build_parent_child
    phase_1_tasks = []
    for sid, pdfs in all_pdfs_by_source.items():
        for pdf in pdfs:
            if "chunk_and_embed" in steps:
                phase_1_tasks.append(ingest_pdf_task.s(tenant_id, sid, pdf))
            if "build_parent_child" in steps:
                phase_1_tasks.append(build_parent_child_pdf_task.s(tenant_id, sid, pdf))

    if not phase_1_tasks:
        # No PDFs found / no Phase 1 steps — skip straight to Phase 2 / finalize
        start_phase_2.apply_async(args=(None, tenant_id, run_id, steps))
        return "no_phase_1_work"

    # Phase 1 chord → start_phase_2 callback
    chord(group(phase_1_tasks))(
        start_phase_2.s(tenant_id, run_id, steps)
    )
    return f"phase_1_dispatched_tasks={len(phase_1_tasks)}_run={run_id}"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _filename_to_slug(filename: str) -> str:
    """Match IngestionPipeline._filename_to_slug."""
    import re
    slug = filename.rsplit(".", 1)[0]
    slug = slug.lower().replace(" ", "-").replace("_", "-")
    slug = re.sub(r"[^a-z0-9-]", "", slug)
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug
