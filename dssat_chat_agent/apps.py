"""
The DSSAT chat product shell.

This app is the assembly point for the DSSAT deployment. It owns:
  - DSSAT-branded templates (via the ``shell/base.html`` override
    convention — Django's template loader picks this app's version over
    the framework's default because dssat_chat_agent is listed BEFORE
    earthrise_agents_base in INSTALLED_APPS)
  - The DSSAT knowledge tenant vocabulary + document sources (loaded
    from ``data/tenants.yaml`` at post_migrate)

The app deliberately holds NO framework code. Every framework primitive
(ChatAgent, SkillTable, envelope, tool_schema) is imported from
earthrise_agents_base. Every DSSAT capability (raster registration,
DSSAT simulation skills) is imported from dssat_agent. This module is
the wiring — nothing else.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict, List

from django.apps import AppConfig

logger = logging.getLogger(__name__)


class DssatChatAgentConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "dssat_chat_agent"
    label = "dssat_chat_agent"
    display_name = "DSSAT Chat Agent"

    def ready(self) -> None:
        from django.db.models.signals import post_migrate

        # Register the DSSAT knowledge tenant + its document sources
        # after migrations complete. Deferred to post_migrate because
        # register_tenant / register_source write via the ORM, and
        # Django emits RuntimeWarning if AppConfig.ready() touches the
        # DB directly.
        post_migrate.connect(_register_knowledge_tenant_signal, sender=self)


def _register_knowledge_tenant_signal(sender: AppConfig, **kwargs: Any) -> None:
    _register_knowledge_tenant()


# ---------------------------------------------------------------------------
# Knowledge tenant + document source registration
# ---------------------------------------------------------------------------

_APP_DIR = Path(__file__).resolve().parent
_DATA_DIR = _APP_DIR / "data"
_TENANTS_YAML = _DATA_DIR / "tenants.yaml"


def _load_tenants_config() -> List[Dict[str, Any]]:
    """Load and validate tenants.yaml. Returns the list of tenant configs."""
    if not _TENANTS_YAML.exists():
        logger.info("dssat_chat_agent: no tenants.yaml found — skipping registration")
        return []
    try:
        import yaml  # local import so a fresh clone without pyyaml doesn't crash
    except ImportError:
        logger.warning("dssat_chat_agent: pyyaml not installed — cannot load tenants.yaml")
        return []
    with open(_TENANTS_YAML, "r") as f:
        data = yaml.safe_load(f) or {}
    return list(data.get("tenants") or [])


def _resolve_documents_dir(tenant_cfg: Dict[str, Any]) -> str | None:
    """Resolve the tenant's documents directory.

    Precedence:
      1. ``KNOWLEDGE_DOCUMENTS_DIR`` env var (deployment override, typical
         Docker volume mount)
      2. ``documents_dir`` in tenants.yaml, resolved relative to the
         app's ``data/`` directory
    """
    env_override = os.environ.get("KNOWLEDGE_DOCUMENTS_DIR")
    if env_override:
        return env_override if os.path.isdir(env_override) else None

    yaml_path = tenant_cfg.get("documents_dir")
    if yaml_path:
        candidate = (_DATA_DIR / yaml_path).resolve()
        return str(candidate) if candidate.is_dir() else None

    return None


def _register_knowledge_tenant() -> None:
    """Register every tenant in tenants.yaml and its document sources."""
    try:
        from knowledge_agent.registry import register_tenant, register_source
    except ImportError:
        logger.debug(
            "knowledge_agent not installed — skipping tenant registration",
        )
        return

    for tenant_cfg in _load_tenants_config():
        tenant_id = tenant_cfg.get("tenant_id")
        if not tenant_id:
            logger.warning("Skipping tenant with no tenant_id: %r", tenant_cfg)
            continue

        try:
            register_tenant(
                tenant_id=tenant_id,
                display_name=tenant_cfg.get("display_name", tenant_id),
                default_strategy=tenant_cfg.get("default_strategy", "hybrid"),
                domain=tenant_cfg.get("domain") or {},
            )
        except Exception as e:
            logger.warning("Failed to register tenant %s: %s", tenant_id, e)
            continue

        docs_root = _resolve_documents_dir(tenant_cfg)
        if not docs_root:
            logger.info(
                "Tenant %s: no documents directory found (env=%s, yaml=%s) — "
                "skipping source registration",
                tenant_id,
                os.environ.get("KNOWLEDGE_DOCUMENTS_DIR"),
                tenant_cfg.get("documents_dir"),
            )
            continue

        # Each top-level subdirectory becomes one KnowledgeSource so the
        # tenant's SourceCategory dropdown mirrors the folder tree.
        for entry in sorted(os.listdir(docs_root)):
            full = os.path.join(docs_root, entry)
            if not os.path.isdir(full) or entry.startswith("."):
                continue
            try:
                register_source(
                    tenant_id=tenant_id,
                    path=full,
                    label=entry,
                    agent_label="dssat_chat_agent",
                )
            except Exception as e:
                logger.warning("Failed to register source %s: %s", full, e)

        logger.info(
            "dssat_chat_agent: tenant '%s' + sources registered from %s",
            tenant_id, docs_root,
        )
