"""
KnowledgeAgent configuration.

Loads settings from environment variables. When deployed alongside other
Django apps in this project, DB credentials are read from the shared
HOST/USERNAME/PASSWORD env vars so all apps point at the same Postgres
instance.

Model roles
-----------
Three independent model roles allow different models for different tasks:

| Role           | Model env var                        | URL env var                           |
|----------------|--------------------------------------|---------------------------------------|
| Embedding      | EMBEDDING_MODEL                      | EMBEDDING_OLLAMA_URL → OLLAMA_URL     |
| Retrieval LLM  | RETRIEVAL_LLM_MODEL → MODEL_NAME     | RETRIEVAL_OLLAMA_URL → OLLAMA_URL     |
| Ingestion LLM  | INGESTION_LLM_MODEL → MODEL_NAME     | INGESTION_OLLAMA_URL → OLLAMA_URL     |

Arrows (→) show fallback chain.  Existing deployments using only
``OLLAMA_URL`` + ``MODEL_NAME`` continue to work with zero config changes.
"""

import logging
import os

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Ollama / LLM  — shared base (backward compat)
# ---------------------------------------------------------------------------
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "nomic-embed-text")
LLM_MODEL = os.environ.get("MODEL_NAME", "llama3.1")

# ---------------------------------------------------------------------------
# Per-role Ollama URLs  (fallback → OLLAMA_URL)
# ---------------------------------------------------------------------------
EMBEDDING_OLLAMA_URL = os.environ.get("EMBEDDING_OLLAMA_URL", OLLAMA_URL)
RETRIEVAL_OLLAMA_URL = os.environ.get("RETRIEVAL_OLLAMA_URL", OLLAMA_URL)
INGESTION_OLLAMA_URL = os.environ.get("INGESTION_OLLAMA_URL", OLLAMA_URL)

# ---------------------------------------------------------------------------
# Per-role LLM models  (fallback → LLM_MODEL / MODEL_NAME)
# ---------------------------------------------------------------------------
RETRIEVAL_LLM_MODEL = os.environ.get("RETRIEVAL_LLM_MODEL", LLM_MODEL)
INGESTION_LLM_MODEL = os.environ.get("INGESTION_LLM_MODEL", LLM_MODEL)

# ---------------------------------------------------------------------------
# PostgreSQL — uses the host project's shared Postgres instance by default;
# standalone deployments override via env vars.
# ---------------------------------------------------------------------------
DB_HOST = os.environ.get("DBHOST", "localhost")
DB_PORT = int(os.environ.get("DBPORT", "5432"))
DB_NAME = os.environ.get("DBNAME", "")  # DB name (shared Postgres)
DB_USER = os.environ.get("DBUSER", "postgres")
DB_PASSWORD = os.environ.get("PASSWORD", "")
DB_SCHEMA = os.environ.get("KNOWLEDGE_SCHEMA", "knowledge")

# ---------------------------------------------------------------------------
# Known embedding dimensions  (model name → expected dim)
# ---------------------------------------------------------------------------
KNOWN_EMBEDDING_DIMS: dict[str, int] = {
    "nomic-embed-text": 768,
    "all-minilm": 384,
    "mxbai-embed-large": 1024,
    "snowflake-arctic-embed": 1024,
    "bge-large": 1024,
    "bge-m3": 1024,
}

# ---------------------------------------------------------------------------
# Vector / embedding dimensions
# ---------------------------------------------------------------------------
_raw_dim = os.environ.get("VECTOR_DIM")
if _raw_dim is not None:
    VECTOR_DIM = int(_raw_dim)
else:
    VECTOR_DIM = KNOWN_EMBEDDING_DIMS.get(EMBEDDING_MODEL, 768)

# ---------------------------------------------------------------------------
# Chunking defaults
# ---------------------------------------------------------------------------
CHUNK_SIZE = int(os.environ.get("CHUNK_SIZE", "1000"))
CHUNK_OVERLAP = int(os.environ.get("CHUNK_OVERLAP", "300"))
PARENT_CHUNK_SIZE = int(os.environ.get("PARENT_CHUNK_SIZE", "3000"))

# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------
KNOWLEDGE_AGENT_PORT = int(os.environ.get("KNOWLEDGE_AGENT_PORT", "9100"))

# ---------------------------------------------------------------------------
# Document storage
# ---------------------------------------------------------------------------
_KNOWLEDGE_AGENT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("KNOWLEDGE_DATA_DIR", "")

# Maximum PDFs to ingest per source category (0 = unlimited)
MAX_DOCS_PER_CATEGORY = int(os.environ.get("KNOWLEDGE_MAX_DOCS_PER_CATEGORY", "0"))

# Maximum chunks to keep per parsed PDF (0 = unlimited). Lets you smoke-
# test the full pipeline against, say, 20 chunks per doc without waiting
# hours for the LLM-bound branches (contextual, graph, ontology) to
# process the full corpus. Applied inside ingest_pdf_task and
# build_parent_child_pdf_task — chunks past the cap are simply dropped
# from the in-memory list before embedding.
MAX_CHUNKS_PER_DOC = int(os.environ.get("KNOWLEDGE_MAX_CHUNKS_PER_DOC", "0"))
MEDIA_DIR = os.environ.get("KNOWLEDGE_MEDIA_DIR", "media/knowledge/sources")

# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def get_dsn() -> str:
    """Return a libpq-style DSN string."""
    return (
        f"host={DB_HOST} port={DB_PORT} dbname={DB_NAME} "
        f"user={DB_USER} password={DB_PASSWORD}"
    )


def get_connection_params() -> dict:
    """Return a dict suitable for psycopg2.connect()."""
    return {
        "host": DB_HOST,
        "port": DB_PORT,
        "dbname": DB_NAME,
        "user": DB_USER,
        "password": DB_PASSWORD,
    }


def validate_embedding_config() -> None:
    """Warn if VECTOR_DIM doesn't match the known dimension for EMBEDDING_MODEL."""
    expected = KNOWN_EMBEDDING_DIMS.get(EMBEDDING_MODEL)
    if expected is not None and expected != VECTOR_DIM:
        logger.warning(
            "VECTOR_DIM=%d does not match known dimension for '%s' (%d). "
            "Set VECTOR_DIM=%d or choose a matching model.",
            VECTOR_DIM,
            EMBEDDING_MODEL,
            expected,
            expected,
        )
