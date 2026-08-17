"""问答 API 测试：mock 检索与 LLM 流，验证 SSE 事件序列与引用溯源。"""

import json

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_current_user
from app.main import app
from app.models import User
from app.retrieval.base import RetrievedChunk

_client = TestClient(app)


@pytest.fixture(autouse=True)
def override_auth() -> None:
    """绕过真实 JWT 校验：QA 认证逻辑由 test_auth.py 覆盖。"""
    test_user = User(id=1, username="tester", role="user", department="研发部", is_active=True)

    def fake_user():
        return test_user

    app.dependency_overrides[get_current_user] = fake_user
    yield
    app.dependency_overrides.pop(get_current_user, None)


class FakeStreamLLM:
    """模拟 LLM：complete 用于意图路由，stream 用于回答生成。"""

    def complete(self, messages, **kwargs):  # noqa: ANN001, ANN002, ANN003
        return '{"intent": "qa", "reason": "测试"}'

    async def stream(self, messages, **kwargs):  # noqa: ANN001, ANN002, ANN003
        assert messages[0]["role"] == "system"
        for piece in ["根据文档，", "权限过滤必须在检索层执行[1]。", "这是依据[2]。"]:
            yield piece


def _fake_chunks() -> list[RetrievedChunk]:
    return [
        RetrievedChunk(
            chunk_id=1,
            doc_id="PRD",
            chunk_index=11,
            content="FR-33 权限条件下推到向量库 filter。",
            section_path="4. 功能需求 > 4.6 权限与安全",
            department="研发部",
            score=0.9,
        ),
        RetrievedChunk(
            chunk_id=2,
            doc_id="TECH_STACK",
            chunk_index=5,
            content="ADR-05 权限过滤在检索层强制执行。",
            section_path="8. 关键技术决策记录",
            department="研发部",
            score=0.8,
        ),
    ]


def _parse_sse(text: str) -> list[tuple[str, dict]]:
    events = []
    for block in text.strip().split("\n\n"):
        lines = block.splitlines()
        if not lines:
            continue
        event = lines[0].replace("event: ", "")
        data = json.loads(lines[1].replace("data: ", ""))
        events.append((event, data))
    return events


@pytest.fixture()
def mock_pipeline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.agent.nodes.get_llm", lambda: FakeStreamLLM())
    monkeypatch.setattr("app.agent.nodes.run_search", lambda *a, **k: _fake_chunks())
    monkeypatch.setattr("app.agent.service.get_llm", lambda: FakeStreamLLM())


def test_ask_streams_events_in_order(mock_pipeline: None) -> None:
    resp = _client.post("/api/v1/qa/ask", json={"question": "权限过滤怎么做？"})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")

    events = _parse_sse(resp.text)
    types = [e for e, _ in events]
    # 顺序契约：meta → intent → delta* → citations → session → done
    # （delta 数量不绑定：出口脱敏缓冲可能合并小帧，前端增量渲染不受影响）
    assert types[0] == "meta" and types[1] == "intent"
    assert types[-2:] == ["session", "done"]
    assert "citations" in types
    assert types.index("delta") < types.index("citations")

    meta = events[0][1]
    assert meta["retrieved"] == 2

    intent = events[1][1]
    assert intent["intent"] == "qa"

    session_event = events[-2][1]
    assert session_event["session_id"] > 0  # 会话已持久化

    deltas = [d["text"] for e, d in events if e == "delta"]
    assert "".join(deltas) == "根据文档，权限过滤必须在检索层执行[1]。这是依据[2]。"

    citations = events[-3][1]["citations"]
    assert len(citations) == 2
    assert citations[0]["index"] == 1
    assert citations[0]["doc_id"] == "PRD"
    assert citations[0]["section_path"].endswith("4.6 权限与安全")


def test_ask_passes_history(mock_pipeline: None) -> None:
    body = {
        "question": "那评估指标呢？",
        "history": [{"role": "user", "content": "权限过滤怎么做？"}],
        "top_k": 3,
    }
    resp = _client.post("/api/v1/qa/ask", json=body)
    assert resp.status_code == 200
    assert "done" in [e for e, _ in _parse_sse(resp.text)]


def test_ask_validation() -> None:
    resp = _client.post("/api/v1/qa/ask", json={"question": ""})
    assert resp.status_code == 422
    resp = _client.post("/api/v1/qa/ask", json={"question": "x", "top_k": 100})
    assert resp.status_code == 422


def test_ask_error_emits_error_event(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        raise RuntimeError("检索失败")

    monkeypatch.setattr("app.agent.nodes.get_llm", lambda: FakeStreamLLM())
    monkeypatch.setattr("app.agent.nodes.run_search", boom)
    resp = _client.post("/api/v1/qa/ask", json={"question": "问题"})
    assert resp.status_code == 200  # SSE 流内报错，不中断 HTTP
    events = _parse_sse(resp.text)
    assert events[-1][0] == "error"
    assert "内部错误" in events[-1][1]["message"]


def test_build_context_numbers_and_sources() -> None:
    from app.llm.prompts import build_context

    context = build_context(_fake_chunks())
    assert "[1] 来源：PRD › 4. 功能需求 > 4.6 权限与安全" in context
    assert "[2] 来源：TECH_STACK › 8. 关键技术决策记录" in context
    assert build_context([]) == "（无检索结果）"
