"""
Pluggable backend layer for embeddings and LLM calls.

Two interfaces (`EmbeddingBackend`, `LLMBackend`) plus a switch-case
selector that resolves a backend string from `settings.KNOWLEDGE_AGENT`
to a concrete class. v1 ships only the Ollama backends; adding OpenAI /
Anthropic / SentenceTransformers requires (1) a new impl class under
`knowledge_agent/backends/` and (2) a new case in the switch.

Per Q14: this is intentionally a switch-case rather than a dynamic
dotted-path import. Smaller attack surface, explicit allowlist, no
arbitrary class loading from settings.

Per Q15: backends are module-wide, not per-tenant. All tenants share
one LLM backend and one embedding backend.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from django.core.exceptions import ImproperlyConfigured

from .base import EmbeddingBackend, LLMBackend
from .ollama import OllamaEmbeddingBackend, OllamaLLMBackend

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


__all__ = [
    "EmbeddingBackend",
    "LLMBackend",
    "OllamaEmbeddingBackend",
    "OllamaLLMBackend",
    "get_llm_backend",
    "get_embedding_backend",
    "validate_backends",
]


# Lazy singletons — built on first access, cached for the process lifetime.
# Backends are module-wide so caching here is safe; a multi-tenant
# deployment that ever needs per-tenant backends will rip this out.
_llm_backend: LLMBackend | None = None
_embedding_backend: EmbeddingBackend | None = None


def _settings():
    """Read settings.KNOWLEDGE_AGENT, returning an empty dict if unset."""
    from django.conf import settings
    return getattr(settings, "KNOWLEDGE_AGENT", {}) or {}


def get_llm_backend() -> LLMBackend:
    """Return the configured LLM backend singleton.

    Reads `settings.KNOWLEDGE_AGENT['llm_backend']` (default 'ollama').
    The matching per-backend config block is keyed by backend name —
    `settings.KNOWLEDGE_AGENT['ollama']` for the Ollama impl, etc.
    """
    global _llm_backend
    if _llm_backend is not None:
        return _llm_backend

    cfg = _settings()
    name = cfg.get("llm_backend", "ollama")

    if name == "ollama":
        ollama_cfg = cfg.get("ollama", {}) or {}
        _llm_backend = OllamaLLMBackend(
            base_url=ollama_cfg.get("retrieval_url") or ollama_cfg.get("base_url"),
            model=ollama_cfg.get("retrieval_model") or ollama_cfg.get("model"),
        )
    else:
        raise ImproperlyConfigured(
            f"Unknown LLM backend {name!r}. Supported: 'ollama'. "
            f"See knowledge_agent/backends/__init__.py to add a new one."
        )

    logger.info("LLM backend initialized: %s", name)
    return _llm_backend


def get_embedding_backend() -> EmbeddingBackend:
    """Return the configured embedding backend singleton.

    Reads `settings.KNOWLEDGE_AGENT['embedding_backend']` (default
    'ollama'). Per-backend config in the nested block.
    """
    global _embedding_backend
    if _embedding_backend is not None:
        return _embedding_backend

    cfg = _settings()
    name = cfg.get("embedding_backend", "ollama")

    if name == "ollama":
        ollama_cfg = cfg.get("ollama", {}) or {}
        _embedding_backend = OllamaEmbeddingBackend(
            base_url=ollama_cfg.get("embedding_url") or ollama_cfg.get("base_url"),
            model=ollama_cfg.get("embedding_model") or ollama_cfg.get("model"),
        )
    else:
        raise ImproperlyConfigured(
            f"Unknown embedding backend {name!r}. Supported: 'ollama'. "
            f"See knowledge_agent/backends/__init__.py to add a new one."
        )

    logger.info("Embedding backend initialized: %s", name)
    return _embedding_backend


def reset_backends() -> None:
    """Drop the cached backend singletons. For tests and reconfiguration."""
    global _llm_backend, _embedding_backend
    _llm_backend = None
    _embedding_backend = None


def validate_backends() -> None:
    """Construct both backends and probe them.

    Called from `KnowledgeAgentConfig.ready()` (Q8). Raises
    ImproperlyConfigured if either backend is misconfigured. Logs a
    warning (does not raise) if the health check fails — deployments
    where Ollama starts in parallel with Django shouldn't block startup
    on a transient connection refusal.
    """
    try:
        llm = get_llm_backend()
    except ImproperlyConfigured:
        raise
    except Exception as e:
        raise ImproperlyConfigured(
            f"LLM backend construction failed: {e}"
        ) from e

    try:
        emb = get_embedding_backend()
    except ImproperlyConfigured:
        raise
    except Exception as e:
        raise ImproperlyConfigured(
            f"Embedding backend construction failed: {e}"
        ) from e

    for backend, label in [(llm, "LLM"), (emb, "embedding")]:
        try:
            backend.health_check()
        except Exception as e:
            logger.warning(
                "%s backend health check failed (continuing startup): %s",
                label, e,
            )
