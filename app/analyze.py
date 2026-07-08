"""Adım 5 — drill-down blueprint. Map-reduce against Lost-in-the-Middle (doc Risk 3):
per-paper intermediate summaries first, then one reasoning pass over those summaries."""

from collections.abc import AsyncIterator

from sqlalchemy import select

from . import llm, vectors
from .db import Session
from .models import Chunk, Paper

MAP_SYSTEM = (
    "Summarize what these passages from one paper say about the given topic. "
    "Be technical and specific: methods, formulas, results, open problems. 5-8 sentences."
)

REDUCE_SYSTEM = """You are a senior research engineer. Using the per-paper notes provided,
produce an engineering/research blueprint on the topic:
1. Problem framing and state of the art
2. Candidate architecture / method (with reasoning)
3. Key formulas or algorithms, explained
4. Step-by-step implementation plan
5. Open risks and research gaps
Ground every claim in the notes; name the source paper when you use it."""


async def _gather_context(paper_id: str, topic: str) -> dict[str, list[Chunk]]:
    """Top chunks for the topic: the selected paper's parents + related passages from other papers."""
    async with Session() as s:
        paper = await s.get(Paper, paper_id)
    hits = await vectors.search(topic, limit=12, paper_id=paper_id)
    if paper:  # cross-paper context, scoped to the same project
        hits += await vectors.search(topic, limit=8, project_id=paper.project_id)
    parent_ids = {h["parent_id"] for h in hits if h.get("parent_id")}
    by_paper: dict[str, list[Chunk]] = {}
    async with Session() as s:
        parents = (await s.execute(select(Chunk).where(Chunk.id.in_(parent_ids)))).scalars().all()
        for c in parents:
            by_paper.setdefault(c.paper_id, []).append(c)
    return by_paper


async def deep_analysis(paper_id: str, topic: str) -> AsyncIterator[str]:
    """Yields the blueprint token by token (for SSE)."""
    provider = await llm.provider_for_paper(paper_id)
    by_paper = await _gather_context(paper_id, topic)
    if not by_paper:
        yield "No indexed content found for this topic yet."
        return

    notes = []
    async with Session() as s:
        for pid, chunks in by_paper.items():
            paper = await s.get(Paper, pid)
            title = paper.title if paper else pid
            passages = "\n\n".join(c.text for c in chunks[:8])
            note = await llm.complete("analyze", MAP_SYSTEM,
                                      f"Topic: {topic}\nPaper: {title}\n\n{passages}", provider=provider)
            notes.append(f"### {title}\n{note}")

    async for token in llm.stream(
        "analyze", REDUCE_SYSTEM, f"Topic: {topic}\n\nPer-paper notes:\n\n" + "\n\n".join(notes),
        provider=provider,
    ):
        yield token
