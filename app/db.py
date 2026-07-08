from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from .config import settings

engine = create_async_engine(settings.database_url)
Session = async_sessionmaker(engine, expire_on_commit=False)

# ponytail: ad-hoc DDL instead of Alembic — one upgrade path (v1 → projects); switch to
# Alembic at the next schema change
_MIGRATE = [
    "ALTER TYPE paperstatus ADD VALUE IF NOT EXISTS 'discovered'",
    "ALTER TABLE papers ADD COLUMN IF NOT EXISTS project_id VARCHAR(36)",
    "ALTER TABLE papers ADD COLUMN IF NOT EXISTS source VARCHAR(16) DEFAULT 'arxiv'",
    "ALTER TABLE papers ADD COLUMN IF NOT EXISTS pdf_url TEXT",
    # same paper may now exist in several projects (v1 had a unique index / constraint)
    "DROP INDEX IF EXISTS ix_papers_arxiv_id",
    "CREATE INDEX IF NOT EXISTS ix_papers_arxiv_id ON papers (arxiv_id)",
    "ALTER TABLE papers DROP CONSTRAINT IF EXISTS papers_arxiv_id_key",
    "ALTER TABLE papers DROP CONSTRAINT IF EXISTS papers_content_hash_key",
]


async def init_db() -> None:
    from . import models

    async with engine.begin() as conn:
        await conn.run_sync(models.Base.metadata.create_all)

    async with engine.connect() as conn:  # AUTOCOMMIT: ALTER TYPE can't run in a tx block
        ac = await conn.execution_options(isolation_level="AUTOCOMMIT")
        for ddl in _MIGRATE:
            await ac.execute(text(ddl))

    async with Session() as s:  # adopt pre-projects papers into a default project
        orphans = (await s.execute(text("SELECT count(*) FROM papers WHERE project_id IS NULL"))).scalar()
        if orphans:
            default = models.Project(name="Default")
            s.add(default)
            await s.flush()
            await s.execute(text("UPDATE papers SET project_id = :p WHERE project_id IS NULL"),
                            {"p": default.id})
            await s.commit()
