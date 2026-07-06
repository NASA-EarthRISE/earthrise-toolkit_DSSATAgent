"""
Explorer views for the knowledge_agent.

Provides page-level views:
  - Strategy dashboard  (index)
  - Documents browser
  - Source / chunk detail
  - Corpus inspection pages (graphrag, raptor, ontology)
"""

import logging
import mimetypes
from pathlib import Path

from django.http import FileResponse, Http404
from django.shortcuts import render
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.views.decorators.csrf import ensure_csrf_cookie

from knowledge_agent import services
from knowledge_agent.strategies import STRATEGY_REGISTRY, STRATEGY_DEPENDENCIES

logger = logging.getLogger(__name__)

# Human-friendly labels for pipeline steps
STEP_LABELS = {
    "setup_tables": "Create tables",
    "download": "Download documents",
    "parse": "Parse PDFs",
    "chunk_and_embed": "Chunk & embed",
    "build_parent_child": "Parent / child hierarchy",
    "build_contextual": "Contextual enrichment",
    "build_graph": "GraphRAG extraction",
    "build_ontology": "OWL / RDF ontology",
    "build_raptor": "RAPTOR summary tree",
}

@method_decorator(ensure_csrf_cookie, name='dispatch')
class ExplorerIndexView(View):
    """Strategy dashboard — shows all retrieval strategies and their status."""

    def get(self, request):
        strategies_data = services.list_strategies()
        strategies = strategies_data.get("strategies", [])

        try:
            active = services.get_active_strategy()
        except Exception:
            active = "hybrid"

        try:
            readiness = services.check_readiness()
            row_counts = readiness.get("table_counts", {})
        except Exception:
            row_counts = {}

        # Enrich each strategy with dependency info
        for s in strategies:
            name = s["name"]
            deps = STRATEGY_DEPENDENCIES.get(name, {})
            s["required_steps"] = deps.get("steps", [])
            s["step_labels"] = [
                {"key": st, "label": STEP_LABELS.get(st, st)}
                for st in s["required_steps"]
            ]
            s["is_active"] = name == active
            # Table row counts for this strategy
            s["table_counts"] = {
                t: row_counts.get(t, 0)
                for t in s.get("required_tables", [])
            }

        return render(request, "knowledge_agent/explorer/index.html", {
            "strategies": strategies,
            "active_strategy": active,
            "row_counts": row_counts,
            "step_labels": STEP_LABELS,
        })


class DocumentsView(View):
    """Documents browser — shows ingested sources grouped by category."""

    def get(self, request):
        try:
            data = services.get_document_sources()
            sources = data.get("sources", [])
        except Exception:
            logger.exception("Failed to load document sources")
            sources = []

        # Group by source_category (auto-discovered from directory names)
        categories = {}
        for src in sources:
            key = src.get("category") or "uncategorized"
            label = src.get("category_label") or key
            categories.setdefault(key, {
                "key": key,
                "label": label,
                "sources": [],
                "total_chunks": 0,
            })
            categories[key]["sources"].append(src)
            categories[key]["total_chunks"] += src.get("chunk_count", 0)

        # Sort alphabetically by label
        sorted_categories = sorted(categories.values(), key=lambda c: c["label"])
        total_chunks = sum(c["total_chunks"] for c in sorted_categories)

        return render(request, "knowledge_agent/explorer/documents.html", {
            "categories": sorted_categories,
            "total_sources": len(sources),
            "total_chunks": total_chunks,
        })


class SourceDetailView(View):
    """Chunk viewer for a single document source."""

    def get(self, request, source_slug):
        page = int(request.GET.get("page", 1))
        per_page = int(request.GET.get("per_page", 50))

        try:
            chunk_data = services.get_chunks(source_slug, page=page, per_page=per_page)
        except Exception:
            logger.exception("Failed to load chunks for %s", source_slug)
            chunk_data = {"chunks": [], "total": 0, "page": 1, "per_page": per_page, "total_pages": 1}

        try:
            detail = services.get_source_detail(source_slug)
        except Exception:
            detail = {"slug": source_slug, "base_chunks": 0, "contextual": 0, "parents": 0, "children": 0}

        # Check if the source PDF is available
        has_source_file = services.get_source_file_path(source_slug) is not None

        # Derive a display title: prefer the source_title stored in chunk
        # metadata at ingest time; fall back to a humanized slug.
        source_title = ""
        for c in chunk_data["chunks"]:
            meta = c.get("metadata") or {}
            if meta.get("source_title"):
                source_title = meta["source_title"]
                break
        if not source_title:
            source_title = source_slug.replace("-", " ").title()

        # Build page range for pagination
        total_pages = chunk_data["total_pages"]
        current = chunk_data["page"]
        page_range = _page_window(current, total_pages)

        return render(request, "knowledge_agent/explorer/source_detail.html", {
            "source_slug": source_slug,
            "source_title": source_title,
            "chunks": chunk_data["chunks"],
            "total": chunk_data["total"],
            "page": current,
            "per_page": per_page,
            "total_pages": total_pages,
            "page_range": page_range,
            "detail": detail,
            "has_source_file": has_source_file,
        })


class SourceFileView(View):
    """Serve the original PDF for a source slug."""

    @method_decorator(xframe_options_sameorigin)
    def get(self, request, source_slug):
        file_path = services.get_source_file_path(source_slug)
        if not file_path:
            raise Http404("Source file not found")

        path = Path(file_path)
        if not path.is_file():
            raise Http404("Source file not found on disk")

        content_type, _ = mimetypes.guess_type(str(path))
        response = FileResponse(
            open(path, "rb"),
            content_type=content_type or "application/pdf",
        )
        # Inline display (browser PDF viewer), not download
        response["Content-Disposition"] = f'inline; filename="{path.name}"'
        return response


def _page_window(current, total, radius=3):
    """Return a list of page numbers around *current*."""
    lo = max(1, current - radius)
    hi = min(total, current + radius)
    return list(range(lo, hi + 1))


# ---------------------------------------------------------------------------
# Corpus inspection page views (graphrag, raptor, ontology)
# ---------------------------------------------------------------------------

@method_decorator(ensure_csrf_cookie, name='dispatch')
class GraphRAGInspectView(View):
    """Whole-corpus GraphRAG visualization page."""

    def get(self, request):
        return render(request, "knowledge_agent/explorer/inspect_graphrag.html", {
            "strategy_name": "graphrag",
            "page_title": "GraphRAG — corpus knowledge graph",
        })


@method_decorator(ensure_csrf_cookie, name='dispatch')
class RaptorInspectView(View):
    """Whole-corpus RAPTOR tree page."""

    def get(self, request):
        return render(request, "knowledge_agent/explorer/inspect_raptor.html", {
            "strategy_name": "raptor",
            "page_title": "RAPTOR — corpus summary tree",
        })


@method_decorator(ensure_csrf_cookie, name='dispatch')
class OntologyInspectView(View):
    """Whole-corpus ontology page."""

    def get(self, request):
        return render(request, "knowledge_agent/explorer/inspect_ontology.html", {
            "strategy_name": "ontology",
            "page_title": "Ontology — corpus OWL/RDF graph",
        })
