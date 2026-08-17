"""在线反馈回流（FR-40，P2 W2）：用户点踩样本 → 评估用例。

闭环：用户点踩（front-end down）→ messages.feedback='down' → 本模块收集
（问题 + 回答 + 引用）→ LLM 分类意图、生成参考答案 → GoldenCase 追加到
docs/feedback_cases.json → 回归门禁的生成评估自动包含（golden_set.all_cases）。

点踩样本没有"期望命中文档"（检索结果错误正是点踩的原因之一），
因此只并入生成评估，不进入检索评估（避免污染 recall/MRR）。

用法：uv run python -m app.eval.feedback_collect [--limit 20] [--since-days 30]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta

logger = logging.getLogger(__name__)

INTENT_PROMPT = (
    "判断下面用户问题的意图，只输出 JSON（不要输出其他内容）："
    '{{"intent": "qa"|"summary"|"chat"|"clarify"}}\n'
    "- qa：向企业知识库查询事实/信息\n"
    "- summary：要求总结某个文档或主题\n"
    "- chat：闲聊、时事、个人问题等与知识库无关\n"
    "- clarify：问题含糊，需要追问\n\n"
    "问题：{question}"
)

REFERENCE_PROMPT = (
    "为下面的问题写一句简洁、准确的参考答案（面向企业知识库问答，不超过 60 字），"
    "只输出答案文本本身，不要任何解释。\n\n问题：{question}"
)


async def _classify_intent(question: str) -> str:
    """LLM 判断意图（依赖注入点：collect_feedback_cases 可替换）。"""
    from app.agent.nodes import _complete_json  # noqa: PLC2701 复用 JSON 解析
    from app.llm.gateway import get_llm

    data = await _complete_json(get_llm(), INTENT_PROMPT.format(question=question))
    intent = (data or {}).get("intent", "qa")
    return intent if intent in ("qa", "summary", "chat", "clarify") else "qa"


async def _generate_reference(question: str) -> str:
    from app.agent.nodes import _complete_text  # noqa: PLC2701 复用文本解析
    from app.llm.gateway import get_llm

    return await _complete_text(get_llm(), REFERENCE_PROMPT.format(question=question))


async def _fetch_down_messages(limit: int, since_days: int | None) -> list[dict]:
    """查询点踩的 assistant 消息；问题 = 同会话最近一条更早的 user 消息。"""
    from sqlalchemy import select

    from app.db import session_factory  # 函数内 import：调用时解析，测试可覆盖
    from app.models import ChatMessage

    items: list[dict] = []
    async with session_factory() as session:
        stmt = (
            select(ChatMessage)
            .where(ChatMessage.role == "assistant", ChatMessage.feedback == "down")
            .order_by(ChatMessage.created_at.desc())
        )
        if since_days:
            cutoff = datetime.now(UTC) - timedelta(days=since_days)
            stmt = stmt.where(ChatMessage.created_at >= cutoff)
        down_msgs = (await session.execute(stmt.limit(limit * 4))).scalars().all()

        for msg in down_msgs:
            prev = await session.execute(
                select(ChatMessage)
                .where(
                    ChatMessage.session_id == msg.session_id,
                    ChatMessage.role == "user",
                    ChatMessage.created_at < msg.created_at,
                )
                .order_by(ChatMessage.created_at.desc())
                .limit(1)
            )
            prev_msg = prev.scalar_one_or_none()
            if prev_msg is None:
                continue  # 孤儿消息（会话首条即点踩）：无法还原问题，跳过
            items.append(
                {
                    "question": prev_msg.content,
                    "message_id": msg.id,
                    "created_at": msg.created_at.isoformat() if msg.created_at else "",
                    "citations": _parse_refs(msg.refs_json),
                }
            )
            if len(items) >= limit:
                break
    return items


def _parse_refs(refs_json: str) -> list[dict]:
    try:
        refs = json.loads(refs_json)
        return refs if isinstance(refs, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


async def collect_feedback_cases(
    limit: int = 20,
    since_days: int | None = None,
    classify=None,  # noqa: ANN001  (question) -> intent，测试注入
    make_reference=None,  # noqa: ANN001  (question) -> reference，测试注入
) -> list[dict]:
    """收集点踩样本并沉淀为可序列化的评估用例（写入前不做文件 IO）。"""
    from app.eval import golden_set

    classify = classify or _classify_intent
    make_reference = make_reference or _generate_reference

    existing = {c.question for c in golden_set.GOLDEN_SET}
    existing |= {c.question for c in golden_set.load_feedback_cases()}

    items = await _fetch_down_messages(limit, since_days)
    new_cases: list[dict] = []
    for item in items:
        question = item["question"].strip()
        if not question or question in existing:
            continue
        intent = await classify(question)
        if intent not in ("qa", "summary"):
            logger.info("跳过非知识类点踩：%s（intent=%s）", question[:20], intent)
            continue
        reference = await make_reference(question)
        new_cases.append(
            {
                "question": question,
                "doc_id": "",
                "section_prefix": "",
                "intent": intent,
                "reference": reference,
                "source": f"feedback:{item['message_id']}",
            }
        )
        existing.add(question)
    return new_cases


def append_feedback_cases(cases: list[dict]) -> int:
    """把新用例追加进 docs/feedback_cases.json；返回文件内总条数。"""
    from app.eval import golden_set

    if not cases:
        return len(golden_set.load_feedback_cases())
    path = golden_set.FEEDBACK_CASES_PATH
    data = [
        {
            "question": c.question,
            "doc_id": c.doc_id,
            "section_prefix": c.section_prefix,
            "intent": c.intent,
            "reference": c.reference,
            "source": c.source,
        }
        for c in golden_set.load_feedback_cases()
    ] + cases
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return len(data)


def main() -> int:
    parser = argparse.ArgumentParser(description="反馈回流：点踩样本 → 评估用例")
    parser.add_argument("--limit", type=int, default=20, help="最多收集多少条新样本")
    parser.add_argument("--since-days", type=int, default=None, help="只看最近 N 天的点踩")
    parser.add_argument("--dry-run", action="store_true", help="只打印不写入")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)

    new_cases = asyncio.run(collect_feedback_cases(limit=args.limit, since_days=args.since_days))
    if not new_cases:
        print("无新点踩样本（或含问法的全部已沉淀）")
        return 0
    for case in new_cases:
        print(f"  [{case['intent']}] {case['question'][:40]}  ← 参考：{case['reference'][:30]}")
    if args.dry_run:
        print(f"（dry-run，不写入）共 {len(new_cases)} 条待沉淀")
        return 0
    total = append_feedback_cases(new_cases)
    print(f"已沉淀 {len(new_cases)} 条 → docs/feedback_cases.json（累计 {total} 条）")
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
