"""
Tool adapter for the knowledge_agent's retrieval surface.

`knowledge_retrieve_tool` is what the ReAct loop calls. It:
  1. Validates inputs via `RetrieveInput` (Pydantic).
  2. Resolves the effective strategy: explicit arg > user preference > system
     default (never hardcoded to `hybrid`).
  3. Wraps the existing `knowledge_agent.services.retrieve()` callable.
  4. Returns a ReAct envelope with the retrieved chunks under `status: ok`.
  5. Pre-formats citation markdown so the LLM uses our URLs verbatim
     rather than hallucinating external hosts (e.g. "www.example.com//…").
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

from pydantic import ValidationError

from earthrise_agents_base.schemas import validation_error_to_react
from knowledge_agent.schemas import RetrieveInput

logger = logging.getLogger(__name__)


# Metadata keys the LLM has no use for and that tempt it to hallucinate
# external URLs or categories. Everything else in metadata (chunk_type,
# has_table, page_number, section_title) is kept.
_METADATA_DROP = {
    "source_category",
    "source_category_label",
    "source_filename",
    "source_title",
    "source_slug",
}


def _build_citation_markdown(citation: Dict[str, Any]) -> str:
    """Compose a ready-to-use markdown link from a citation dict. The LLM is
    instructed to use this verbatim — don't let it rebuild URLs.
    """
    title = (citation.get("source_title") or "").strip() or "source"
    page = citation.get("page_number")
    url = (citation.get("url") or "").strip()
    if not url:
        return ""
    # Subpath-aware: when the deployment lives under /subpath, prepend it.
    subpath = (os.environ.get("SUBPATH") or "").strip("/")
    if subpath and url.startswith("/") and not url.startswith(f"/{subpath}/"):
        url = f"/{subpath}{url}"
    label = f"{title}, p. {page}" if page else title
    return f"[{label}]({url})"


def _shape_for_llm(results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Strip noisy metadata and attach pre-built citation markdown to each
    retrieval hit before it ever reaches the orchestrator's synthesis step.
    """
    shaped: List[Dict[str, Any]] = []
    for r in results:
        out = dict(r)
        meta = dict(out.get("metadata") or {})
        for k in _METADATA_DROP:
            meta.pop(k, None)
        out["metadata"] = meta
        citation = dict(out.get("citation") or {})
        if citation:
            citation["markdown"] = _build_citation_markdown(citation)
            out["citation"] = citation
        shaped.append(out)
    return shaped


def _dedupe_sources_markdown(results: List[Dict[str, Any]]) -> List[str]:
    """Collect unique markdown links (by URL) so the orchestrator / LLM can
    drop them into a trailing Sources section as-is.
    """
    seen: set = set()
    out: List[str] = []
    for r in results:
        citation = r.get("citation") or {}
        md = citation.get("markdown")
        url = citation.get("url")
        if not md or not url:
            continue
        if url in seen:
            continue
        seen.add(url)
        out.append(md)
    return out


KNOWLEDGE_RETRIEVE_TOOL_DESCRIPTION = (
    "Search the indexed knowledge base — returns cited document chunks "
    "relevant to the query. Use when the user asks a conceptual or factual "
    "question whose answer lives in the documentation. Accepts an optional "
    "`strategy` override; leave it unset to use the user-preferred or "
    "tenant-default retrieval strategy."
)


def knowledge_retrieve_tool(
    raw: Dict[str, Any], *, user: Any = None,
) -> Dict[str, Any]:
    """Validated entry point for the knowledge-search skill's primary tool."""
    raw = dict(raw or {})

    try:
        params = RetrieveInput.model_validate(raw)
    except ValidationError as e:
        return validation_error_to_react(e)

    # Resolve effective strategy if the caller didn't pin one.
    from knowledge_agent.services import get_active_strategy, retrieve

    strategy = params.strategy.value if params.strategy else None
    if strategy is None:
        try:
            strategy = get_active_strategy(user=user)
        except Exception as e:
            logger.warning(
                "[knowledge] get_active_strategy failed (%s); falling back to 'hybrid'",
                e,
            )
            strategy = "hybrid"

    result = retrieve(
        query=params.query,
        strategy=strategy,
        top_k=params.top_k,
        filters=params.filters,
    )

    if "error" in result and "results" not in result:
        from knowledge_agent.errors import format_strategy_error
        return format_strategy_error(
            user=user,
            error_message=result["error"],
            admin_detail={"strategy": strategy, "query": params.query},
        )

    raw_results = result.get("results", [])
    shaped_results = _shape_for_llm(raw_results)

    return {
        "status": "ok",
        "strategy": strategy,
        "query": params.query,
        "count": result.get("count", len(shaped_results)),
        "results": shaped_results,
        # Pre-built, deduped markdown links so the responder doesn't have
        # to compose a Sources list — it just drops these in.
        "sources_markdown": _dedupe_sources_markdown(shaped_results),
    }
