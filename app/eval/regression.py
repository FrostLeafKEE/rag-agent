"""回归门禁（FR-39）：检索 + 生成评估对比基线，指标劣化即退出码 1。

用法：
  uv run python -m app.eval.regression                 # 全量回归（RAGAS 较慢）
  uv run python -m app.eval.regression --limit 8       # 快速回归（前 N 条）
  uv run python -m app.eval.regression --write-baseline  # 更新基线
  uv run python -m app.eval.regression --no-ragas      # 只跑检索端

基线文件：docs/eval_baseline.json（首次用 --write-baseline 生成）。
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from app.agent.service import stream_answer
from app.eval.generation_eval import run_generation_eval
from app.eval.golden_set import chat_cases, qa_cases
from app.eval.retrieval_eval import run_eval as run_retrieval_eval

logger = logging.getLogger("regression")

BASELINE_PATH = Path("docs/eval_baseline.json")
# 各指标允许的最大劣化幅度（与基线相比）
TOLERANCE = {
    "recall_at_k": 0.05,
    "mrr": 0.05,
    "faithfulness": 0.05,
    "answer_relevancy": 0.05,
    "context_precision": 0.05,
}


def _load_baseline() -> dict:
    if not BASELINE_PATH.exists():
        return {}
    return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))


def _check_chat_refusal(verbose: bool = True) -> dict:
    """闲聊类用例：期望拒答（回答不得编造知识库外内容）。"""
    import asyncio

    passed = 0
    for case in chat_cases():

        async def collect(q: str = case.question) -> str:
            parts = []
            async for item in stream_answer(q, top_k=5):
                if item["type"] == "delta":
                    parts.append(item["text"])
            return "".join(parts)

        answer = asyncio.run(collect())
        # 拒答判定：包含知识库/无法/抱歉等拒答信号，且不含具体编造内容（长度限制）
        ok = any(k in answer for k in ("知识库", "无法", "抱歉", "只能")) and len(answer) < 200
        passed += 1 if ok else 0
        if verbose:
            print(f"  [{'✓' if ok else '✗'}] {case.question[:24]} → {answer[:50]}")
    total = len(chat_cases())
    return {"chat_refusal": round(passed / total, 3), "chat_total": total}


def main() -> int:
    parser = argparse.ArgumentParser(description="评估回归门禁")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--limit", type=int, default=None, help="生成评估只跑前 N 条")
    parser.add_argument("--no-ragas", action="store_true", help="跳过生成端评估")
    parser.add_argument("--write-baseline", action="store_true", help="更新基线文件")
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING)

    # 1. 检索端
    ret = run_retrieval_eval(top_k=args.top_k, cases=qa_cases())
    print(
        f"检索端：recall@{args.top_k} = {ret['recall_at_k']}  "
        f"MRR = {ret['mrr']}（{ret['cases']} 条）"
    )

    # 2. 生成端（可选）
    gen: dict = {}
    if not args.no_ragas:
        gen = run_generation_eval(cases=qa_cases(), top_k=args.top_k, limit=args.limit)
        print(
            f"生成端：faithfulness = {gen['faithfulness']}  "
            f"answer_relevancy = {gen['answer_relevancy']}  "
            f"context_precision = {gen['context_precision']}（{gen['cases']} 条）"
        )

    # 3. 闲聊拒答
    chat = _check_chat_refusal()
    print(f"闲聊拒答：{chat['chat_refusal']}（{chat['chat_total']} 条）")

    metrics = {
        "recall_at_k": ret["recall_at_k"],
        "mrr": ret["mrr"],
        **{
            k: gen[k]
            for k in ("faithfulness", "answer_relevancy", "context_precision")
            if k in gen
        },
        "chat_refusal": chat["chat_refusal"],
    }

    # 4. 基线对比
    baseline = _load_baseline()
    if args.write_baseline or not baseline:
        BASELINE_PATH.write_text(
            json.dumps({"metrics": metrics, "cases": ret["cases"]}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"\n基线已写入 {BASELINE_PATH}")
        return 0

    print("\n=== 基线对比 ===")
    failed = []
    for key, value in metrics.items():
        base = baseline["metrics"].get(key)
        if base is None:
            continue
        delta = value - base
        status = "✓" if delta >= -TOLERANCE.get(key, 0.05) else "✗ 劣化"
        print(f"  {key:18s} 当前 {value:.3f}  基线 {base:.3f}  Δ{delta:+.3f}  {status}")
        if delta < -TOLERANCE.get(key, 0.05):
            failed.append(key)
    if failed:
        print(f"\n❌ 回归失败：{failed} 指标劣化超过容差")
        return 1
    print("\n✅ 回归通过：所有指标在容差范围内")
    return 0


if __name__ == "__main__":
    sys.exit(main())
