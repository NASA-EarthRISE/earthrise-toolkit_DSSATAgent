"""
Compatibility shim exposing the EmbeddingService class; calls route to
the configured embedding backend.

Strategies and ingestion code that imported `EmbeddingService` directly
keep working. Under the hood, calls route to the configured embedding
backend (`knowledge_agent.backends.get_embedding_backend()`). New code
should import the backend directly instead.

When `EmbeddingService(model=..., base_url=...)` is constructed with
explicit args, a private `OllamaEmbeddingBackend` instance is created
(matches the historic "construct a fresh service per call site" behavior).
When constructed with no args, the shared module-wide backend singleton
is reused.
"""

from __future__ import annotations

import logging
from typing import List

logger = logging.getLogger(__name__)


class EmbeddingService:
    """Deprecated wrapper around the Ollama embedding backend.

    Prefer `from knowledge_agent.backends import get_embedding_backend`
    in new code.
    """

    def __init__(
        self,
        model: str | None = None,
        base_url: str | None = None,
    ):
        if model is not None or base_url is not None:
            # Caller passed explicit config → construct a dedicated
            # backend instance (don't pollute the shared singleton).
            from .backends.ollama import OllamaEmbeddingBackend
            self._backend = OllamaEmbeddingBackend(model=model, base_url=base_url)
        else:
            from .backends import get_embedding_backend
            self._backend = get_embedding_backend()

        self.model = self._backend.model
        self.base_url = self._backend.base_url

    @property
    def dimension(self) -> int:
        return self._backend.dimension

    def embed_text(self, text: str) -> List[float]:
        return self._backend.embed_text(text)

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        return self._backend.embed_batch(texts)
