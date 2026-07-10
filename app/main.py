import json
from contextlib import asynccontextmanager

import httpx
from arq.connections import RedisSettings, create_pool
from fastapi import FastAPI, File, Form, HTTPException, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import delete, func, select, text

from . import analyze as analysis
from . import chat as chatmod
from . import db, ideate, ingest, rerank, storage, summarize, vectors
from .config import settings
from .models import ChatMessage, Chunk, Feedback, Idea, Job, Paper, PaperStatus, Project, Summary


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.init_db()
    storage.ensure_bucket()
    await vectors.ensure_collection()
    app.state.arq = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    yield
    await app.state.arq.aclose()


app = FastAPI(title="idea-scraper", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.get("/health")
async def health():
    """Ping every backing service; the real compose healthcheck lives here."""
    checks: dict[str, str] = {}
    try:
        async with db.Session() as s:
            await s.execute(text("SELECT 1"))
        checks["postgres"] = "ok"
    except Exception as e:
        checks["postgres"] = f"error: {e}"

    async with httpx.AsyncClient(timeout=5) as http:
        for name, url in [
            ("qdrant", f"{settings.qdrant_url}/readyz"),
            ("grobid", f"{settings.grobid_url}/api/isalive"),
            ("minio", f"http://{settings.minio_endpoint}/minio/health/live"),
            ("ollama", f"{settings.llm_base_url.removesuffix('/v1')}/api/version"),
        ]:
            try:
                r = await http.get(url)
                checks[name] = "ok" if r.status_code == 200 else f"error: HTTP {r.status_code}"
            except Exception as e:
                checks[name] = f"error: {e}"

    try:
        await app.state.arq.ping()
        checks["redis"] = "ok"
    except Exception as e:
        checks["redis"] = f"error: {e}"

    ok = all(v == "ok" for v in checks.values())
    return {"status": "ok" if ok else "degraded", "services": checks}


@app.get("/providers")
async def providers():
    """LLM providers the UI can offer; availability = key configured (.env)."""
    return {"providers": [
        {"id": "ollama", "label": "Ollama", "available": True, "model": settings.summary_model},
        {"id": "claude", "label": "Claude", "available": bool(settings.anthropic_api_key),
         "model": settings.claude_model},
        {"id": "gemini", "label": "Gemini", "available": bool(settings.gemini_api_key),
         "model": settings.gemini_model},
    ]}


# ---- Projects: independent workspaces (papers, likes, settings) --------------------------

class ProjectRequest(BaseModel):
    name: str | None = None
    settings: dict | None = None


def _project_dict(p: Project, paper_count: int = 0) -> dict:
    return {"id": p.id, "name": p.name, "settings": p.settings or {},
            "created_at": p.created_at.isoformat(), "paper_count": paper_count}


async def _project(s, project_id: str) -> Project:
    p = await s.get(Project, project_id)
    if p is None:
        raise HTTPException(404, "unknown project")
    return p


async def _provider(project_id: str) -> str:
    async with db.Session() as s:
        p = await _project(s, project_id)
    return (p.settings or {}).get("provider", "ollama")


@app.get("/projects")
async def list_projects():
    async with db.Session() as s:
        counts = dict((await s.execute(
            select(Paper.project_id, func.count()).where(Paper.status != PaperStatus.discovered)
            .group_by(Paper.project_id))).all())
        rows = (await s.execute(select(Project).order_by(Project.created_at))).scalars().all()
        return {"projects": [_project_dict(p, counts.get(p.id, 0)) for p in rows]}


@app.post("/projects")
async def create_project(req: ProjectRequest):
    if not (req.name or "").strip():
        raise HTTPException(422, "name required")
    async with db.Session() as s:
        p = Project(name=req.name.strip(), settings=req.settings or {})
        s.add(p)
        await s.commit()
        return _project_dict(p)


@app.patch("/projects/{project_id}")
async def update_project(project_id: str, req: ProjectRequest):
    async with db.Session() as s:
        p = await _project(s, project_id)
        if req.name and req.name.strip():
            p.name = req.name.strip()
        if req.settings is not None:
            p.settings = {**(p.settings or {}), **req.settings}
        await s.commit()
        return _project_dict(p)


async def _delete_paper_row(s, p: Paper) -> None:
    """Remove one paper everywhere: Qdrant points, MinIO PDF, DB rows."""
    await vectors.delete_paper(p.id)
    if p.storage_key:
        try:
            storage.delete_pdf(p.storage_key)
        except Exception:
            pass  # missing object is fine
    await s.execute(delete(Chunk).where(Chunk.paper_id == p.id))
    await s.execute(delete(Feedback).where(Feedback.paper_id == p.id))
    await s.execute(delete(Summary).where(Summary.paper_id == p.id))
    await s.delete(p)


@app.delete("/projects/{project_id}")
async def delete_project(project_id: str):
    async with db.Session() as s:
        p = await _project(s, project_id)
        for paper in (await s.execute(select(Paper).where(Paper.project_id == project_id))).scalars():
            await _delete_paper_row(s, paper)
        await s.execute(delete(Idea).where(Idea.project_id == project_id))
        await s.execute(delete(ChatMessage).where(ChatMessage.project_id == project_id))
        await s.delete(p)
        await s.commit()
    return {"ok": True}


@app.delete("/projects/{project_id}/papers")
async def delete_project_papers(project_id: str):
    """Settings → clear all downloaded content but keep the project."""
    async with db.Session() as s:
        await _project(s, project_id)
        papers = (await s.execute(select(Paper).where(Paper.project_id == project_id))).scalars().all()
        for paper in papers:
            await _delete_paper_row(s, paper)
        await s.commit()
    return {"ok": True, "deleted": len(papers)}


# ---- Adım 1a: pre-search AI help ----------------------------------------------------------

class HelpRequest(BaseModel):
    topic: str


@app.post("/projects/{project_id}/search-help")
async def search_help(project_id: str, req: HelpRequest):
    if not req.topic.strip():
        raise HTTPException(422, "topic required")
    queries = await rerank.suggest_queries(req.topic, project_id, await _provider(project_id))
    return {"queries": queries}


# ---- Adım 1b: discovery (candidates only — nothing is downloaded yet) ---------------------

DISCOVER_PAGE = 20  # candidates per query per page, split across arXiv + S2 + OpenAlex


class DiscoverRequest(BaseModel):
    queries: list[str]
    page: int = 0
    year_min: int | None = None
    min_citations: int | None = None


@app.post("/projects/{project_id}/discover")
async def discover(project_id: str, req: DiscoverRequest):
    queries = [q.strip() for q in req.queries if q.strip()]
    if not queries:
        raise HTTPException(422, "at least one query required")
    async with db.Session() as s:
        await _project(s, project_id)

    per_query = max(1, DISCOVER_PAGE // 3)  # each query gets a full share; more queries → more results, not fewer
    arxiv_lists, s2_lists, oa_lists = [], [], []
    for q in queries:
        arxiv_lists.append(await ingest.search_arxiv(q, per_query, start=req.page * per_query))
        s2_lists.append(await ingest.search_s2(q, per_query, offset=req.page * per_query))
        oa_lists.append(await ingest.search_openalex(q, per_query, page=req.page))
    candidates = ingest.merge_candidates(*arxiv_lists, *s2_lists, *oa_lists)
    await ingest.enrich_s2(candidates)
    if req.year_min:
        candidates = [c for c in candidates if (c.get("year") or 0) >= req.year_min]
    if req.min_citations:
        candidates = [c for c in candidates if (c.get("citation_count") or 0) >= req.min_citations]

    out = []
    async with db.Session() as s:
        for e in candidates:
            stmt = select(Paper).where(Paper.project_id == project_id)
            if e.get("arxiv_id"):
                stmt = stmt.where(Paper.arxiv_id == e["arxiv_id"])
            else:
                stmt = stmt.where(func.lower(Paper.title) == e["title"].lower())
            existing = (await s.execute(stmt.limit(1))).scalar_one_or_none()
            if existing is None:
                existing = Paper(**e, project_id=project_id, status=PaperStatus.discovered)
                s.add(existing)
                await s.flush()
            out.append(_paper_dict(existing))
        await s.commit()
    return {"papers": out, "page": req.page}


# ---- Adım 1c: ingest the user's selection --------------------------------------------------

class IngestRequest(BaseModel):
    paper_ids: list[str]


@app.post("/projects/{project_id}/ingest")
async def start_ingest(project_id: str, req: IngestRequest):
    async with db.Session() as s:
        rows = (await s.execute(
            select(Paper).where(Paper.id.in_(req.paper_ids), Paper.project_id == project_id)
        )).scalars().all()
        if not rows:
            raise HTTPException(404, "no matching papers in project")
        for p in rows:
            if p.status == PaperStatus.discovered:
                p.status = PaperStatus.queued
        job = Job(kind="ingest", payload={"paper_ids": [p.id for p in rows],
                                          "project_id": project_id})
        s.add(job)
        await s.commit()
        job_id = job.id
    await app.state.arq.enqueue_job("run_ingest", job_id)
    return {"job_id": job_id}


import hashlib
import re


async def _enqueue_one(project_id: str, paper: Paper) -> str:
    """Persist a manually-added paper + its ingest job, mirror of start_ingest."""
    async with db.Session() as s:
        await _project(s, project_id)
        s.add(paper)
        await s.flush()
        job = Job(kind="ingest", payload={"paper_ids": [paper.id], "project_id": project_id})
        s.add(job)
        await s.commit()
        job_id, paper_id = job.id, paper.id
    await app.state.arq.enqueue_job("run_ingest", job_id)
    return job_id


@app.post("/projects/{project_id}/add")
async def add_paper(project_id: str, link: str = Form(None), file: UploadFile = File(None)):
    """Manual add: a PDF/arXiv link, or an uploaded PDF file. Title is refined by GROBID later."""
    if file is not None:
        data = await file.read()
        if not data.startswith(b"%PDF"):
            raise HTTPException(422, "uploaded file is not a PDF")
        paper = Paper(project_id=project_id, title=file.filename or "Uploaded PDF",
                      source="upload", status=PaperStatus.downloaded,
                      content_hash=hashlib.sha256(data).hexdigest())
        async with db.Session() as s:  # need the id before we can key storage
            s.add(paper)
            await s.flush()
            paper.storage_key = f"{paper.id}.pdf"
            storage.put_pdf(paper.storage_key, data)
            job = Job(kind="ingest", payload={"paper_ids": [paper.id], "project_id": project_id})
            s.add(job)
            await s.commit()
            job_id = job.id
        await app.state.arq.enqueue_job("run_ingest", job_id)
        return {"job_id": job_id}

    if link:
        link = link.strip()
        m = re.search(r"arxiv\.org/(?:abs|pdf)/([\w.\-/]+?)(?:v\d+)?(?:\.pdf)?/?$", link)
        arxiv_id = m.group(1) if m else None
        paper = Paper(project_id=project_id, title=link.rstrip("/").rsplit("/", 1)[-1],
                      arxiv_id=arxiv_id, pdf_url=None if arxiv_id else link,
                      source="arxiv" if arxiv_id else "link", status=PaperStatus.queued)
        return {"job_id": await _enqueue_one(project_id, paper)}

    raise HTTPException(422, "provide a link or a file")


def _paper_dict(p: Paper) -> dict:
    return {"id": p.id, "project_id": p.project_id, "title": p.title, "authors": p.authors,
            "year": p.year, "venue": p.venue, "citation_count": p.citation_count, "doi": p.doi,
            "arxiv_id": p.arxiv_id, "source": p.source, "abstract": p.abstract,
            "status": p.status.value, "error": p.error,
            "downloadable": bool(p.arxiv_id or p.pdf_url), "has_pdf": bool(p.storage_key)}


@app.get("/jobs/{job_id}")
async def get_job(job_id: str):
    async with db.Session() as s:
        job = await s.get(Job, job_id)
        if job is None:
            raise HTTPException(404)
        papers = []
        if job.result and job.result.get("paper_ids"):
            rows = await s.execute(select(Paper).where(Paper.id.in_(job.result["paper_ids"])))
            papers = [_paper_dict(p) for p in rows.scalars()]
    return {"id": job.id, "status": job.status, "progress": job.progress, "papers": papers,
            "result": job.result}


# ---- Adım 3: filtered / semantic listing -------------------------------------------------

@app.get("/papers")
async def list_papers(project_id: str, q: str | None = None, year: int | None = None,
                      min_citations: int | None = None, limit: int = 50):
    if q:  # semantic search over chunks + metadata filter in one Qdrant query
        hits = await vectors.search(q, limit=50, year_min=year, min_citations=min_citations,
                                    project_id=project_id)
        hits = await rerank.rerank(q, hits, project_id)  # feedback-aware reranking (Faz 6)
        seen, papers = set(), []
        async with db.Session() as s:
            for h in hits:
                if h["paper_id"] in seen:
                    continue
                seen.add(h["paper_id"])
                p = await s.get(Paper, h["paper_id"])
                if p:
                    papers.append({**_paper_dict(p), "match": h["text"][:300], "score": h["score"]})
                if len(papers) >= limit:
                    break
        return {"papers": papers}

    stmt = (select(Paper).where(Paper.project_id == project_id,
                                Paper.status != PaperStatus.discovered)
            .order_by(Paper.created_at.desc()).limit(limit))
    if year:
        stmt = stmt.where(Paper.year >= year)
    if min_citations:
        stmt = stmt.where(Paper.citation_count >= min_citations)
    async with db.Session() as s:
        rows = await s.execute(stmt)
        return {"papers": [_paper_dict(p) for p in rows.scalars()]}


@app.get("/papers/{paper_id}")
async def get_paper(paper_id: str):
    async with db.Session() as s:
        p = await s.get(Paper, paper_id)
        if p is None:
            raise HTTPException(404)
        sections = (
            await s.execute(
                select(Chunk).where(Chunk.paper_id == paper_id, Chunk.parent_id.is_(None))
                .order_by(Chunk.section, Chunk.paragraph)
            )
        ).scalars().all()
    section_map: dict[str, list] = {}
    for c in sections:
        section_map.setdefault(c.section, []).append(
            {"chunk_id": c.id, "paragraph": c.paragraph, "page": c.page, "bbox": c.bbox,
             "preview": c.text[:150]}
        )
    return {**_paper_dict(p), "sections": section_map}


@app.get("/papers/{paper_id}/extract")
async def get_extract(paper_id: str):
    """LLM-free quick view right after download: abstract + conclusion/future-work text."""
    async with db.Session() as s:
        p = await s.get(Paper, paper_id)
        if p is None:
            raise HTTPException(404)
        rows = (await s.execute(
            select(Chunk).where(Chunk.paper_id == paper_id, Chunk.parent_id.is_(None),
                                Chunk.section.in_(["abstract", "future_work"]))
            .order_by(Chunk.section, Chunk.paragraph)
        )).scalars().all()
    abstract = [c.text for c in rows if c.section == "abstract"]
    conclusion = [{"text": c.text, "page": c.page} for c in rows if c.section == "future_work"]
    return {"title": p.title, "status": p.status.value,
            "abstract": "\n\n".join(abstract) or p.abstract,
            "conclusion": conclusion}


@app.get("/papers/{paper_id}/pdf")
async def get_pdf(paper_id: str):
    async with db.Session() as s:
        p = await s.get(Paper, paper_id)
    if p is None or not p.storage_key:
        raise HTTPException(404)
    return Response(storage.get_pdf(p.storage_key), media_type="application/pdf")


@app.delete("/papers/{paper_id}")
async def delete_paper(paper_id: str):
    async with db.Session() as s:
        p = await s.get(Paper, paper_id)
        if p is None:
            raise HTTPException(404)
        await _delete_paper_row(s, p)
        await s.commit()
    return {"ok": True}


# ---- Feedback (Faz 6 input) --------------------------------------------------------------

class FeedbackRequest(BaseModel):
    signal: str  # like | dislike


@app.post("/papers/{paper_id}/feedback")
async def post_feedback(paper_id: str, req: FeedbackRequest):
    if req.signal not in ("like", "dislike"):
        raise HTTPException(422, "signal must be like|dislike")
    async with db.Session() as s:
        if await s.get(Paper, paper_id) is None:
            raise HTTPException(404)
        s.add(Feedback(paper_id=paper_id, signal=req.signal))
        await s.commit()
    return {"ok": True}


# ---- Adım 4: summary matrix --------------------------------------------------------------

@app.get("/papers/{paper_id}/summary")
async def get_summary(paper_id: str, refresh: bool = False):
    if refresh:  # e.g. after switching LLM provider
        async with db.Session() as s:
            await s.execute(delete(Summary).where(Summary.paper_id == paper_id))
            await s.commit()
    try:
        return await summarize.summarize_paper(paper_id)
    except ValueError:
        raise HTTPException(404)
    except Exception as e:  # LLM/upstream down → legible 502, not raw 500
        raise HTTPException(502, f"summary failed: {e}")


# ---- Adım 5: deep analysis ---------------------------------------------------------------

class AnalyzeRequest(BaseModel):
    paper_id: str
    topic: str


@app.post("/analyze")
async def start_analyze(req: AnalyzeRequest):
    async with db.Session() as s:
        job = Job(kind="analyze", payload=req.model_dump())
        s.add(job)
        await s.commit()
        return {"job_id": job.id, "stream_url": f"/analyze/{job.id}/stream"}


@app.get("/analyze/{job_id}/stream")
async def stream_analysis(job_id: str):
    async with db.Session() as s:
        job = await s.get(Job, job_id)
        if job is None or job.kind != "analyze":
            raise HTTPException(404)
        params = job.payload

    async def sse():
        try:
            async for token in analysis.deep_analysis(params["paper_id"], params["topic"]):
                yield f"data: {json.dumps(token)}\n\n"  # JSON keeps newlines SSE-safe
            yield "data: [DONE]\n\n"
        except Exception as e:
            yield f"data: [ERROR] {e}\n\n"

    return StreamingResponse(sse(), media_type="text/event-stream")


# ---- Ideas wizard: topic → gap analysis → grounded article ideas --------------------------

class IdeateRequest(BaseModel):
    topic: str
    guidance: str | None = None


@app.post("/projects/{project_id}/ideate")
async def start_ideate(project_id: str, req: IdeateRequest):
    if not req.topic.strip():
        raise HTTPException(422, "topic required")
    async with db.Session() as s:
        await _project(s, project_id)
        job = Job(kind="ideate", payload={"project_id": project_id, "topic": req.topic.strip(),
                                          "guidance": req.guidance})
        s.add(job)
        await s.commit()
        job_id = job.id
    await app.state.arq.enqueue_job("run_ideate", job_id)
    return {"job_id": job_id}


def _idea_dict(i: Idea) -> dict:
    return {"id": i.id, "topic": i.topic, "content": i.content, "signal": i.signal,
            "has_proposal": bool(i.proposal), "created_at": i.created_at.isoformat()}


@app.get("/projects/{project_id}/ideas")
async def list_ideas(project_id: str):
    async with db.Session() as s:
        rows = (await s.execute(select(Idea).where(Idea.project_id == project_id)
                                .order_by(Idea.created_at.desc()))).scalars().all()
    return {"ideas": [_idea_dict(i) for i in rows]}


class IdeaSignalRequest(BaseModel):
    signal: str | None = None  # like | dislike | null (clear)


@app.patch("/ideas/{idea_id}")
async def patch_idea(idea_id: str, req: IdeaSignalRequest):
    if req.signal not in ("like", "dislike", None):
        raise HTTPException(422, "signal must be like|dislike|null")
    async with db.Session() as s:
        idea = await s.get(Idea, idea_id)
        if idea is None:
            raise HTTPException(404)
        idea.signal = req.signal
        await s.commit()
    return {"ok": True}


@app.delete("/ideas/{idea_id}")
async def delete_idea(idea_id: str):
    async with db.Session() as s:
        idea = await s.get(Idea, idea_id)
        if idea is None:
            raise HTTPException(404)
        await s.delete(idea)
        await s.commit()
    return {"ok": True}


@app.get("/ideas/{idea_id}/develop")
async def develop_idea(idea_id: str, refresh: bool = False):
    """Streamed proposal blueprint; cached in Idea.proposal and replayed unless refresh."""
    async with db.Session() as s:
        idea = await s.get(Idea, idea_id)
        if idea is None:
            raise HTTPException(404)
        cached = None if refresh else idea.proposal

    async def sse():
        if cached:
            yield f"data: {json.dumps(cached)}\n\n"
            yield "data: [DONE]\n\n"
            return
        acc = ""
        try:
            async for token in ideate.develop(idea_id):
                acc += token
                yield f"data: {json.dumps(token)}\n\n"
            async with db.Session() as s:
                (await s.get(Idea, idea_id)).proposal = acc
                await s.commit()
            yield "data: [DONE]\n\n"
        except Exception as e:
            yield f"data: [ERROR] {e}\n\n"

    return StreamingResponse(sse(), media_type="text/event-stream")


# ---- Library-settings chat ---------------------------------------------------------------

class ChatRequest(BaseModel):
    content: str
    paper_ids: list[str] = []


@app.get("/projects/{project_id}/chat")
async def get_chat(project_id: str):
    async with db.Session() as s:
        rows = (await s.execute(
            select(ChatMessage).where(ChatMessage.project_id == project_id)
            .order_by(ChatMessage.created_at)
        )).scalars().all()
    return {"messages": [{"role": m.role, "content": m.content, "paper_ids": m.paper_ids}
                         for m in rows]}


@app.delete("/projects/{project_id}/chat")
async def clear_chat(project_id: str):
    async with db.Session() as s:
        await s.execute(delete(ChatMessage).where(ChatMessage.project_id == project_id))
        await s.commit()
    return {"ok": True}


@app.post("/projects/{project_id}/chat")
async def post_chat(project_id: str, req: ChatRequest):
    async with db.Session() as s:
        s.add(ChatMessage(project_id=project_id, role="user", content=req.content,
                          paper_ids=req.paper_ids))
        await s.commit()
        history = [{"role": m.role, "content": m.content} for m in (await s.execute(
            select(ChatMessage).where(ChatMessage.project_id == project_id)
            .order_by(ChatMessage.created_at)
        )).scalars().all()]

    async def sse():
        acc = ""
        try:
            async for token in chatmod.answer(project_id, history, req.paper_ids):
                acc += token
                yield f"data: {json.dumps(token)}\n\n"
            async with db.Session() as s:
                s.add(ChatMessage(project_id=project_id, role="assistant", content=acc))
                await s.commit()
            yield "data: [DONE]\n\n"
        except Exception as e:
            yield f"data: [ERROR] {e}\n\n"

    return StreamingResponse(sse(), media_type="text/event-stream")
