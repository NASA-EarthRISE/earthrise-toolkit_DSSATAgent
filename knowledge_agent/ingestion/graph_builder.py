"""
GraphRAG entity and relationship extraction.

For each chunk, the LLM extracts named entities and relationships of the
types declared in the tenant's DomainConfig. Deduplicates entities by
(name, type) within the tenant before storing.

Behavior notes:
- Every stored row carries a required `tenant_id` FK.
- Entity types and relationship types come from `tenant.domain.entity_types`
  and `tenant.domain.relationship_types` — none are hardcoded.
- Extraction runs through the configured LLM / embedding backend interfaces.
"""

import json
import logging
import re
from typing import Dict, List, Optional

from ..backends import get_embedding_backend, get_llm_backend
from ..store import VectorStore
from .chunker import Chunk

logger = logging.getLogger(__name__)


# {entity_types}, {relationship_types}, and {acronym_hints} are filled
# per-chunk at build time. The prompt is otherwise domain-agnostic.
_EXTRACT_PROMPT = """Extract ALL entities and relationships from the
following text chunk. Be exhaustive: include every named concept that
fits the allowed types, even if mentioned only briefly. Aim for high
recall — multiple short entities are better than one over-general
entity.

Entity types: {entity_types}

Relationship types: {relationship_types}

Acronym handling:
- When the text introduces an acronym, e.g. "near-isogenic lines (NILs)"
  or "Geographic Information System (GIS)",
  emit ONE entity using the FULL form as `name` and put the acronym in
  `aliases`. Do not emit the acronym as a separate entity.
- If the text later mentions the acronym alone (e.g. just "GIS"),
  attribute the mention to the same canonical entity.
{acronym_hints_block}
Text:
{text}

Return ONLY valid JSON (no markdown, no commentary):
{{
  "entities": [
    {{"name": "<canonical / full form>", "type": "...",
      "description": "...", "aliases": ["<acronym>", "..."]}}
  ],
  "relationships": [
    {{"source": "<canonical name>", "target": "<canonical name>",
      "relationship": "..."}}
  ]
}}"""


class GraphBuilder:
    """Extracts entities and relationships from chunks and stores them."""

    def __init__(
        self,
        store: VectorStore | None = None,
        embedding_service=None,
        llm=None,
        *,
        tenant_id: Optional[str] = None,
        entity_types: Optional[List[str]] = None,
        relationship_types: Optional[List[str]] = None,
    ):
        self.store = store or VectorStore()
        self.embedding_service = embedding_service or get_embedding_backend()
        self.llm = llm or get_llm_backend()
        self.tenant_id = tenant_id
        self.entity_types = entity_types or []
        self.relationship_types = relationship_types or []
        self._entity_cache: Dict[str, str] = {}  # "name|type" -> entity_id

        if not self.entity_types or not self.relationship_types:
            logger.warning(
                "GraphBuilder for tenant %r has empty entity_types or "
                "relationship_types — extraction may be unfocused. Configure "
                "domain.entity_types / domain.relationship_types in the "
                "tenant registration.",
                self.tenant_id,
            )

    def build_from_chunks(self, chunks: List[Chunk]):
        for i, chunk in enumerate(chunks):
            try:
                self._process_chunk(chunk)
                if (i + 1) % 20 == 0:
                    logger.info("GraphRAG processed %d/%d chunks", i + 1, len(chunks))
            except Exception as e:
                logger.error("GraphRAG failed on chunk %s: %s", chunk.id, e)

        logger.info(
            "GraphRAG complete: %d unique entities cached", len(self._entity_cache),
        )

    def _process_chunk(self, chunk: Chunk):
        from ._acronyms import canonicalize_name, find_acronym_pairs, format_acronym_hints

        acronym_pairs = find_acronym_pairs(chunk.content)
        hints = format_acronym_hints(acronym_pairs)
        hints_block = (
            f"\nKnown acronym definitions in this chunk:\n{hints}\n"
            if hints else ""
        )

        prompt = _EXTRACT_PROMPT.format(
            entity_types=", ".join(self.entity_types) or "(unspecified)",
            relationship_types=", ".join(self.relationship_types) or "(unspecified)",
            acronym_hints_block=hints_block,
            text=chunk.content[:2000],
        )
        raw = self.llm.generate(prompt)

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            match = re.search(r'\{[\s\S]*\}', raw)
            if not match:
                return
            try:
                data = json.loads(match.group())
            except json.JSONDecodeError:
                return

        entities = data.get("entities", [])
        relationships = data.get("relationships", [])

        entity_name_to_id: Dict[str, str] = {}
        for ent in entities:
            raw_name = ent.get("name", "").strip()
            etype = ent.get("type", "").strip()
            desc = ent.get("description", "")
            llm_aliases = ent.get("aliases") or []
            if not raw_name or not etype:
                continue

            # Canonicalize: if the LLM emitted an acronym as the name,
            # promote the full form and store the acronym as an alias.
            canonical = canonicalize_name(raw_name, acronym_pairs)
            aliases = set(a.strip() for a in llm_aliases if isinstance(a, str) and a.strip())
            if canonical != raw_name:
                aliases.add(raw_name)
            # If the canonical form has a known acronym not yet in aliases,
            # inject it so future mentions match this entity.
            for acr, full in acronym_pairs.items():
                if full.lower() == canonical.lower():
                    aliases.add(acr)

            name = canonical
            cache_key = f"{name.lower()}|{etype.lower()}"
            if cache_key in self._entity_cache:
                eid = self._entity_cache[cache_key]
                entity_name_to_id[name.lower()] = eid
                for alias in aliases:
                    entity_name_to_id[alias.lower()] = eid
                if aliases:
                    self.store.merge_entity_aliases(eid, sorted(aliases))
                continue

            embedding = self.embedding_service.embed_text(f"{etype}: {name}. {desc}")
            eid = self.store.insert_entity(
                name=name,
                entity_type=etype,
                description=desc,
                embedding=embedding,
                metadata={"aliases": sorted(aliases)} if aliases else None,
                tenant_id=self.tenant_id,
            )
            self._entity_cache[cache_key] = eid
            entity_name_to_id[name.lower()] = eid
            for alias in aliases:
                entity_name_to_id[alias.lower()] = eid

        for rel in relationships:
            src_raw = rel.get("source", "").strip()
            tgt_raw = rel.get("target", "").strip()
            rel_type = rel.get("relationship", "").strip()
            if not src_raw or not tgt_raw or not rel_type:
                continue

            # Rewrite acronym references to their canonical entity.
            src_name = canonicalize_name(src_raw, acronym_pairs).lower()
            tgt_name = canonicalize_name(tgt_raw, acronym_pairs).lower()

            src_id = entity_name_to_id.get(src_name) or entity_name_to_id.get(src_raw.lower())
            tgt_id = entity_name_to_id.get(tgt_name) or entity_name_to_id.get(tgt_raw.lower())
            if src_id and tgt_id:
                self.store.insert_relationship(
                    source_id=src_id,
                    target_id=tgt_id,
                    relationship=rel_type,
                    source_chunk_id=chunk.id,
                    tenant_id=self.tenant_id,
                )
