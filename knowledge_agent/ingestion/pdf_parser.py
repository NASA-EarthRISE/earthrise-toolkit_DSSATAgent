"""
PDF parser using PyMuPDF (fitz) with pdfplumber for table detection.

Returns structured ParsedDocument objects preserving page numbers,
detected section headers, and table flags.
"""

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

import fitz  # PyMuPDF

logger = logging.getLogger(__name__)


@dataclass
class ParsedPage:
    """A single parsed page from a PDF."""
    page_number: int
    text: str
    has_table: bool = False
    table_data: List[List[str]] = field(default_factory=list)
    section_headers: List[str] = field(default_factory=list)


@dataclass
class ParsedDocument:
    """A fully parsed PDF document."""
    filename: str
    title: str
    page_count: int
    pages: List[ParsedPage] = field(default_factory=list)

    @property
    def full_text(self) -> str:
        return "\n\n".join(p.text for p in self.pages if p.text)


class PDFParser:
    """Extracts text and structure from PDF files."""

    # Heuristic: lines in ALL CAPS or starting with digits followed by period
    _HEADER_PATTERNS = [
        re.compile(r"^(\d+\.[\d.]*)\s+(.+)$", re.MULTILINE),  # "1.2 Section Name"
        re.compile(r"^(Chapter\s+\d+)\s*[:\-]?\s*(.+)$", re.MULTILINE | re.IGNORECASE),
        re.compile(r"^([A-Z][A-Z\s]{4,})$", re.MULTILINE),  # ALL CAPS line
    ]

    def parse(self, pdf_path: str | Path) -> ParsedDocument:
        """Parse a PDF file and return a ParsedDocument."""
        from ._text_normalize import normalize_page_text

        pdf_path = Path(pdf_path)
        logger.info("Parsing PDF: %s", pdf_path.name)

        doc = fitz.open(str(pdf_path))
        pages: List[ParsedPage] = []

        for page_num in range(len(doc)):
            page = doc[page_num]
            raw_text = self._extract_text_in_reading_order(page)
            text = normalize_page_text(raw_text)

            # Detect section headers
            headers = self._extract_headers(text)

            # Detect tables using pdfplumber
            has_table, table_data = self._detect_tables(pdf_path, page_num)

            pages.append(ParsedPage(
                page_number=page_num + 1,  # 1-indexed
                text=text,
                has_table=has_table,
                table_data=table_data,
                section_headers=headers,
            ))

        doc.close()

        title = pdf_path.stem.replace("_", " ").replace("-", " ").title()
        parsed = ParsedDocument(
            filename=pdf_path.name,
            title=title,
            page_count=len(pages),
            pages=pages,
        )
        logger.info(
            "Parsed %s: %d pages, %d with tables",
            pdf_path.name,
            parsed.page_count,
            sum(1 for p in pages if p.has_table),
        )
        return parsed

    @staticmethod
    def _extract_text_in_reading_order(page) -> str:
        """Extract text from a fitz Page in reading order, column-aware.

        fitz's ``get_text("text")`` returns text in a heuristic reading
        order that often gets two-column papers wrong (interleaving
        left/right column lines). ``get_text("blocks")`` returns
        (x0, y0, x1, y1, text, block_no, block_type) tuples that we can
        sort ourselves.

        Algorithm: cluster blocks by their x0 (left edge) into columns
        — if there's a gap > 25% of page width between clustered x0
        groups, treat them as separate columns and emit each column's
        blocks (top-to-bottom) before moving to the next column.
        """
        try:
            blocks = page.get_text("blocks")
        except Exception:
            return page.get_text("text")

        # Filter out image/empty blocks; keep (x0, y0, text)
        text_blocks = []
        for b in blocks:
            if len(b) < 5:
                continue
            x0, y0, _x1, _y1, txt = b[0], b[1], b[2], b[3], b[4]
            if not isinstance(txt, str) or not txt.strip():
                continue
            text_blocks.append((x0, y0, txt))

        if not text_blocks:
            return page.get_text("text")

        # Column detection: sort by x0, find a gap large enough to
        # imply two columns. Page width = page.rect.width.
        page_width = float(page.rect.width) if page.rect.width else 612.0
        column_gap_threshold = page_width * 0.15

        sorted_by_x = sorted(text_blocks, key=lambda b: b[0])
        column_breaks = []
        for i in range(1, len(sorted_by_x)):
            if sorted_by_x[i][0] - sorted_by_x[i - 1][0] > column_gap_threshold:
                column_breaks.append(sorted_by_x[i][0])

        if not column_breaks:
            # Single column — just sort top-to-bottom
            text_blocks.sort(key=lambda b: (b[1], b[0]))
            return "\n".join(b[2] for b in text_blocks)

        # Multi-column — assign each block to a column by x0 threshold
        # (use the first detected break as the column boundary)
        boundary = column_breaks[0]
        left_col = [b for b in text_blocks if b[0] < boundary]
        right_col = [b for b in text_blocks if b[0] >= boundary]
        left_col.sort(key=lambda b: (b[1], b[0]))
        right_col.sort(key=lambda b: (b[1], b[0]))

        return "\n".join(b[2] for b in left_col + right_col)

    def _extract_headers(self, text: str) -> List[str]:
        headers = []
        for pattern in self._HEADER_PATTERNS:
            for match in pattern.finditer(text):
                header = match.group(0).strip()
                if len(header) > 3:
                    headers.append(header)
        return headers

    @staticmethod
    def _detect_tables(pdf_path: Path, page_idx: int) -> tuple[bool, list]:
        """Use pdfplumber to detect tables on a specific page."""
        try:
            import pdfplumber
            with pdfplumber.open(str(pdf_path)) as pdf:
                if page_idx < len(pdf.pages):
                    page = pdf.pages[page_idx]
                    tables = page.extract_tables()
                    if tables:
                        return True, tables
        except Exception as e:
            logger.debug("pdfplumber table detection failed on page %d: %s", page_idx, e)
        return False, []

    def parse_directory(self, directory: str | Path) -> List[ParsedDocument]:
        """Parse all PDFs in a directory."""
        directory = Path(directory)
        docs = []
        for pdf in sorted(directory.rglob("*.pdf")):
            try:
                docs.append(self.parse(pdf))
            except Exception as e:
                logger.error("Failed to parse %s: %s", pdf, e)
        return docs
