"""
Django ORM models for the knowledge agent.

Layered as:
- KnowledgeTenant: the isolation boundary. Owns per-tenant config (domain
  hint, entity/relationship vocabularies, optional ontology block, default
  strategy). One row per deployed corpus context. v1: one tenant per
  deployment in practice.
- KnowledgeSource: a registered document origin (directory). Belongs to
  exactly one tenant. Many per tenant.
- Eight data tables (chunks, contextual, parent/child, entities,
  relationships, raptor nodes, ontology): each row carries a `tenant` FK
  so queries filter by tenant via the `TenantManager.for_tenant()`
  primitive.
- KnowledgeConfig: per-(tenant, user) preferences. Strategy resolution
  chain: user pref → tenant default → 'hybrid' constant.
- KnowledgeIngestionRun: per-tenant audit trail of ingestion executions.

Data lives in the dedicated `knowledge` Postgres schema.
"""

import uuid

from django.conf import settings
from django.contrib.postgres.fields import ArrayField
from django.contrib.postgres.search import SearchVector, SearchVectorField
from django.db import models
from django.db.models import GeneratedField

from pgvector.django import HnswIndex, VectorField

from . import config


def _uuid_str() -> str:
    """Default for TEXT primary keys storing UUIDs as strings.

    Kept module-level (not a lambda) so Django's migration autodetector
    can serialize it.
    """
    return str(uuid.uuid4())


_VECTOR_DIM = config.VECTOR_DIM


def _schema_table(name: str) -> str:
    """Compose a Django `db_table` value targeting the `knowledge` schema.

    The double-quote trick closes the quote Django wraps around `db_table`,
    inserts the schema separator, then reopens — yielding
    `"knowledge"."<name>"` in generated SQL.
    """
    return f'knowledge"."{name}'


# ---------------------------------------------------------------------------
# Tenant manager — the primitive every retrieval path goes through
# ---------------------------------------------------------------------------

class TenantManager(models.Manager):
    """Default manager that adds a tenant-scoping primitive.

    All queries that should be tenant-isolated must route through
    `.for_tenant(tenant)`. Forgetting to call it is the failure mode this
    method exists to make obvious in code review.

    A few legitimate operations (admin analytics, migrations, dirty-tenant
    detection) need cross-tenant access; for those, use `Model.objects.all()`
    explicitly. Not adding an `unscoped()` helper because the verbose form
    is the right amount of friction.
    """

    def for_tenant(self, tenant):
        """Filter to one tenant. Accepts a `KnowledgeTenant` instance or
        a tenant_id string."""
        if tenant is None:
            raise ValueError(
                "for_tenant(None) is not allowed — pass a KnowledgeTenant "
                "or tenant_id string. Use Model.objects.all() if you "
                "intentionally want cross-tenant access."
            )
        if isinstance(tenant, str):
            return self.filter(tenant_id=tenant)
        return self.filter(tenant=tenant)


# ---------------------------------------------------------------------------
# Tenant + source + ingestion-run config models
# ---------------------------------------------------------------------------

class KnowledgeTenant(models.Model):
    """The isolation boundary plus per-tenant LLM-vocabulary configuration.

    `domain` is a JSONField that holds the validated `DomainConfig` shape:
    `{hint, entity_types, relationship_types, ontology: {namespace, prefix,
    relationship_typing, data_properties}}`. The `*_config_hash` columns
    let the registration layer detect granular changes and trigger
    selective re-ingestion (only RAPTOR if `hint` changed; GraphRAG +
    ontology if entity/relationship types changed; ontology alone if only
    typing/namespace/properties changed).
    """

    id = models.CharField(
        primary_key=True,
        max_length=64,
        help_text="The tenant_id slug — e.g. 'legal', 'agronomy'. "
                  "Pattern: lowercase letters, digits, underscore, dash.",
    )
    display_name = models.CharField(max_length=200, blank=True, default="")
    default_strategy = models.CharField(max_length=32, default="hybrid")
    domain = models.JSONField(default=dict, blank=True)

    # Hashes used by selective re-ingestion. When the
    # current registered config produces a different hash than what's
    # stored here, the corresponding `*_dirty` flag is set and the next
    # ingest_knowledge run regenerates only that pipeline's outputs.
    raptor_config_hash = models.CharField(max_length=64, blank=True, default="")
    graphrag_config_hash = models.CharField(max_length=64, blank=True, default="")
    ontology_config_hash = models.CharField(max_length=64, blank=True, default="")
    raptor_dirty = models.BooleanField(default=False)
    graphrag_dirty = models.BooleanField(default=False)
    ontology_dirty = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = _schema_table("knowledge_tenants")

    def __str__(self):
        return self.display_name or self.id


class KnowledgeSource(models.Model):
    """A registered document directory belonging to one tenant.

    `path` is the absolute filesystem path the ingestion pipeline scans.
    `last_ingested_at` is the change-detection signal — it's set when an
    ingestion run completes successfully for this source. Triggering
    ingestion is a runtime concern (`manage.py ingest_knowledge --tenant=
    X [--source=Y]`); there is no boolean flag on the model.
    """

    id = models.BigAutoField(primary_key=True)
    tenant = models.ForeignKey(
        KnowledgeTenant,
        on_delete=models.CASCADE,
        related_name="sources",
    )
    path = models.TextField()
    label = models.CharField(max_length=200, blank=True, default="")
    category = models.CharField(max_length=200, blank=True, default="")
    agent_label = models.CharField(max_length=64, blank=True, default="default")
    last_ingested_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = _schema_table("knowledge_sources")
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "path"],
                name="uq_source_tenant_path",
            ),
        ]

    def __str__(self):
        return f"{self.tenant_id}:{self.label or self.path}"


class KnowledgeIngestionPartial(models.Model):
    """Durable scratch storage for chord-member results during ingestion.

    Solves the CELERY_RESULT_EXPIRES race: when a per-chunk Celery task
    finishes, instead of returning its payload (which evaporates from
    Redis after 1 hour), it writes the payload here. The chord callback
    then reads from this table and deletes the rows once aggregation
    completes successfully.

    Primary user is the ontology branch — `extract_ontology_triples_task`
    writes its triple-lists here; `assemble_ontology_task` queries them
    by (tenant, run, branch='ontology'), assembles the rdflib graph,
    inserts the final KnowledgeOntology row, then truncates these partial
    rows. Suitable for any future branch whose chord aggregator needs
    to survive long pipeline durations.
    """

    id = models.BigAutoField(primary_key=True)
    tenant = models.ForeignKey(
        "KnowledgeTenant",
        on_delete=models.CASCADE,
        related_name="ingestion_partials",
    )
    run = models.ForeignKey(
        "KnowledgeIngestionRun",
        on_delete=models.CASCADE,
        related_name="partials",
    )
    branch = models.CharField(
        max_length=32,
        help_text="Pipeline branch name: 'ontology', 'raptor_summary', etc.",
    )
    chunk_id = models.TextField(null=True, blank=True)
    payload = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = _schema_table("knowledge_ingestion_partials")
        indexes = [
            models.Index(fields=["tenant", "run", "branch"],
                         name="idx_partial_run_branch"),
        ]


class KnowledgeIngestionRun(models.Model):
    """Per-tenant audit trail of ingestion executions.

    Replaces the `active_task_id` knowledge_settings KV pair. Created at
    the start of every `ingest_knowledge` invocation; updated as the run
    progresses through the pipeline stages.
    """

    STATUS_CHOICES = [
        ("pending",   "Pending"),
        ("running",   "Running"),
        ("completed", "Completed"),
        ("failed",    "Failed"),
    ]

    id = models.BigAutoField(primary_key=True)
    tenant = models.ForeignKey(
        KnowledgeTenant,
        on_delete=models.CASCADE,
        related_name="ingestion_runs",
    )
    source = models.ForeignKey(
        KnowledgeSource,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ingestion_runs",
        help_text="Null means the run targeted all sources for the tenant.",
    )
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default="pending")
    pipelines_run = models.JSONField(
        default=list,
        blank=True,
        help_text="Names of pipeline stages executed: chunk_and_embed, "
                  "build_contextual, build_parent_child, build_graph, "
                  "build_raptor, build_ontology.",
    )
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    chunks_created = models.IntegerField(default=0)
    error_message = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = _schema_table("knowledge_ingestion_runs")
        ordering = ["-created_at"]

    def __str__(self):
        scope = self.source_id or "all"
        return f"IngestionRun({self.tenant_id}/{scope}): {self.status}"


# ---------------------------------------------------------------------------
# Base content chunks
# ---------------------------------------------------------------------------

class KnowledgeChunk(models.Model):
    """The atomic unit of retrievable content. One row per parsed chunk."""

    id = models.TextField(primary_key=True, default=_uuid_str)
    tenant = models.ForeignKey(
        KnowledgeTenant,
        on_delete=models.CASCADE,
        related_name="chunks",
    )
    content = models.TextField()
    embedding = VectorField(dimensions=_VECTOR_DIM, null=True, blank=True)
    content_tsv = GeneratedField(
        expression=SearchVector("content", config="english"),
        output_field=SearchVectorField(),
        db_persist=True,
    )
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = TenantManager()

    class Meta:
        db_table = _schema_table("knowledge_chunks")
        indexes = [
            HnswIndex(
                name="idx_chunks_embedding",
                fields=["embedding"],
                m=16,
                ef_construction=64,
                opclasses=["vector_cosine_ops"],
            ),
            models.Index(fields=["tenant"], name="idx_chunks_tenant"),
        ]


class KnowledgeChunkContextual(models.Model):
    """Context-enriched copies of base chunks (for the `contextual` strategy)."""

    id = models.TextField(primary_key=True, default=_uuid_str)
    tenant = models.ForeignKey(
        KnowledgeTenant,
        on_delete=models.CASCADE,
        related_name="contextual_chunks",
    )
    base_chunk = models.ForeignKey(
        KnowledgeChunk,
        on_delete=models.CASCADE,
        related_name="contextual_copies",
        db_column="base_chunk_id",
        null=True,
        blank=True,
    )
    content = models.TextField()
    context_prefix = models.TextField(blank=True, default="")
    embedding = VectorField(dimensions=_VECTOR_DIM, null=True, blank=True)
    content_tsv = GeneratedField(
        expression=SearchVector("content", config="english"),
        output_field=SearchVectorField(),
        db_persist=True,
    )
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = TenantManager()

    class Meta:
        db_table = _schema_table("knowledge_chunks_contextual")
        indexes = [
            HnswIndex(
                name="idx_ctx_chunks_embedding",
                fields=["embedding"],
                m=16,
                ef_construction=64,
                opclasses=["vector_cosine_ops"],
            ),
            models.Index(fields=["tenant"], name="idx_ctx_chunks_tenant"),
        ]


# ---------------------------------------------------------------------------
# Parent / child chunks (for the `parent_child` strategy)
# ---------------------------------------------------------------------------

class KnowledgeParentChunk(models.Model):
    id = models.TextField(primary_key=True, default=_uuid_str)
    tenant = models.ForeignKey(
        KnowledgeTenant,
        on_delete=models.CASCADE,
        related_name="parent_chunks",
    )
    content = models.TextField()
    embedding = VectorField(dimensions=_VECTOR_DIM, null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    objects = TenantManager()

    class Meta:
        db_table = _schema_table("knowledge_parent_chunks")
        indexes = [
            models.Index(fields=["tenant"], name="idx_parent_tenant"),
        ]


class KnowledgeChildChunk(models.Model):
    id = models.TextField(primary_key=True, default=_uuid_str)
    tenant = models.ForeignKey(
        KnowledgeTenant,
        on_delete=models.CASCADE,
        related_name="child_chunks",
    )
    parent = models.ForeignKey(
        KnowledgeParentChunk,
        on_delete=models.CASCADE,
        related_name="children",
        db_column="parent_id",
        null=True,
        blank=True,
    )
    content = models.TextField()
    embedding = VectorField(dimensions=_VECTOR_DIM, null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    objects = TenantManager()

    class Meta:
        db_table = _schema_table("knowledge_child_chunks")
        indexes = [
            HnswIndex(
                name="idx_child_embedding",
                fields=["embedding"],
                m=16,
                ef_construction=64,
                opclasses=["vector_cosine_ops"],
            ),
            models.Index(fields=["tenant"], name="idx_child_tenant"),
        ]


# ---------------------------------------------------------------------------
# GraphRAG entities + relationships
# ---------------------------------------------------------------------------

class KnowledgeEntity(models.Model):
    id = models.TextField(primary_key=True, default=_uuid_str)
    tenant = models.ForeignKey(
        KnowledgeTenant,
        on_delete=models.CASCADE,
        related_name="entities",
    )
    name = models.TextField()
    entity_type = models.TextField()
    description = models.TextField(blank=True, default="")
    embedding = VectorField(dimensions=_VECTOR_DIM, null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    objects = TenantManager()

    class Meta:
        db_table = _schema_table("knowledge_entities")
        constraints = [
            # Same (name, type) can exist in different tenants — entity
            # vocabularies are tenant-specific.
            models.UniqueConstraint(
                fields=["tenant", "name", "entity_type"],
                name="uq_entity_tenant_name_type",
            ),
        ]
        indexes = [
            HnswIndex(
                name="idx_entity_embedding",
                fields=["embedding"],
                m=16,
                ef_construction=64,
                opclasses=["vector_cosine_ops"],
            ),
            models.Index(fields=["tenant"], name="idx_entity_tenant"),
        ]


class KnowledgeRelationship(models.Model):
    id = models.TextField(primary_key=True, default=_uuid_str)
    tenant = models.ForeignKey(
        KnowledgeTenant,
        on_delete=models.CASCADE,
        related_name="relationships",
    )
    source_entity = models.ForeignKey(
        KnowledgeEntity,
        on_delete=models.CASCADE,
        related_name="outgoing_relationships",
        db_column="source_id",
        null=True,
        blank=True,
    )
    target_entity = models.ForeignKey(
        KnowledgeEntity,
        on_delete=models.CASCADE,
        related_name="incoming_relationships",
        db_column="target_id",
        null=True,
        blank=True,
    )
    relationship = models.TextField()
    weight = models.FloatField(default=1.0)
    source_chunk_id = models.TextField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    objects = TenantManager()

    class Meta:
        db_table = _schema_table("knowledge_relationships")
        constraints = [
            # Dedupes literal LLM-duplicate extractions from the same
            # chunk (e.g. extraction prompt emits the same edge twice).
            # Different chunks producing the same logical edge still
            # create separate rows — that's provenance signal worth
            # keeping.
            models.UniqueConstraint(
                fields=["tenant", "source_entity", "target_entity",
                        "relationship", "source_chunk_id"],
                name="uq_rel_tenant_endpoints_rel_chunk",
            ),
        ]
        indexes = [
            models.Index(fields=["tenant"], name="idx_rel_tenant"),
        ]


# ---------------------------------------------------------------------------
# RAPTOR summary tree
# ---------------------------------------------------------------------------

class KnowledgeRaptorNode(models.Model):
    """Node in the RAPTOR hierarchical summary tree."""

    id = models.TextField(primary_key=True, default=_uuid_str)
    tenant = models.ForeignKey(
        KnowledgeTenant,
        on_delete=models.CASCADE,
        related_name="raptor_nodes",
    )
    content = models.TextField()
    embedding = VectorField(dimensions=_VECTOR_DIM, null=True, blank=True)
    level = models.IntegerField(default=0)
    parent_id = models.TextField(null=True, blank=True)
    children_ids = ArrayField(models.TextField(), default=list, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    objects = TenantManager()

    class Meta:
        db_table = _schema_table("knowledge_raptor_nodes")
        indexes = [
            HnswIndex(
                name="idx_raptor_embedding",
                fields=["embedding"],
                m=16,
                ef_construction=64,
                opclasses=["vector_cosine_ops"],
            ),
            models.Index(fields=["tenant"], name="idx_raptor_tenant"),
        ]


# ---------------------------------------------------------------------------
# OWL / RDF ontology
# ---------------------------------------------------------------------------

class KnowledgeOntology(models.Model):
    """Serialized OWL/RDF graph built during ingestion.

    Per-tenant. Latest version per tenant wins; on destructive re-ingest
    (entity/relationship type changes), prior versions are deleted before
    the new one is written.
    """

    id = models.TextField(primary_key=True, default=_uuid_str)
    tenant = models.ForeignKey(
        KnowledgeTenant,
        on_delete=models.CASCADE,
        related_name="ontologies",
    )
    version = models.IntegerField(default=1)
    turtle_data = models.TextField()
    source_chunk_ids = ArrayField(models.TextField(), default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = TenantManager()

    class Meta:
        db_table = _schema_table("knowledge_ontology")
        indexes = [
            models.Index(fields=["tenant"], name="idx_ontology_tenant"),
        ]


# ---------------------------------------------------------------------------
# User / system preferences (tenant-scoped)
# ---------------------------------------------------------------------------

class KnowledgeConfig(models.Model):
    """Per-(tenant, user) preferences.

    Strategy resolution chain in `services.get_active_strategy`:
        1. User pref: KnowledgeConfig(tenant=t, user=u, key='preferred_strategy')
        2. Tenant default: KnowledgeTenant(id=t).default_strategy
        3. Hardcoded 'hybrid'

    `user=NULL` is allowed (system default within a tenant). The
    tenant-wide default strategy lives on KnowledgeTenant.default_strategy;
    KnowledgeConfig holds only genuinely per-user preferences.
    """

    id = models.BigAutoField(primary_key=True)
    tenant = models.ForeignKey(
        KnowledgeTenant,
        on_delete=models.CASCADE,
        related_name="user_configs",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="knowledge_configs",
    )
    key = models.CharField(max_length=100)
    value = models.JSONField()
    description = models.TextField(blank=True, default="")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "knowledge_config"
        unique_together = [("tenant", "user", "key")]

    def __str__(self):
        owner = self.user.username if self.user else "SYSTEM"
        return f"KnowledgeConfig({self.tenant_id}/{owner}): {self.key}={self.value}"
