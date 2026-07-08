"""Adım 3 — embeddings (Ollama, OpenAI-compatible) + Qdrant with payload filtering."""

from openai import AsyncOpenAI
from qdrant_client import AsyncQdrantClient, models as qm

from .config import settings

COLLECTION = "chunks"

_embed = AsyncOpenAI(base_url=settings.llm_base_url, api_key=settings.llm_api_key)
qdrant = AsyncQdrantClient(url=settings.qdrant_url)


async def ensure_collection() -> None:
    if not await qdrant.collection_exists(COLLECTION):
        try:
            await qdrant.create_collection(
                COLLECTION,
                vectors_config=qm.VectorParams(size=settings.embed_dim, distance=qm.Distance.COSINE),
            )
        except Exception:  # concurrent create by api+worker — someone else won, fine
            pass
    # payload-indexed fields → "year > 2024 AND citations > 50" in one query (doc Adım 3);
    # idempotent so new indexes (project_id) also land on pre-existing collections
    for field, ftype in [("year", "integer"), ("citation_count", "integer"),
                         ("section", "keyword"), ("paper_id", "keyword"),
                         ("project_id", "keyword")]:
        try:
            await qdrant.create_payload_index(COLLECTION, field, ftype)
        except Exception:
            pass


async def embed(texts: list[str]) -> list[list[float]]:
    r = await _embed.embeddings.create(model=settings.embed_model, input=texts)
    return [d.embedding for d in r.data]


async def upsert_chunks(children: list[dict], paper: dict) -> None:
    """Embed child chunks and store with paper metadata in the payload."""
    for i in range(0, len(children), 32):
        batch = children[i : i + 32]
        vectors = await embed([c["text"] for c in batch])
        await qdrant.upsert(
            COLLECTION,
            points=[
                qm.PointStruct(
                    id=c["id"],
                    vector=v,
                    payload={
                        "paper_id": c["paper_id"], "parent_id": c["parent_id"],
                        "section": c["section"], "text": c["text"],
                        "page": c["page"], "paragraph": c["paragraph"],
                        "year": paper.get("year"), "citation_count": paper.get("citation_count", 0),
                        "title": paper.get("title"), "project_id": paper.get("project_id"),
                    },
                )
                for c, v in zip(batch, vectors)
            ],
        )


def _filter(year_min: int | None = None, min_citations: int | None = None,
            section: str | None = None, paper_id: str | None = None,
            project_id: str | None = None) -> qm.Filter | None:
    must: list[qm.Condition] = []
    if year_min:
        must.append(qm.FieldCondition(key="year", range=qm.Range(gte=year_min)))
    if min_citations:
        must.append(qm.FieldCondition(key="citation_count", range=qm.Range(gte=min_citations)))
    if section:
        must.append(qm.FieldCondition(key="section", match=qm.MatchValue(value=section)))
    if paper_id:
        must.append(qm.FieldCondition(key="paper_id", match=qm.MatchValue(value=paper_id)))
    if project_id:
        must.append(qm.FieldCondition(key="project_id", match=qm.MatchValue(value=project_id)))
    return qm.Filter(must=must) if must else None


async def delete_paper(paper_id: str) -> None:
    await qdrant.delete(COLLECTION, points_selector=qm.FilterSelector(filter=_filter(paper_id=paper_id)))


async def search(query: str, limit: int = 10, **filters) -> list[dict]:
    """Semantic search + metadata filter in one Qdrant query. Returns child payloads + scores."""
    vec = (await embed([query]))[0]
    res = await qdrant.query_points(COLLECTION, query=vec, limit=limit, query_filter=_filter(**filters))
    return [{**p.payload, "score": p.score} for p in res.points]
