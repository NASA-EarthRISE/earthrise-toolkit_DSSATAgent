"""
Local document source scanner for the knowledge agent.

Auto-discovers PDF files from subdirectories under DATA_DIR.
Each subdirectory becomes a source category.  No downloading — works
with files already on disk.
"""

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List

from .. import config

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class SourceDirectory:
    """A directory of PDFs to ingest."""
    name: str           # Raw directory name
    label: str          # Human-readable label (= directory name)
    category: str       # URL-safe slug derived from name
    path: Path          # Absolute path


@dataclass
class SourceFile:
    """A single PDF and its source category."""
    path: Path
    category: str       # Slug of the parent source directory
    category_label: str # Human-readable category label


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def slugify(name: str) -> str:
    """Convert a directory or file name to a URL-safe slug."""
    slug = name.rsplit(".", 1)[0] if "." in name else name
    slug = slug.lower().replace(" ", "-").replace("_", "-").replace("'", "")
    slug = re.sub(r"[^a-z0-9-]", "", slug)
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug


# ---------------------------------------------------------------------------
# Scanner
# ---------------------------------------------------------------------------

def discover_source_directories(data_dir: str | None = None) -> List[SourceDirectory]:
    """Return one SourceDirectory per top-level subdirectory in *data_dir*."""
    raw = data_dir or config.DATA_DIR
    if not raw:
        return []  # No DATA_DIR configured — rely on registered sources
    root = Path(raw)
    if not root.is_dir():
        return []

    sources = []
    for entry in sorted(root.iterdir()):
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        sources.append(SourceDirectory(
            name=entry.name,
            label=entry.name,
            category=slugify(entry.name),
            path=entry,
        ))

    logger.info("Discovered %d source directories in %s", len(sources), root)
    return sources


class DocumentScanner:
    """Scans local directories for PDF files to ingest.

    Three input sources, merged at `scan()` time:
      1. Auto-discovery of subdirectories under DATA_DIR (legacy).
      2. KnowledgeSource model rows for `tenant_id` — the canonical
         multi-tenant path.
      3. The in-memory `services.register_source_directory()` registry
         — the programmatic registration path used by embedded callers.
    """

    def __init__(
        self,
        data_dir: str | None = None,
        *,
        tenant_id: str | None = None,
    ):
        raw = data_dir or config.DATA_DIR
        self.data_dir = Path(raw) if raw else None
        self.tenant_id = tenant_id
        self._sources: List[SourceDirectory] = []
        self._file_category: Dict[str, SourceFile] = {}   # filename → SourceFile

    def scan(self) -> List[SourceDirectory]:
        """Discover source directories and index PDFs by category."""
        # 1) Auto-discover from DATA_DIR (only if explicitly configured)
        if self.data_dir and self.data_dir.is_dir():
            self._sources = discover_source_directories(str(self.data_dir))
        else:
            self._sources = []

        # 2) KnowledgeSource rows for this tenant (canonical path)
        if self.tenant_id:
            try:
                from knowledge_agent.models import KnowledgeSource
                for src in KnowledgeSource.objects.filter(tenant_id=self.tenant_id):
                    p = Path(src.path)
                    if not p.is_dir():
                        logger.warning(
                            "KnowledgeSource path not found (tenant=%s): %s",
                            self.tenant_id, p,
                        )
                        continue
                    if any(s.path.resolve() == p.resolve() for s in self._sources):
                        continue
                    self._sources.append(SourceDirectory(
                        name=p.name,
                        label=src.label or p.name,
                        category=src.category or slugify(p.name),
                        path=p,
                    ))
            except Exception as e:
                logger.warning("Failed to read KnowledgeSource rows: %s", e)

        # 3) In-memory registry via the services API — the programmatic
        # registration path for embedded callers
        try:
            from knowledge_agent.services import get_all_source_directories
            for reg in get_all_source_directories():
                p = Path(reg["path"])
                if not p.is_dir():
                    logger.warning("Registered source path not found: %s", p)
                    continue
                if any(s.path.resolve() == p.resolve() for s in self._sources):
                    continue
                self._sources.append(SourceDirectory(
                    name=p.name,
                    label=reg.get("label", p.name),
                    category=reg.get("category", slugify(p.name)),
                    path=p,
                ))
        except Exception:
            pass  # Services not available during early startup

        limit = config.MAX_DOCS_PER_CATEGORY

        self._file_category.clear()
        for src in self._sources:
            pdfs = sorted(src.path.rglob("*.pdf"))
            if limit > 0:
                pdfs = pdfs[:limit]
            for pdf in pdfs:
                self._file_category[pdf.name] = SourceFile(
                    path=pdf,
                    category=src.category,
                    category_label=src.label,
                )

        total = len(self._file_category)
        msg = "Scanned %d PDFs across %d categories"
        if limit > 0:
            msg += " (limited to %d per category)" % limit
        logger.info(msg, total, len(self._sources))
        return self._sources

    def list_pdfs(self) -> List[Path]:
        """Return all PDFs across all source directories (registered + auto-discovered)."""
        if not self._sources:
            self.scan()
        return sorted(sf.path for sf in self._file_category.values())

    def get_source_file(self, filename: str) -> SourceFile | None:
        """Look up category info for a PDF by its filename."""
        if not self._file_category:
            self.scan()
        return self._file_category.get(filename)

    def get_category_for_file(self, filename: str) -> str:
        """Return the category slug for a filename, or 'uncategorized'."""
        sf = self.get_source_file(filename)
        return sf.category if sf else "uncategorized"

    def get_category_label_for_file(self, filename: str) -> str:
        """Return the human-readable category label for a filename."""
        sf = self.get_source_file(filename)
        return sf.category_label if sf else "Uncategorized"

    def get_file_by_slug(self, slug: str) -> SourceFile | None:
        """Look up a source file by its URL slug."""
        if not self._file_category:
            self.scan()
        for sf in self._file_category.values():
            if slugify(sf.path.name) == slug:
                return sf
        return None

    @property
    def sources(self) -> List[SourceDirectory]:
        if not self._sources:
            self.scan()
        return self._sources
