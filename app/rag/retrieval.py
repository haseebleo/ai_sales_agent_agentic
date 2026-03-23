"""
RAG Retrieval Pipeline
Orchestrates query → embed → retrieve → format → inject into prompt.
Includes fallback logic when retrieval confidence is low.
"""
from __future__ import annotations

import logging
from typing import Optional

from app.core.config import settings
from app.rag.vector_store import RetrievedChunk, get_vector_store

logger = logging.getLogger("trango_agent.rag.retrieval")

# Natural language labels for citation transparency
_CATEGORY_LABELS: dict[str, str] = {
    "service_overview": "Services",
    "package_details": "Packages & Plans",
    "pricing": "Pricing",
    "revisions": "Revision Policy",
    "payment": "Payment Methods",
    "addons": "Add-Ons",
    "delivery": "Delivery Timelines",
    "discounts": "Discounts & Offers",
    "faq": "FAQs",
    "industry_use_case": "Industry Use Cases",
}


def retrieve(
    query: str,
    top_k: int | None = None,
    score_threshold: float | None = None,
    category_filter: Optional[str] = None,
) -> tuple[list[RetrievedChunk], bool]:
    """
    Returns (chunks, strong_match).
    strong_match=False signals the caller that retrieval is weak
    and it should ask a clarifying question rather than answering.
    """
    top_k = top_k or settings.RAG_TOP_K
    threshold = score_threshold or settings.RAG_SCORE_THRESHOLD

    store = get_vector_store()
    if store.collection_count() == 0:
        logger.warning("Vector store is empty — run ingestion first")
        return [], False

    chunks = store.query(query, top_k=top_k, score_threshold=threshold)

    if category_filter:
        chunks = [c for c in chunks if c.metadata.get("category") == category_filter]

    strong = len(chunks) >= 1 and (chunks[0].score if chunks else 0) >= 0.55
    top_score = chunks[0].score if chunks else 0.0
    logger.debug(
        f"RAG query='{query[:60]}' → {len(chunks)} chunks, "
        f"top_score={top_score:.3f}, strong={strong}"
    )
    return chunks, strong


def format_context_block(chunks: list[RetrievedChunk]) -> str:
    """Format retrieved chunks into a clean context block for the LLM."""
    if not chunks:
        return ""
    parts = ["=== RETRIEVED KNOWLEDGE (use this as ground truth) ==="]
    for i, chunk in enumerate(chunks, 1):
        label = _CATEGORY_LABELS.get(chunk.metadata.get("category", ""), "Knowledge Base")
        parts.append(f"\n[{i}] Source: {label} | Score: {chunk.score:.2f}\n{chunk.text}")
    parts.append("=== END KNOWLEDGE ===")
    return "\n".join(parts)


def source_labels(chunks: list[RetrievedChunk]) -> list[str]:
    """Return human-readable source labels for logging and lead capture."""
    seen: set[str] = set()
    labels: list[str] = []
    for c in chunks:
        lbl = _CATEGORY_LABELS.get(c.metadata.get("category", ""), c.source_label)
        if lbl not in seen:
            labels.append(lbl)
            seen.add(lbl)
    return labels


def retrieve_and_format(
    query: str,
    top_k: int | None = None,
    score_threshold: float | None = None,
) -> tuple[str, list[str], bool]:
    """
    Convenience wrapper: returns (context_block, source_labels, strong_match).
    """
    chunks, strong = retrieve(query, top_k=top_k, score_threshold=score_threshold)
    context = format_context_block(chunks)
    sources = source_labels(chunks)
    return context, sources, strong
