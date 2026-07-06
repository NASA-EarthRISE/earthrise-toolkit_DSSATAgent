"""
Tenant-aware ingestion command — the canonical entrypoint for running ingestion.

Usage:
    python manage.py ingest_knowledge --tenant=dssat
    python manage.py ingest_knowledge --tenant=dssat --source=42
    python manage.py ingest_knowledge --all-tenants
    python manage.py ingest_knowledge --tenant=dssat \
        --steps=chunk_and_embed,build_graph
    python manage.py ingest_knowledge --tenant=dssat --force         # ignore dirty flags
    python manage.py ingest_knowledge --tenant=dssat --sync          # run in-process (no Celery)
    python manage.py ingest_knowledge --tenant=dssat --no-wait       # fire-and-forget

Default behavior:
    Dispatches the ingestion DAG via Celery. Each PDF chunks + embeds
    in parallel; downstream pipelines (contextual, graph, raptor,
    ontology) fan out 4-way; within each, per-chunk tasks parallelize
    across Celery workers. Waits synchronously on the top-level
    AsyncResult unless --no-wait is set.

    Step selection follows the same smart logic as before:
    chunk_and_embed + parent_child + contextual always run;
    raptor/graph/ontology run when their dirty flag is set or the table
    is empty. Override with --steps or --force.

--sync routes through CELERY_TASK_ALWAYS_EAGER so the same Celery task
code path runs synchronously in-process — useful for debugging.
"""

from __future__ import annotations

import logging
import traceback
from datetime import datetime, timezone

from django.core.management.base import BaseCommand, CommandError


ALL_STEPS = [
    "chunk_and_embed",
    "build_parent_child",
    "build_contextual",
    "build_graph",
    "build_raptor",
    "build_ontology",
]

_STEP_TO_DIRTY_FLAG = {
    "build_raptor":   "raptor_dirty",
    "build_graph":    "graphrag_dirty",
    "build_ontology": "ontology_dirty",
}

_STEP_TO_TABLE = {
    "build_raptor":   "knowledge_raptor_nodes",
    "build_graph":    "knowledge_entities",
    "build_ontology": "knowledge_ontology",
}


class Command(BaseCommand):
    help = "Run the tenant-aware Celery-parallelized knowledge ingestion pipeline."

    def add_arguments(self, parser):
        parser.add_argument(
            "--tenant", type=str,
            help="Tenant ID to ingest. Required unless --all-tenants is set.",
        )
        parser.add_argument(
            "--all-tenants", action="store_true",
            help="Ingest every registered tenant sequentially.",
        )
        parser.add_argument(
            "--source", type=int,
            help="Optional KnowledgeSource ID to limit ingestion to one source.",
        )
        parser.add_argument(
            "--steps", type=str, default=None,
            help=f"Comma-separated subset of steps to run. "
                 f"Available: {','.join(ALL_STEPS)}. Default: smart selection.",
        )
        parser.add_argument(
            "--force", action="store_true",
            help="Run all selected steps regardless of dirty flags.",
        )
        parser.add_argument(
            "--sync", action="store_true",
            help="Run inline via Celery's ALWAYS_EAGER mode — no broker, "
                 "no workers, sequential. Same task code path as the "
                 "Celery dispatch — useful for debugging.",
        )
        parser.add_argument(
            "--no-wait", action="store_true",
            help="Dispatch the orchestrator task and return immediately. "
                 "Without this flag, the command waits for the entire "
                 "pipeline to complete (or fail).",
        )
        parser.add_argument(
            "--timeout", type=int, default=14400,
            help="Seconds to wait for completion when not --no-wait "
                 "(default 4 hours).",
        )
        parser.add_argument(
            "--log-level", default="INFO",
            choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        )

    def handle(self, *args, **opts):
        logging.basicConfig(
            level=getattr(logging, opts["log_level"]),
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        )

        if opts["sync"]:
            # CELERY_TASK_ALWAYS_EAGER routes all .apply_async() calls
            # through .apply() so they run in-process synchronously.
            # Same task code, no broker required.
            from django.conf import settings
            settings.CELERY_TASK_ALWAYS_EAGER = True
            settings.CELERY_TASK_EAGER_PROPAGATES = True
            self.stdout.write(self.style.NOTICE(
                "Running in --sync mode (CELERY_TASK_ALWAYS_EAGER=True). "
                "No broker, no workers, sequential execution."
            ))

        from knowledge_agent.models import KnowledgeTenant

        if opts["all_tenants"]:
            tenant_ids = list(KnowledgeTenant.objects.values_list("id", flat=True))
            if not tenant_ids:
                self.stdout.write(self.style.WARNING("No tenants registered."))
                return
        elif opts["tenant"]:
            tenant_ids = [opts["tenant"]]
        else:
            raise CommandError("Pass --tenant=<id> or --all-tenants.")

        requested_steps = None
        if opts["steps"]:
            requested_steps = [s.strip() for s in opts["steps"].split(",") if s.strip()]
            invalid = [s for s in requested_steps if s not in ALL_STEPS]
            if invalid:
                raise CommandError(
                    f"Unknown steps: {invalid}. Valid: {ALL_STEPS}"
                )

        any_failed = False
        for tid in tenant_ids:
            try:
                self._ingest_tenant(
                    tenant_id=tid,
                    source_id=opts["source"],
                    requested_steps=requested_steps,
                    force=opts["force"],
                    no_wait=opts["no_wait"],
                    timeout=opts["timeout"],
                )
            except Exception as e:
                any_failed = True
                logging.exception("Ingestion failed for tenant %r", tid)
                self.stderr.write(self.style.ERROR(f"FAIL tenant={tid}: {e}"))

        if any_failed:
            raise CommandError("One or more tenant ingestions failed.")

    # ------------------------------------------------------------------
    # Per-tenant dispatch
    # ------------------------------------------------------------------

    def _ingest_tenant(
        self, *, tenant_id, source_id, requested_steps, force,
        no_wait, timeout,
    ):
        from knowledge_agent.models import (
            KnowledgeIngestionRun, KnowledgeSource, KnowledgeTenant,
        )
        from knowledge_agent.store import VectorStore
        from knowledge_agent.tasks import ingest_tenant_task

        tenant = KnowledgeTenant.objects.get(id=tenant_id)

        # Determine which steps to run
        if requested_steps is not None:
            steps_to_run = list(requested_steps)
            reason = "explicit --steps"
        elif force:
            steps_to_run = list(ALL_STEPS)
            reason = "--force"
        else:
            steps_to_run = [
                "chunk_and_embed", "build_parent_child", "build_contextual",
            ]
            row_counts = VectorStore().table_row_counts(tenant_id=tenant_id)
            for step, flag in _STEP_TO_DIRTY_FLAG.items():
                table = _STEP_TO_TABLE[step]
                if getattr(tenant, flag) or row_counts.get(table, 0) == 0:
                    steps_to_run.append(step)
            reason = "smart-selected from dirty flags + table population"

        self.stdout.write(self.style.NOTICE(
            f"Tenant {tenant_id!r}: dispatching steps {steps_to_run} ({reason})"
        ))

        # Source filter
        sources_qs = KnowledgeSource.objects.filter(tenant=tenant)
        if source_id is not None:
            sources_qs = sources_qs.filter(id=source_id)
        source_ids = list(sources_qs.values_list("id", flat=True))
        if not source_ids:
            self.stdout.write(self.style.WARNING(
                f"Tenant {tenant_id!r} has no sources matching the filter — "
                f"nothing to ingest."
            ))
            return

        # Create the IngestionRun row upfront so check_knowledge shows
        # the in-flight state immediately
        run = KnowledgeIngestionRun.objects.create(
            tenant=tenant,
            source_id=source_id,
            status="running",
            started_at=datetime.now(timezone.utc),
            pipelines_run=steps_to_run,
        )

        # Dispatch the orchestrator — same call shape whether Celery or
        # ALWAYS_EAGER. apply_async() returns immediately in both cases.
        async_result = ingest_tenant_task.apply_async(
            args=(tenant_id, source_ids, steps_to_run, run.id),
        )
        self.stdout.write(
            f"  → orchestrator dispatched: task_id={async_result.id} run_id={run.id}"
        )

        if no_wait:
            self.stdout.write(self.style.SUCCESS(
                "  (--no-wait) Returning immediately. Monitor with: "
                f"`python manage.py check_knowledge --tenant={tenant_id}`"
            ))
            return

        # Wait for the orchestrator's return — but that only confirms
        # the FIRST chord was dispatched (the rest of the canvas runs
        # asynchronously). For real completion, poll the run row.
        try:
            async_result.get(timeout=60)
        except Exception as e:
            self.stderr.write(self.style.ERROR(
                f"  ✗ orchestrator dispatch failed: {e}"
            ))
            raise

        # Poll KnowledgeIngestionRun.status until the finalize callback
        # flips it. In CELERY_TASK_ALWAYS_EAGER mode the run is already
        # completed by the time we get here, so the first poll succeeds.
        import time
        from knowledge_agent.models import KnowledgeIngestionRun

        deadline = time.monotonic() + timeout
        poll_interval = 5
        last_chunk_count = -1
        while time.monotonic() < deadline:
            run.refresh_from_db()
            if run.status in ("completed", "failed"):
                break
            if run.chunks_created != last_chunk_count:
                self.stdout.write(
                    f"  … status={run.status} chunks={run.chunks_created}"
                )
                last_chunk_count = run.chunks_created
            time.sleep(poll_interval)
        else:
            self.stderr.write(self.style.ERROR(
                f"  ✗ timeout after {timeout}s — pipeline still running. "
                f"Check `manage.py check_knowledge --tenant={tenant_id}`"
            ))
            raise CommandError(f"Ingestion timed out after {timeout}s")

        if run.status == "completed":
            self.stdout.write(self.style.SUCCESS(
                f"  ✓ run #{run.id} completed: {run.chunks_created} chunks"
            ))
        else:
            self.stderr.write(self.style.ERROR(
                f"  ✗ run #{run.id} failed: {run.error_message[:200]}"
            ))
            raise CommandError(f"Ingestion failed: {run.error_message[:200]}")
