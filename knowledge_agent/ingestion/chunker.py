"""
Document chunking with multiple modes.

Supports fixed-size, structure-aware, parent-child, and contextual chunking.
"""

import logging
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from langchain_text_splitters import RecursiveCharacterTextSplitter

from .. import config
from .pdf_parser import ParsedDocument, ParsedPage

logger = logging.getLogger(__name__)


@dataclass
class Chunk:
    """A text chunk with metadata."""
    id: str = ""
    content: str = ""
    metadata: Dict = field(default_factory=dict)
    parent_id: Optional[str] = None

    def __post_init__(self):
        if not self.id:
            self.id = str(uuid.uuid4())


class DocumentChunker:
    """Splits parsed documents into chunks using various strategies."""

    def __init__(
        self,
        chunk_size: int | None = None,
        chunk_overlap: int | None = None,
        parent_chunk_size: int | None = None,
    ):
        self.chunk_size = chunk_size or config.CHUNK_SIZE
        self.chunk_overlap = chunk_overlap or config.CHUNK_OVERLAP
        self.parent_chunk_size = parent_chunk_size or config.PARENT_CHUNK_SIZE

    # ------------------------------------------------------------------
    # Section-aware chunking (primary strategy)
    # ------------------------------------------------------------------

    def chunk_fixed(
        self,
        doc: ParsedDocument,
        source_slug: str = "",
        source_title: str = "",
    ) -> List[Chunk]:
        """Split respecting section boundaries with overlap.

        1. Walk pages and accumulate text into sections (delimited by
           detected headers).  A section may span multiple pages.
        2. Sub-split long sections with RecursiveCharacterTextSplitter
           so every chunk stays within ``chunk_size``.
        3. Overlap is applied both within a section (splitter overlap)
           and *between* sections: the last ``chunk_overlap`` characters
           of the previous section are prepended to the first chunk of
           the next section so retrieval doesn't miss boundary content.
        4. Tables detected on a page get their own dedicated chunks.
        """
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=self.chunk_size,
            chunk_overlap=self.chunk_overlap,
            separators=["\n\n", "\n", ". ", " ", ""],
        )

        # --- Pass 1: collect (section_title, text, page_range, tables) ---
        sections: list[dict] = []
        current_section = ""
        current_text = ""
        current_start_page = 1
        current_tables: list[tuple[int, list]] = []  # (page_num, table_data)

        for page in doc.pages:
            if not page.text.strip():
                continue

            if page.section_headers:
                # Flush the previous section
                if current_text.strip():
                    sections.append({
                        "title": current_section,
                        "text": current_text,
                        "start_page": current_start_page,
                        "end_page": page.page_number - 1,
                        "tables": current_tables,
                    })
                current_section = page.section_headers[-1]
                current_text = page.text
                current_start_page = page.page_number
                current_tables = []
            else:
                current_text += "\n\n" + page.text

            if page.has_table and page.table_data:
                for tbl in page.table_data:
                    current_tables.append((page.page_number, tbl))

        # Flush final section
        if current_text.strip():
            sections.append({
                "title": current_section,
                "text": current_text,
                "start_page": current_start_page,
                "end_page": doc.page_count,
                "tables": current_tables,
            })

        # --- Pass 2: split sections into chunks with cross-section overlap ---
        chunks: List[Chunk] = []
        prev_tail = ""  # last chunk_overlap chars of previous section

        for sec in sections:
            text = sec["text"]

            # Prepend overlap from the previous section for continuity
            if prev_tail:
                text = prev_tail + "\n\n" + text

            splits = splitter.split_text(text)

            for split_text in splits:
                meta = {
                    "source_slug": source_slug,
                    "source_title": source_title,
                    "source_filename": doc.filename,
                    "page_number": sec["start_page"],
                    "section_title": sec["title"],
                    "has_table": bool(sec["tables"]),
                    "chunk_type": "section",
                }
                chunks.append(Chunk(content=split_text, metadata=meta))

            # Keep the tail for cross-section overlap
            prev_tail = sec["text"][-self.chunk_overlap:] if len(sec["text"]) > self.chunk_overlap else sec["text"]

            # Dedicated table chunks
            for page_num, tbl in sec["tables"]:
                table_text = self._table_to_text(tbl)
                if table_text:
                    # Guard against oversized tables
                    if len(table_text) > self.chunk_size:
                        table_splits = splitter.split_text(table_text)
                    else:
                        table_splits = [table_text]
                    for tt in table_splits:
                        meta = {
                            "source_slug": source_slug,
                            "source_title": source_title,
                            "source_filename": doc.filename,
                            "page_number": page_num,
                            "section_title": sec["title"],
                            "has_table": True,
                            "chunk_type": "table",
                        }
                        chunks.append(Chunk(content=tt, metadata=meta))

        logger.info(
            "Section-aware chunking: %d chunks (%d sections) from %s",
            len(chunks), len(sections), doc.filename,
        )
        return chunks

    # ------------------------------------------------------------------
    # Parent-child chunking
    # ------------------------------------------------------------------

    def chunk_parent_child(
        self,
        doc: ParsedDocument,
        source_slug: str = "",
        source_title: str = "",
    ) -> tuple[List[Chunk], List[Chunk]]:
        """Create large parent chunks and small child chunks with FK."""
        parent_splitter = RecursiveCharacterTextSplitter(
            chunk_size=self.parent_chunk_size,
            chunk_overlap=self.chunk_overlap,
        )
        child_splitter = RecursiveCharacterTextSplitter(
            chunk_size=self.chunk_size,
            chunk_overlap=self.chunk_overlap // 2,
        )

        full_text = doc.full_text
        parent_texts = parent_splitter.split_text(full_text)

        parents: List[Chunk] = []
        children: List[Chunk] = []

        for pt in parent_texts:
            parent = Chunk(
                content=pt,
                metadata={
                    "source_slug": source_slug,
                    "source_title": source_title,
                    "source_filename": doc.filename,
                    "chunk_type": "parent",
                },
            )
            parents.append(parent)

            child_texts = child_splitter.split_text(pt)
            for ct in child_texts:
                child = Chunk(
                    content=ct,
                    parent_id=parent.id,
                    metadata={
                        "source_slug": source_slug,
                        "source_title": source_title,
                        "source_filename": doc.filename,
                        "parent_id": parent.id,
                        "chunk_type": "child",
                    },
                )
                children.append(child)

        logger.info(
            "Parent-child chunking: %d parents, %d children from %s",
            len(parents), len(children), doc.filename,
        )
        return parents, children

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _table_to_text(table: List[List[str]]) -> str:
        """Convert a pdfplumber table to readable text."""
        if not table:
            return ""
        rows = []
        for row in table:
            cells = [str(c).strip() if c else "" for c in row]
            rows.append(" | ".join(cells))
        return "Table:\n" + "\n".join(rows)
