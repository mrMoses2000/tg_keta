"""
tg_keto.knowledge.loader — Knowledge base loader (future feature, stub now).

When KNOWLEDGE_MODE=on:
  1. Read knowledge/index.json (document metadata, keywords)
  2. For a query, find relevant documents by keyword match
  3. Load 1-2 summaries (knowledge/<doc_id>.summary.md)
  4. Include in LLM prompt as additional context

When KNOWLEDGE_MODE=off (MVP default):
  No-op. All functions return empty results.
"""

from __future__ import annotations

import json
from pathlib import Path

import structlog

from src.config import settings

logger = structlog.get_logger(__name__)

KNOWLEDGE_DIR = Path(__file__).parent.parent.parent / "knowledge"


def get_relevant_knowledge(query: str, max_docs: int = 2) -> list[dict]:
    """
    Find relevant knowledge documents for a query.
    Returns empty list when KNOWLEDGE_MODE=off.
    """
    if settings.knowledge_mode != "on":
        return []

    index_path = KNOWLEDGE_DIR / "index.json"
    if not index_path.exists():
        logger.warning("knowledge_index_not_found", path=str(index_path))
        return []

    try:
        with open(index_path, "r", encoding="utf-8") as f:
            index = json.load(f)
    except Exception as e:
        logger.error("knowledge_index_load_error", error=str(e))
        return []

    # Simple keyword matching (upgrade to embeddings later)
    query_lower = query.lower()
    scored = []
    for doc in index.get("documents", []):
        keywords = doc.get("keywords", [])
        score = sum(1 for kw in keywords if kw.lower() in query_lower)
        if score > 0:
            scored.append((score, doc))

    scored.sort(key=lambda x: x[0], reverse=True)
    results = []
    for _, doc in scored[:max_docs]:
        summary_path = KNOWLEDGE_DIR / f"{doc['id']}.summary.md"
        if summary_path.exists():
            content = summary_path.read_text(encoding="utf-8")[:2000]
            results.append({
                "title": doc.get("title", ""),
                "content": content,
            })

    return results
