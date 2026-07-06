"""
JSON API views for the knowledge explorer.

All endpoints return ``application/json`` and are consumed by the
knowledge.js frontend via ``fetch()``.
"""

import json
import logging

from celery.result import AsyncResult
from django.http import JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from knowledge_agent import services
from knowledge_agent.store import VectorStore, _tenant_str
from knowledge_agent.strategies import STRATEGY_REGISTRY

logger = logging.getLogger(__name__)


class StrategiesAPI(View):
    """GET  → list strategies with readiness and active flag."""

    def get(self, request):
        data = services.list_strategies()
        active = services.get_active_strategy()
        for s in data.get("strategies", []):
            s["is_active"] = s["name"] == active
        data["active_strategy"] = active
        return JsonResponse(data)


class ActiveStrategyAPI(View):
    """GET  → current active strategy.
       POST → set active strategy  (body: {"strategy": "..."})."""

    def get(self, request):
        return JsonResponse({"active_strategy": services.get_active_strategy()})

    def post(self, request):
        try:
            body = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            return JsonResponse({"error": "Invalid JSON"}, status=400)
        strategy = body.get("strategy", "").strip()
        if not strategy:
            return JsonResponse({"error": "Missing 'strategy' field"}, status=400)
        result = services.set_active_strategy(strategy)
        if "error" in result:
            return JsonResponse(result, status=400)
        return JsonResponse(result)


class IngestAPI(View):
    """POST → dispatch the tenant ingestion orchestrator.

    Body: {"tenant_id": "dssat", "steps": [...]?, "source_ids": [...]?}.
    Returns the Celery task ID of the orchestrator. The orchestrator
    creates per-PDF and per-chunk subtasks; poll the AsyncResult or
    use the `KnowledgeIngestionRun` row to track progress.
    """

    def post(self, request):
        from knowledge_agent.tasks import ingest_tenant_task
        from knowledge_agent.store import _tenant_str

        try:
            body = json.loads(request.body) if request.body else {}
        except (json.JSONDecodeError, ValueError):
            body = {}
        tenant_id = _tenant_str(body.get("tenant_id"))
        steps = body.get("steps")            # None ⇒ all
        source_ids = body.get("source_ids")  # None ⇒ all sources

        task = ingest_tenant_task.apply_async(
            args=(tenant_id, source_ids, steps, None),
        )
        return JsonResponse({"task_id": task.id, "status": "started"})


class IngestStepAPI(View):
    """POST → dispatch ingestion limited to a subset of pipeline steps.

    Body: {"tenant_id": "dssat", "step": "build_graph"}.
    Thin wrapper over IngestAPI for single-step UI buttons.
    """

    def post(self, request):
        from knowledge_agent.tasks import ingest_tenant_task
        from knowledge_agent.store import _tenant_str

        try:
            body = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            return JsonResponse({"error": "Invalid JSON"}, status=400)
        step = body.get("step", "").strip()
        if not step:
            return JsonResponse({"error": "Missing 'step' field"}, status=400)
        tenant_id = _tenant_str(body.get("tenant_id"))

        task = ingest_tenant_task.apply_async(
            args=(tenant_id, None, [step], None),
        )
        return JsonResponse(
            {"task_id": task.id, "step": step, "status": "started"}
        )


class IngestStatusAPI(View):
    """GET → poll Celery task progress by task_id."""

    def get(self, request, task_id):
        result = AsyncResult(task_id)
        state = result.state

        if state == "PENDING":
            payload = {"status": "pending"}
        elif state == "PROGRESS":
            payload = {"status": "progress", **(result.info or {})}
        elif state == "SUCCESS":
            payload = {"status": "completed", "result": result.result}
        elif state == "FAILURE":
            payload = {"status": "failed", "error": str(result.result)}
        else:
            payload = {"status": state.lower()}

        return JsonResponse(payload)


class DocumentsAPI(View):
    """GET → list document sources with chunk counts."""

    def get(self, request):
        data = services.get_document_sources()
        return JsonResponse(data)


class ChunksAPI(View):
    """GET → paginated chunks for a source slug."""

    def get(self, request, source_slug):
        page = int(request.GET.get("page", 1))
        per_page = int(request.GET.get("per_page", 50))
        data = services.get_chunks(source_slug, page=page, per_page=per_page)
        return JsonResponse(data)


class ActiveTaskAPI(View):
    """GET  → current active ingestion task_id (or null).
       POST → store a task_id (body: {"task_id": "..."}).
       DELETE → clear the stored task_id."""

    def get(self, request):
        try:
            from knowledge_agent.store import VectorStore
            store = VectorStore()
            task_id = store.get_setting("active_task_id")
        except Exception:
            task_id = None
        return JsonResponse({"task_id": task_id})

    def post(self, request):
        try:
            body = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            return JsonResponse({"error": "Invalid JSON"}, status=400)
        task_id = body.get("task_id", "").strip()
        if not task_id:
            return JsonResponse({"error": "Missing 'task_id'"}, status=400)
        try:
            from knowledge_agent.store import VectorStore
            store = VectorStore()
            store.set_setting("active_task_id", task_id)
        except Exception as e:
            return JsonResponse({"error": str(e)}, status=500)
        return JsonResponse({"task_id": task_id})

    def delete(self, request):
        try:
            from knowledge_agent.store import VectorStore
            store = VectorStore()
            store.set_setting("active_task_id", "")
        except Exception:
            pass
        return JsonResponse({"task_id": None})


class ReadinessAPI(View):
    """GET → table row counts and per-strategy readiness."""

    def get(self, request):
        data = services.list_strategies()
        active = services.get_active_strategy()
        try:
            from knowledge_agent.store import VectorStore
            store = VectorStore()
            row_counts = store.table_row_counts()
        except Exception:
            row_counts = {}
        data["row_counts"] = row_counts
        data["active_strategy"] = active
        return JsonResponse(data)


# ===========================================================================
# Per-document inspection endpoints
# ===========================================================================

class SourceContextualAPI(View):
    """GET /api/source/<slug>/contextual/?page=N&per_page=M

    Paginated contextual-enriched chunks for a single source.
    """

    def get(self, request, source_slug):
        page = max(1, int(request.GET.get("page", 1)))
        per_page = max(1, min(int(request.GET.get("per_page", 25)), 200))
        offset = (page - 1) * per_page
        rows = VectorStore().get_contextual_chunks_for_source(
            source_slug, limit=per_page, offset=offset,
        )
        return JsonResponse({"contextual_chunks": rows, "page": page, "per_page": per_page})


class SourceParentChildAPI(View):
    """GET /api/source/<slug>/parent-child/

    Parent + nested children chunks for one source. No pagination —
    parent chunks are typically << base chunks, so the full list fits.
    """

    def get(self, request, source_slug):
        rows = VectorStore().get_parent_child_for_source(source_slug)
        return JsonResponse({"parents": rows})


class SourceGraphAPI(View):
    """GET /api/source/<slug>/graph/

    GraphRAG subgraph for one source — every entity reachable from
    this source's chunks plus the relationships between them.
    Output is in cytoscape.js elements format.
    """

    def get(self, request, source_slug):
        data = VectorStore().get_graph_for_source(source_slug)
        return JsonResponse(data)


class SourceRaptorAPI(View):
    """GET /api/source/<slug>/raptor/?max_depth=N

    RAPTOR subtree for one source — leaves with metadata matching this
    source plus all ancestor summary nodes (walked upward up to max_depth).
    """

    def get(self, request, source_slug):
        max_depth = max(1, min(int(request.GET.get("max_depth", 3)), 10))
        data = VectorStore().get_raptor_subtree_for_source(
            source_slug, max_depth=max_depth,
        )
        return JsonResponse(data)


class SourceOntologyAPI(View):
    """GET /api/source/<slug>/ontology/

    Returns the corpus-wide ontology schema (TBox — same for every doc
    in a tenant) alongside per-source contribution stats.
    """

    def get(self, request, source_slug):
        data = VectorStore().get_ontology_per_source(source_slug)
        return JsonResponse(data)


@method_decorator(csrf_exempt, name='dispatch')
class SourceQueryAPI(View):
    """POST /api/source/<slug>/query/

    Run any retrieval strategy with this source as a metadata filter.
    Body: {"query": "...", "strategy": "...", "top_k": N,
           "restrict_to_doc": true/false}
    """

    def post(self, request, source_slug):
        try:
            body = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            return JsonResponse({"error": "Invalid JSON"}, status=400)

        query = (body.get("query") or "").strip()
        if not query:
            return JsonResponse({"error": "Missing 'query'"}, status=400)
        strategy = body.get("strategy") or services.get_active_strategy()
        top_k = max(1, min(int(body.get("top_k", 5)), 50))
        restrict = bool(body.get("restrict_to_doc", True))

        filters = {"source_slug": source_slug} if restrict else None
        result = services.retrieve(
            query=query, strategy=strategy, top_k=top_k, filters=filters,
        )
        return JsonResponse(result)


class EntitySourcesAPI(View):
    """GET /api/entity/<entity_id>/sources/

    Given an entity ID (the canonical KnowledgeEntity row), return the
    list of source documents whose chunks contribute relationships
    involving this entity. Powers the "click a node → which docs
    mention it" interaction in graph viz.
    """

    def get(self, request, entity_id):
        rows = VectorStore().get_entity_sources(entity_id)
        return JsonResponse({"entity_id": entity_id, "sources": rows})


# ===========================================================================
# Corpus-wide inspection endpoints
# ===========================================================================

class InspectGraphAPI(View):
    """GET /api/inspect/graphrag/?max_edges=N

    Full-corpus GraphRAG graph (capped). Truncated flag indicates if
    the result was clipped.
    """

    def get(self, request):
        max_edges = max(100, min(int(request.GET.get("max_edges", 2000)), 10000))
        data = VectorStore().get_graph_full(max_edges=max_edges)
        return JsonResponse(data)


class InspectRaptorAPI(View):
    """GET /api/inspect/raptor/?max_depth=N

    Full-corpus RAPTOR tree up to max_depth levels.
    """

    def get(self, request):
        max_depth = max(1, min(int(request.GET.get("max_depth", 5)), 10))
        data = VectorStore().get_raptor_tree_full(max_depth=max_depth)
        return JsonResponse(data)


class InspectOntologyAPI(View):
    """GET /api/inspect/ontology/

    Full-corpus ontology: schema (TBox) + parsed individuals grouped
    by class + triple counts.
    """

    def get(self, request):
        data = VectorStore().get_ontology_full()
        return JsonResponse(data)
