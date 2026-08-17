"""Agent 图测试：意图路由 / CRAG 反思 / 重写降级（mock LLM 与检索）。

注意：用 asyncio.run 独立事件循环执行，避免与 TestClient 模块级 loop 交互冲突。
"""

import asyncio

import pytest

from app.agent.graph import agent_graph
from app.retrieval.base import RetrievedChunk


def _chunk(score: float, doc_id: str = "DOC", content: str = "内容") -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=hash(doc_id) & 0xFFFF,
        doc_id=doc_id,
        chunk_index=0,
        content=content,
        section_path="章节",
        department="研发部",
        score=score,
    )


class FakeLLM:
    """complete 按提示词类型返回预设结果；stream 不应被图调用（生成在 service 层）。"""

    def __init__(self, intent: str = "qa", rewritten: str | None = None) -> None:
        self._intent = intent
        self._rewritten = rewritten
        self.complete_calls: list[str] = []

    def complete(self, messages, **kwargs):  # noqa: ANN001, ANN002, ANN003
        content = messages[-1]["content"]
        self.complete_calls.append(content)
        if "判断用户问题的意图" in content:
            return f'{{"intent": "{self._intent}", "reason": "测试"}}'
        if "拆分成多个子问题" in content:
            return '{"decompose": false, "sub_questions": []}'
        if "改写成更适合" in content:
            return self._rewritten or "改写后的查询"
        if "生成 3 个检索式" in content:
            return '["变体一", "变体二"]'
        if "改写查询以便重新检索" in content:
            return self._rewritten or "改进后的查询"
        return "{}"

    async def stream(self, messages, **kwargs):  # noqa: ANN001, ANN002, ANN003
        yield "不应走到生成"


def test_chat_intent_returns_refusal(monkeypatch: pytest.MonkeyPatch) -> None:
    llm = FakeLLM(intent="chat")
    monkeypatch.setattr("app.agent.nodes.get_llm", lambda: llm)
    result = asyncio.run(agent_graph.ainvoke({"question": "今天天气如何", "top_k": 5}))
    assert result["intent"] == "chat"
    assert "知识库" in result["refusal"]
    assert result["chunks"] == []


def test_qa_intent_high_score_assemble(monkeypatch: pytest.MonkeyPatch) -> None:
    llm = FakeLLM(intent="qa")
    monkeypatch.setattr("app.agent.nodes.get_llm", lambda: llm)
    monkeypatch.setattr(
        "app.agent.nodes.run_search", lambda *a, **k: [_chunk(0.9, content="权限过滤在检索层执行")]
    )
    result = asyncio.run(agent_graph.ainvoke({"question": "权限过滤怎么做", "top_k": 5}))
    assert result["intent"] == "qa"
    assert result["rewrite_count"] == 0
    assert len(result["messages"]) == 2  # system + user
    assert "权限过滤在检索层执行" in result["messages"][0]["content"]


def test_low_score_triggers_rewrite(monkeypatch: pytest.MonkeyPatch) -> None:
    """低置信 → 重写查询 → 重检索高分 → 正常生成。"""
    llm = FakeLLM(intent="qa", rewritten="权限过滤 filter 下推")
    monkeypatch.setattr("app.agent.nodes.get_llm", lambda: llm)
    call = {"n": 0}

    def fake_search(query, **kwargs):  # noqa: ANN001, ANN002, ANN003
        call["n"] += 1
        # 多查询扩展产生多次检索；前几轮低分，重写后高分
        if call["n"] <= 3:
            return [_chunk(0.1, content=f"低分:{query}")]
        return [_chunk(0.85, content=f"结果:{query}")]

    monkeypatch.setattr("app.agent.nodes.run_search", fake_search)
    result = asyncio.run(agent_graph.ainvoke({"question": "权限过滤怎么做", "top_k": 5}))
    assert result["rewrite_count"] == 1
    assert result["chunks"][0].score == 0.85
    assert len(llm.complete_calls) >= 3  # 路由 + 主动改写 + 多查询


def test_rewrite_exhausts_then_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    """重写轮数超限后降级使用现有结果，不无限循环。"""
    llm = FakeLLM(intent="qa", rewritten="仍不相关")
    monkeypatch.setattr("app.agent.nodes.get_llm", lambda: llm)
    monkeypatch.setattr(
        "app.agent.nodes.run_search", lambda *a, **k: [_chunk(0.05, content="低分内容")]
    )
    result = asyncio.run(agent_graph.ainvoke({"question": "问题", "top_k": 5}))
    assert result["rewrite_count"] == 2  # 达到上限
    assert result["rewrite"] is False  # 降级
    assert result["chunks"][0].score == 0.05  # 用现有结果
    assert len(result["messages"]) == 2


def test_no_results_triggers_rewrite(monkeypatch: pytest.MonkeyPatch) -> None:
    llm = FakeLLM(intent="qa", rewritten="改写查询")
    monkeypatch.setattr("app.agent.nodes.get_llm", lambda: llm)
    call = {"n": 0}

    def fake_search(query, **kwargs):  # noqa: ANN001, ANN002, ANN003
        call["n"] += 1
        if call["n"] <= 3:  # 多查询首次检索全空
            return []
        return [_chunk(0.8)]

    monkeypatch.setattr("app.agent.nodes.run_search", fake_search)
    result = asyncio.run(agent_graph.ainvoke({"question": "冷门问题", "top_k": 5}))
    assert result["rewrite_count"] == 1
    assert len(result["chunks"]) == 1


def test_route_parse_failure_defaults_to_qa(monkeypatch: pytest.MonkeyPatch) -> None:
    class BrokenLLM(FakeLLM):
        def complete(self, messages, **kwargs):  # noqa: ANN001, ANN002, ANN003
            return "不是 JSON"

    monkeypatch.setattr("app.agent.nodes.get_llm", lambda: BrokenLLM())
    monkeypatch.setattr("app.agent.nodes.run_search", lambda *a, **k: [_chunk(0.9)])
    result = asyncio.run(agent_graph.ainvoke({"question": "权限过滤", "top_k": 5}))
    assert result["intent"] == "qa"  # 解析失败兜底
    assert len(result["messages"]) == 2


class DecomposeLLM(FakeLLM):
    """decompose 返回需要拆分的子问题。"""

    def complete(self, messages, **kwargs):  # noqa: ANN001, ANN002, ANN003
        content = messages[-1]["content"]
        self.complete_calls.append(content)
        if "判断用户问题的意图" in content:
            return '{"intent": "qa", "reason": "测试"}'
        if "拆分成多个子问题" in content:
            return '{"decompose": true, "sub_questions": ["A 的部署要求", "B 的兼容性"]}'
        return "{}"


def test_multi_hop_decomposes_and_merges(monkeypatch: pytest.MonkeyPatch) -> None:
    """多跳：对比类问题拆成子问题，分别检索后合并去重。"""
    llm = DecomposeLLM()
    monkeypatch.setattr("app.agent.nodes.get_llm", lambda: llm)
    queries_seen: list[str] = []

    def fake_search(query, **kwargs):  # noqa: ANN001, ANN002, ANN003
        queries_seen.append(query)
        # 不同子问题返回不同 chunk_id，避免被去重合并
        return [_chunk(0.9, doc_id=f"DOC-{len(queries_seen)}", content=f"内容:{query}")]

    monkeypatch.setattr("app.agent.nodes.run_search", fake_search)
    result = asyncio.run(agent_graph.ainvoke({"question": "A 与 B 的兼容性如何", "top_k": 5}))
    assert result["sub_questions"] == ["A 的部署要求", "B 的兼容性"]
    assert queries_seen == ["A 的部署要求", "B 的兼容性"]  # 子问题直接检索，跳过改写/多查询
    assert len(result["chunks"]) == 2


def test_multi_query_expands_variants(monkeypatch: pytest.MonkeyPatch) -> None:
    """多查询：基于改写后的查询生成变体，检索按变体合并。"""
    llm = FakeLLM(intent="qa", rewritten="权限过滤下推")
    monkeypatch.setattr("app.agent.nodes.get_llm", lambda: llm)
    queries_seen: list[str] = []

    def fake_search(query, **kwargs):  # noqa: ANN001, ANN002, ANN003
        queries_seen.append(query)
        return [_chunk(0.9)]

    monkeypatch.setattr("app.agent.nodes.run_search", fake_search)
    result = asyncio.run(agent_graph.ainvoke({"question": "权限过滤怎么做", "top_k": 5}))
    # 改写后查询 + 2 个变体 → 3 次检索
    assert queries_seen[0] == "权限过滤下推"
    assert len(queries_seen) == 3
    assert result["trace"]["multi_queries"] == ["变体一", "变体二"]


def test_json_format_adds_instruction(monkeypatch: pytest.MonkeyPatch) -> None:
    """结构化输出：format=json 时系统提示词追加 JSON 指令。"""
    llm = FakeLLM(intent="qa")
    monkeypatch.setattr("app.agent.nodes.get_llm", lambda: llm)
    monkeypatch.setattr("app.agent.nodes.run_search", lambda *a, **k: [_chunk(0.9)])
    result = asyncio.run(
        agent_graph.ainvoke({"question": "列出部署要求", "top_k": 5, "format": "json"})
    )
    assert "合法 JSON" in result["messages"][0]["content"]

    result2 = asyncio.run(agent_graph.ainvoke({"question": "列出部署要求", "top_k": 5}))
    assert "合法 JSON" not in result2["messages"][0]["content"]
