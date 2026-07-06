# Filter operator grammar

The `filters=` parameter on `retrieve()` and `retrieve_batch()` accepts
a Chroma-compatible operator dict. The grammar lets you express
sophisticated metadata predicates without writing SQL.

## Shorthand equality

```python
filters = {"source_category": "manuals"}
# equivalent to:
filters = {"source_category": {"$eq": "manuals"}}
```

## All operators

| Operator | Example | SQL semantics |
|---|---|---|
| `$eq` | `{"year": 2024}` (shorthand) | `metadata->>'year' = 2024` |
| `$ne` | `{"year": {"$ne": 2024}}` | not equal |
| `$gt` / `$gte` / `$lt` / `$lte` | `{"year": {"$gte": 2020, "$lt": 2025}}` | range comparisons |
| `$in` | `{"category": {"$in": ["a", "b"]}}` | membership |
| `$nin` | `{"category": {"$nin": ["c", "d"]}}` | not in |
| `$exists` | `{"page_number": {"$exists": True}}` | key present in metadata |
| `$and` | `{"$and": [{"year": 2024}, {"category": "a"}]}` | logical AND (also implicit between top-level keys) |
| `$or` | `{"$or": [{"category": "a"}, {"category": "b"}]}` | logical OR |
| `$not` | `{"$not": {"category": "spam"}}` | negation |

## Nested metadata access

Dotted keys traverse JSONB:

```python
filters = {"author.name": "John"}
# matches rows where metadata = {"author": {"name": "John", ...}}
```

## Combining

Top-level keys AND together; explicit `$and`/`$or`/`$not` for richer
expressions:

```python
filters = {
    "category": {"$in": ["manuals", "papers"]},
    "$or": [
        {"year": {"$gte": 2020}},
        {"is_recent": True},
    ],
}
# → (category in ['manuals','papers']) AND (year>=2020 OR is_recent=True)
```

## Safety guarantees

- **Tenant scoping is outside this grammar.** The user filter is applied
  AFTER `for_tenant(tid)` — there is no way for a malicious filter to
  cross tenant boundaries.
- **Operator allowlist.** Unknown `$`-prefixed keys raise
  `FilterCompileError` with the offending operator name.
- **Key allowlist.** Keys must match `[A-Za-z_][A-Za-z0-9_.]*`. Anything
  with `';--` style characters raises immediately.
- **Max recursion depth** (default 6) — prevents stack blowups from
  malicious deeply-nested `{"$or": [{"$or": [...]}]}` payloads.

## How the compiler works

`knowledge_agent.filtering.compile_filter(filter_dict)` returns a
single Django `Q` object that the store layer ANDs into the queryset.
Each operator has a small compile function that emits a `Q(...)` using
Django's JSONField lookup family (`metadata__key`, `metadata__key__gt`,
`metadata__key__in`, etc.).

JSONB queries on indexed metadata fields are fast — the migration
includes a GIN index on the `metadata` column.
