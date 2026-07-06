"""
Domain-agnostic, parameterized SPARQL templates.

The `ontology` strategy runs these against the in-memory rdflib graph
built during ingestion. They're constants, not generated per-tenant —
the *iteration* over entity types / relationships / data properties
happens at query time inside `OntologyStrategy.retrieve()`, which formats
each template with the tenant's actual vocabulary.

Tenants never write SPARQL themselves (Q14 + C4): they declare their
ontology vocabulary, and the strategy uses these templates to ask the
graph the questions that vocabulary makes possible.

Available format keys:
  {prefix}       — tenant.domain.ontology.prefix
  {namespace}    — tenant.domain.ontology.namespace
  {query_lower}  — the user's query, lowercased
  {class_name}   — one of tenant.domain.entity_types        (iterated)
  {relation}     — one of tenant.domain.relationship_types  (iterated)
  {domain_class} — for typed relationships, the LHS class   (iterated)
  {range_class}  — for typed relationships, the RHS class   (iterated)
  {property_name}— one of tenant.domain.ontology.data_properties (iterated)
"""

# Wildcard match on any triple in the graph. The only template that
# always runs regardless of tenant configuration — works against any
# ontology because it doesn't reference specific classes or properties.
GENERAL_CONCEPT = """
SELECT ?s ?p ?o WHERE {{
    ?s ?p ?o .
    FILTER(CONTAINS(LCASE(STR(?s)), "{query_lower}")
        || CONTAINS(LCASE(STR(?o)), "{query_lower}"))
}} LIMIT 20
""".strip()


# Find all instances of a given class whose name matches the query.
# Runs once per entry in tenant.domain.entity_types.
FIND_CLASS_INSTANCES = """
SELECT ?instance ?desc WHERE {{
    ?instance a {prefix}:{class_name} .
    OPTIONAL {{ ?instance {prefix}:hasDescription ?desc }}
    FILTER(CONTAINS(LCASE(STR(?instance)), "{query_lower}"))
}} LIMIT 20
""".strip()


# Find typed (domain_class) → relationship → (range_class) triples
# where either side matches the query. Runs once per entry in
# tenant.domain.ontology.relationship_typing.
FIND_TYPED_RELATION = """
SELECT ?source ?target WHERE {{
    ?source a {prefix}:{domain_class} ;
            {prefix}:{relation} ?target .
    ?target a {prefix}:{range_class} .
    FILTER(CONTAINS(LCASE(STR(?source)), "{query_lower}")
        || CONTAINS(LCASE(STR(?target)), "{query_lower}"))
}} LIMIT 20
""".strip()


# Find any (?s) → relationship → (?o) triples regardless of typing.
# Runs once per relationship in tenant.domain.relationship_types that
# isn't covered by relationship_typing.
FIND_UNTYPED_RELATION = """
SELECT ?source ?target WHERE {{
    ?source {prefix}:{relation} ?target .
    FILTER(CONTAINS(LCASE(STR(?source)), "{query_lower}")
        || CONTAINS(LCASE(STR(?target)), "{query_lower}"))
}} LIMIT 20
""".strip()


# Find instances that have a data property value containing the query.
# Runs once per entry in tenant.domain.ontology.data_properties.
FIND_WITH_DATA_PROPERTY = """
SELECT ?instance ?value WHERE {{
    ?instance {prefix}:{property_name} ?value .
    FILTER(CONTAINS(LCASE(STR(?value)), "{query_lower}")
        || CONTAINS(LCASE(STR(?instance)), "{query_lower}"))
}} LIMIT 20
""".strip()


# The five templates as a name → string map. Ordered by specificity
# (most specific first) so the strategy can run them in priority order
# and stop early if it gathers enough matches.
TEMPLATES = {
    "find_typed_relation":     FIND_TYPED_RELATION,
    "find_untyped_relation":   FIND_UNTYPED_RELATION,
    "find_class_instances":    FIND_CLASS_INSTANCES,
    "find_with_data_property": FIND_WITH_DATA_PROPERTY,
    "general_concept":         GENERAL_CONCEPT,
}
