"""Adım 3 — parent-child chunking. Parents = whole paragraphs (wide context for the LLM),
children = ~200-token splits (precise retrieval). Child hit → serve its parent."""

import uuid

CHILD_TOKENS = 200

# ponytail: token ≈ words/0.75 — close enough for chunk sizing; add tiktoken if budgets get tight
def approx_tokens(text: str) -> int:
    return int(len(text.split()) / 0.75) or 1


def split_child_texts(text: str) -> list[str]:
    words = text.split()
    per_chunk = int(CHILD_TOKENS * 0.75)
    if len(words) <= per_chunk:
        return [text]
    step = int(per_chunk * 0.8)  # 20% overlap so no idea is cut mid-thought
    return [" ".join(words[i : i + per_chunk]) for i in range(0, len(words) - int(step * 0.25), step)]


def build_chunks(paper_id: str, records: list[dict]) -> tuple[list[dict], list[dict]]:
    """Paragraph records (from parse.extract_sections) → (parent_rows, child_rows)."""
    parents, children = [], []
    for rec in records:
        pid = str(uuid.uuid4())
        parents.append(
            {"id": pid, "paper_id": paper_id, "parent_id": None, "section": rec["section"],
             "text": rec["text"], "token_count": approx_tokens(rec["text"]),
             "page": rec["page"], "bbox": rec["bbox"], "paragraph": rec["paragraph"]}
        )
        for t in split_child_texts(rec["text"]):
            children.append(
                {"id": str(uuid.uuid4()), "paper_id": paper_id, "parent_id": pid,
                 "section": rec["section"], "text": t, "token_count": approx_tokens(t),
                 "page": rec["page"], "bbox": rec["bbox"], "paragraph": rec["paragraph"]}
            )
    return parents, children
