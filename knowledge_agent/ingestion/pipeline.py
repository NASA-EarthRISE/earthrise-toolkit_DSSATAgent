"""
Master ingestion pipeline.

Orchestrates: scan → parse → chunk → embed → store → contextual
enrichment → GraphRAG extraction → ontology → RAPTOR tree.

Each step is independently runnable and resumable.
"""

import logging
import re
import uuid
from typing import List, Optional

from langchain_ollama import OllamaLLM

from .. import config
from ..embeddings import EmbeddingService
from ..store import VectorStore
from ._llm_output import strip_llm_preamble
from .chunker import Chunk, DocumentChunker
from .downloader import DocumentScanner
from .graph_builder import GraphBuilder
from .ontology_builder import OntologyBuilder
from .pdf_parser import PDFParser, ParsedDocument
from .raptor_builder import RAPTORBuilder

logger = logging.getLogger(__name__)

_CONTEXTUAL_PROMPT = """Given the following document chunk, write a short
(2-3 sentence) context description that explains where this chunk fits
in the broader document and what topic it covers. This will be prepended
to the chunk for better retrieval.

CRITICAL OUTPUT RULES:
- Respond with ONLY the description itself.
- Do NOT include any preamble like "Here is the context description"
  or "Here is a summary".
- Do NOT restate the task or explain what you're doing.
- Start directly with the first word of the description.

Document: {source_title}
Section: {section_title}
Chunk:
{content}"""


class IngestionPipeline:
    """End-to-end document ingestion pipeline, scoped to a single tenant.

    `tenant_id` defaults to `store.DEFAULT_TENANT_ID` for backward compat
    with the legacy `python manage.py ingest_documents` flow (which has
    no tenant flag). The new `ingest_knowledge` command passes an
    explicit tenant and source list.
    """

    def __init__(
        self,
        llm_model: str | None = None,
        llm_url: str | None = None,
        embedding_model: str | None = None,
        embedding_url: str | None = None,
        *,
        tenant_id: str | None = None,
    ):
        self.store = VectorStore()
        self.embedding_service = EmbeddingService(
            model=embedding_model,
            base_url=embedding_url or config.EMBEDDING_OLLAMA_URL,
        )
        self.llm = OllamaLLM(
            base_url=llm_url or config.INGESTION_OLLAMA_URL,
            model=llm_model or config.INGESTION_LLM_MODEL,
        )
        from ..store import _tenant_str
        self.tenant_id = _tenant_str(tenant_id)

        # Scanner is scoped to the same tenant — it'll pull KnowledgeSource
        # rows for this tenant in addition to the legacy in-memory registry.
        self.scanner = DocumentScanner(tenant_id=self.tenant_id)
        self.parser = PDFParser()
        self.chunker = DocumentChunker()

        self._parsed_docs: List[ParsedDocument] = []
        self._chunks: List[Chunk] = []

    def _load_tenant_domain(self) -> dict:
        """Pull the domain dict off the KnowledgeTenant row for this run.

        Returns {} if the tenant doesn't exist yet — builders downgrade
        gracefully (graph_builder logs a warning, ontology_builder
        emits a TBox-only graph).
        """
        try:
            from ..models import KnowledgeTenant
            return KnowledgeTenant.objects.get(id=self.tenant_id).domain or {}
        except Exception:
            return {}

    # ------------------------------------------------------------------
    # DB fallback for standalone build steps
    # ------------------------------------------------------------------

    def _load_chunks_from_db(self):
        """Load existing chunks from DB into memory (for standalone build steps)."""
        rows = self.store.get_all_chunks(tenant_id=self.tenant_id)
        self._chunks = [
            Chunk(id=r["id"], content=r["content"], metadata=r.get("metadata") or {})
            for r in (rows or [])
        ]
        logger.info("Loaded %d chunks from database", len(self._chunks))

    # ------------------------------------------------------------------
    # Individual steps
    # ------------------------------------------------------------------

    def setup_tables(self):
        """Create pgvector tables (idempotent)."""
        self.store.create_tables()
        logger.info("Tables created/verified")

    def reset_tables(self):
        """Drop and recreate all tables."""
        self.store.drop_tables()
        self.store.create_tables()
        logger.info("Tables reset")

    def download(self):
        """Scan local directories for PDFs to ingest (sources are local; no network download)."""
        sources = self.scanner.scan()
        pdfs = self.scanner.list_pdfs()
        for src in sources:
            logger.info("  Category '%s': %s", src.label, src.path)
        logger.info("Found %d PDFs across %d categories", len(pdfs), len(sources))

    def parse(self):
        """Parse all discovered PDFs."""
        pdfs = self.scanner.list_pdfs()
        logger.info("Found %d PDFs to parse", len(pdfs))
        self._parsed_docs = []
        for pdf_path in pdfs:
            try:
                doc = self.parser.parse(pdf_path)
                self._parsed_docs.append(doc)
            except Exception as e:
                logger.error("Failed to parse %s: %s", pdf_path, e)
        logger.info("Parsed %d documents", len(self._parsed_docs))

    def chunk_and_embed(self):
        """Chunk all parsed documents, embed, and store in pgvector."""
        if not self._parsed_docs:
            logger.warning("No parsed documents — run parse() first")
            return

        self._chunks = []
        for doc in self._parsed_docs:
            slug = self._filename_to_slug(doc.filename)
            category = self.scanner.get_category_for_file(doc.filename)
            category_label = self.scanner.get_category_label_for_file(doc.filename)

            chunks = self.chunker.chunk_fixed(
                doc, source_slug=slug, source_title=doc.title,
            )
            # Tag every chunk with its source category
            for c in chunks:
                c.metadata["source_category"] = category
                c.metadata["source_category_label"] = category_label

            self._chunks.extend(chunks)

        logger.info("Total chunks: %d", len(self._chunks))

        # Embed in batches
        batch_size = 32
        for i in range(0, len(self._chunks), batch_size):
            batch = self._chunks[i:i + batch_size]
            texts = [c.content for c in batch]
            embeddings = self.embedding_service.embed_batch(texts)

            rows = [
                (c.id, c.content.replace("\x00", ""), emb, c.metadata)
                for c, emb in zip(batch, embeddings)
            ]
            self.store.insert_chunks_bulk(rows, tenant_id=self.tenant_id)

            if (i + batch_size) % 100 == 0 or i + batch_size >= len(self._chunks):
                logger.info(
                    "Embedded and stored %d/%d chunks",
                    min(i + batch_size, len(self._chunks)),
                    len(self._chunks),
                )

    def build_parent_child(self):
        """Create parent-child chunk hierarchy."""
        if not self._parsed_docs:
            logger.warning("No parsed documents — run parse() first")
            return

        for doc in self._parsed_docs:
            slug = self._filename_to_slug(doc.filename)
            category = self.scanner.get_category_for_file(doc.filename)
            category_label = self.scanner.get_category_label_for_file(doc.filename)

            parents, children = self.chunker.chunk_parent_child(
                doc, source_slug=slug, source_title=doc.title,
            )

            # Tag with category
            for p in parents:
                p.metadata["source_category"] = category
                p.metadata["source_category_label"] = category_label
            for c in children:
                c.metadata["source_category"] = category
                c.metadata["source_category_label"] = category_label

            # Embed and store parents (preserve chunk ID so children FK works)
            stored_parent_ids = set()
            for p in parents:
                try:
                    logger.debug(
                        "Embedding parent chunk %s (%d chars) from %s",
                        p.id[:8], len(p.content), slug,
                    )
                    emb = self.embedding_service.embed_text(p.content)
                    self.store.insert_parent_chunk(
                        p.content, emb, p.metadata, chunk_id=p.id,
                        tenant_id=self.tenant_id,
                    )
                    stored_parent_ids.add(p.id)
                except Exception as e:
                    logger.error(
                        "Failed to embed parent chunk %s (%d chars): %s",
                        p.id[:8], len(p.content), e,
                    )

            # Embed and store children (skip if parent failed)
            for c in children:
                if c.parent_id not in stored_parent_ids:
                    logger.warning(
                        "Skipping child chunk %s — parent %s was not stored",
                        c.id[:8], c.parent_id[:8],
                    )
                    continue
                try:
                    logger.debug(
                        "Embedding child chunk %s (%d chars)",
                        c.id[:8], len(c.content),
                    )
                    emb = self.embedding_service.embed_text(c.content)
                    self.store.insert_child_chunk(
                        c.parent_id, c.content, emb, c.metadata,
                        tenant_id=self.tenant_id,
                    )
                except Exception as e:
                    logger.error(
                        "Failed to embed child chunk %s (%d chars): %s",
                        c.id[:8], len(c.content), e,
                    )

        logger.info("Parent-child indexing complete")

    def build_contextual(self):
        """Create contextual chunk copies with LLM-generated context prefixes."""
        if not self._chunks:
            self._load_chunks_from_db()
        if not self._chunks:
            logger.warning("No chunks available — run chunk_and_embed() first")
            return

        for i, chunk in enumerate(self._chunks):
            try:
                raw_context = self.llm.invoke(_CONTEXTUAL_PROMPT.format(
                    source_title=chunk.metadata.get("source_title", ""),
                    section_title=chunk.metadata.get("section_title", ""),
                    content=chunk.content[:1500],
                ))
                context = strip_llm_preamble(raw_context)
                enriched = (
                    f"Context:\n{context}\n\n"
                    f"Source:\n{chunk.content}"
                )
                emb = self.embedding_service.embed_text(enriched)
                self.store.insert_contextual_chunk(
                    base_chunk_id=chunk.id,
                    content=enriched,
                    context_prefix=context,
                    embedding=emb,
                    metadata=chunk.metadata,
                    tenant_id=self.tenant_id,
                )
            except Exception as e:
                logger.error("Contextual enrichment failed for chunk %s: %s", chunk.id, e)

            if (i + 1) % 20 == 0:
                logger.info("Contextual enrichment: %d/%d", i + 1, len(self._chunks))

        logger.info("Contextual enrichment complete")

    def build_graph(self):
        """Run GraphRAG entity/relationship extraction."""
        if not self._chunks:
            self._load_chunks_from_db()
        if not self._chunks:
            logger.warning("No chunks available — run chunk_and_embed() first")
            return
        domain = self._load_tenant_domain()
        builder = GraphBuilder(
            self.store, self.embedding_service, self.llm,
            tenant_id=self.tenant_id,
            entity_types=domain.get("entity_types") or [],
            relationship_types=domain.get("relationship_types") or [],
        )
        builder.build_from_chunks(self._chunks)

    def build_ontology(self):
        """Build OWL/RDF ontology from chunks."""
        if not self._chunks:
            self._load_chunks_from_db()
        if not self._chunks:
            logger.warning("No chunks available — run chunk_and_embed() first")
            return
        domain = self._load_tenant_domain()
        ontology = domain.get("ontology") or {}
        builder = OntologyBuilder(
            self.store, self.llm,
            tenant_id=self.tenant_id,
            namespace=ontology.get("namespace", "http://example.com/ontology#"),
            prefix=ontology.get("prefix", "ex"),
            entity_types=domain.get("entity_types") or [],
            relationship_types=domain.get("relationship_types") or [],
            relationship_typing=ontology.get("relationship_typing") or {},
            data_properties=ontology.get("data_properties") or [],
        )
        builder.build(self._chunks)

    def build_raptor(self):
        """Build RAPTOR summary tree."""
        if not self._chunks:
            self._load_chunks_from_db()
        if not self._chunks:
            logger.warning("No chunks available — run chunk_and_embed() first")
            return
        domain = self._load_tenant_domain()
        builder = RAPTORBuilder(
            self.store, self.embedding_service, self.llm,
            tenant_id=self.tenant_id,
            domain_hint=domain.get("hint"),
        )
        builder.build(self._chunks)

    # ------------------------------------------------------------------
    # Full pipeline
    # ------------------------------------------------------------------

    def run_all(self):
        """Run the complete ingestion pipeline."""
        logger.info("=== Starting full ingestion pipeline ===")
        self.setup_tables()
        self.download()
        self.parse()
        self.chunk_and_embed()
        self.build_parent_child()
        self.build_contextual()
        self.build_graph()
        self.build_ontology()
        self.build_raptor()
        logger.info("=== Ingestion pipeline complete ===")
        logger.info("Total chunks in DB: %d", self.store.chunk_count())

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _filename_to_slug(filename: str) -> str:
        """Convert a filename to a URL-safe slug."""
        slug = filename.rsplit(".", 1)[0]
        slug = slug.lower().replace(" ", "-").replace("_", "-")
        slug = re.sub(r"[^a-z0-9-]", "", slug)
        slug = re.sub(r"-+", "-", slug).strip("-")
        return slug
