"""Adım 2 — layout-aware parsing via GROBID → canonical sections + bbox for UI navigation."""

import xml.etree.ElementTree as ET

import httpx

from .config import settings

TEI = "{http://www.tei-c.org/ns/1.0}"

# head-text keyword → canonical section (doc: Bölüm Segmentasyonu)
SECTION_MAP = [
    ("future_work", ["future", "limitation", "conclusion", "outlook"]),
    ("methodology", ["method", "approach", "architecture", "model", "proposed", "implementation", "setup", "design"]),
    ("results", ["result", "discussion", "evaluation", "experiment", "analysis", "finding", "ablation"]),
    ("introduction", ["introduction", "background", "related work", "preliminar", "motivation"]),
]


def classify_section(head: str) -> str:
    h = head.lower()
    for name, keys in SECTION_MAP:
        if any(k in h for k in keys):
            return name
    return "other"


async def grobid_parse(pdf: bytes) -> str:
    """PDF → TEI XML with paragraph/sentence coordinates."""
    async with httpx.AsyncClient(timeout=300) as http:
        r = await http.post(
            f"{settings.grobid_url}/api/processFulltextDocument",
            files={"input": ("paper.pdf", pdf, "application/pdf")},
            data={"teiCoordinates": ["p", "head"], "segmentSentences": "0"},
        )
        r.raise_for_status()
    return r.text


def _coords_to_bbox(coords: str | None) -> tuple[int | None, dict | None]:
    """GROBID coords="page,x,y,w,h;..." → (page, first bbox)."""
    if not coords:
        return None, None
    try:
        page, x, y, w, h = coords.split(";")[0].split(",")[:5]
        return int(page), {"page": int(page), "x": float(x), "y": float(y), "w": float(w), "h": float(h)}
    except ValueError:
        return None, None


def extract_sections(tei_xml: str) -> list[dict]:
    """TEI → paragraph-level records: {section, title, text, page, bbox, paragraph}."""
    root = ET.fromstring(tei_xml)
    records: list[dict] = []

    abstract = root.find(f".//{TEI}profileDesc/{TEI}abstract")
    if abstract is not None:
        for i, p in enumerate(abstract.iter(f"{TEI}p")):
            text = " ".join("".join(p.itertext()).split())
            if text:
                records.append(
                    {"section": "abstract", "title": "Abstract", "text": text,
                     "page": None, "bbox": None, "paragraph": i}
                )

    body = root.find(f".//{TEI}body")
    if body is None:
        return records

    counters: dict[str, int] = {}
    for div in body.iterfind(f"{TEI}div"):
        head = div.find(f"{TEI}head")
        title = " ".join("".join(head.itertext()).split()) if head is not None else ""
        section = classify_section(title)
        for p in div.iterfind(f"{TEI}p"):
            text = " ".join("".join(p.itertext()).split())
            if len(text) < 40:  # skip stubs/captions leaked into divs
                continue
            page, bbox = _coords_to_bbox(p.get("coords"))
            idx = counters.get(section, 0)
            counters[section] = idx + 1
            records.append(
                {"section": section, "title": title or section, "text": text,
                 "page": page, "bbox": bbox, "paragraph": idx}
            )
    return records
