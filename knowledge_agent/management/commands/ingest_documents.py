#!/usr/bin/env python
"""
Django management command driving the KnowledgeAgent ingestion pipeline.

Invoke via manage.py (it is a management command, not a standalone script):
    python manage.py ingest_documents --all          # Full pipeline
    python manage.py ingest_documents --download     # Download only
    python manage.py ingest_documents --parse        # Parse PDFs only
    python manage.py ingest_documents --embed        # Embed + store only
    python manage.py ingest_documents --graph        # GraphRAG extraction
    python manage.py ingest_documents --raptor       # RAPTOR tree
    python manage.py ingest_documents --contextual   # Contextual enrichment
    python manage.py ingest_documents --ontology     # OWL/RDF ontology
    python manage.py ingest_documents --parent-child # Parent-child chunks
    python manage.py ingest_documents --reset        # Drop + recreate tables
    python manage.py ingest_documents --setup        # Create tables only
"""

import argparse
import logging
import sys
import os

# Add the KnowledgeAgent root to the path so imports work
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from knowledge_agent.ingestion.pipeline import IngestionPipeline


def main():
    parser = argparse.ArgumentParser(
        description="KnowledgeAgent document ingestion pipeline"
    )
    parser.add_argument("--all", action="store_true", help="Run full pipeline")
    parser.add_argument("--setup", action="store_true", help="Create tables only")
    parser.add_argument("--reset", action="store_true", help="Drop + recreate tables")
    parser.add_argument("--download", action="store_true", help="Download documents")
    parser.add_argument("--parse", action="store_true", help="Parse PDFs")
    parser.add_argument("--embed", action="store_true", help="Chunk, embed, and store")
    parser.add_argument("--parent-child", action="store_true", help="Build parent-child chunks")
    parser.add_argument("--contextual", action="store_true", help="Contextual enrichment")
    parser.add_argument("--graph", action="store_true", help="GraphRAG extraction")
    parser.add_argument("--ontology", action="store_true", help="OWL/RDF ontology")
    parser.add_argument("--raptor", action="store_true", help="RAPTOR tree")
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level",
    )
    # Model override flags
    parser.add_argument(
        "--llm-model", default=None,
        help="Override the LLM model for ingestion (default: INGESTION_LLM_MODEL env)",
    )
    parser.add_argument(
        "--llm-url", default=None,
        help="Override the Ollama URL for the ingestion LLM",
    )
    parser.add_argument(
        "--embedding-model", default=None,
        help="Override the embedding model (default: EMBEDDING_MODEL env)",
    )
    parser.add_argument(
        "--embedding-url", default=None,
        help="Override the Ollama URL for embeddings",
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    pipeline = IngestionPipeline(
        llm_model=args.llm_model,
        llm_url=args.llm_url,
        embedding_model=args.embedding_model,
        embedding_url=args.embedding_url,
    )

    # Validate embedding dimension before any embedding work
    from knowledge_agent import config as ka_config
    ka_config.validate_embedding_config()
    try:
        pipeline.store.validate_dimension(pipeline.embedding_service)
    except ValueError as e:
        logging.error("Dimension validation failed: %s", e)
        sys.exit(1)
    except Exception as e:
        logging.warning("Could not validate dimensions: %s", e)

    if args.all:
        pipeline.run_all()
        return

    if args.reset:
        pipeline.reset_tables()
        return

    if args.setup:
        pipeline.setup_tables()
        return

    # Individual steps (can be combined)
    ran_something = False

    if args.download:
        pipeline.download()
        ran_something = True

    if args.parse:
        pipeline.parse()
        ran_something = True

    if args.embed:
        if not pipeline._parsed_docs:
            pipeline.parse()
        pipeline.chunk_and_embed()
        ran_something = True

    if args.parent_child:
        if not pipeline._parsed_docs:
            pipeline.parse()
        pipeline.build_parent_child()
        ran_something = True

    if args.contextual:
        if not pipeline._chunks:
            if not pipeline._parsed_docs:
                pipeline.parse()
            pipeline.chunk_and_embed()
        pipeline.build_contextual()
        ran_something = True

    if args.graph:
        if not pipeline._chunks:
            if not pipeline._parsed_docs:
                pipeline.parse()
            pipeline.chunk_and_embed()
        pipeline.build_graph()
        ran_something = True

    if args.ontology:
        if not pipeline._chunks:
            if not pipeline._parsed_docs:
                pipeline.parse()
            pipeline.chunk_and_embed()
        pipeline.build_ontology()
        ran_something = True

    if args.raptor:
        if not pipeline._chunks:
            if not pipeline._parsed_docs:
                pipeline.parse()
            pipeline.chunk_and_embed()
        pipeline.build_raptor()
        ran_something = True

    if not ran_something:
        parser.print_help()


if __name__ == "__main__":
    main()
