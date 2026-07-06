"""
Diagnostic dump for the knowledge agent.

Shows per-tenant:
- Dirty flags (raptor_dirty, graphrag_dirty, ontology_dirty)
- Table row counts (chunks, contextual, parents, children, entities, …)
- Source count + last_ingested_at
- Last 3 ingestion runs (status, duration, errors)

Usage:
    python manage.py check_knowledge
    python manage.py check_knowledge --tenant=dssat
"""

from __future__ import annotations

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Inspect knowledge-agent tenants, dirty flags, and ingestion history."

    def add_arguments(self, parser):
        parser.add_argument(
            "--tenant", type=str, default=None,
            help="Limit to one tenant. Default: all tenants.",
        )

    def handle(self, *args, **opts):
        from knowledge_agent.models import (
            KnowledgeIngestionRun, KnowledgeSource, KnowledgeTenant,
        )
        from knowledge_agent.store import VectorStore

        qs = KnowledgeTenant.objects.all().order_by("id")
        if opts["tenant"]:
            qs = qs.filter(id=opts["tenant"])

        if not qs.exists():
            self.stdout.write(self.style.WARNING("No tenants found."))
            return

        store = VectorStore()
        for t in qs:
            self.stdout.write(self.style.MIGRATE_HEADING(f"Tenant: {t.id}"))
            self.stdout.write(f"  display_name:     {t.display_name!r}")
            self.stdout.write(f"  default_strategy: {t.default_strategy!r}")

            # Dirty flags
            flags = []
            if t.raptor_dirty:   flags.append("raptor")
            if t.graphrag_dirty: flags.append("graphrag")
            if t.ontology_dirty: flags.append("ontology")
            if flags:
                self.stdout.write(self.style.WARNING(
                    f"  dirty flags:      {', '.join(flags)} "
                    f"(run `manage.py ingest_knowledge --tenant={t.id}`)"
                ))
            else:
                self.stdout.write(f"  dirty flags:      (none)")

            # Row counts per data table
            counts = store.table_row_counts(tenant_id=t.id)
            self.stdout.write("  row counts:")
            for name, n in counts.items():
                self.stdout.write(f"    {name:<32} {n:>8}")

            # Sources
            sources = KnowledgeSource.objects.filter(tenant=t).order_by("category", "label")
            self.stdout.write(f"  sources ({sources.count()}):")
            for s in sources:
                last = s.last_ingested_at.isoformat() if s.last_ingested_at else "(never)"
                self.stdout.write(
                    f"    #{s.id:<4} {s.label or s.path}  "
                    f"category={s.category or '-'}  last_ingested={last}"
                )

            # Recent runs
            runs = KnowledgeIngestionRun.objects.filter(tenant=t)[:3]
            if runs:
                self.stdout.write("  recent ingestion runs:")
                for r in runs:
                    started = r.started_at.isoformat() if r.started_at else "-"
                    done = r.completed_at.isoformat() if r.completed_at else "-"
                    self.stdout.write(
                        f"    #{r.id:<5} {r.status:<10} "
                        f"started={started}  done={done}  chunks={r.chunks_created}"
                    )
                    if r.error_message:
                        first_line = r.error_message.splitlines()[0]
                        self.stdout.write(self.style.ERROR(
                            f"           error: {first_line[:120]}"
                        ))

            self.stdout.write("")
