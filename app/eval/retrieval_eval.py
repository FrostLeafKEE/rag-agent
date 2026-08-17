"""检索评估入口：对黄金集跑一次检索，输出 recall@k 与 MRR。

用法：uv run python -m app.eval.retrieval_eval [--top-k 5] [--no-rerank]
"""

from __future__ import annotations

import argparse
import logging
import sys

from app.eval.golden_set import GoldenCase, hit_at_k, qa_cases
from app.retrieval.search import search

logger = logging.getLogger("eval")


def run_eval(
    top_k: int = 5, use_rerank: bool = True, cases: list[GoldenCase] | None = None
) -> dict:
    cases = cases or qa_cases()
    hits = []
    for case in cases:
        results = search(case.question, top_k=top_k, use_rerank=use_rerank)
        pos = hit_at_k(case, [(r.doc_id, r.section_path) for r in results])
        hits.append((case, pos))

    hit_cases = [h for h in hits if h[1] is not None]
    recall_at_k = len(hit_cases) / len(cases)
    mrr = sum(1.0 / (pos + 1) for _, pos in hit_cases if pos is not None) / len(cases)
    return {
        "cases": len(cases),
        "top_k": top_k,
        "recall_at_k": round(recall_at_k, 3),
        "mrr": round(mrr, 3),
        "details": [
            {
                "question": case.question,
                "hit": pos is not None,
                "first_hit_rank": (pos + 1) if pos is not None else None,
            }
            for case, pos in hits
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="检索评估")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--no-rerank", action="store_true", help="跳过精排对比")
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING)

    result = run_eval(args.top_k, use_rerank=not args.no_rerank)
    print(f"黄金集 {result['cases']} 条 / top-{result['top_k']}")
    print(f"recall@{result['top_k']} = {result['recall_at_k']}   MRR = {result['mrr']}")
    for d in result["details"]:
        rank = d["first_hit_rank"] or "-"
        print(f"  [{'✓' if d['hit'] else '✗'}] rank={rank}  {d['question'][:40]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
