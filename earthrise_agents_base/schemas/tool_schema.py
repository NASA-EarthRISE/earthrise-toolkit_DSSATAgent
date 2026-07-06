"""
Build a JSON schema from a Pydantic model with dynamic enum injection.

The LLM's tool binding needs a JSON schema where fields like `crop`,
`weather_dataset`, and `cultivar` have their allowed values listed as
`enum: [...]`. Pydantic can't express DB-derived enums at class-definition
time, so the model field is typed as plain `str` and the enum is injected
at schema-build time per the `enum_providers` map.

Contract:
    providers: {field_path: callable-returning-iterable-of-str}
    `field_path` is either a top-level field name (`crop`) or a dotted
    path into a nested object (`planting.method`). For discriminated
    unions, the injector searches every variant.
"""

from __future__ import annotations

import copy
from typing import Any, Callable, Dict, Iterable, List, Optional

from pydantic import BaseModel


EnumProvider = Callable[[], Iterable[str]]


def build_tool_schema(
    model_cls: type[BaseModel],
    *,
    enum_providers: Optional[Dict[str, EnumProvider]] = None,
    inline_refs: bool = True,
) -> Dict[str, Any]:
    """
    Return the JSON schema for `model_cls`, with dynamic enums injected.

    `enum_providers` maps a field name (or dotted path into a nested
    object) to a callable that returns the allowed values. Providers are
    invoked *now*; wrap in closures to capture user/crop context.

    `inline_refs` (default True) resolves every local `$ref` into the
    referenced subschema and drops `$defs`. Tool-calling LLMs (notably
    llama3.1 on Ollama) misread `$ref` nodes and sometimes emit the
    referenced class name as a literal string instead of an object
    — inlining avoids that pitfall.
    """
    schema = copy.deepcopy(model_cls.model_json_schema())
    if inline_refs:
        schema = _inline_defs(schema)
    if enum_providers:
        for field_path, provider in enum_providers.items():
            values = list(provider())
            inject_enum_into_schema(schema, field_path, values)
    return schema


def _inline_defs(schema: Dict[str, Any]) -> Dict[str, Any]:
    """Resolve all local `$ref`s into inline subschemas; drop `$defs`."""
    defs: Dict[str, Any] = {}
    for key in ("$defs", "definitions"):
        if key in schema and isinstance(schema[key], dict):
            defs.update(schema[key])

    def resolve(node: Any) -> Any:
        if isinstance(node, list):
            return [resolve(item) for item in node]
        if not isinstance(node, dict):
            return node
        if "$ref" in node and isinstance(node["$ref"], str):
            ref = node["$ref"]
            if ref.startswith("#/$defs/"):
                key = ref[len("#/$defs/"):]
            elif ref.startswith("#/definitions/"):
                key = ref[len("#/definitions/"):]
            else:
                return {k: resolve(v) for k, v in node.items()}
            target = defs.get(key)
            if target is None:
                return {k: resolve(v) for k, v in node.items() if k != "$ref"}
            # Inline the target; merge any sibling keys (description, etc.)
            resolved = resolve(copy.deepcopy(target))
            merged = dict(resolved) if isinstance(resolved, dict) else {}
            for k, v in node.items():
                if k == "$ref":
                    continue
                merged[k] = resolve(v)
            return merged
        return {k: resolve(v) for k, v in node.items()}

    inlined = resolve(schema)
    if isinstance(inlined, dict):
        inlined.pop("$defs", None)
        inlined.pop("definitions", None)
    return inlined


def inject_enum_into_schema(
    schema: Dict[str, Any],
    field_path: str,
    values: List[str],
) -> int:
    """
    Walk `schema` (in place) and attach `enum: values` to every property
    that matches `field_path`. Returns the number of injections performed.

    Field matching:
      - `"crop"` matches any top-level `crop` property found in the root
        model OR in any variant of a discriminated union OR in any `$defs`
        component.
      - `"planting.method"` matches only a `method` property nested
        directly under a `planting` property.
    """
    parts = field_path.split(".")
    injections = _walk_inject(schema, parts, values)
    # Also search $defs (Pydantic places nested component schemas there)
    defs = schema.get("$defs") or schema.get("definitions") or {}
    for node in defs.values():
        injections += _walk_inject(node, parts, values)
    return injections


def _walk_inject(
    node: Any,
    path_parts: List[str],
    values: List[str],
) -> int:
    """Recursively locate the path in this sub-schema and inject `enum`."""
    if not isinstance(node, dict):
        return 0
    count = 0

    # Case 1: the current node has a `properties` dict — check if the first
    # path part lives here and recurse deeper (if more parts) or inject.
    props = node.get("properties")
    if isinstance(props, dict):
        head, *tail = path_parts
        if head in props:
            target = props[head]
            if tail:
                count += _walk_inject(target, tail, values)
            else:
                _attach_enum(target, values)
                count += 1

    # Case 2: discriminated union / union. Recurse into all variants
    # (oneOf / anyOf / allOf).
    for key in ("oneOf", "anyOf", "allOf"):
        variants = node.get(key)
        if isinstance(variants, list):
            for variant in variants:
                count += _walk_inject(variant, path_parts, values)

    # Case 3: some property nested inside an object whose shape is described
    # by `items` (array) or by an inline $ref object — recurse through those.
    items = node.get("items")
    if isinstance(items, dict):
        count += _walk_inject(items, path_parts, values)

    return count


def _attach_enum(target: Dict[str, Any], values: List[str]) -> None:
    """
    Attach `enum: values` to a property schema. If the property is nullable
    (anyOf with a 'null' variant), attach to the string variant.
    """
    if "anyOf" in target and isinstance(target["anyOf"], list):
        for variant in target["anyOf"]:
            if isinstance(variant, dict) and variant.get("type") == "string":
                variant["enum"] = values
                return
    target["enum"] = values
