"""Adım 3/Faz 6 — relevance feedback loop: FlashRank cross-encoder reranking + LLM query expansion."""

import asyncio
import re

from sqlalchemy import select

from . import llm
from .db import Session
from .models import Feedback, Paper

_ranker = None


def _get_ranker():
    global _ranker
    if _ranker is None:
        from flashrank import Ranker

        _ranker = Ranker(model_name="ms-marco-MiniLM-L-12-v2")  # small, local, CPU-fast
    return _ranker


async def liked_papers(project_id: str | None = None, limit: int = 5) -> list[Paper]:
    stmt = (
        select(Paper)
        .join(Feedback, Feedback.paper_id == Paper.id)
        .where(Feedback.signal == "like")
        .order_by(Feedback.created_at.desc())
        .limit(limit)
    )
    if project_id:
        stmt = stmt.where(Paper.project_id == project_id)
    async with Session() as s:
        return list((await s.execute(stmt)).scalars())


async def rerank(query: str, results: list[dict], project_id: str | None = None,
                 text_key: str = "text") -> list[dict]:
    """Re-order retrieval results with a cross-encoder, biased toward the project's likes."""
    if not results:
        return results
    likes = await liked_papers(project_id)
    profile = " ".join(p.title for p in likes)
    q = f"{query} {profile}".strip()

    from flashrank import RerankRequest

    ranker = _get_ranker()
    req = RerankRequest(query=q, passages=[{"id": i, "text": r[text_key]} for i, r in enumerate(results)])
    ranked = await asyncio.to_thread(ranker.rerank, req)
    return [results[item["id"]] | {"rerank_score": float(item["score"])} for item in ranked]


def parse_query_lines(text: str) -> list[str]:
    """LLM output → clean query strings (strip bullets/numbering/quotes, drop chatter)."""
    queries = []
    for line in text.splitlines():
        q = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", line).strip().strip('"\'')
        if 2 <= len(q.split()) <= 8 and not q.endswith((":", "?")):
            queries.append(q)
    return queries[:6]


async def suggest_queries(topic: str, project_id: str, provider: str = "ollama") -> list[str]:
    """Pre-search AI help: turn a research topic into concrete literature-search queries,
    informed by the project's liked papers (doc: Query Expansion, now user-facing)."""
    likes = await liked_papers(project_id)
    liked = "\n".join(f"- {p.title}" for p in likes)
    context = f"\nPapers the user liked in this project:\n{liked}" if liked else ""
    text = await llm.complete(
        "expand",
        "You help a researcher search arXiv and Semantic Scholar. Given a research topic, "
        "propose 4-6 short English search queries (2-6 words each) covering different angles: "
        "core methods, applications, surveys, adjacent techniques. "
        "Reply with ONLY the queries, one per line, no numbering, no explanation.",
        f"Topic: {topic}{context}",
        provider=provider,
    )
    return parse_query_lines(text)
