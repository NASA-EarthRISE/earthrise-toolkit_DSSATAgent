"""
RAPTOR tree builder.

Recursively clusters chunks by embedding similarity (k-means), summarises
each cluster via LLM, embeds summaries, and stores as tree nodes.
Repeats up the tree until fewer than 5 nodes remain.

Behavior notes:
- `tenant_id` threads through so all stored nodes carry the tenant FK.
- `domain.hint` from the tenant's DomainConfig is interpolated into the
  summarization prompt (so summaries are written in the corpus's voice).
- Summarization / embedding run through the configured backend interfaces.
"""

import logging
from typing import List, Optional

import numpy as np
from sklearn.cluster import KMeans

from ..backends import get_embedding_backend, get_llm_backend
from ..store import VectorStore
from ._llm_output import strip_llm_preamble
from .chunker import Chunk

logger = logging.getLogger(__name__)


# {domain_hint} is filled per-tenant at build time. Defaults to a neutral
# phrasing when the tenant doesn't declare one.
_SUMMARIZE_PROMPT = """You are an expert summariser for {domain_hint}.
Summarise the following group of document excerpts into a single concise
paragraph that captures the key concepts and relationships.

CRITICAL OUTPUT RULES:
- Respond with ONLY the summary paragraph itself.
- Do NOT include any preamble like "Here is a concise paragraph" or
  "Here is a summary that captures the key concepts".
- Do NOT restate the task or explain what you're doing.
- Start directly with the first word of the summary.

Excerpts:
{texts}"""

_DEFAULT_DOMAIN_HINT = "the indexed corpus"


class RAPTORBuilder:
    """Builds a RAPTOR summary tree from document chunks."""

    def __init__(
        self,
        store: VectorStore | None = None,
        embedding_service=None,
        llm=None,
        max_cluster_size: int = 10,
        min_nodes_to_stop: int = 5,
        *,
        tenant_id: Optional[str] = None,
        domain_hint: Optional[str] = None,
    ):
        self.store = store or VectorStore()
        self.embedding_service = embedding_service or get_embedding_backend()
        self.llm = llm or get_llm_backend()
        self.max_cluster_size = max_cluster_size
        self.min_nodes_to_stop = min_nodes_to_stop
        self.tenant_id = tenant_id
        self.domain_hint = domain_hint or _DEFAULT_DOMAIN_HINT

    def build(self, chunks: List[Chunk]):
        """Build the full RAPTOR tree from leaf chunks."""
        if not chunks:
            return

        logger.info("RAPTOR: creating %d leaf nodes (level 0)", len(chunks))
        texts = [c.content for c in chunks]
        embeddings = self.embedding_service.embed_batch(texts)

        current_level_ids: List[str] = []
        current_embeddings: List[List[float]] = []
        current_texts: List[str] = []

        for chunk, emb in zip(chunks, embeddings):
            nid = self.store.insert_raptor_node(
                content=chunk.content,
                embedding=emb,
                level=0,
                metadata=chunk.metadata,
                tenant_id=self.tenant_id,
            )
            current_level_ids.append(nid)
            current_embeddings.append(emb)
            current_texts.append(chunk.content)

        level = 1
        while len(current_level_ids) > self.min_nodes_to_stop:
            logger.info(
                "RAPTOR: building level %d from %d nodes",
                level, len(current_level_ids),
            )
            next_ids, next_embeddings, next_texts = self._build_level(
                current_level_ids, current_embeddings, current_texts, level,
            )
            if not next_ids or len(next_ids) >= len(current_level_ids):
                break
            current_level_ids = next_ids
            current_embeddings = next_embeddings
            current_texts = next_texts
            level += 1

        logger.info("RAPTOR tree complete: %d levels", level)

    def _build_level(
        self,
        node_ids: List[str],
        embeddings: List[List[float]],
        texts: List[str],
        level: int,
    ) -> tuple[List[str], List[List[float]], List[str]]:
        n = len(node_ids)
        n_clusters = max(1, n // self.max_cluster_size)
        if n_clusters >= n:
            return [], [], []

        emb_array = np.array(embeddings)
        kmeans = KMeans(n_clusters=n_clusters, random_state=42, n_init="auto")
        labels = kmeans.fit_predict(emb_array)

        new_ids, new_embeddings, new_texts = [], [], []

        for cluster_id in range(n_clusters):
            cluster_indices = [i for i, l in enumerate(labels) if l == cluster_id]
            if not cluster_indices:
                continue

            cluster_texts = [texts[i] for i in cluster_indices]
            children_ids = [node_ids[i] for i in cluster_indices]

            combined = "\n---\n".join(t[:500] for t in cluster_texts[:5])
            raw_summary = self.llm.generate(_SUMMARIZE_PROMPT.format(
                domain_hint=self.domain_hint, texts=combined,
            ))
            summary = strip_llm_preamble(raw_summary)

            summary_emb = self.embedding_service.embed_text(summary)

            parent_id = self.store.insert_raptor_node(
                content=summary,
                embedding=summary_emb,
                level=level,
                children_ids=children_ids,
                metadata={"cluster_size": len(cluster_indices)},
                tenant_id=self.tenant_id,
            )

            new_ids.append(parent_id)
            new_embeddings.append(summary_emb)
            new_texts.append(summary)

        return new_ids, new_embeddings, new_texts
