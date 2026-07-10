"""Ideas wizard — grounded article-idea generation from the project library.
Map-reduce like analyze.py, plus an explicit gap stage so novelty is argued, not asserted:
MAP per-paper structured notes → GAP synthesis → IDEATE (arq job), DEVELOP streams (SSE).
Grounding is by verbatim paper title, resolved to ids post-hoc (trust-but-verify,
same spirit as summarize.verify_citations — 8B models won't emit UUIDs reliably)."""

import json
from collections.abc import AsyncIterator

from sqlalchemy import select

from . import llm, rerank, vectors
from .db import Session
from .models import Chunk, Idea, Job, Paper, Project

IDEA_COUNT = 6
MAX_PAPERS = 8  # MAP fan-out cap — token budget (K papers × ≤6 parents)

MAP_SYSTEM = """You extract structured research notes from passages of ONE academic paper,
focused on a given topic. Reply with ONLY a JSON object, no prose, no markdown
fences, with exactly these keys:
{"contributions": [], "methods": [], "limitations": [], "future_work": []}
Rules:
- Each list holds 1-4 short, specific, technical items (one sentence each).
- Only state what the passages support. If a category is not covered, leave its list empty.
- "future_work" means directions the authors themselves state as open problems or future work."""

GAPS_SYSTEM = """You are a senior researcher performing a gap analysis across the per-paper
notes provided. Identify 4-8 concrete research gaps in the topic area: open
problems, contradictions between papers, untested combinations of methods,
assumptions worth challenging, or transfers of a method to a new domain.
Format: a numbered list. Each item is one paragraph: state the gap, then the
supporting evidence, citing papers by their exact title in square brackets,
e.g. [Some Paper Title]. Only claim gaps the notes support; never invent
findings. No preamble, no conclusion."""

IDEAS_SYSTEM = """You are a creative but rigorous research supervisor. Using the gap analysis
and the allowed paper list, propose {n} distinct ideas for a NEW research
article on the topic. Reply with ONLY a JSON array, no prose, no markdown
fences. Each element has exactly these keys:
- "title": a working article title
- "research_question": one precise, answerable question
- "novelty": which gap it fills and which papers it moves beyond (name them)
- "method_sketch": 2-4 sentences - approach, data, key techniques
- "required_resources": datasets, compute, equipment, expertise needed
- "risks": the main technical risk and a plausible mitigation
- "grounded_in": 2-5 titles copied VERBATIM from the ALLOWED PAPERS list
Rules:
- Every idea must trace to at least one numbered gap; spread ideas across different gaps.
- Mix ambition: some incremental and safe, some bold combinations or transfers.
- Never invent paper titles; "grounded_in" must quote the ALLOWED PAPERS list exactly.
- Do not duplicate or trivially rephrase any PRIOR IDEAS listed.
- Favor the style of LIKED ideas and avoid the directions of DISLIKED ideas, when listed.
- USER GUIDANCE, when present, overrides all other steering."""

DEVELOP_SYSTEM = """You are a senior researcher writing a research proposal blueprint for the
given article idea. Ground every claim in the provided excerpts and name
source papers by title in square brackets. Write plain text (no markdown)
with these UPPERCASE headings, in order:
POSITIONING - related work and exactly what is new versus the cited papers
CONTRIBUTIONS - 3-5 numbered, falsifiable contribution claims
METHOD PLAN - the approach in enough technical detail to start work
EXPERIMENT & EVALUATION PLAN - datasets, baselines, metrics, ablations
EXPECTED RESULTS - outcomes that would confirm or refute the research question
RISKS & MITIGATIONS - the top 3 risks, each with a mitigation
Be concrete and technical. Where the excerpts do not cover something, say so
explicitly instead of inventing it."""


async def _provider(project_id: str) -> str:
    async with Session() as s:
        project = await s.get(Project, project_id)
    return (project.settings or {}).get("provider", "ollama") if project else "ollama"


async def _pick_papers(project_id: str, topic: str, k: int = MAX_PAPERS) -> list[Paper]:
    """Top-K distinct papers for the topic: vector search + feedback-aware rerank."""
    hits = await vectors.search(topic, limit=48, project_id=project_id)
    hits = await rerank.rerank(topic, hits, project_id)
    ids: list[str] = []
    for h in hits:
        if h["paper_id"] not in ids:
            ids.append(h["paper_id"])
        if len(ids) >= k:
            break
    async with Session() as s:
        rows = (await s.execute(select(Paper).where(Paper.id.in_(ids)))).scalars().all()
    by_id = {p.id: p for p in rows}
    return [by_id[i] for i in ids if i in by_id]


async def _paper_context(paper_id: str, topic: str) -> str:
    """Section-labeled parent passages: all future_work + the paper's top topic hits."""
    hits = await vectors.search(topic, limit=6, paper_id=paper_id)
    parent_ids = {h["parent_id"] for h in hits if h.get("parent_id")}
    async with Session() as s:
        fw = (await s.execute(
            select(Chunk).where(Chunk.paper_id == paper_id, Chunk.parent_id.is_(None),
                                Chunk.section == "future_work").order_by(Chunk.paragraph)
        )).scalars().all()
        topical = (await s.execute(
            select(Chunk).where(Chunk.id.in_(parent_ids))
        )).scalars().all() if parent_ids else []
    seen: set[str] = set()
    parents = [c for c in fw + topical if not (c.id in seen or seen.add(c.id))]
    return "\n\n".join(f"[{c.section}] {c.text}" for c in parents[:6])


def _resolve_grounding(titles: list[str] | None, by_title: dict[str, str]) -> list[dict]:
    """LLM-cited titles → {paper_id, title}; unknown titles kept title-only, deduped."""
    out, seen = [], set()
    for t in titles or []:
        if not isinstance(t, str) or not t.strip():
            continue
        key = t.strip().lower()
        if key in seen:
            continue
        seen.add(key)
        pid = by_title.get(key)
        if pid is None and len(key) >= 4:  # 8B models abbreviate titles — accept a unique substring
            hits = {v for k, v in by_title.items() if key in k}
            if len(hits) == 1:
                pid = hits.pop()
        out.append({"paper_id": pid, "title": t.strip()})
    return out


def _valid_ideas(items) -> list[dict]:
    """Keep only well-formed idea objects (title + research_question present)."""
    if not isinstance(items, list):
        return []
    return [i for i in items if isinstance(i, dict)
            and str(i.get("title") or "").strip()
            and str(i.get("research_question") or "").strip()]


async def run_pipeline(job_id: str, progress) -> None:
    """MAP → GAPS → IDEATE. `progress` is worker._progress, passed in to avoid a cycle."""
    async with Session() as s:
        job = await s.get(Job, job_id)
        project_id, topic = job.payload["project_id"], job.payload["topic"]
        guidance = (job.payload.get("guidance") or "").strip()
    provider = await _provider(project_id)

    await progress(job_id, "selecting papers")
    papers = await _pick_papers(project_id, topic)
    if not papers:
        await progress(job_id, "no indexed papers match this topic — ingest some first", "failed")
        return

    notes = []
    for i, p in enumerate(papers, 1):
        user = f"Topic: {topic}\nPaper: {p.title}\n\nPassages:\n{await _paper_context(p.id, topic)}"
        try:
            note = json.dumps(await llm.complete_json("ideate", MAP_SYSTEM, user, provider=provider))
        except ValueError:  # degraded, not fatal: free-text note still feeds the gap stage
            note = await llm.complete("ideate", MAP_SYSTEM, user, provider=provider)
        notes.append(f"### {p.title}\n{note}")
        await progress(job_id, f"analyzed {i}/{len(papers)} papers")

    await progress(job_id, "synthesizing research gaps")
    gaps = await llm.complete(
        "ideate", GAPS_SYSTEM,
        f"Topic: {topic}\n\nPer-paper notes:\n\n" + "\n\n".join(notes), provider=provider)

    await progress(job_id, "generating ideas")
    async with Session() as s:
        prior = (await s.execute(select(Idea).where(Idea.project_id == project_id))).scalars().all()
    liked = [i.content.get("title", "") for i in prior if i.signal == "like"]
    disliked = [i.content.get("title", "") for i in prior if i.signal == "dislike"]
    prior_titles = [i.content.get("title", "") for i in prior if i.topic == topic]

    blocks = [f"Topic: {topic}", f"Gap analysis:\n{gaps}",
              "ALLOWED PAPERS:\n" + "\n".join(p.title for p in papers)]
    for label, titles in (("PRIOR IDEAS", prior_titles), ("LIKED", liked), ("DISLIKED", disliked)):
        if titles:
            blocks.append(f"{label}:\n" + "\n".join(f"- {t}" for t in titles if t))
    if guidance:
        blocks.append(f"USER GUIDANCE: {guidance}")

    raw = await llm.complete_json("ideate", IDEAS_SYSTEM.format(n=IDEA_COUNT),
                                  "\n\n".join(blocks), provider=provider, temperature=0.7)
    ideas = _valid_ideas(raw)
    if not ideas:
        await progress(job_id, "model returned no usable ideas — retry or switch provider", "failed")
        return

    by_title = {p.title.strip().lower(): p.id for p in papers}
    idea_ids = []
    async with Session() as s:
        for it in ideas[:IDEA_COUNT]:
            it["grounded_in"] = _resolve_grounding(it.get("grounded_in"), by_title)
            row = Idea(project_id=project_id, topic=topic, content=it)
            s.add(row)
            await s.flush()
            idea_ids.append(row.id)
        await s.commit()
    await progress(job_id, f"done: {len(idea_ids)} ideas generated", "done",
                   {"gaps": gaps, "idea_ids": idea_ids})


async def develop(idea_id: str) -> AsyncIterator[str]:
    """Streamed proposal blueprint for one idea, grounded in fresh retrieval."""
    async with Session() as s:
        idea = await s.get(Idea, idea_id)
        if idea is None:
            yield "Idea not found."
            return
        project_id, content, topic = idea.project_id, idea.content, idea.topic
    provider = await _provider(project_id)

    query = f"{content.get('title', '')} {content.get('research_question', '')}".strip() or topic
    hits = await vectors.search(query, limit=12, project_id=project_id)
    parent_ids = {h["parent_id"] for h in hits if h.get("parent_id")}
    blocks = []
    async with Session() as s:
        by_paper: dict[str, list[Chunk]] = {}
        if parent_ids:
            for c in (await s.execute(select(Chunk).where(Chunk.id.in_(parent_ids)))).scalars():
                by_paper.setdefault(c.paper_id, []).append(c)
        for pid, chunks in by_paper.items():
            paper = await s.get(Paper, pid)
            blocks.append(f"### {paper.title if paper else pid}\n"
                          + "\n\n".join(c.text for c in chunks[:4]))
    # ponytail: gap analysis not re-fed here — the idea's novelty field already carries it
    user = (f"Idea:\n{json.dumps(content, indent=1)}\n\nPaper excerpts:\n\n"
            + ("\n\n".join(blocks) or "(no excerpts retrieved)"))
    async for token in llm.stream("develop", DEVELOP_SYSTEM, user, provider=provider):
        yield token
