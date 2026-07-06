"""
OWL/RDF ontology builder.

Builds a tenant-scoped OWL graph from the entity_types, relationship_types,
and (optional) ontology block declared in the tenant's DomainConfig:

  1. Reads classes from `domain.entity_types`
  2. Reads object properties from `domain.relationship_types` (with optional
     domain/range typing from `domain.ontology.relationship_typing`)
  3. Reads data properties from `domain.ontology.data_properties`
  4. Asks the LLM to classify entities in each chunk into one of the
     declared classes and emit their relationships
  5. Serializes the populated graph to Turtle, stores in
     `knowledge_ontology` scoped to the tenant

Ontology classes and object/data properties are supplied per tenant
through registration (via `domain.ontology`), not hardcoded.
"""

import json
import logging
import re
from typing import Dict, List, Optional, Tuple

from ..backends import get_llm_backend
from ..store import VectorStore
from .chunker import Chunk

logger = logging.getLogger(__name__)


_CLASSIFY_PROMPT = """Classify ALL entities from the following text into
the provided ontology classes. Be exhaustive: include every named
concept that fits a class, even if mentioned briefly.

Available classes: {classes}

Available relationships: {relationships}

Acronym handling:
- When the text introduces an acronym, e.g. "near-isogenic lines (NILs)",
  emit ONE individual using the FULL form as `name` and put the acronym
  in `aliases`. Do not emit the acronym as a separate individual.
- Later mentions of the acronym alone refer back to the same canonical
  individual.
{acronym_hints_block}
Text:
{text}

For each entity found, classify it into one of the available classes and
list any relationships to other entities.

Return ONLY valid JSON:
{{
  "individuals": [
    {{
      "name": "<canonical / full form>",
      "class": "...",
      "aliases": ["<acronym>", "..."],
      "properties": {{"hasDescription": "..."}},
      "relationships": [
        {{"property": "...", "target": "<canonical name>", "target_class": "..."}}
      ]
    }}
  ]
}}"""


def _normalize_typing(value) -> Optional[Tuple[str, str]]:
    """Accept either (dom, rng) tuple/list or {'domain': ..., 'range': ...}."""
    if isinstance(value, dict):
        dom, rng = value.get("domain"), value.get("range")
    elif isinstance(value, (list, tuple)) and len(value) == 2:
        dom, rng = value
    else:
        return None
    if not dom or not rng:
        return None
    return dom, rng


class OntologyBuilder:
    """Builds a tenant-scoped formal OWL/RDF ontology from chunks."""

    def __init__(
        self,
        store: VectorStore | None = None,
        llm=None,
        *,
        tenant_id: Optional[str] = None,
        namespace: str = "http://example.com/ontology#",
        prefix: str = "ex",
        entity_types: Optional[List[str]] = None,
        relationship_types: Optional[List[str]] = None,
        relationship_typing: Optional[Dict[str, Tuple[str, str]]] = None,
        data_properties: Optional[List[str]] = None,
    ):
        self.store = store or VectorStore()
        self.llm = llm or get_llm_backend()
        self.tenant_id = tenant_id
        self.namespace = namespace
        self.prefix = prefix
        self.entity_types = entity_types or []
        self.relationship_types = relationship_types or []
        self.relationship_typing = relationship_typing or {}
        self.data_properties = data_properties or []

    def build(self, chunks: List[Chunk]) -> str:
        """Build ontology from chunks and store. Returns Turtle string."""
        try:
            from rdflib import Graph, Namespace, Literal, RDF, RDFS, OWL
        except ImportError:
            logger.error("rdflib not installed — cannot build ontology")
            return ""

        if not self.entity_types:
            logger.warning(
                "OntologyBuilder for tenant %r has no entity_types — "
                "ontology will only contain ABox triples the LLM invents. "
                "Configure domain.entity_types in tenant registration.",
                self.tenant_id,
            )

        ns = Namespace(self.namespace)
        g = Graph()
        g.bind(self.prefix, ns)
        g.bind("owl", OWL)

        # Classes from entity_types (TBox)
        for cls_name in self.entity_types:
            cls_uri = ns[cls_name]
            g.add((cls_uri, RDF.type, OWL.Class))
            g.add((cls_uri, RDFS.label, Literal(cls_name)))

        # Object properties from relationship_types — with optional typing
        for prop_name in self.relationship_types:
            prop_uri = ns[prop_name]
            g.add((prop_uri, RDF.type, OWL.ObjectProperty))
            typing = self.relationship_typing.get(prop_name)
            typing = _normalize_typing(typing) if typing else None
            if typing:
                dom, rng = typing
                g.add((prop_uri, RDFS.domain, ns[dom]))
                g.add((prop_uri, RDFS.range, ns[rng]))

        # Data properties
        for prop_name in self.data_properties:
            prop_uri = ns[prop_name]
            g.add((prop_uri, RDF.type, OWL.DatatypeProperty))

        # Populate individuals from chunks
        source_chunk_ids: List[str] = []
        relationships_str = ", ".join(self.relationship_types) or "(unspecified)"

        for i, chunk in enumerate(chunks):
            try:
                self._process_chunk(g, ns, chunk, relationships_str)
                source_chunk_ids.append(chunk.id)
            except Exception as e:
                logger.debug("Ontology extraction failed for chunk %s: %s", chunk.id, e)

            if (i + 1) % 20 == 0:
                logger.info("Ontology processed %d/%d chunks", i + 1, len(chunks))

        turtle_data = g.serialize(format="turtle")
        logger.info("Ontology built: %d triples", len(g))

        self.store.insert_ontology(
            turtle_data=turtle_data,
            source_chunk_ids=source_chunk_ids,
            version=1,
            tenant_id=self.tenant_id,
        )

        return turtle_data

    def _process_chunk(self, g, ns, chunk: Chunk, relationships_str: str):
        from rdflib import Literal, RDF, RDFS

        from ._acronyms import canonicalize_name, find_acronym_pairs, format_acronym_hints

        acronym_pairs = find_acronym_pairs(chunk.content)
        hints = format_acronym_hints(acronym_pairs)
        hints_block = (
            f"\nKnown acronym definitions in this chunk:\n{hints}\n"
            if hints else ""
        )

        raw = self.llm.generate(_CLASSIFY_PROMPT.format(
            classes=", ".join(self.entity_types) or "(unspecified)",
            relationships=relationships_str,
            acronym_hints_block=hints_block,
            text=chunk.content[:2000],
        ))

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

        individuals = data.get("individuals", [])
        for ind in individuals:
            raw_name = ind.get("name", "").strip()
            cls_name = ind.get("class", "").strip()
            llm_aliases = ind.get("aliases") or []
            # Only accept entities in our declared class vocabulary
            if not raw_name or cls_name not in self.entity_types:
                continue

            canonical = canonicalize_name(raw_name, acronym_pairs)
            aliases = {a.strip() for a in llm_aliases if isinstance(a, str) and a.strip()}
            if canonical != raw_name:
                aliases.add(raw_name)
            for acr, full in acronym_pairs.items():
                if full.lower() == canonical.lower():
                    aliases.add(acr)

            safe_name = canonical.replace(" ", "_").replace("/", "_")
            ind_uri = ns[safe_name]
            g.add((ind_uri, RDF.type, ns[cls_name]))
            # Preserve the canonical label and emit aliases as additional
            # labels so SPARQL queries can match either form.
            g.add((ind_uri, RDFS.label, Literal(canonical)))
            for alias in sorted(aliases):
                g.add((ind_uri, RDFS.label, Literal(alias)))

            props = ind.get("properties", {})
            for prop_name in self.data_properties:
                if prop_name in props and props[prop_name]:
                    g.add((ind_uri, ns[prop_name], Literal(str(props[prop_name]))))

            rels = ind.get("relationships", [])
            for rel in rels:
                prop = rel.get("property", "")
                target_raw = rel.get("target", "").strip()
                if prop and target_raw and prop in self.relationship_types:
                    target = canonicalize_name(target_raw, acronym_pairs)
                    safe_target = target.replace(" ", "_").replace("/", "_")
                    g.add((ind_uri, ns[prop], ns[safe_target]))
