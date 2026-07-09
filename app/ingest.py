"""Adım 1 — discovery & download. API-first: arXiv (primary) + Semantic Scholar + OpenAlex.

ponytail: ResearchGate has no public API (Cloudflare-blocked) — OpenAlex covers the same
broad, non-arXiv literature with a real free API instead.
"""

import asyncio
import hashlib
import re
import xml.etree.ElementTree as ET

import httpx

from .config import settings

ARXIV_API = "https://export.arxiv.org/api/query"
S2_BATCH = "https://api.semanticscholar.org/graph/v1/paper/batch"
S2_SEARCH = "https://api.semanticscholar.org/graph/v1/paper/search"
OPENALEX_SEARCH = "https://api.openalex.org/works"
ATOM = "{http://www.w3.org/2005/Atom}"
ARXIV_NS = "{http://arxiv.org/schemas/atom}"
_ARXIV_URL_RE = re.compile(r"arxiv\.org/(?:abs|pdf)/([\w.]+?)(?:v\d+)?(?:\.pdf)?$")

# arXiv asks for >=3s between requests
ARXIV_DELAY = 3.0


async def search_arxiv(keywords: str, max_results: int | None = None, start: int = 0) -> list[dict]:
    """Query the arXiv Atom API → list of paper metadata dicts."""
    params = {
        "search_query": f"all:{keywords}",
        "start": start,
        "max_results": max_results or settings.search_max_results,
        "sortBy": "relevance",
    }
    async with httpx.AsyncClient(timeout=30) as http:
        r = await http.get(ARXIV_API, params=params)
        r.raise_for_status()

    entries = []
    for e in ET.fromstring(r.text).iter(f"{ATOM}entry"):
        arxiv_id = e.findtext(f"{ATOM}id", "").rsplit("/abs/", 1)[-1]  # e.g. 2401.12345v2
        published = e.findtext(f"{ATOM}published", "")
        entries.append(
            {
                "arxiv_id": arxiv_id,
                "title": " ".join(e.findtext(f"{ATOM}title", "").split()),
                "abstract": " ".join(e.findtext(f"{ATOM}summary", "").split()),
                "authors": [a.findtext(f"{ATOM}name", "") for a in e.iter(f"{ATOM}author")],
                "year": int(published[:4]) if published[:4].isdigit() else None,
                "doi": e.findtext(f"{ARXIV_NS}doi"),
                "venue": e.findtext(f"{ARXIV_NS}journal_ref"),
                "citation_count": 0,
                "source": "arxiv",
                "pdf_url": None,  # arXiv PDFs come from the id
            }
        )
    return entries


async def search_s2(keywords: str, max_results: int = 10, offset: int = 0) -> list[dict]:
    """Second discovery source: Semantic Scholar relevance search, open-access PDFs only.

    S2 rate-limits aggressively — failure is non-fatal, returns [].
    """
    try:
        async with httpx.AsyncClient(timeout=30) as http:
            r = await http.get(
                S2_SEARCH,
                params={"query": keywords, "limit": max_results, "offset": offset,
                        "fields": "title,abstract,year,authors,venue,citationCount,"
                                  "externalIds,openAccessPdf"},
            )
            r.raise_for_status()
    except Exception:
        return []  # ponytail: best-effort second source; add API key + retry when S2 matters

    entries = []
    for d in r.json().get("data") or []:
        ext = d.get("externalIds") or {}
        pdf = (d.get("openAccessPdf") or {}).get("url")
        if not (pdf or ext.get("ArXiv")):
            continue  # only list what we can actually download
        entries.append(
            {
                "arxiv_id": ext.get("ArXiv"),
                "title": d.get("title") or "",
                "abstract": d.get("abstract"),
                "authors": [a.get("name", "") for a in d.get("authors") or []],
                "year": d.get("year"),
                "doi": ext.get("DOI"),
                "venue": d.get("venue"),
                "citation_count": d.get("citationCount") or 0,
                "source": "s2",
                "pdf_url": pdf,
            }
        )
    return entries


def _openalex_abstract(inv: dict | None) -> str | None:
    """OpenAlex ships abstracts as an inverted index {word: [positions]} — rebuild the text."""
    if not inv:
        return None
    words = sorted((pos, w) for w, positions in inv.items() for pos in positions)
    return " ".join(w for _, w in words) or None


async def search_openalex(keywords: str, max_results: int = 10, page: int = 0) -> list[dict]:
    """Third discovery source: OpenAlex full-text search, open-access downloadables only.

    Free, no key. Failure is non-fatal (returns []). ResearchGate replacement.
    """
    try:
        async with httpx.AsyncClient(timeout=30) as http:
            r = await http.get(
                OPENALEX_SEARCH,
                params={
                    "search": keywords,
                    "per-page": max_results,
                    "page": page + 1,  # OpenAlex pages are 1-based
                    "mailto": settings.openalex_mailto or None,  # polite pool
                    "select": "title,abstract_inverted_index,authorships,publication_year,"
                              "doi,cited_by_count,best_oa_location,primary_location,ids",
                },
            )
            r.raise_for_status()
    except Exception:
        return []  # ponytail: best-effort third source

    entries = []
    for d in r.json().get("results") or []:
        loc = d.get("best_oa_location") or d.get("primary_location") or {}
        pdf = loc.get("pdf_url")
        landing = loc.get("landing_page_url") or ""
        m = _ARXIV_URL_RE.search(pdf or "") or _ARXIV_URL_RE.search(landing)
        arxiv_id = m.group(1) if m else None
        if not (pdf or arxiv_id):
            continue  # only list what we can actually download
        doi = (d.get("doi") or "").removeprefix("https://doi.org/") or None
        entries.append(
            {
                "arxiv_id": arxiv_id,
                "title": d.get("title") or "",
                "abstract": _openalex_abstract(d.get("abstract_inverted_index")),
                "authors": [(a.get("author") or {}).get("display_name", "")
                            for a in d.get("authorships") or []],
                "year": d.get("publication_year"),
                "doi": doi,
                "venue": (loc.get("source") or {}).get("display_name"),
                "citation_count": d.get("cited_by_count") or 0,
                "source": "openalex",
                "pdf_url": pdf,
            }
        )
    return entries


def _candidate_key(e: dict) -> str:
    return (e.get("arxiv_id") or "").split("v")[0] or " ".join(e["title"].lower().split())


def merge_candidates(*result_lists: list[dict]) -> list[dict]:
    """Merge multi-source/multi-query results, dedup by arxiv_id then normalized title.

    A paper found by several queries/sources ranks ahead of one found by a single query —
    overlap is a relevance signal, not a reason to drop results.
    """
    merged: dict[str, dict] = {}
    hits: dict[str, int] = {}
    for entries in result_lists:
        for e in entries:
            key = _candidate_key(e)
            if not key:
                continue
            hits[key] = hits.get(key, 0) + 1
            merged.setdefault(key, e)
    # stable sort keeps discovery order within an equal-overlap tier
    return sorted(merged.values(), key=lambda e: -hits[_candidate_key(e)])


async def enrich_s2(entries: list[dict]) -> None:
    """Add citation counts (and venue if missing) from Semantic Scholar, in one batch call.

    S2 is flaky/rate-limited — failure is non-fatal, citation_count just stays 0.
    """
    entries = [e for e in entries if e.get("arxiv_id") and e.get("source", "arxiv") == "arxiv"]
    if not entries:
        return
    ids = ["ARXIV:" + e["arxiv_id"].split("v")[0] for e in entries]
    try:
        async with httpx.AsyncClient(timeout=30) as http:
            r = await http.post(
                S2_BATCH,
                params={"fields": "citationCount,venue,externalIds"},
                json={"ids": ids},
            )
            r.raise_for_status()
            for entry, s2 in zip(entries, r.json()):
                if not s2:
                    continue
                entry["citation_count"] = s2.get("citationCount") or 0
                entry["venue"] = entry["venue"] or s2.get("venue")
                entry["doi"] = entry["doi"] or (s2.get("externalIds") or {}).get("DOI")
    except Exception:
        pass  # ponytail: enrichment is best-effort; add retry/API-key when S2 matters


def prefilter(entries: list[dict]) -> list[dict]:
    """Cheap decision before the expensive download (doc: Kalite Ön-Filtresi)."""
    return [
        e
        for e in entries
        if (e["year"] or 0) >= settings.prefilter_min_year
        and e["citation_count"] >= settings.prefilter_min_citations
    ]


async def download_pdf(arxiv_id: str | None, pdf_url: str | None = None) -> tuple[bytes, str]:
    """Fetch the PDF (arXiv id preferred, else direct open-access URL),
    return (bytes, sha256 content-hash for dedup)."""
    if arxiv_id:
        await asyncio.sleep(ARXIV_DELAY)
        url = f"https://arxiv.org/pdf/{arxiv_id}"
    elif pdf_url:
        url = pdf_url
    else:
        raise ValueError("paper has neither arxiv_id nor pdf_url")
    async with httpx.AsyncClient(timeout=120, follow_redirects=True) as http:
        r = await http.get(url)
        r.raise_for_status()
    if not r.content.startswith(b"%PDF"):
        raise ValueError(f"not a PDF response from {url}")
    return r.content, hashlib.sha256(r.content).hexdigest()
