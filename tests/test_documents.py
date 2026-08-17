"""文档管理 API 测试：上传入队（Redis Stream）/列表/删除/任务状态/worker 流转/权限隔离。"""

import asyncio
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.api.deps import get_current_user
from app.ingestion.queue import process_ingestion
from app.main import app
from app.models import Document, User

_client = TestClient(app)

TEST_DOC_ID = "itest-doc"


@pytest.fixture(autouse=True)
def _auth():
    # 文档管理已管理员化：上传/删除/列表测试统一以 super_admin 身份执行
    test_user = User(id=1, username="doc_tester", role="super_admin", department="研发部", is_active=True)
    app.dependency_overrides[get_current_user] = lambda: test_user
    yield
    app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture()
def _cleanup(_sqlite_engine):
    yield
    async def clean() -> None:
        factory = async_sessionmaker(_sqlite_engine, expire_on_commit=False)
        async with factory() as session:
            await session.execute(delete(Document).where(Document.doc_id.like("itest%")))
            await session.commit()

    asyncio.run(clean())


@pytest.fixture()
def _no_enqueue(monkeypatch: pytest.MonkeyPatch):
    """上传不入真实队列（测试不启动 worker）。"""
    async def fake_enqueue(*args, **kwargs):  # noqa: ANN002, ANN003
        return None

    monkeypatch.setattr("app.api.routes.documents.enqueue", fake_enqueue)


def _upload(
    content: str = "# 测试文档\n这是用于 API 测试的内容。",
    filename: str = "itest_upload.md",
):
    return _client.post(
        "/api/v1/documents/upload",
        files={"file": (filename, content.encode("utf-8"), "text/markdown")},
        data={"department": "研发部", "doc_id": TEST_DOC_ID},
    )


def test_upload_enqueues_and_status_uploading(_cleanup: None, _no_enqueue: None) -> None:
    resp = _upload()
    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "uploading"
    assert body["doc_id"] == TEST_DOC_ID

    task = _client.get(f"/api/v1/documents/tasks/{body['task_id']}")
    assert task.json()["status"] == "uploading"


def test_upload_rejects_unsupported_format(_cleanup: None) -> None:
    resp = _client.post(
        "/api/v1/documents/upload",
        files={"file": ("evil.exe", b"MZ", "application/octet-stream")},
    )
    assert resp.status_code == 415


def test_upload_rejects_oversized(_cleanup: None, monkeypatch: pytest.MonkeyPatch) -> None:
    import app.api.routes.documents as docs_mod

    monkeypatch.setattr(docs_mod, "MAX_UPLOAD_BYTES", 10)
    resp = _upload(content="超过十字节的内容内容内容")
    assert resp.status_code == 413


def _run_process(payload: dict, _sqlite_engine, monkeypatch: pytest.MonkeyPatch) -> None:
    """在测试库会话上运行 worker 处理（patch app.db.get_session 源头）。"""
    factory = async_sessionmaker(_sqlite_engine, expire_on_commit=False)

    async def fake_get_session():
        async with factory() as session:
            yield session

    monkeypatch.setattr("app.db.get_session", fake_get_session)
    asyncio.run(process_ingestion(payload))


def test_worker_process_marks_indexed(
    _cleanup: None, _no_enqueue: None, _sqlite_engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """worker 消费路径：process_ingestion 成功后 documents 标记 indexed + 分块数。"""
    task_id = _upload().json()["task_id"]  # 先创建 documents 记录
    time.sleep(0.3)

    async def fake_ingest(session, path, doc_id, **kwargs):  # noqa: ANN001, ANN002, ANN003
        # 模拟真实 ingest_document 的元数据更新
        doc = await session.scalar(select(Document).where(Document.doc_id == doc_id))
        if doc:
            doc.status = "indexed"
            doc.chunk_count = 5
            await session.commit()
        return 5

    monkeypatch.setattr("app.ingestion.pipeline.ingest_document", fake_ingest)
    _run_process(_make_payload(), _sqlite_engine, monkeypatch)
    task = _client.get(f"/api/v1/documents/tasks/{task_id}")
    assert task.json()["status"] == "indexed"
    assert task.json()["chunk_count"] == 5


def test_worker_process_marks_failed(
    _cleanup: None, _no_enqueue: None, _sqlite_engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """worker 摄入失败：异常冒泡（重投逻辑在 worker_loop）。"""
    _upload()
    time.sleep(0.3)

    async def boom(session, path, doc_id, **kwargs):  # noqa: ANN001, ANN002, ANN003
        raise ValueError("解析失败")

    monkeypatch.setattr("app.ingestion.pipeline.ingest_document", boom)
    try:
        _run_process(_make_payload(), _sqlite_engine, monkeypatch)
        raise AssertionError("应抛出 ValueError")
    except ValueError:
        pass


def _make_payload() -> dict:
    return {
        "doc_id": TEST_DOC_ID,
        "stored_path": "data/uploads/x.md",
        "department": "研发部",
        "source_name": "itest_upload.md",
        "username": "doc_tester",
    }


def _seed_admin_depts(user_id: int, departments: list[str]) -> None:
    """预置部门管理员的负责部门（AdminDepartment，RBAC 可见范围来源）。"""
    from app.models import AdminDepartment

    async def _seed() -> None:
        from app.db import session_factory

        async with session_factory() as session:
            session.add_all(
                AdminDepartment(user_id=user_id, department=d) for d in departments
            )
            await session.commit()

    asyncio.run(_seed())


def test_list_documents_scoped_by_admin_department(
    _cleanup: None, _no_enqueue: None
) -> None:
    """RBAC：admin 仅见负责部门；普通用户 403。"""
    _upload()
    time.sleep(0.3)

    # 财务部普通用户：无文档管理权限
    fin_user = User(id=2, username="fin", role="user", department="财务部", is_active=True)
    app.dependency_overrides[get_current_user] = lambda: fin_user
    resp = _client.get("/api/v1/documents")
    assert resp.status_code == 403

    # 负责"财务部"的部门管理员：看不到研发部文档（空）
    fin_admin = User(id=4, username="fin_admin", role="admin", department="", is_active=True)
    _seed_admin_depts(4, ["财务部"])
    app.dependency_overrides[get_current_user] = lambda: fin_admin
    resp = _client.get("/api/v1/documents")
    assert resp.json()["items"] == []

    # 负责"研发部"的部门管理员：可见
    rd_admin = User(id=6, username="rd_admin", role="admin", department="", is_active=True)
    _seed_admin_depts(6, ["研发部"])
    app.dependency_overrides[get_current_user] = lambda: rd_admin
    resp = _client.get("/api/v1/documents")
    assert any(d["doc_id"] == TEST_DOC_ID for d in resp.json()["items"])


def test_delete_document(
    _cleanup: None, _no_enqueue: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    _upload()
    time.sleep(0.3)

    deleted = []
    monkeypatch.setattr(
        "app.ingestion.indexer.MilvusIndexer",
        lambda *a, **k: type(
            "FakeIdx", (), {"delete_by_doc": lambda self, d: deleted.append(d)}
        )(),
    )
    resp = _client.delete(f"/api/v1/documents/{TEST_DOC_ID}")
    assert resp.status_code == 200
    assert resp.json()["status"] == "disabled"
    assert deleted == [TEST_DOC_ID]


def test_delete_foreign_document_forbidden(_cleanup: None, _no_enqueue: None) -> None:
    _upload()
    time.sleep(0.3)
    fin_user = User(id=5, username="fin3", role="user", department="财务部", is_active=True)
    app.dependency_overrides[get_current_user] = lambda: fin_user
    resp = _client.delete(f"/api/v1/documents/{TEST_DOC_ID}")
    assert resp.status_code == 403


def test_delete_unknown_document(_cleanup: None) -> None:
    resp = _client.delete("/api/v1/documents/no-such-doc")
    assert resp.status_code == 404


def test_task_status_unknown() -> None:
    resp = _client.get("/api/v1/documents/tasks/999999")
    assert resp.status_code == 404
