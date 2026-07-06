# SPARQL templates

The `ontology` retrieval strategy uses a small fixed set of
parameterized SPARQL templates. Tenants **never** write SPARQL — the
strategy synthesizes queries by iterating over the tenant's
`entity_types`, `relationship_types`, `relationship_typing`, and
`data_properties` and plugging each into the appropriate template.

## The five templates (in priority order)

| Template | Iterates over | Question it answers |
|---|---|---|
| `find_typed_relation` | `ontology.relationship_typing` items | "Find `?source` of class X connected to `?target` of class Y via this relationship, where either side matches the query" |
| `find_untyped_relation` | `relationship_types` not covered by typing | "Find triples connected by this relationship, where either side matches the query" |
| `find_class_instances` | `entity_types` | "Find instances of this class whose name matches the query" |
| `find_with_data_property` | `ontology.data_properties` | "Find instances with this data property where the value matches the query" |
| `general_concept` | (runs once) | Domain-agnostic wildcard match on any triple |

Source: `knowledge_agent/strategies/ontology_templates.py`.

## Format placeholders

Every template accepts these — the strategy fills them in per iteration:

| Placeholder | Source |
|---|---|
| `{prefix}` | `tenant.domain.ontology.prefix` |
| `{namespace}` | `tenant.domain.ontology.namespace` |
| `{query_lower}` | The user's query, lowercased |
| `{class_name}` | Per-iteration entity type |
| `{relation}` | Per-iteration relationship name |
| `{domain_class}` / `{range_class}` | Per-iteration typing values |
| `{property_name}` | Per-iteration data property |

## Why not let tenants supply their own templates

Earlier design considered exposing `sparql_templates` as a tenant
config field. Rejected because:

1. Most tenants don't know SPARQL well enough to write safe queries.
2. The 5-template family covers the canonical question shapes
   (class lookup, typed/untyped relation lookup, data property lookup,
   wildcard).
3. SPARQL injection becomes a real concern when user-supplied templates
   are formatted with arbitrary query text. The packaged templates use
   the lowercased-substring-match pattern consistently, which is safe.

Power users who want custom SPARQL can subclass `OntologyStrategy.retrieve`
and run additional templates against the rdflib graph directly — that's
a code path, not a config path.
