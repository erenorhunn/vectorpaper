# VectorPaper

Academic research assistant with project workspaces: AI-assisted query suggestions →
arXiv + Semantic Scholar discovery → user selects candidates → PDF download → GROBID
structural parsing (LLM-free abstract/conclusion extract) → parent-child chunks → Qdrant
vectors → grounded LLM summary matrix → relevance feedback → deep-analysis blueprints.
Design doc: [doc.md](doc.md).

## Run

```bash
docker compose up -d --build   # everything: Postgres, Redis, Qdrant, MinIO, GROBID, Ollama (GPU), API, worker
docker exec idea-scraper-ollama-1 ollama pull qwen3:8b   # first machine only (models persist in ~/.ollama)
docker exec idea-scraper-ollama-1 ollama pull bge-large

cd ui && flutter run -d linux  # UI — the only non-docker piece
```

Cloud LLMs (Claude / Gemini): copy `.env.example` → `.env`, add the API keys,
`docker compose up -d` again. The providers become selectable per project in the app's
Settings; without keys everything runs on local Ollama.

For development you can still run the API/worker on the host
(`.venv/bin/uvicorn app.main:app --port 8000` + `.venv/bin/arq app.worker.WorkerSettings`
after stopping the `api`/`worker` containers); defaults in `app/config.py` point at
localhost ports, container overrides live in `docker-compose.yml`.

## Flow (UI)

1. **Proje seç/oluştur** — papers, likes, and LLM settings are per-project.
2. **Keşfet** — optionally ask the AI for query suggestions, add multiple queries as chips,
   search: ~20 downloadable candidates per page from arXiv + Semantic Scholar ("load more"
   pages further). Nothing is downloaded yet.
3. **Select & ingest** — checked papers are downloaded, parsed and embedded; abstract +
   conclusion appear immediately in the detail view's Çıkarım tab (no LLM involved).
4. **Kütüphane** — semantic search, like/dislike (feeds reranking), dual-pane detail
   (PDF + sections / grounded summary / deep analysis).
5. **Ayarlar** — light/dark theme, LLM provider per project, export PDFs, delete
   papers/content/project.

## API (same flow via curl)

```bash
curl http://localhost:8000/health
curl -X POST localhost:8000/projects -H 'content-type: application/json' -d '{"name": "demo"}'
curl -X POST localhost:8000/projects/<pid>/search-help -H 'content-type: application/json' \
  -d '{"topic": "cerrahi navigasyonda artırılmış gerçeklik"}'      # AI query suggestions
curl -X POST localhost:8000/projects/<pid>/discover -H 'content-type: application/json' \
  -d '{"queries": ["AR surgical navigation", "mixed reality surgery"], "page": 0}'
curl -X POST localhost:8000/projects/<pid>/ingest -H 'content-type: application/json' \
  -d '{"paper_ids": ["..."]}'                            # download+parse+embed selection
curl localhost:8000/jobs/<job_id>                        # poll progress
curl localhost:8000/papers/<paper_id>/extract            # abstract+conclusion, no LLM
curl "localhost:8000/papers?project_id=<pid>&q=chunking" # semantic search in project
curl localhost:8000/papers/<paper_id>/summary            # grounded summary matrix (cached)
curl -X POST localhost:8000/papers/<paper_id>/feedback -H 'content-type: application/json' \
  -d '{"signal": "like"}'
curl -X POST localhost:8000/analyze -H 'content-type: application/json' \
  -d '{"paper_id": "...", "topic": "..."}'               # then GET the stream_url (SSE)
curl -X DELETE localhost:8000/papers/<paper_id>          # removes PDF+vectors+rows
```

## Quality checks

```bash
PYTHONPATH= .venv/bin/python -m pytest tests/   # per-phase acceptance tests
.venv/bin/python eval/eval_retrieval.py         # golden-set hit@k / MRR (doc §9)
cd ui && flutter analyze && flutter test
```

Grounding: every summary claim must cite `[paper_id, Page N, Para M]`; citations are
post-hoc verified against the DB, unverifiable ones surface as "⚠ kaynaksız" in the UI.
LLM spend is logged per call in the `llm_calls` table; a daily token budget
(`DAILY_TOKEN_BUDGET`) hard-stops runaway costs.

## Layout

- `app/` — FastAPI (`main.py`), arq pipeline (`worker.py`), one module per pipeline step
  (`ingest` → `parse` → `chunks` → `vectors` → `summarize` / `analyze` / `rerank`)
- `ui/` — Flutter app: `main.dart` (theme/state/projects) + one file per page
- `tests/`, `eval/` — acceptance tests and retrieval-quality eval

Deliberate shortcuts are marked with `ponytail:` comments in code (OCR fallback,
Langfuse, Alembic, multi-user auth are stubs/deferred — see doc.md Faz 8).
