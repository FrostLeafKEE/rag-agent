"""会话 API 测试：创建/列表/消息/删除/反馈 + qa 自动建会话。"""

import asyncio
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.api.deps import get_current_user
from app.main import app
from app.models import ChatMessage, ChatSession, User

_client = TestClient(app)


@pytest.fixture(autouse=True)
def _auth():
    test_user = User(
        id=100, username="sess_tester", role="user", department="研发部", is_active=True
    )
    app.dependency_overrides[get_current_user] = lambda: test_user
    yield
    app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture()
def _cleanup(_sqlite_engine):
    yield
    async def clean() -> None:
        factory = async_sessionmaker(_sqlite_engine, expire_on_commit=False)
        async with factory() as session:
            await session.execute(delete(ChatMessage))
            await session.execute(delete(ChatSession))
            await session.commit()

    asyncio.run(clean())


def test_session_crud(_cleanup: None) -> None:
    # 创建
    resp = _client.post("/api/v1/sessions", json={"title": "测试会话"})
    assert resp.status_code == 201
    sess = resp.json()
    assert sess["title"] == "测试会话"

    # 列表
    items = _client.get("/api/v1/sessions").json()["items"]
    assert any(s["id"] == sess["id"] for s in items)

    # 消息为空
    msgs = _client.get(f"/api/v1/sessions/{sess['id']}/messages").json()["items"]
    assert msgs == []

    # 删除
    resp = _client.delete(f"/api/v1/sessions/{sess['id']}")
    assert resp.status_code == 200
    assert _client.get("/api/v1/sessions").json()["items"] == []


def test_other_user_cannot_access(_cleanup: None) -> None:
    resp = _client.post("/api/v1/sessions", json={"title": "私有会话"})
    sess_id = resp.json()["id"]
    other = User(id=101, username="other", role="user", department="财务部", is_active=True)
    app.dependency_overrides[get_current_user] = lambda: other
    assert _client.get(f"/api/v1/sessions/{sess_id}/messages").status_code == 404
    assert _client.delete(f"/api/v1/sessions/{sess_id}").status_code == 404


def test_feedback(_cleanup: None, _sqlite_engine) -> None:
    sess = _client.post("/api/v1/sessions", json={"title": "反馈会话"}).json()
    # 手工插入一条 assistant 消息
    async def add_msg() -> int:
        factory = async_sessionmaker(_sqlite_engine, expire_on_commit=False)
        async with factory() as session:
            msg = ChatMessage(session_id=sess["id"], role="assistant", content="回答内容")
            session.add(msg)
            await session.commit()
            await session.refresh(msg)
            return msg.id

    msg_id = asyncio.run(add_msg())
    resp = _client.post(
        f"/api/v1/sessions/{sess['id']}/messages/{msg_id}/feedback", json={"feedback": "up"}
    )
    assert resp.status_code == 200
    assert resp.json()["feedback"] == "up"
    # 校验持久化
    msgs = _client.get(f"/api/v1/sessions/{sess['id']}/messages").json()["items"]
    assert msgs[0]["feedback"] == "up"


def test_qa_creates_session_and_saves_messages(
    _cleanup: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """qa 无 session_id 时自动建会话并保存问答。"""
    from tests.test_qa import FakeStreamLLM, _fake_chunks

    monkeypatch.setattr("app.agent.nodes.get_llm", lambda: FakeStreamLLM())
    monkeypatch.setattr("app.agent.nodes.run_search", lambda *a, **k: _fake_chunks())
    monkeypatch.setattr("app.agent.service.get_llm", lambda: FakeStreamLLM())

    resp = _client.post(
        "/api/v1/qa/ask", json={"question": "权限过滤怎么做？"}
    )
    assert resp.status_code == 200
    assert "event: session" in resp.text

    sessions = _client.get("/api/v1/sessions").json()["items"]
    assert len(sessions) == 1
    sid = sessions[0]["id"]
    msgs = _client.get(f"/api/v1/sessions/{sid}/messages").json()["items"]
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[0]["content"] == "权限过滤怎么做？"
    refs = json.loads(msgs[1]["refs"])
    assert len(refs) == 2  # 引用已持久化
