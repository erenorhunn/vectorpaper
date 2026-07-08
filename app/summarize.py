"""Adım 4 — summary matrix (Methodology + Future Work) with mandatory grounded citations
and post-hoc citation verification (doc Risk 5)."""

import re

from sqlalchemy import select

from . import llm
from .db import Session
from .models import Chunk, Paper, Summary

TARGET_SECTIONS = ["methodology", "future_work"]

SYSTEM = """You are an academic research assistant. Summarize the given paper section.
STRICT RULES:
- Every claim MUST be grounded in the provided context passages.
- After every claim you MUST cite its source in the exact format [{paper_id}, Page X, Para Y]
  using the Page/Para values shown before each passage.
- Never invent findings. If the context does not support a claim, do not make it.
- Answer in 3-6 concise bullet points."""

CITATION_RE = re.compile(r"\[([0-9a-f-]{36}),\s*Page\s*(\w+),\s*Para\s*(\d+)\]", re.I)


def _context(paper_id: str, chunks: list[Chunk]) -> str:
    return "\n\n".join(
        f"(Page {c.page if c.page is not None else '?'}, Para {c.paragraph}) {c.text}" for c in chunks
    )


async def verify_citations(paper_id: str, text: str) -> tuple[list[dict], list[str]]:
    """Post-hoc check: does each generated [id, Page, Para] exist in the DB?
    Returns (verified citation dicts, unverified raw citation strings)."""
    verified, unverified = [], []
    async with Session() as s:
        for m in CITATION_RE.finditer(text):
            cited_paper, page, para = m.group(1), m.group(2), int(m.group(3))
            q = select(Chunk).where(Chunk.paper_id == cited_paper, Chunk.parent_id.is_(None),
                                    Chunk.paragraph == para)
            if page.isdigit():
                q = q.where(Chunk.page == int(page))
            chunk = (await s.execute(q.limit(1))).scalar_one_or_none()
            if chunk is not None and cited_paper == paper_id:
                verified.append({"paper_id": cited_paper, "page": chunk.page, "paragraph": para,
                                 "bbox": chunk.bbox, "chunk_id": chunk.id})
            else:
                unverified.append(m.group(0))
    return verified, unverified


async def summarize_paper(paper_id: str) -> dict:
    """Build (or return cached) summary matrix for one paper."""
    async with Session() as s:
        cached = await s.get(Summary, paper_id)
        if cached:
            return cached.matrix
        paper = await s.get(Paper, paper_id)
        if paper is None:
            raise ValueError(f"unknown paper {paper_id}")
        provider = await llm.provider_for_paper(paper_id)

        matrix: dict = {"title": paper.title, "sections": {}}
        for section in TARGET_SECTIONS:
            chunks = (
                await s.execute(
                    select(Chunk)
                    .where(Chunk.paper_id == paper_id, Chunk.section == section,
                           Chunk.parent_id.is_(None))
                    .order_by(Chunk.paragraph)
                )
            ).scalars().all()
            if not chunks:
                matrix["sections"][section] = {"summary": None, "note": "section not found in paper"}
                continue
            text = await llm.complete(
                "summary",
                SYSTEM.replace("{paper_id}", paper_id),
                f"Paper: {paper.title}\nSection: {section}\n\nContext passages:\n{_context(paper_id, chunks)}",
                provider=provider,
            )
            verified, unverified = await verify_citations(paper_id, text)
            entry = {"summary": text, "citations": verified}
            if unverified:
                entry["unverified"] = unverified  # UI shows "⚠ kaynaksız"
            matrix["sections"][section] = entry

        s.add(Summary(paper_id=paper_id, matrix=matrix))
        await s.commit()
    return matrix
