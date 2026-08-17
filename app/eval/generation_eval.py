"""生成端评估：黄金集走生产链路（检索 + 生成），RAGAS 三指标评分（PRD FR-38）。

指标：
  - faithfulness：回答忠于检索上下文（防幻觉）
  - answer_relevancy：回答与问题相关
  - context_precision：检索上下文对回答的精确性

LLM judge 复用 .env 的 LLM 网关配置（OpenAI 兼容）。

用法：uv run python -m app.eval.generation_eval [--top-k 5]
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from typing import cast

from langchain_openai import ChatOpenAI
from langchain_openai import OpenAIEmbeddings as LangchainOpenAIEmbeddings
from pydantic import SecretStr
from ragas import EvaluationDataset, EvaluationResult, SingleTurnSample, evaluate
from ragas.embeddings import LangchainEmbeddingsWrapper
from ragas.llms import LangchainLLMWrapper
from ragas.metrics import answer_relevancy, context_precision, faithfulness

from app.agent.service import stream_answer
from app.config import get_settings
from app.eval.golden_set import GoldenCase, all_cases, qa_cases

logger = logging.getLogger("gen_eval")


async def collect_answer(question: str, top_k: int) -> tuple[str, list[str]]:
    """走生产链路（stream_answer）收集回答全文与检索上下文。"""
    answer_parts: list[str] = []
    contexts: list[str] = []
    async for item in stream_answer(question, top_k=top_k):
        if item["type"] == "delta":
            answer_parts.append(item["text"])
        elif item["type"] == "citations":
            contexts = [c["content"] for c in item["citations"]]
    return "".join(answer_parts), contexts


def build_judge():  # noqa: ANN202  ragas stub 类型不可用，返回 Any
    settings = get_settings()
    return LangchainLLMWrapper(
        ChatOpenAI(
            base_url=settings.llm_base_url,
            api_key=SecretStr(settings.llm_api_key),
            model=settings.llm_model,
            temperature=0,
            max_retries=2,
        )
    )


def build_embeddings():  # noqa: ANN202  ragas stub 类型不可用，返回 Any
    """RAGAS 内部需要 embedding（SiliconFlow BGE-M3，LangChain 包装提供 embed_query）。"""
    settings = get_settings()
    return LangchainEmbeddingsWrapper(
        LangchainOpenAIEmbeddings(
            model=settings.embedding_model,
            api_key=SecretStr(settings.embedding_api_key),
            base_url=settings.embedding_base_url,
        )
    )


def run_generation_eval(
    cases: list[GoldenCase] | None = None,
    top_k: int = 5,
    limit: int | None = None,
    verbose: bool = True,
    include_feedback: bool = True,
) -> dict:
    """生成端评估：返回三指标均值 + 逐条明细（供回归门禁调用）。

    include_feedback=True 时默认样本 = 黄金集非 chat 类 + 反馈回流样本（FR-40）。
    """
    base = all_cases() if include_feedback else qa_cases()
    cases = (cases or base)[:limit] if limit else (cases or base)
    samples = []
    for i, case in enumerate(cases, start=1):
        answer, contexts = asyncio.run(collect_answer(case.question, top_k=top_k))
        if verbose:
            print(
                f"[{i}/{len(cases)}] {case.question[:36]} → "
                f"{len(answer)} 字 / {len(contexts)} 条上下文"
            )
        samples.append(
            SingleTurnSample(
                user_input=case.question,
                response=answer,
                retrieved_contexts=contexts,
                reference=case.reference,
            )
        )

    print("开始 RAGAS 评分（faithfulness / answer_relevancy / context_precision）...")
    result = evaluate(  # type: ignore  # ragas stub 类型过宽（EvaluationDataset samples union）
        dataset=EvaluationDataset(samples=samples),  # type: ignore  # ragas stub samples 类型过宽
        metrics=[faithfulness, answer_relevancy, context_precision],
        llm=build_judge(),
        embeddings=build_embeddings(),
    )
    df = cast(EvaluationResult, result).to_pandas()
    return {
        "cases": len(cases),
        "faithfulness": round(float(df["faithfulness"].mean()), 3),
        "answer_relevancy": round(float(df["answer_relevancy"].mean()), 3),
        "context_precision": round(float(df["context_precision"].mean()), 3),
        "details": df.to_dict(orient="records"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="生成端评估（RAGAS）")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--limit", type=int, default=None, help="只评估前 N 条（快速回归）")
    parser.add_argument(
        "--exclude-feedback",
        action="store_true",
        help="只评估黄金集，不含反馈回流样本（FR-40）",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING)

    result = run_generation_eval(
        top_k=args.top_k, limit=args.limit, include_feedback=not args.exclude_feedback
    )
    print("\n=== 汇总 ===")
    print(
        f"faithfulness={result['faithfulness']}  answer_relevancy={result['answer_relevancy']}  "
        f"context_precision={result['context_precision']}（{result['cases']} 条）"
    )
    print("\n=== 逐条得分 ===")
    for d in result["details"]:
        q = d.get("user_input", "")[:28]
        print(
            f"  rel={d.get('answer_relevancy', 0):.2f}  faith={d.get('faithfulness', 0):.2f}  "
            f"prec={d.get('context_precision', 0):.2f}  {q}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
