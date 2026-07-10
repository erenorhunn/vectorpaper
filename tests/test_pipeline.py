"""Per-phase acceptance checks from doc §7. Unit-level tests run offline;
tests hitting live services are marked and skipped automatically when services are down.
"""

import socket

import pytest

from app import chunks, parse


def _up(host: str, port: int) -> bool:
    try:
        socket.create_connection((host, port), timeout=1).close()
        return True
    except OSError:
        return False


# ---- Faz 2: section classification (offline) ---------------------------------------------

def test_classify_sections():
    assert parse.classify_section("3. Methodology") == "methodology"
    assert parse.classify_section("Our Proposed Approach") == "methodology"
    assert parse.classify_section("Conclusion and Future Work") == "future_work"
    assert parse.classify_section("Limitations") == "future_work"
    assert parse.classify_section("5 Experiments and Results") == "results"
    assert parse.classify_section("1 Introduction") == "introduction"
    assert parse.classify_section("Acknowledgements") == "other"


def test_coords_to_bbox():
    page, bbox = parse._coords_to_bbox("3,53.34,88.03,204.08,7.93;3,1,2,3,4")
    assert page == 3
    assert bbox == {"page": 3, "x": 53.34, "y": 88.03, "w": 204.08, "h": 7.93}
    assert parse._coords_to_bbox(None) == (None, None)
    assert parse._coords_to_bbox("garbage") == (None, None)


def test_extract_sections_from_tei():
    tei = """<TEI xmlns="http://www.tei-c.org/ns/1.0">
      <teiHeader><profileDesc><abstract><p>We propose a novel retrieval method for science.</p></abstract></profileDesc></teiHeader>
      <text><body>
        <div><head>3. Methodology</head>
          <p coords="2,10,20,300,40">Our method embeds parent and child chunks separately, which improves recall.</p>
          <p coords="2,10,80,300,40">We then rerank the retrieved candidates using a cross encoder model for precision.</p>
        </div>
        <div><head>Conclusion and Future Work</head>
          <p coords="8,10,20,300,40">Future work includes extending the approach to multimodal documents and tables.</p>
        </div>
      </body></text></TEI>"""
    recs = parse.extract_sections(tei)
    sections = {r["section"] for r in recs}
    assert {"abstract", "methodology", "future_work"} <= sections
    meth = [r for r in recs if r["section"] == "methodology"]
    assert meth[0]["page"] == 2 and meth[0]["bbox"]["x"] == 10.0
    assert [r["paragraph"] for r in meth] == [0, 1]


# ---- Faz 3: parent-child chunking (offline) -----------------------------------------------

def test_build_chunks_parent_child():
    long_text = " ".join(f"word{i}" for i in range(400))  # ~530 tokens → multiple children
    records = [{"section": "methodology", "title": "M", "text": long_text,
                "page": 2, "bbox": {"page": 2, "x": 0, "y": 0, "w": 1, "h": 1}, "paragraph": 0}]
    parents, children = chunks.build_chunks("paper-1", records)
    assert len(parents) == 1
    assert len(children) > 1
    assert all(c["parent_id"] == parents[0]["id"] for c in children)
    assert all(c["token_count"] <= 280 for c in children)
    # overlap: consecutive children share words
    assert set(children[0]["text"].split()) & set(children[1]["text"].split())


def test_short_paragraph_single_child():
    records = [{"section": "abstract", "title": "A", "text": "Short abstract text here.",
                "page": None, "bbox": None, "paragraph": 0}]
    parents, children = chunks.build_chunks("p", records)
    assert len(children) == 1
    assert children[0]["text"] == records[0]["text"]


# ---- Faz 4: citation format + verifier regex (offline) ------------------------------------

def test_citation_regex():
    from app.summarize import CITATION_RE

    text = ("The method uses contrastive loss [1b9d9942-2f4b-4d4a-9b64-0d5c2c111111, Page 3, Para 2] "
            "and future work targets tables [1b9d9942-2f4b-4d4a-9b64-0d5c2c111111, Page 8, Para 0].")
    m = CITATION_RE.findall(text)
    assert len(m) == 2
    assert m[0] == ("1b9d9942-2f4b-4d4a-9b64-0d5c2c111111", "3", "2")


# ---- Two-stage discovery: merge/dedup + AI query-suggestion parsing (offline) -------------

def test_merge_candidates_dedup():
    from app.ingest import merge_candidates

    arxiv = [{"arxiv_id": "2401.00001v2", "title": "Paper A"},
             {"arxiv_id": "2401.00002v1", "title": "Paper B"}]
    s2 = [{"arxiv_id": "2401.00001", "title": "Paper A"},   # same paper, other version
          {"arxiv_id": None, "title": "  paper b "},        # dup only by title? no — B has id
          {"arxiv_id": None, "title": "Paper C"}]
    merged = merge_candidates(arxiv, s2)
    titles = [e["title"] for e in merged]
    assert titles == ["Paper A", "Paper B", "  paper b ", "Paper C"]


def test_parse_query_lines():
    from app.rerank import parse_query_lines

    text = """Here are some queries:
1. 3D imaging surgical guidance
- augmented reality navigation
* "mixed reality surgery survey"
too short
What about this one?"""
    qs = parse_query_lines(text)
    assert "3D imaging surgical guidance" in qs  # numbering stripped, digits kept
    assert "augmented reality navigation" in qs
    assert "mixed reality surgery survey" in qs
    assert all("?" not in q for q in qs)


# ---- Ideas wizard: tolerant JSON extraction (offline) -------------------------------------

def test_extract_json():
    import json

    from app.llm import extract_json

    assert extract_json('```json\n{"a": 1}\n```') == '{"a": 1}'
    lifted = extract_json('Sure! {"a": [1, 2], "b": "x[y] {z}"} hope that helps')
    assert json.loads(lifted) == {"a": [1, 2], "b": "x[y] {z}"}
    assert extract_json('preamble [{"t": "A"}, {"t": "B"}] trailing') == '[{"t": "A"}, {"t": "B"}]'
    assert extract_json("no json here") is None
    assert extract_json('{"unclosed": 1') is None


def test_resolve_grounding():
    from app.ideate import _resolve_grounding

    by_title = {"attention is all you need": "id-1", "bert": "id-2",
                "factually: wearable fact-checking": "id-3"}
    out = _resolve_grounding(["Attention Is All You Need", " BERT ", "Made Up Paper",
                              "attention is all you need", None, "Factually"], by_title)
    assert out == [{"paper_id": "id-1", "title": "Attention Is All You Need"},
                   {"paper_id": "id-2", "title": "BERT"},
                   {"paper_id": None, "title": "Made Up Paper"},  # unknown kept title-only
                   {"paper_id": "id-3", "title": "Factually"}]   # abbreviated → unique substring


def test_valid_ideas_filter():
    from app.ideate import _valid_ideas

    items = [{"title": "A", "research_question": "Q?"},
             {"title": "", "research_question": "Q?"},   # empty title
             {"title": "B"},                              # missing RQ
             "not a dict"]
    assert [i["title"] for i in _valid_ideas(items)] == ["A"]
    assert _valid_ideas({"title": "not a list"}) == []


# ---- Faz 1: live arXiv ingestion (needs network) ------------------------------------------

@pytest.mark.anyio
@pytest.mark.skipif(not _up("export.arxiv.org", 80), reason="no network")
async def test_arxiv_search_live():
    entries = await __import__("app.ingest", fromlist=["ingest"]).search_arxiv(
        "retrieval augmented generation", max_results=3)
    assert len(entries) >= 1
    e = entries[0]
    assert e["arxiv_id"] and e["title"] and e["authors"] and e["year"] >= 2000


@pytest.fixture
def anyio_backend():
    return "asyncio"
