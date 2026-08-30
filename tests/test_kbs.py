"""知识库管理测试（内容分组层）：CRUD 权限 / 删除保护 / 上传归属继承。"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.deps import get_current_user
from app.main import app
from app.models import Document, KnowledgeBase, User

_client = TestClient(app)


@pytest.fixture(autouse=True)
def _auth():
    sa = User(id=1, username="kb_boss", role="super_admin", department="", is_active=True)
    app.dependency_overrides[get_current_user] = lambda: sa
    yield
    app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture()
def _cleanup(_sqlite_engine):
    yield

    async def clean() -> None:
        from sqlalchemy.ext.asyncio import async_sessionmaker

        factory = async_sessionmaker(_sqlite_engine, expire_on_commit=False)
        async with factory() as session:
            docs = list(await session.scalars(select(Document).where(Document.doc_id.like("kbt%"))))
            for d in docs:
                await session.delete(d)
            kbs = list((await session.scalars(select(KnowledgeBase))).all())
            for k in kbs:
                await session.delete(k)
            await session.commit()

    asyncio.run(clean())


@pytest.fixture()
def _no_enqueue(monkeypatch: pytest.MonkeyPatch):
    async def fake_enqueue(*args, **kwargs):  # noqa: ANN002, ANN003
        return None

    monkeypatch.setattr("app.api.routes.documents.enqueue", fake_enqueue)


def _create_kb(name: str = "kbt-库", department: str = "研发部") -> dict:
    r = _client.post(
        "/api/v1/kbs",
        json={"name": name, "description": "测试库", "department": department},
    )
    assert r.status_code == 201
    return r.json()


def test_create_requires_super_admin() -> None:
    admin_user = User(id=2, username="kb_admin", role="admin", department="", is_active=True)
    app.dependency_overrides[get_current_user] = lambda: admin_user
    resp = _client.post("/api/v1/kbs", json={"name": "kbt-非法", "department": "研发部"})
    assert resp.status_code == 403


def test_create_kb_and_list(_cleanup: None) -> None:
    _create_kb()
    resp = _client.get("/api/v1/kbs").json()
    assert any(k["name"] == "kbt-库" and k["doc_count"] == 0 for k in resp["items"])


def test_create_duplicate_name_conflict(_cleanup: None) -> None:
    _create_kb("kbt-重复")
    resp = _client.post("/api/v1/kbs", json={"name": "kbt-重复", "department": "研发部"})
    assert resp.status_code == 409


def test_delete_kb_with_docs_rejected(_cleanup: None, _no_enqueue: None) -> None:
    kb = _create_kb()
    r = _client.post(
        "/api/v1/documents/upload",
        files={"file": ("kbt.md", b"# content", "text/markdown")},
        data={"doc_id": "kbt-doc", "kb_id": str(kb["id"])},
    )
    assert r.status_code == 202
    # 文档的部门权限继承知识库
    doc = _client.get("/api/v1/documents", params={"keyword": "kbt-doc"}).json()["items"][0]
    assert doc["department"] == "研发部" and doc["kb_id"] == kb["id"]
    # 有文档时删除 → 409
    assert _client.delete(f"/api/v1/kbs/{kb['id']}").status_code == 409


def test_upload_to_unknown_kb_404(_cleanup: None, _no_enqueue: None) -> None:
    resp = _client.post(
        "/api/v1/documents/upload",
        files={"file": ("x.md", b"x", "text/markdown")},
        data={"doc_id": "kbt-x", "kb_id": "99999"},
    )
    assert resp.status_code == 404


def test_upload_requires_kb_for_admin(_cleanup: None, _no_enqueue: None) -> None:
    """部门管理员上传必须选择知识库（部门由库继承，不允许自由指定）。"""
    admin_user = User(id=3, username="kb_rd_admin", role="admin", department="", is_active=True)
    app.dependency_overrides[get_current_user] = lambda: admin_user
    resp = _client.post(
        "/api/v1/documents/upload",
        files={"file": ("y.md", b"y", "text/markdown")},
        data={"department": "研发部", "doc_id": "kbt-nokb"},
    )
    assert resp.status_code == 403  # 无库则无负责部门 → 无权上传到任何部门


def test_list_filter_by_kb(_cleanup: None, _no_enqueue: None) -> None:
    kb1 = _create_kb("kbt-库一")
    kb2 = _create_kb("kbt-库二", department="财务部")
    _client.post(
        "/api/v1/documents/upload",
        files={"file": ("a.md", b"a", "text/markdown")},
        data={"doc_id": "kbt-a", "kb_id": str(kb1["id"])},
    )
    _client.post(
        "/api/v1/documents/upload",
        files={"file": ("b.md", b"b", "text/markdown")},
        data={"doc_id": "kbt-b", "kb_id": str(kb2["id"])},
    )
    import time

    time.sleep(0.2)
    items = _client.get("/api/v1/documents", params={"kb_id": kb1["id"]}).json()["items"]
    assert [d["doc_id"] for d in items] == ["kbt-a"]
