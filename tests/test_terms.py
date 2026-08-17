"""术语表/同义词测试（P2 W5）：别名替换逻辑 + 图内变体注入。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from app.config import Settings
from app.retrieval.base import RetrievedChunk
from app.retrieval.terms import expand_terms, load_terms


def _write_terms(path: Path, mapping: dict) -> Path:
    path.write_text(json.dumps(mapping, ensure_ascii=False), encoding="utf-8")
    return path


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


# ---- 别名替换逻辑 ----


def test_expand_basic(tmp_path: Path) -> None:
    cfg = _write_terms(tmp_path / "t.json", {"发版": "发布", "离职": "注销"})
    assert expand_terms("系统今天发版了吗", str(cfg)) == ["系统今天发布了吗"]


def test_expand_multiple_aliases_all_replaced(tmp_path: Path) -> None:
    cfg = _write_terms(tmp_path / "t.json", {"发版": "发布", "上线": "发布"})
    assert expand_terms("明天发版并上线", str(cfg)) == ["明天发布并发布"]


def test_expand_longest_match_first(tmp_path: Path) -> None:
    """长别名优先替换，避免短别名破坏长词。"""
    cfg = _write_terms(tmp_path / "t.json", {"注销": "账号注销", "账号注销": "账号注销"})
    assert expand_terms("用户注销流程", str(cfg)) == ["用户账号注销流程"]


def test_expand_no_hit(tmp_path: Path) -> None:
    cfg = _write_terms(tmp_path / "t.json", {"发版": "发布"})
    assert expand_terms("权限过滤怎么做", str(cfg)) == []


def test_expand_noop_when_same(tmp_path: Path) -> None:
    """别名替换后文本无变化（已用标准词）→ 不生成无用变体。"""
    cfg = _write_terms(tmp_path / "t.json", {"发布": "发布"})
    assert expand_terms("系统已发布", str(cfg)) == []


def test_load_terms_missing_file(tmp_path: Path) -> None:
    assert load_terms(str(tmp_path / "none.json")) == {}
    assert expand_terms("发版", str(tmp_path / "none.json")) == []


def test_load_terms_bad_json(tmp_path: Path) -> None:
    path = tmp_path / "t.json"
    path.write_text("{bad", encoding="utf-8")
    assert load_terms(str(path)) == {}


# ---- 图内变体注入 ----


def test_term_expand_node_injects_variant(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from app.agent import nodes

    cfg = _write_terms(tmp_path / "t.json", {"发版": "发布"})
    monkeypatch.setattr(nodes, "get_settings", lambda: Settings(term_config=str(cfg)))
    state = {"question": "系统发版频率", "queries": ["系统发版频率"], "trace": {}}
    result = asyncio.run(nodes.term_expand(state))
    assert result["queries"] == ["系统发版频率", "系统发布频率"]
    assert result["trace"]["term_variants"] == ["系统发布频率"]


def test_term_expand_disabled(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from app.agent import nodes

    cfg = _write_terms(tmp_path / "t.json", {"发版": "发布"})
    monkeypatch.setattr(
        nodes,
        "get_settings",
        lambda: Settings(term_config=str(cfg), term_expand_enabled=False),
    )
    result = asyncio.run(nodes.term_expand({"question": "发版", "queries": ["发版"], "trace": {}}))
    assert result["queries"] == ["发版"]


def test_term_expand_no_hit_passthrough(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from app.agent import nodes

    cfg = _write_terms(tmp_path / "t.json", {"发版": "发布"})
    monkeypatch.setattr(nodes, "get_settings", lambda: Settings(term_config=str(cfg)))
    result = asyncio.run(
        nodes.term_expand({"question": "权限过滤", "queries": ["权限过滤"], "trace": {}})
    )
    assert result["queries"] == ["权限过滤"]


class FakeLLM:
    def complete(self, messages, **kwargs):  # noqa: ANN001, ANN002, ANN003
        content = messages[-1]["content"]
        if "判断用户问题的意图" in content:
            return '{"intent": "qa", "reason": "测试"}'
        if "拆分成多个子问题" in content:
            return '{"decompose": false, "sub_questions": []}'
        if "改写成更适合" in content:
            return "系统发版频率"
        if "生成 3 个检索式" in content:
            return '["发版频率一", "发版频率二"]'
        return "{}"

    async def stream(self, messages, **kwargs):  # noqa: ANN001, ANN002, ANN003
        yield ""


def test_graph_term_variant_reaches_retrieval(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """全图：多查询扩展后，术语变体确实作为检索查询之一被使用。"""
    from app.agent.graph import agent_graph

    cfg = _write_terms(tmp_path / "t.json", {"发版": "发布"})
    monkeypatch.setattr("app.agent.nodes.get_llm", lambda: FakeLLM())
    monkeypatch.setattr("app.agent.nodes.get_settings", lambda: Settings(term_config=str(cfg)))
    seen: list[str] = []

    def fake_search(query, **kwargs):  # noqa: ANN001, ANN002
        seen.append(query)
        return [_chunk(0.9, content=f"结果：{query}")]

    monkeypatch.setattr("app.agent.nodes.run_search", fake_search)
    result = asyncio.run(agent_graph.ainvoke({"question": "系统发版频率", "top_k": 5}))
    assert result["trace"]["term_variants"] == ["系统发布频率"]
    assert "系统发布频率" in seen  # 变体确实进入了检索
    assert "系统发版频率" in seen  # 原查询保留
