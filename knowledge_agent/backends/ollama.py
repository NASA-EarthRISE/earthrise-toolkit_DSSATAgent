"""
Ollama-backed implementations of EmbeddingBackend and LLMBackend.

Wraps `langchain_ollama` (already a required dep). Other backends
(OpenAI, Anthropic, SentenceTransformers, vLLM) get their own modules
in this directory and a case in `knowledge_agent.backends.__init__`.
"""

from __future__ import annotations

import logging
from typing import List

from .. import config
from .base import EmbeddingBackend, LLMBackend

logger = logging.getLogger(__name__)


class OllamaEmbeddingBackend(EmbeddingBackend):
    """`langchain_ollama.OllamaEmbeddings` adapter.

    Config defaults come from `knowledge_agent.config` (env-var fallback
    chain) so deployments without a Django settings block keep working
    in standalone scripts.
    """

    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
    ):
        from langchain_ollama import OllamaEmbeddings

        self.base_url = base_url or config.EMBEDDING_OLLAMA_URL
        self.model = model or config.EMBEDDING_MODEL
        self._client = OllamaEmbeddings(model=self.model, base_url=self.base_url)
        self._dimension: int | None = None
        logger.info(
            "OllamaEmbeddingBackend: model=%s url=%s", self.model, self.base_url,
        )

    def embed_text(self, text: str) -> List[float]:
        return self._client.embed_query(text)

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        return self._client.embed_documents(texts)

    @property
    def dimension(self) -> int:
        if self._dimension is None:
            # Probe lazily — first call pays a single embed roundtrip to
            # discover the model's output width. Cached for the lifetime
            # of this instance.
            self._dimension = len(self.embed_text("dimension probe"))
            logger.info(
                "OllamaEmbeddingBackend dim probed: model=%s dim=%d",
                self.model, self._dimension,
            )
        return self._dimension

    def health_check(self) -> None:
        """Hit Ollama's /api/tags endpoint to confirm reachability.

        We use tags (lightweight) rather than embed_text (loads model)
        because health checks shouldn't trigger model warm-up.
        """
        import urllib.request

        url = f"{self.base_url.rstrip('/')}/api/tags"
        try:
            with urllib.request.urlopen(url, timeout=3) as r:
                if r.status != 200:
                    raise RuntimeError(f"Ollama returned HTTP {r.status}")
        except Exception as e:
            raise RuntimeError(
                f"Ollama unreachable at {url}: {type(e).__name__}: {e}"
            ) from e


class OllamaLLMBackend(LLMBackend):
    """`langchain_ollama.OllamaLLM` adapter."""

    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
    ):
        from langchain_ollama import OllamaLLM

        self.base_url = base_url or config.RETRIEVAL_OLLAMA_URL
        self.model = model or config.RETRIEVAL_LLM_MODEL
        self._client = OllamaLLM(model=self.model, base_url=self.base_url)
        logger.info("OllamaLLMBackend: model=%s url=%s", self.model, self.base_url)

    def generate(self, prompt: str, **kwargs) -> str:
        return self._client.invoke(prompt, **kwargs)

    def health_check(self) -> None:
        import urllib.request

        url = f"{self.base_url.rstrip('/')}/api/tags"
        try:
            with urllib.request.urlopen(url, timeout=3) as r:
                if r.status != 200:
                    raise RuntimeError(f"Ollama returned HTTP {r.status}")
        except Exception as e:
            raise RuntimeError(
                f"Ollama unreachable at {url}: {type(e).__name__}: {e}"
            ) from e
