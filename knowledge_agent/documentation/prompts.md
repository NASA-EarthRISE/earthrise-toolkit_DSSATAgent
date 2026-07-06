# How domain config drives LLM prompts

Tenant config (`KnowledgeTenant.domain` JSONField, populated by
`register_tenant(...)`) controls every LLM-touching prompt in the
knowledge agent. There's no DSSAT-specific (or any-domain-specific)
text baked into the package.

## What gets filled in where

| `domain` field | Used by | Effect |
|---|---|---|
| `domain.hint` | HyDE `_HYDE_PROMPT`, RAPTOR `_SUMMARIZE_PROMPT` | The LLM writes hypothetical answers / cluster summaries in the corpus's voice ("You are an expert in {domain_hint}") |
| `domain.entity_types` | GraphRAG `_EXTRACT_PROMPT`, Ontology `_CLASSIFY_PROMPT`, Ontology RDF class declarations, all `find_class_instances` SPARQL templates | The set of node labels the extraction prompts ask for and the ontology builder declares |
| `domain.relationship_types` | GraphRAG `_EXTRACT_PROMPT`, Ontology `_CLASSIFY_PROMPT`, Ontology RDF property declarations, all `find_*_relation` SPARQL templates | The set of edge labels the extraction prompts ask for |
| `domain.ontology.namespace` | Ontology RDF base URI, all SPARQL templates' `{namespace}` placeholder | The OWL/RDF namespace the graph is anchored at |
| `domain.ontology.prefix` | Ontology RDF prefix declaration, all SPARQL templates' `{prefix}` placeholder | The short prefix used in SPARQL (e.g. `dssat:CropModel`) |
| `domain.ontology.relationship_typing` | Ontology RDF `RDFS:domain`/`RDFS:range` triples, `find_typed_relation` SPARQL template | Adds typed edges with domain/range class constraints |
| `domain.ontology.data_properties` | Ontology RDF data-property declarations, `find_with_data_property` SPARQL template | Literal-valued attributes (e.g. `hasUnit`) |

## Single source of truth

`entity_types` and `relationship_types` are declared **once** at the
top level of `domain`. The ontology block does not redefine them.
GraphRAG extraction and ontology classification read from the same
lists. This prevents drift between the two extractors and is enforced
by the Pydantic validator (`relationship_typing` keys must be in
`relationship_types`; typing values must be in `entity_types`).

## Decorative phrases stripped from non-domain-dependent strategies

Three Tier-2 strategies use the LLM for query manipulation that does
not benefit from corpus context:

- `adaptive._CLASSIFY_PROMPT` — picks a strategy based on query shape
- `crag._REWRITE_PROMPT` — rewrites a poorly-performing query
- `rag_fusion._GENERATE_QUERIES_PROMPT` — generates query variants

These prompts are entirely domain-agnostic.

## Default when `domain` is empty

If a tenant is registered with no `domain` dict (or a minimal one):

- `domain.hint` defaults to `"the indexed corpus"` (HyDE / RAPTOR use a
  generic phrasing)
- `entity_types` and `relationship_types` default to `[]` (GraphRAG
  extraction prompt warns about empty types but still runs; ontology
  builder emits only ABox triples the LLM invents)
- `ontology` defaults to `None` (ontology strategy emits only the
  `general_concept` SPARQL template, which is domain-agnostic)
