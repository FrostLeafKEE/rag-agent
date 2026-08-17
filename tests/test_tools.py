"""工具调用测试（FR-25，P2 W1）：只读 SQL 安全约束 / 工具执行 / plan_tools 节点 / 图集成。

数据库查询走 conftest 的 SQLite session_factory 覆盖（真实执行）。
图测试用 asyncio.run 独立事件循环，与 TestClient 模块级 loop 隔离。
"""

from __future__ import annotations

import asyncio

import pytest

from app.agent.tools import _safe_select, tool_registry
from app.config import Settings
from app.retrieval.base import RetrievedChunk


def _chunk(score: float = 0.9, content: str = "权限过滤在检索层执行") -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=hash(content) & 0xFFFF,
        doc_id="DOC",
        chunk_index=0,
        content=content,
        section_path="章节",
        department="研发部",
        score=score,
    )


async def _seed_documents() -> None:
    """插入两份文档元数据（幂等：先清空测试库的 documents 表）。"""
    from sqlalchemy import text

    from app.db import session_factory
    from app.models import Document

    async with session_factory() as session:
        await session.execute(text("DELETE FROM documents"))
        session.add(
            Document(
                doc_id="D1",
                title="入职手册",
                source_name="入职手册.pdf",
                department="人事部",
            )
        )
        session.add(
            Document(
                doc_id="D2",
                title="发布规范",
                source_name="发布规范.md",
                department="研发部",
            )
        )
        await session.commit()


# ---- 只读 SQL 安全约束 ----


def test_safe_select_rejects_non_select() -> None:
    with pytest.raises(ValueError, match="SELECT"):
        _safe_select("DELETE FROM documents", 50)


def test_safe_select_rejects_multi_statement() -> None:
    with pytest.raises(ValueError, match="多语句"):
        _safe_select("SELECT * FROM documents; DROP TABLE documents", 50)


def test_safe_select_rejects_unknown_table() -> None:
    with pytest.raises(ValueError, match="白名单"):
        _safe_select("SELECT * FROM users", 50)


def test_safe_select_rejects_empty() -> None:
    with pytest.raises(ValueError, match="空查询"):
        _safe_select("-- 只有注释\n  ", 50)


def test_safe_select_strips_comments_and_adds_limit() -> None:
    sql = _safe_select("SELECT COUNT(*) AS n -- 统计\nFROM documents", 50)
    assert "--" not in sql
    assert sql.upper().endswith("LIMIT 50")


def test_safe_select_keeps_existing_limit() -> None:
    sql = _safe_select("select id, title from documents limit 3", 50)
    assert sql.upper().endswith("LIMIT 3")


# ---- 工具执行（SQLite 真实查询）----


def test_query_documents_unknown_tool() -> None:
    result = asyncio.run(tool_registry.call("not_exist", {}))
    assert result["ok"] is False
    assert "未知工具" in result["error"]


def test_query_documents_real_query() -> None:
    asyncio.run(_seed_documents())
    result = asyncio.run(
        tool_registry.call(
            "query_documents",
            {"sql": "SELECT department AS dept, COUNT(*) AS n FROM documents GROUP BY department"},
        )
    )
    assert result["ok"] is True
    rows = {row["dept"]: row["n"] for row in result["result"]["rows"]}
    assert rows["人事部"] == 1
    assert rows["研发部"] == 1


def test_query_documents_sql_injection_blocked() -> None:
    asyncio.run(_seed_documents())
    result = asyncio.run(
        tool_registry.call(
            "query_documents",
            {"sql": "SELECT * FROM documents; SELECT password_hash FROM users"},
        )
    )
    assert result["ok"] is False
    assert "多语句" in result["error"]


# ---- plan_tools 节点 ----


class FakeLLM:
    """按提示词特征返回预设结果；stream 在图内不应被调用。"""

    def __init__(self, tool_json: str) -> None:
        self._tool_json = tool_json
        self.complete_calls: list[str] = []

    def complete(self, messages, **kwargs):  # noqa: ANN001, ANN002, ANN003
        content = messages[-1]["content"]
        self.complete_calls.append(content)
        if "判断用户问题的意图" in content:
            return '{"intent": "qa", "reason": "测试"}'
        if "是否需要调用工具" in content:
            return self._tool_json
        if "拆分成多个子问题" in content:
            return '{"decompose": false, "sub_questions": []}'
        if "改写成更适合" in content:
            return "统计文档数量"
        if "生成 3 个检索式" in content:
            return '["统计一", "统计二"]'
        return "{}"

    async def stream(self, messages, **kwargs):  # noqa: ANN001, ANN002, ANN003
        yield ""

    @property
    def stream_calls(self) -> bool:
        return False


def test_plan_tools_invokes_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    """LLM 选择工具 → 真实执行 → 结果进 tool_results 与 trace。"""
    from app.agent import nodes

    asyncio.run(_seed_documents())
    llm = FakeLLM(
        '{"tool": "query_documents", "args": {"sql": "SELECT COUNT(*) AS n FROM documents"}, "reason": "统计"}'
    )
    monkeypatch.setattr("app.agent.nodes.get_llm", lambda: llm)
    state = {
        "question": "知识库有多少文档",
        "intent": "qa",
        "top_k": 5,
        "trace": {},
    }
    result = asyncio.run(nodes.plan_tools(state))
    assert result["tool_results"][0]["ok"] is True
    assert result["tool_results"][0]["result"]["rows"][0]["n"] == 2
    assert result["trace"]["tools"][0]["tool"] == "query_documents"


def test_plan_tools_no_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.agent import nodes

    llm = FakeLLM('{"tool": null, "args": {}, "reason": "无需工具"}')
    monkeypatch.setattr("app.agent.nodes.get_llm", lambda: llm)
    result = asyncio.run(
        nodes.plan_tools({"question": "权限过滤怎么做", "intent": "qa", "trace": {}})
    )
    assert result["tool_results"] == []


def test_plan_tools_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.agent import nodes

    settings = Settings(agent_tools_enabled=False)
    monkeypatch.setattr("app.agent.nodes.get_settings", lambda: settings)
    llm = FakeLLM("")
    monkeypatch.setattr("app.agent.nodes.get_llm", lambda: llm)
    result = asyncio.run(
        nodes.plan_tools({"question": "知识库有多少文档", "intent": "qa", "trace": {}})
    )
    assert result["tool_results"] == []
    assert llm.complete_calls == []  # 未调 LLM 判断


# ---- 图集成：工具结果进入生成上下文 ----


def test_graph_integration_tool_context(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.agent.graph import agent_graph

    asyncio.run(_seed_documents())
    monkeypatch.setattr(
        "app.agent.nodes.get_llm",
        lambda: FakeLLM(
            '{"tool": "query_documents", "args": {"sql": "SELECT department AS dept, COUNT(*) AS n FROM documents GROUP BY department"}, "reason": "统计"}'
        ),
    )
    monkeypatch.setattr(
        "app.agent.nodes.run_search", lambda *a, **k: [_chunk(0.9, content="入职手册")]
    )
    result = asyncio.run(agent_graph.ainvoke({"question": "各部门有多少文档", "top_k": 5}))
    assert result["tool_results"][0]["ok"] is True
    system = result["messages"][0]["content"]
    assert "结构化数据" in system
    assert "人事部" in system  # 工具结果已注入生成上下文
