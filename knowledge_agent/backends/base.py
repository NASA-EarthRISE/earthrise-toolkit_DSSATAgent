"""
Abstract base classes for LLM and embedding backends.

Strategies and ingestion code call backends through these interfaces
instead of importing langchain_ollama (or any specific provider)
directly. To add a new provider, subclass these and add a case to the
switch in `knowledge_agent.backends.__init__`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List


class EmbeddingBackend(ABC):
    """Embedding model interface — single text or batch."""

    @abstractmethod
    def embed_text(self, text: str) -> List[float]:
        """Embed a single string and return the vector."""

    @abstractmethod
    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """Embed a list of strings and return a list of vectors. Backends
        that don't have native batch APIs should still implement this —
        looping internally is fine but the method must exist so callers
        (especially `retrieve_batch`) can express batch intent."""

    @property
    @abstractmethod
    def dimension(self) -> int:
        """Embedding output dimension. Must be a constant per backend
        instance (model-dependent)."""

    @abstractmethod
    def health_check(self) -> None:
        """Verify the backend can be reached and is responding correctly.
        Raise on failure with a message suitable for ops to act on."""


class LLMBackend(ABC):
    """LLM interface — single prompt or batch of independent prompts.

    `generate_batch` exists for `retrieve_batch`'s Tier-2 strategies
    (HyDE, CRAG, RAG-Fusion, adaptive) that need one LLM call per
    query. The default impl in subclasses can be a sequential loop;
    chunk 2e's strategy updates parallelize across `ThreadPoolExecutor`
    at the strategy layer, not the backend layer, so individual
    backend impls don't have to worry about concurrency.
    """

    @abstractmethod
    def generate(self, prompt: str, **kwargs) -> str:
        """Run the model on a single prompt, return the completion text."""

    def generate_batch(self, prompts: List[str], **kwargs) -> List[str]:
        """Default: sequential loop over `generate`. Subclasses with
        native batch APIs override this."""
        return [self.generate(p, **kwargs) for p in prompts]

    @abstractmethod
    def health_check(self) -> None:
        """Verify the backend can be reached. Raise on failure."""
