import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


class PaperStatus(str, enum.Enum):
    discovered = "discovered"  # search candidate, not selected for download yet
    queued = "queued"
    downloaded = "downloaded"
    parsed = "parsed"
    embedded = "embedded"
    failed = "failed"
    metadata_only = "metadata_only"  # paywalled / unparseable (doc Risk 4)


# doc §4: Paper.status is a state machine; each pipeline step advances it.
VALID_TRANSITIONS = {
    PaperStatus.discovered: {PaperStatus.queued},  # user selected the candidate
    PaperStatus.queued: {PaperStatus.downloaded, PaperStatus.failed, PaperStatus.metadata_only},
    PaperStatus.downloaded: {PaperStatus.parsed, PaperStatus.failed, PaperStatus.metadata_only},
    PaperStatus.parsed: {PaperStatus.embedded, PaperStatus.failed},
    PaperStatus.failed: {PaperStatus.queued},  # idempotent retry
}


class Project(Base):
    """Independent workspace: papers, likes, and LLM settings are scoped to a project."""

    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(Text)
    settings: Mapped[dict] = mapped_column(JSON, default=dict)  # {"provider": "ollama|claude|gemini"}
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Paper(Base):
    __tablename__ = "papers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id"), index=True)
    title: Mapped[str] = mapped_column(Text)
    authors: Mapped[list] = mapped_column(JSON, default=list)
    year: Mapped[int | None] = mapped_column(Integer)
    venue: Mapped[str | None] = mapped_column(Text)
    citation_count: Mapped[int] = mapped_column(Integer, default=0)
    doi: Mapped[str | None] = mapped_column(String(255), index=True)
    arxiv_id: Mapped[str | None] = mapped_column(String(64), index=True)  # unique per project (code-enforced)
    source: Mapped[str] = mapped_column(String(16), default="arxiv")  # arxiv | s2
    pdf_url: Mapped[str | None] = mapped_column(Text)  # non-arXiv open-access PDF
    abstract: Mapped[str | None] = mapped_column(Text)
    storage_key: Mapped[str | None] = mapped_column(String(255))
    content_hash: Mapped[str | None] = mapped_column(String(64))  # dedup within a project (doc Adım 1)
    status: Mapped[PaperStatus] = mapped_column(Enum(PaperStatus), default=PaperStatus.queued)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Chunk(Base):
    __tablename__ = "chunks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    paper_id: Mapped[str] = mapped_column(ForeignKey("papers.id"), index=True)
    parent_id: Mapped[str | None] = mapped_column(String(36), index=True)  # self-ref; parents have NULL
    section: Mapped[str] = mapped_column(String(64))
    text: Mapped[str] = mapped_column(Text)
    token_count: Mapped[int] = mapped_column(Integer, default=0)
    page: Mapped[int | None] = mapped_column(Integer)
    bbox: Mapped[dict | None] = mapped_column(JSON)  # {page,x,y,w,h} → UI navigation
    paragraph: Mapped[int | None] = mapped_column(Integer)  # index within section, for citations


class Feedback(Base):
    __tablename__ = "feedback"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    paper_id: Mapped[str] = mapped_column(ForeignKey("papers.id"), index=True)
    user_id: Mapped[str] = mapped_column(String(64), default="default")  # ponytail: single-user, add auth when multi-user
    signal: Mapped[str] = mapped_column(String(16))  # like | dislike
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    kind: Mapped[str] = mapped_column(String(32))  # search | analyze
    status: Mapped[str] = mapped_column(String(16), default="queued")  # queued|running|done|failed
    progress: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    result: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class LlmCall(Base):
    # ponytail: cost/latency ledger in Postgres; add Langfuse when a dashboard is needed
    __tablename__ = "llm_calls"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    model: Mapped[str] = mapped_column(String(64))
    purpose: Mapped[str] = mapped_column(String(32))  # summary | analyze | expand
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Summary(Base):
    # doc §5: GET /papers/{id}/summary is cached — this is the cache
    __tablename__ = "summaries"

    paper_id: Mapped[str] = mapped_column(ForeignKey("papers.id"), primary_key=True)
    matrix: Mapped[dict] = mapped_column(JSON)  # {section: {summary, citations[], unverified[]}}
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
