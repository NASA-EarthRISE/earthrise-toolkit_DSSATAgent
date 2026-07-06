"""
Translate Pydantic ValidationError into the ReAct-friendly envelope.

Caller passes a `field_values` map so the helper can enrich each issue with
a truncated valid-value list and fuzzy-matched suggestions. The map is
supplied by the tool wrapper (which knows what's in its DB) so this
function stays agnostic.
"""

from __future__ import annotations

import difflib
from typing import Any, Dict, Iterable, List, Optional

from pydantic import ValidationError


_MAX_VALID_VALUES = 50
_SUGGESTION_CUTOFF = 0.4
_SUGGESTION_COUNT = 3


def validation_error_to_react(
    err: ValidationError,
    *,
    field_values: Optional[Dict[str, Iterable[str]]] = None,
    hint: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Convert a Pydantic ValidationError into a ValidationErrorEnvelope dict.

    Args:
        err: the raised ValidationError.
        field_values: map of field-name → allowed values. Keys are matched
            against both the full dotted path (`planting.method`) and the
            leaf name (`method`), preferring the full path.
        hint: optional next-step guidance for the LLM.

    Returns:
        Dict shaped like ValidationErrorEnvelope.
    """
    field_values = field_values or {}
    resolved_fields = {k: list(v) for k, v in field_values.items()}

    issues: List[Dict[str, Any]] = []
    for e in err.errors():
        loc = ".".join(str(p) for p in e.get("loc", ()))
        leaf = loc.split(".")[-1] if loc else ""

        valid = resolved_fields.get(loc) or resolved_fields.get(leaf)
        issue: Dict[str, Any] = {
            "field": loc,
            "message": e.get("msg", ""),
            "received": e.get("input"),
        }
        if valid is not None:
            issue["valid_values"] = valid[:_MAX_VALID_VALUES]
            received = e.get("input")
            if isinstance(received, str):
                issue["suggestions"] = difflib.get_close_matches(
                    received, valid, n=_SUGGESTION_COUNT, cutoff=_SUGGESTION_CUTOFF,
                )
            if len(valid) > _MAX_VALID_VALUES:
                issue["has_more_valid_values"] = True
        issues.append(issue)

    envelope: Dict[str, Any] = {
        "status": "validation_error",
        "error": "Invalid parameters for this tool.",
        "issues": issues,
    }
    if hint:
        envelope["hint"] = hint
    return envelope
