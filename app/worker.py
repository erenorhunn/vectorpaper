"""arq worker — the ingestion pipeline. One task per ingest job (user-selected candidates);
each paper walks the status state machine queued → downloaded → parsed → embedded
(or failed/metadata_only)."""

import logging

from arq.connections import RedisSettings
from sqlalchemy import delete, select

from . import chunks as chunking
from . import db, ingest, parse, storage, vectors
from .config import settings
from .db import Session
from .models import Chunk, Job, Paper, PaperStatus, VALID_TRANSITIONS

log = logging.getLogger("worker")


async def set_status(paper_id: str, new: PaperStatus, error: str | None = None) -> None:
    async with Session() as s:
        paper = await s.get(Paper, paper_id)
        if new not in VALID_TRANSITIONS.get(paper.status, set()):
            raise RuntimeError(f"invalid transition {paper.status} -> {new}")
        paper.status = new
        paper.error = error
        await s.commit()


async def _progress(job_id: str, msg: str, status: str = "running", result: dict | None = None) -> None:
    async with Session() as s:
        job = await s.get(Job, job_id)
        job.progress = msg
        job.status = status
        if result is not None:
            job.result = result
        await s.commit()
    log.info("job %s: %s", job_id, msg)


async def process_paper(paper_id: str) -> None:
    """download → parse → chunk → embed for one paper. Idempotent: safe to re-run from failed."""
    async with Session() as s:
        paper = await s.get(Paper, paper_id)
        if paper.status == PaperStatus.failed:  # idempotent retry: resume from what exists
            paper.status = PaperStatus.downloaded if paper.storage_key else PaperStatus.queued
            paper.error = None
            await s.commit()
        meta = {"title": paper.title, "year": paper.year, "citation_count": paper.citation_count,
                "project_id": paper.project_id}
        arxiv_id, pdf_url, project_id, status = paper.arxiv_id, paper.pdf_url, paper.project_id, paper.status

    if status == PaperStatus.queued:
        pdf, content_hash = await ingest.download_pdf(arxiv_id, pdf_url)
        async with Session() as s:
            dup = await s.scalar(select(Paper.id).where(Paper.content_hash == content_hash,
                                                        Paper.project_id == project_id,
                                                        Paper.id != paper_id))
            if dup:  # content-hash dedup (doc Adım 1)
                paper = await s.get(Paper, paper_id)
                paper.status = PaperStatus.metadata_only
                paper.error = f"duplicate content of {dup}"
                await s.commit()
                return
            key = f"{paper_id}.pdf"
            storage.put_pdf(key, pdf)
            paper = await s.get(Paper, paper_id)
            paper.storage_key = key
            paper.content_hash = content_hash
            await s.commit()
        await set_status(paper_id, PaperStatus.downloaded)
        status = PaperStatus.downloaded
    else:
        pdf = storage.get_pdf(f"{paper_id}.pdf")

    if status == PaperStatus.downloaded:
        tei = await parse.grobid_parse(pdf)
        records = parse.extract_sections(tei)
        if not records:
            # ponytail: OCR (Nougat/Llava) fallback hook goes here when formula-heavy PDFs matter
            await set_status(paper_id, PaperStatus.metadata_only, "GROBID returned no sections")
            return
        parents, children = chunking.build_chunks(paper_id, records)
        async with Session() as s:
            await s.execute(delete(Chunk).where(Chunk.paper_id == paper_id))  # re-parse is idempotent
            for row in parents + children:
                s.add(Chunk(**row))
            await s.commit()
        await set_status(paper_id, PaperStatus.parsed)
        status = PaperStatus.parsed

    if status == PaperStatus.parsed:
        async with Session() as s:
            children_rows = (
                await s.execute(select(Chunk).where(Chunk.paper_id == paper_id,
                                                    Chunk.parent_id.is_not(None)))
            ).scalars().all()
        await vectors.upsert_chunks(
            [{"id": c.id, "paper_id": c.paper_id, "parent_id": c.parent_id, "section": c.section,
              "text": c.text, "page": c.page, "paragraph": c.paragraph} for c in children_rows],
            meta,
        )
        await set_status(paper_id, PaperStatus.embedded)


async def run_ingest(ctx, job_id: str) -> None:
    """Download → parse → embed the papers the user selected from discovery results."""
    async with Session() as s:
        job = await s.get(Job, job_id)
        paper_ids = job.payload["paper_ids"]

    try:
        done = 0
        for pid in paper_ids:
            async with Session() as s:
                st = (await s.get(Paper, pid)).status
            if st == PaperStatus.embedded:
                done += 1
                continue
            try:
                await process_paper(pid)
                done += 1
            except Exception as e:
                log.exception("paper %s failed", pid)
                try:
                    await set_status(pid, PaperStatus.failed, str(e)[:500])
                except Exception:
                    pass
            await _progress(job_id, f"processed {done}/{len(paper_ids)} papers")

        await _progress(job_id, f"done: {done}/{len(paper_ids)} papers embedded", "done",
                        {"paper_ids": paper_ids})
    except Exception as e:
        log.exception("job %s failed", job_id)
        await _progress(job_id, str(e)[:500], "failed")


async def startup(ctx) -> None:
    await db.init_db()
    storage.ensure_bucket()
    await vectors.ensure_collection()


class WorkerSettings:
    functions = [run_ingest]
    on_startup = startup
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    job_timeout = 3600
