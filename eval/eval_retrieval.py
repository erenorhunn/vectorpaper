"""Retrieval quality on the golden set (doc §9): hit@k and MRR.
Run after ingesting papers:  .venv/bin/python eval/eval_retrieval.py
"""

import asyncio
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from app import vectors  # noqa: E402

K = 5


async def main() -> int:
    cases = json.loads((pathlib.Path(__file__).parent / "golden.json").read_text())["cases"]
    hits, rr = 0, 0.0
    for case in cases:
        results = await vectors.search(case["query"], limit=K)
        rank = next((i + 1 for i, r in enumerate(results)
                     if case["expect_substring"].lower() in r["text"].lower()), None)
        if rank:
            hits += 1
            rr += 1 / rank
        print(f"{'HIT' if rank else 'MISS':4} rank={rank or '-':<3} {case['query']!r}")
    n = len(cases)
    print(f"\nhit@{K} = {hits}/{n} = {hits/n:.2f}   MRR = {rr/n:.2f}")
    return 0 if hits else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
