"""
Chroma-compatible filter operator grammar → Django Q-objects.

Lets callers write metadata filters in the idiomatic RAG-ecosystem
shape:

    {
      "author": "John",                              # shorthand $eq
      "year":   {"$gte": 2020, "$lt": 2025},
      "tag":    {"$in": ["a", "b"]},
      "$or": [
          {"category": "primary"},
          {"category": {"$in": ["secondary", "supplementary"]}},
      ],
    }

…and have them compile to Django ORM Q-objects that target the
`metadata` JSONField via the `metadata__<key>` lookup family.

Tenant scoping is NOT expressed through this grammar — the calling
queryset is already pre-filtered by `for_tenant(tenant)` before we
apply user filters. The user filter dict is never trusted to contain
the tenant_id key; the strategy layer enforces tenancy outside of
this module.

Defense-in-depth:
- Max recursion depth (default 6) so a malicious caller can't blow
  the stack with `{"$or": [{"$or": [...]}]}` nested arbitrarily deep.
- Operator allowlist; unknown $-prefixed keys raise.
- Key allowlist (alphanumeric + underscore + dot) so keys can't be
  injected into ORM lookup syntax (e.g. `__exact`, `__regex`).
"""

from __future__ import annotations

import re
from typing import Any

from django.db.models import Q


__all__ = ["compile_filter", "FilterCompileError"]


# Allowed metadata key characters. The strictness rules out attempts to
# inject Django lookup transforms via the key (e.g. `'name__regex'`).
_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")

_MAX_DEPTH = 6

# Per-operator compile functions. Each takes (key_or_None, value,
# depth) and returns a Q-object. $and/$or/$not get key=None because
# they don't reference a metadata key directly.
_OPS: dict[str, callable] = {}


class FilterCompileError(ValueError):
    """Raised when a filter dict can't be compiled — bad operator, bad
    key, or too-deep nesting. Surfaces a message safe to return to a
    caller."""


def compile_filter(filters: dict | None, *, max_depth: int = _MAX_DEPTH) -> Q:
    """Compile a Chroma-style filter dict into a single Django Q-object.

    Returns `Q()` (no constraints) for an empty/None filter — applying
    that to a queryset is a no-op.

    Raises `FilterCompileError` on malformed input.
    """
    if not filters:
        return Q()
    if not isinstance(filters, dict):
        raise FilterCompileError(
            f"filter root must be a dict, got {type(filters).__name__}"
        )
    return _compile_node(filters, depth=0, max_depth=max_depth)


def _compile_node(node: dict, *, depth: int, max_depth: int) -> Q:
    """Compile one level of the filter tree.

    A node is either:
    - A dict with top-level keys (implicit AND across them)
    - One of those keys may be a logical operator ($and / $or / $not)

    Each key in the dict either:
    - Is a logical operator → recurse with the operator's compile fn
    - Is a metadata key whose value is a scalar (shorthand $eq) or
      an operator dict ({"$gt": 10})
    """
    if depth > max_depth:
        raise FilterCompileError(
            f"filter exceeds max depth of {max_depth}"
        )
    if not isinstance(node, dict):
        raise FilterCompileError(
            f"filter node must be a dict, got {type(node).__name__}"
        )

    q = Q()
    for key, value in node.items():
        if key.startswith("$"):
            # Logical operator at this level.
            if key not in _OPS:
                raise FilterCompileError(f"unknown operator {key!r}")
            q &= _OPS[key](None, value, depth + 1, max_depth)
        else:
            _validate_key(key)
            q &= _compile_field(key, value, depth + 1, max_depth)
    return q


def _compile_field(key: str, value: Any, depth: int, max_depth: int) -> Q:
    """Compile a `{metadata_key: value}` clause."""
    if isinstance(value, dict):
        # Operator dict: {"$gt": 10, "$lt": 20}. Multiple ops AND together.
        q = Q()
        for op, op_value in value.items():
            if not op.startswith("$"):
                raise FilterCompileError(
                    f"field {key!r} value is a dict but contains "
                    f"non-operator key {op!r}; nested objects in metadata "
                    f"must be matched with shorthand $eq, not key lookups"
                )
            if op not in _OPS:
                raise FilterCompileError(
                    f"unknown operator {op!r} under field {key!r}"
                )
            q &= _OPS[op](key, op_value, depth, max_depth)
        return q
    else:
        # Shorthand: {"field": value} == {"field": {"$eq": value}}
        return _OPS["$eq"](key, value, depth, max_depth)


def _validate_key(key: str) -> None:
    if not _KEY_RE.match(key):
        raise FilterCompileError(
            f"filter key {key!r} contains characters outside "
            f"[A-Za-z0-9_.] — refusing to compile"
        )


def _lookup(key: str, suffix: str = "") -> str:
    """Build the Django ORM lookup string for a metadata key.

    `metadata__author` for `key='author'`; `metadata__year__gte` for
    `key='year', suffix='gte'`. Dotted keys like 'source.title' become
    `metadata__source__title` — Django's JSONField supports nested
    traversal natively.
    """
    parts = ["metadata"] + key.split(".")
    if suffix:
        parts.append(suffix)
    return "__".join(parts)


# ---------------------------------------------------------------------------
# Operators
# ---------------------------------------------------------------------------

def _op_eq(key, value, depth, max_depth):
    # Django's JSONField exact-match works for any JSON-serializable
    # scalar (string, number, bool, null). For nested objects, this
    # does an equality check on the whole subtree.
    return Q(**{_lookup(key): value})


def _op_ne(key, value, depth, max_depth):
    return ~Q(**{_lookup(key): value})


def _op_gt(key, value, depth, max_depth):
    return Q(**{_lookup(key, "gt"): value})


def _op_gte(key, value, depth, max_depth):
    return Q(**{_lookup(key, "gte"): value})


def _op_lt(key, value, depth, max_depth):
    return Q(**{_lookup(key, "lt"): value})


def _op_lte(key, value, depth, max_depth):
    return Q(**{_lookup(key, "lte"): value})


def _op_in(key, value, depth, max_depth):
    if not isinstance(value, list):
        raise FilterCompileError(
            f"$in expects a list, got {type(value).__name__}"
        )
    return Q(**{_lookup(key, "in"): value})


def _op_nin(key, value, depth, max_depth):
    if not isinstance(value, list):
        raise FilterCompileError(
            f"$nin expects a list, got {type(value).__name__}"
        )
    return ~Q(**{_lookup(key, "in"): value})


def _op_exists(key, value, depth, max_depth):
    if not isinstance(value, bool):
        raise FilterCompileError(
            f"$exists expects a boolean, got {type(value).__name__}"
        )
    has = Q(**{_lookup(key, "isnull"): False})
    return has if value else ~has


def _op_and(_key, value, depth, max_depth):
    if not isinstance(value, list) or not value:
        raise FilterCompileError(
            "$and expects a non-empty list of filter sub-clauses"
        )
    q = Q()
    for sub in value:
        q &= _compile_node(sub, depth=depth, max_depth=max_depth)
    return q


def _op_or(_key, value, depth, max_depth):
    if not isinstance(value, list) or not value:
        raise FilterCompileError(
            "$or expects a non-empty list of filter sub-clauses"
        )
    q = Q()
    for i, sub in enumerate(value):
        sub_q = _compile_node(sub, depth=depth, max_depth=max_depth)
        q = sub_q if i == 0 else (q | sub_q)
    return q


def _op_not(_key, value, depth, max_depth):
    if not isinstance(value, dict):
        raise FilterCompileError(
            "$not expects a filter sub-clause dict"
        )
    return ~_compile_node(value, depth=depth, max_depth=max_depth)


_OPS.update({
    "$eq":     _op_eq,
    "$ne":     _op_ne,
    "$gt":     _op_gt,
    "$gte":    _op_gte,
    "$lt":     _op_lt,
    "$lte":    _op_lte,
    "$in":     _op_in,
    "$nin":    _op_nin,
    "$exists": _op_exists,
    "$and":    _op_and,
    "$or":     _op_or,
    "$not":    _op_not,
})
