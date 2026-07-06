"""
Public registration API for tenants and sources.

Three ways to register, all converging through the same Pydantic
validation and ORM upsert:

  1. Django settings dict — `settings.KNOWLEDGE_AGENT['tenants']`
  2. YAML / JSON file     — `settings.KNOWLEDGE_AGENT['config_file']`
  3. Programmatic         — sister apps call `register_tenant(...)` and
                            `register_source(...)` from their AppConfig.ready()

Order of operations during `KnowledgeAgentConfig.ready()`:
  - `load_registered_config()` reads settings + file, upserts tenants
    then sources. Conflict detection between settings and file is
    fail-loud unless `allow_override=True`.
  - Programmatic calls from other apps' ready() are independent —
    they upsert directly without conflict checks.

Tenants must exist before sources are registered against them; the
loader enforces this by always processing the `tenants` block before
the `sources` block within each source.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "register_tenant",
    "register_source",
    "load_registered_config",
    "compute_domain_hashes",
]


# -------------------------------------------------------------------------
# Public single-record APIs
# -------------------------------------------------------------------------

def register_tenant(
    tenant_id: str,
    *,
    display_name: str = "",
    default_strategy: str = "hybrid",
    domain: dict | None = None,
) -> None:
    """Idempotent upsert of a tenant.

    Computes the three granular config hashes (raptor / graphrag /
    ontology) and sets the corresponding `_dirty` flag if any of them
    changed since the last registration. Chunk 5's ingestion command
    consumes those flags to decide which pipelines to re-run.
    """
    from .schemas import TenantRegistrationInput

    validated = TenantRegistrationInput(
        tenant_id=tenant_id,
        display_name=display_name or tenant_id,
        default_strategy=default_strategy,
        domain=domain or {},
        sources=[],   # sources go through register_source separately
    )
    _upsert_tenant(validated.model_dump())


def register_source(
    tenant_id: str,
    path: str,
    *,
    label: str = "",
    category: str = "",
    agent_label: str = "default",
) -> None:
    """Idempotent upsert of a source under an existing tenant.

    Raises if `tenant_id` isn't registered. Per Q3, tenants must always
    be created before their sources — this prevents typos from silently
    creating orphan source rows.
    """
    from .schemas import SourceConfig
    from ..models import KnowledgeSource, KnowledgeTenant

    if not KnowledgeTenant.objects.filter(id=tenant_id).exists():
        raise ValueError(
            f"register_source: tenant {tenant_id!r} is not registered. "
            f"Call register_tenant() first."
        )

    validated = SourceConfig(
        path=path,
        label=label,
        category=category,
        agent_label=agent_label,
    )

    KnowledgeSource.objects.update_or_create(
        tenant_id=tenant_id,
        path=validated.path,
        defaults={
            "label": validated.label,
            "category": validated.category,
            "agent_label": validated.agent_label,
        },
    )
    logger.info(
        "Source registered: tenant=%s path=%s label=%s",
        tenant_id, validated.path, validated.label,
    )


# -------------------------------------------------------------------------
# Startup loader — settings dict + YAML/JSON file
# -------------------------------------------------------------------------

def load_registered_config() -> None:
    """Load tenants + sources from Django settings and an optional file.

    Called once from `KnowledgeAgentConfig.ready()`. Idempotent on
    re-runs (used during tests that bounce the app state). Programmatic
    registration from other apps' ready() runs independently and isn't
    coordinated through this function.
    """
    from django.conf import settings

    cfg = getattr(settings, "KNOWLEDGE_AGENT", {}) or {}
    allow_override = bool(cfg.get("allow_override", False))

    settings_tenants = cfg.get("tenants", {}) or {}
    file_tenants = _read_config_file(cfg.get("config_file"))

    # Conflict detection between settings and file.
    overlap = set(settings_tenants) & set(file_tenants)
    if overlap and not allow_override:
        raise RuntimeError(
            f"Tenant conflict: {sorted(overlap)} defined in both "
            f"settings.KNOWLEDGE_AGENT['tenants'] and "
            f"{cfg.get('config_file')!r}. Either remove one or set "
            f"KNOWLEDGE_AGENT['allow_override']=True to let the file "
            f"override settings."
        )

    # File wins when override is allowed (last-write semantics — the
    # external file is usually the deploy-time authoritative source).
    merged: dict[str, Any] = {**settings_tenants}
    merged.update(file_tenants)

    if not merged:
        logger.info(
            "No tenants registered via settings or config file. "
            "Other apps may register programmatically via "
            "knowledge_agent.registry.register_tenant()."
        )
        return

    # Two-pass: first register every tenant, then every source. This
    # respects the "tenant-first" rule (Q3) even when the input dict
    # interleaves them.
    from .schemas import TenantRegistrationInput

    validated_list: list[TenantRegistrationInput] = []
    for tid, raw in merged.items():
        try:
            validated_list.append(TenantRegistrationInput(tenant_id=tid, **raw))
        except Exception as e:
            raise RuntimeError(
                f"Tenant {tid!r} config failed validation: {e}"
            ) from e

    for v in validated_list:
        _upsert_tenant(v.model_dump())
    for v in validated_list:
        for src in v.sources:
            register_source(
                tenant_id=v.tenant_id,
                path=src.path,
                label=src.label,
                category=src.category,
                agent_label=src.agent_label,
            )


def _read_config_file(path: str | None) -> dict:
    """Load a YAML or JSON config file. Returns {} if path is None or
    the file doesn't exist (logs a warning in the latter case)."""
    if not path:
        return {}
    if not os.path.exists(path):
        logger.warning(
            "KNOWLEDGE_AGENT['config_file']=%s does not exist; skipping.",
            path,
        )
        return {}

    with open(path) as f:
        text = f.read()

    ext = os.path.splitext(path)[1].lower()
    if ext in (".yml", ".yaml"):
        import yaml
        data = yaml.safe_load(text) or {}
    elif ext == ".json":
        data = json.loads(text)
    else:
        raise RuntimeError(
            f"KNOWLEDGE_AGENT['config_file']={path!r}: unsupported "
            f"extension. Use .yaml/.yml or .json."
        )

    return (data.get("tenants") or {}) if isinstance(data, dict) else {}


# -------------------------------------------------------------------------
# Hash + dirty-flag bookkeeping
# -------------------------------------------------------------------------

def compute_domain_hashes(domain: dict) -> dict[str, str]:
    """Compute the three granular config hashes from a domain dict.

    Used to detect what changed when a tenant is re-registered, so the
    next `ingest_knowledge` run skips up-to-date pipelines (chunk 5,
    task #25).
    """
    def _h(obj) -> str:
        # canonical JSON → SHA256[:16]. Truncated for column width;
        # 16 hex chars = 64 bits, plenty for change detection.
        canonical = json.dumps(obj, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()[:16]

    domain = domain or {}
    hint = domain.get("hint", "")
    entity_types = domain.get("entity_types", []) or []
    relationship_types = domain.get("relationship_types", []) or []
    ontology = domain.get("ontology") or {}

    return {
        "raptor_config_hash":   _h({"hint": hint}),
        "graphrag_config_hash": _h({
            "entity_types": sorted(entity_types),
            "relationship_types": sorted(relationship_types),
        }),
        "ontology_config_hash": _h({
            "entity_types": sorted(entity_types),
            "relationship_types": sorted(relationship_types),
            "namespace":           ontology.get("namespace", ""),
            "prefix":              ontology.get("prefix", ""),
            "relationship_typing": ontology.get("relationship_typing", {}) or {},
            "data_properties":     sorted(ontology.get("data_properties", []) or []),
        }),
    }


# -------------------------------------------------------------------------
# Internal upserts
# -------------------------------------------------------------------------

def _upsert_tenant(data: dict) -> None:
    """Upsert a KnowledgeTenant row. Computes hashes and sets dirty
    flags when any hash differs from the stored value."""
    from ..models import KnowledgeTenant

    tid = data["tenant_id"]
    new_hashes = compute_domain_hashes(data.get("domain") or {})

    existing = KnowledgeTenant.objects.filter(id=tid).first()
    if existing is None:
        # First registration — no "dirty" flags set; ingestion runs
        # against an empty corpus anyway, so all pipelines are needed.
        KnowledgeTenant.objects.create(
            id=tid,
            display_name=data.get("display_name") or tid,
            default_strategy=data.get("default_strategy", "hybrid"),
            domain=data.get("domain") or {},
            raptor_config_hash=new_hashes["raptor_config_hash"],
            graphrag_config_hash=new_hashes["graphrag_config_hash"],
            ontology_config_hash=new_hashes["ontology_config_hash"],
        )
        logger.info("Tenant registered (new): %s", tid)
        return

    # Existing tenant — compute granular diffs.
    raptor_changed   = existing.raptor_config_hash   != new_hashes["raptor_config_hash"]
    graphrag_changed = existing.graphrag_config_hash != new_hashes["graphrag_config_hash"]
    ontology_changed = existing.ontology_config_hash != new_hashes["ontology_config_hash"]

    existing.display_name      = data.get("display_name") or existing.display_name
    existing.default_strategy  = data.get("default_strategy", existing.default_strategy)
    existing.domain            = data.get("domain") or {}
    existing.raptor_config_hash   = new_hashes["raptor_config_hash"]
    existing.graphrag_config_hash = new_hashes["graphrag_config_hash"]
    existing.ontology_config_hash = new_hashes["ontology_config_hash"]
    # Dirty flags are sticky once set — only the ingestion command
    # clears them after a successful pipeline run. OR-merging here
    # ensures we don't lose a pending re-ingestion signal if the
    # tenant is re-registered before the ingest runs.
    existing.raptor_dirty   = existing.raptor_dirty   or raptor_changed
    existing.graphrag_dirty = existing.graphrag_dirty or graphrag_changed
    existing.ontology_dirty = existing.ontology_dirty or ontology_changed
    existing.save()

    if raptor_changed or graphrag_changed or ontology_changed:
        logger.warning(
            "Tenant %s config changed — pipelines flagged dirty: "
            "raptor=%s graphrag=%s ontology=%s. "
            "Run `manage.py ingest_knowledge --tenant=%s` to refresh.",
            tid, raptor_changed, graphrag_changed, ontology_changed, tid,
        )
    else:
        logger.info("Tenant registered (no config change): %s", tid)
