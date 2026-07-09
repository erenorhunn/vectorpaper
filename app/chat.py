"""Library-settings free-form chat. RAG over @-tagged papers, streamed for SSE.
Reuses analyze.py's context pattern (paper-scoped vectors.search → parent chunks)."""

from collections.abc import AsyncIterator

from sqlalchemy import select

from . import llm, vectors
from .db import Session
from .models import Chunk, Paper, Project

SYSTEM = (
    "You are a research assistant for a personal paper library. "
    "When paper excerpts are provided below, ground your answer in them and name the paper you use; "
    "if they don't cover the question, say so and answer from general knowledge. Be concise and technical."
)


async def _context(paper_ids: list[str], query: str) -> str:
    """Top passages from each @-tagged paper for the query, grouped by title."""
    by_paper: dict[str, list[Chunk]] = {}
    async with Session() as s:
        for pid in paper_ids:
            hits = await vectors.search(query, limit=6, paper_id=pid)
            parent_ids = {h["parent_id"] for h in hits if h.get("parent_id")}
            if not parent_ids:
                continue
            parents = (await s.execute(select(Chunk).where(Chunk.id.in_(parent_ids)))).scalars().all()
            by_paper[pid] = parents
    if not by_paper:
        return ""
    blocks = []
    async with Session() as s:
        for pid, chunks in by_paper.items():
            paper = await s.get(Paper, pid)
            title = paper.title if paper else pid
            passages = "\n\n".join(c.text for c in chunks[:6])
            blocks.append(f"### {title}\n{passages}")
    return "Paper excerpts:\n\n" + "\n\n".join(blocks)


async def answer(project_id: str, history: list[dict], paper_ids: list[str]) -> AsyncIterator[str]:
    """Yields the assistant reply token by token. `history` = [{role, content}, ...]."""
    async with Session() as s:
        project = await s.get(Project, project_id)
    provider = (project.settings or {}).get("provider", "ollama") if project else "ollama"

    last_user = next((m["content"] for m in reversed(history) if m["role"] == "user"), "")
    context = await _context(paper_ids, last_user) if paper_ids else ""

    # ponytail: llm.stream takes (system, user) → flatten the transcript into user text.
    # Refactor stream() to accept messages[] if chats get long.
    convo = "\n\n".join(f"{m['role'].upper()}: {m['content']}" for m in history)
    user = f"{context}\n\n{convo}\n\nASSISTANT:" if context else f"{convo}\n\nASSISTANT:"

    async for token in llm.stream("chat", SYSTEM, user, provider=provider):
        yield token
