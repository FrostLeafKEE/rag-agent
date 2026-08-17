"""反馈回流测试（FR-40，P2 W2）：点踩样本 → 评估用例 闭环。

- 数据库：conftest SQLite 覆盖 app.db.session_factory
- 反馈用例文件：monkeypatch golden_set.FEEDBACK_CASES_PATH 指向 tmp_path
  （feedback_collect 动态读取 golden_set 模块属性，patch 两端均生效）
"""

from __future__ import annotations

import asyncio
import json

import pytest

from app.eval import golden_set
from app.eval.feedback_collect import append_feedback_cases, collect_feedback_cases
from app.eval.golden_set import GoldenCase


async def _seed_feedback() -> None:
    """会话：一条点踩问答 + 一条点赞问答 + 一条孤儿点踩（无前置问题）。

    消息时间戳递增错开（< 查询依赖先后），seed 前清空会话表（幂等）。
    """
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import text

    from app.db import session_factory
    from app.models import ChatMessage, ChatSession

    async with session_factory() as session:
        await session.execute(text("DELETE FROM messages"))
        await session.execute(text("DELETE FROM sessions"))
        sess = ChatSession(user_id=1, title="测试会话")
        session.add(sess)
        await session.flush()
        base = datetime.now(UTC)
        session.add_all(
            [
                # 孤儿：会话首条即点踩（前面无任何消息，无法还原问题）
                ChatMessage(
                    session_id=sess.id,
                    role="assistant",
                    content="孤儿回答",
                    feedback="down",
                    created_at=base - timedelta(seconds=10),
                ),
                ChatMessage(session_id=sess.id, role="user", content="分块策略是什么", created_at=base - timedelta(seconds=5)),
                ChatMessage(
                    session_id=sess.id,
                    role="assistant",
                    content="回答跑偏了",
                    refs_json=json.dumps([{"doc_id": "PRD", "section_path": "x"}]),
                    feedback="down",
                    created_at=base - timedelta(seconds=4),
                ),
                ChatMessage(session_id=sess.id, role="user", content="好回答", created_at=base - timedelta(seconds=2)),
                ChatMessage(
                    session_id=sess.id,
                    role="assistant",
                    content="正确的回答",
                    feedback="up",
                    created_at=base - timedelta(seconds=1),
                ),
            ]
        )
        await session.commit()


class FakeCollector:
    """collect 用的依赖注入：意图分类 + 参考答案生成。"""

    def __init__(self, intent: str = "qa", reference: str = "参考答案") -> None:
        self._intent = intent
        self._reference = reference

    async def classify(self, question: str) -> str:
        return self._intent

    async def make_reference(self, question: str) -> str:
        return self._reference


def test_collect_pairs_question_with_down_message() -> None:
    asyncio.run(_seed_feedback())
    fake = FakeCollector()
    cases = asyncio.run(
        collect_feedback_cases(
            limit=10, classify=fake.classify, make_reference=fake.make_reference
        )
    )
    # 只有 1 条有效点踩（up 不算、孤儿跳过）
    assert len(cases) == 1
    case = cases[0]
    assert case["question"] == "分块策略是什么"
    assert case["intent"] == "qa"
    assert case["reference"] == "参考答案"
    assert case["source"].startswith("feedback:")
    assert case["doc_id"] == ""


def test_collect_skips_non_knowledge_intent() -> None:
    asyncio.run(_seed_feedback())
    fake = FakeCollector(intent="chat")
    cases = asyncio.run(
        collect_feedback_cases(
            limit=10, classify=fake.classify, make_reference=fake.make_reference
        )
    )
    assert cases == []


def test_collect_deduplicates_with_golden_set() -> None:
    """黄金集已存在的问题不再重复沉淀。"""
    from app.eval.golden_set import GOLDEN_SET

    first = GOLDEN_SET[0]
    async def seed_one() -> None:
        from datetime import UTC, datetime

        from app.db import session_factory
        from app.models import ChatMessage, ChatSession

        async with session_factory() as session:
            sess = ChatSession(user_id=1, title="去重")
            session.add(sess)
            await session.flush()
            now = datetime.now(UTC)
            session.add_all(
                [
                    ChatMessage(session_id=sess.id, role="user", content=first.question, created_at=now),
                    ChatMessage(
                        session_id=sess.id, role="assistant", content="差", feedback="down", created_at=now
                    ),
                ]
            )
            await session.commit()

    asyncio.run(seed_one())
    fake = FakeCollector()
    cases = asyncio.run(
        collect_feedback_cases(
            limit=10, classify=fake.classify, make_reference=fake.make_reference
        )
    )
    assert all(c["question"] != first.question for c in cases)


def test_feedback_cases_file_roundtrip(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """append → load_feedback_cases → all_cases 全链路（tmp 文件）。"""
    tmp = tmp_path / "feedback_cases.json"
    monkeypatch.setattr(golden_set, "FEEDBACK_CASES_PATH", tmp)

    cases = [
        {
            "question": "部门文档数量", "doc_id": "", "section_prefix": "",
            "intent": "qa", "reference": "人事部 3 篇", "source": "feedback:1",
        }
    ]
    assert append_feedback_cases(cases) == 1

    loaded = golden_set.load_feedback_cases()
    assert len(loaded) == 1
    assert loaded[0].question == "部门文档数量"
    assert loaded[0].intent == "qa"
    assert loaded[0].source == "feedback:1"

    # 追加去重：同 question 再次 append 仍会保留旧条目文件级不查重，
    # 但 all_cases 能装载，且 collect 侧查重（见去重用例）
    assert append_feedback_cases(cases) == 2

    all_c = golden_set.all_cases()
    feedback_only = [c for c in all_c if c.source.startswith("feedback")]
    assert len(feedback_only) == 2


def test_feedback_cases_missing_file(monkeypatch: pytest.MonkeyPatch) -> None:
    from pathlib import Path

    monkeypatch.setattr(golden_set, "FEEDBACK_CASES_PATH", Path("no/such/dir/cases.json"))
    assert golden_set.load_feedback_cases() == []


def test_golden_case_source_field_backward_compatible() -> None:
    """未传 source 的既有用例构造不受影响。"""
    case = GoldenCase("问题", "DOC", "章节")
    assert case.source == ""