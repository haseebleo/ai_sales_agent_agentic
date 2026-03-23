"""
Knowledge Base Indexer
Run this to build (or refresh) the vector store from knowledge_base.xlsx.
Usage:
    python -m app.rag.indexer                    # smart: skip if already current
    python -m app.rag.indexer --force            # always re-index
    python -m app.rag.indexer --file /path/to/new_kb.xlsx
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from app.core.config import settings
from app.core.logging_config import setup_logging
from app.rag.ingestion import (
    get_kb_version,
    load_excel_chunks,
    load_version_record,
    save_version_record,
)
from app.rag.vector_store import get_vector_store, reset_store

logger = logging.getLogger("trango_agent.rag.indexer")


def run_ingestion(kb_path: str, force: bool = False) -> None:
    current_version = get_kb_version(kb_path)
    stored_record = load_version_record(settings.KB_VERSION_FILE)

    if not force and stored_record.get("version") == current_version:
        logger.info(f"Knowledge base is up to date (version {current_version}). Skipping re-index.")
        logger.info("Use --force to override.")
        return

    logger.info(f"Starting ingestion for KB version {current_version}")
    chunks = load_excel_chunks(kb_path)
    if not chunks:
        logger.error("No chunks loaded — check the knowledge base file.")
        return

    store = get_vector_store()
    store.reset()  # Full replacement strategy (safe for KB size)
    count = store.upsert(chunks)

    save_version_record(current_version, settings.KB_VERSION_FILE)
    logger.info(f"✓ Ingestion complete — {count} chunks indexed, version={current_version}")


def main() -> None:
    setup_logging()
    parser = argparse.ArgumentParser(description="Trango Tech KB Indexer")
    parser.add_argument("--file", default=settings.KB_FILE_PATH, help="Path to knowledge_base.xlsx")
    parser.add_argument("--force", action="store_true", help="Re-index even if version unchanged")
    args = parser.parse_args()

    if not Path(args.file).exists():
        logger.error(f"File not found: {args.file}")
        sys.exit(1)

    run_ingestion(args.file, force=args.force)


if __name__ == "__main__":
    main()
