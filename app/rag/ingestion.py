"""
RAG Ingestion Pipeline
Reads every sheet from knowledge_base.xlsx, normalises rows into
semantically rich text chunks, embeds them, and stores in vector DB.

Design goals
------------
- One ingestion call handles full refresh or incremental re-index
- Each chunk carries rich metadata (sheet, row_id, category, etc.)
- Future file types (CSV, PDF, DOCX) can be added via pluggable loaders
- Embeddings provider is swappable through the abstraction layer
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from app.core.config import settings

logger = logging.getLogger("trango_agent.rag.ingestion")


# ── Per-Sheet Row → Text Normalizers ────────────────────────────────────────

def _row_to_text_services(row: pd.Series) -> str:
    return (
        f"Service: {row.get('ServiceName', '')}\n"
        f"Category: {row.get('Category', '')}\n"
        f"Description: {row.get('Description', '')}\n"
        f"Technologies: {row.get('Technologies', '')}\n"
        f"Use Cases: {row.get('UseCases', '')}\n"
        f"Suitable For: {row.get('SuitableFor', '')}\n"
        f"Starting Price: ${row.get('StartingPrice', '')} USD\n"
        f"Estimated Timeline: {row.get('EstimatedTimeline', '')}\n"
        f"Keywords: {row.get('Keywords', '')}"
    )


def _row_to_text_packages(row: pd.Series) -> str:
    return (
        f"Package: {row.get('PackageName', '')} ({row.get('PackageTier', '')} tier)\n"
        f"Service Type: {row.get('ServiceType', '')}\n"
        f"Price: ${row.get('Price', '')} USD\n"
        f"Features: {row.get('Features', '')}\n"
        f"Included Items: {row.get('IncludedItems', '')}\n"
        f"Delivery Time: {row.get('DeliveryTime', '')}\n"
        f"Revisions: {row.get('Revisions', '')}\n"
        f"Best For: {row.get('BestFor', '')}\n"
        f"Notes: {row.get('Notes', '')}"
    )


def _row_to_text_pricing(row: pd.Series) -> str:
    return (
        f"Pricing — {row.get('PackageName', '')} ({row.get('ServiceType', '')})\n"
        f"Base Price: ${row.get('BasePrice', '')} {row.get('Currency', 'USD')}\n"
        f"Billing Type: {row.get('BillingType', '')}\n"
        f"Discount Eligible: {row.get('DiscountEligible', '')}\n"
        f"Custom Quote Allowed: {row.get('CustomQuoteAllowed', '')}\n"
        f"Notes: {row.get('Notes', '')}"
    )


def _row_to_text_revisions(row: pd.Series) -> str:
    return (
        f"Revisions for {row.get('PackageName', '')}\n"
        f"Included Revisions: {row.get('IncludedRevisions', '')}\n"
        f"Extra Revision Cost: {row.get('ExtraRevisionCost', '')}\n"
        f"Terms: {row.get('RevisionTerms', '')}"
    )


def _row_to_text_payment(row: pd.Series) -> str:
    return (
        f"Payment Method: {row.get('PaymentMethod', '')}\n"
        f"Description: {row.get('Description', '')}\n"
        f"Advance Required: {row.get('AdvancePercent', '')}%\n"
        f"Milestone Terms: {row.get('MilestoneTerms', '')}\n"
        f"Installments Allowed: {row.get('InstallmentsAllowed', '')}\n"
        f"Final Delivery Terms: {row.get('FinalDeliveryTerms', '')}"
    )


def _row_to_text_addons(row: pd.Series) -> str:
    return (
        f"Add-On: {row.get('AddOnName', '')} (for {row.get('ServiceType', '')})\n"
        f"Description: {row.get('Description', '')}\n"
        f"Additional Cost: ${row.get('AdditionalCost', '')} USD\n"
        f"Notes: {row.get('Notes', '')}"
    )


def _row_to_text_delivery(row: pd.Series) -> str:
    fast = row.get("FastTrackAvailable", "No")
    surcharge = row.get("FastTrackSurcharge", "N/A")
    return (
        f"Delivery Time — {row.get('ServiceType', '')}\n"
        f"Standard Range: {row.get('MinWeeks', '')}–{row.get('MaxWeeks', '')} weeks\n"
        f"Fast Track Available: {fast} (+{surcharge} surcharge)\n"
        f"Notes: {row.get('Notes', '')}"
    )


def _row_to_text_discounts(row: pd.Series) -> str:
    return (
        f"Discount: {row.get('DiscountName', '')}\n"
        f"Condition: {row.get('Condition', '')}\n"
        f"Discount: {row.get('DiscountPercent', '')}% off\n"
        f"Applicable To: {row.get('ApplicableTo', '')}\n"
        f"Terms: {row.get('Terms', '')}"
    )


def _row_to_text_faqs(row: pd.Series) -> str:
    return (
        f"FAQ [{row.get('Category', '')}]\n"
        f"Question: {row.get('Question', '')}\n"
        f"Answer: {row.get('Answer', '')}"
    )


def _row_to_text_industry(row: pd.Series) -> str:
    return (
        f"Industry Use Case: {row.get('Industry', '')}\n"
        f"Common Needs: {row.get('CommonNeeds', '')}\n"
        f"Recommended Services: {row.get('RecommendedServices', '')}\n"
        f"Example Project: {row.get('ExampleProject', '')}\n"
        f"Typical Budget Range: {row.get('TypicalBudgetRange', '')}"
    )


def _row_to_text_objections(row: pd.Series) -> str:
    return (
        f"Sales Objection [{row.get('ObjectionType', '')}]\n"
        f"Objection: {row.get('ObjectionText', '')}\n"
        f"Recommended Response: {row.get('RecommendedResponse', '')}\n"
        f"Value Reframe: {row.get('ValueReframe', '')}"
    )


def _row_to_text_company_profile(row: pd.Series) -> str:
    return (
        f"Company: {row.get('CompanyName', '')}\n"
        f"Founded: {row.get('Founded', '')}\n"
        f"HQ: {row.get('HQ', '')}\n"
        f"Team Size: {row.get('TeamSize', '')}\n"
        f"Portfolio: {row.get('Portfolio', '')}\n"
        f"Specialities: {row.get('Specialities', '')}\n"
        f"Certifications: {row.get('Certifications', '')}\n"
        f"Contact Email: {row.get('ContactEmail', '')}\n"
        f"Website: {row.get('Website', '')}\n"
        f"Client Regions: {row.get('ClientRegions', '')}\n"
        f"Value Proposition: {row.get('ValueProposition', '')}"
    )


_SHEET_NORMALIZERS: dict[str, Any] = {
    "Services": _row_to_text_services,
    "Packages": _row_to_text_packages,
    "Pricing": _row_to_text_pricing,
    "Revisions": _row_to_text_revisions,
    "PaymentMethods": _row_to_text_payment,
    "AddOns": _row_to_text_addons,
    "DeliveryTime": _row_to_text_delivery,
    "Discounts": _row_to_text_discounts,
    "FAQs": _row_to_text_faqs,
    "IndustryUseCases": _row_to_text_industry,
    "Objections": _row_to_text_objections,
    "CompanyProfile": _row_to_text_company_profile,
}

# Category tags per sheet for metadata enrichment
_SHEET_CATEGORIES: dict[str, str] = {
    "Services": "service_overview",
    "Packages": "package_details",
    "Pricing": "pricing",
    "Revisions": "revisions",
    "PaymentMethods": "payment",
    "AddOns": "addons",
    "DeliveryTime": "delivery",
    "Discounts": "discounts",
    "FAQs": "faq",
    "IndustryUseCases": "industry_use_case",
    "Objections": "objection_handling",
    "CompanyProfile": "company_info",
}


def _clean(val: Any) -> str:
    if pd.isna(val):
        return ""
    return str(val).strip()


def _extract_metadata(sheet: str, row: pd.Series, row_idx: int) -> dict[str, str]:
    meta: dict[str, str] = {
        "sheet_name": sheet,
        "row_id": str(row_idx),
        "category": _SHEET_CATEGORIES.get(sheet, "general"),
    }
    for field in ("ServiceName", "PackageName", "ServiceType", "Industry", "Category", "Question"):
        if field in row.index and not pd.isna(row[field]):
            meta[field.lower()] = _clean(row[field])
    return meta


def load_excel_chunks(kb_path: str) -> list[dict[str, Any]]:
    """
    Read all sheets from the knowledge base XLSX and return a list of
    {"text": ..., "metadata": {...}} dicts ready for embedding.
    """
    kb_path = Path(kb_path)
    if not kb_path.exists():
        raise FileNotFoundError(f"Knowledge base not found: {kb_path}")

    all_chunks: list[dict[str, Any]] = []
    xl = pd.ExcelFile(kb_path)

    for sheet in xl.sheet_names:
        if sheet not in _SHEET_NORMALIZERS:
            logger.warning(f"No normalizer for sheet '{sheet}' — skipping")
            continue

        df = pd.read_excel(kb_path, sheet_name=sheet, dtype=str).fillna("")
        normalizer = _SHEET_NORMALIZERS[sheet]

        for idx, row in df.iterrows():
            text = normalizer(row).strip()
            if len(text) < 20:
                continue  # skip near-empty rows

            metadata = _extract_metadata(sheet, row, idx)
            all_chunks.append({"text": text, "metadata": metadata})

    logger.info(f"Loaded {len(all_chunks)} chunks from {kb_path.name}")
    return all_chunks


def get_kb_version(kb_path: str) -> str:
    """Return mtime-based version string for change detection."""
    p = Path(kb_path)
    if not p.exists():
        return "unknown"
    mtime = p.stat().st_mtime
    return datetime.utcfromtimestamp(mtime).strftime("%Y%m%d_%H%M%S")


def save_version_record(version: str, version_file: str) -> None:
    Path(version_file).parent.mkdir(parents=True, exist_ok=True)
    with open(version_file, "w") as f:
        json.dump({"version": version, "indexed_at": datetime.utcnow().isoformat()}, f, indent=2)


def load_version_record(version_file: str) -> dict[str, str]:
    p = Path(version_file)
    if not p.exists():
        return {}
    with open(p) as f:
        return json.load(f)
