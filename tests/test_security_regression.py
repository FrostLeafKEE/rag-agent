"""安全回归测试（2026-08-17 安全审计修复）：

- A1 空部门用户零可见（RBAC 兜底）
- A2 doc_id 白名单（Milvus filter 注入）
- A3 doc_id 冲突归属校验（文档接管）
- A4 worker 跳过已删除文档（删除竞态）
- B 批：密码 72 字节 / 审计 delete+denied / JWT prod 校验 / limit 钳制
"""

from __future__ import annotations

import asyncio
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.api.deps import get_current_user, user_departments
from app.config import Settings
from app.ingestion.indexer import SAFE_DOC_ID
from app.main import app
from app.models import AuditLog, Document, User
from app.retrieval.base import build_filter

_client = TestClient(app)

TEST_DOC_ID = "sec-test-doc"


@pytest.fixture(autouse=True)
def _auth():
    # 文档管理已管理员化：文档相关用例以 super_admin 身份执行
    user = User(
        id=1, username="sec_tester", role="super_admin", department="研发部", is_active=True
    )
    app.dependency_overrides[get_current_user] = lambda: user
    yield
    app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture()
def _cleanup(_sqlite_engine):
    yield

    async def clean() -> None:
        from sqlalchemy.ext.asyncio import async_sessionmaker

        factory = async_sessionmaker(_sqlite_engine, expire_on_commit=False)
        async with factory() as session:
            await session.execute(delete(Document).where(Document.doc_id.like("sec-%")))
            await session.execute(delete(AuditLog))
            await session.commit()

    asyncio.run(clean())


@pytest.fixture()
def _no_enqueue(monkeypatch: pytest.MonkeyPatch):
    async def fake_enqueue(*args, **kwargs):  # noqa: ANN002, ANN003
        return None

    monkeypatch.setattr("app.api.routes.documents.enqueue", fake_enqueue)


def _upload(filename: str = "sec_test.md", doc_id: str = TEST_DOC_ID, department: str = "研发部"):
    return _client.post(
        "/api/v1/documents/upload",
        files={"file": (filename, "# 安全测试文档\n内容".encode(), "text/markdown")},
        data={"department": department, "doc_id": doc_id},
    )


# ---- A1：空部门用户零可见 ----


def test_build_filter_none_means_unrestricted() -> None:
    assert build_filter(None) == ""  # admin


def test_build_filter_empty_means_zero_visible() -> None:
    assert build_filter([]) == "1 == 0"  # 未分配部门：永假，而非空串（绕过 RBAC）


def test_user_departments_semantics() -> None:
    assert user_departments(User(id=1, username="a", role="admin", department="")) is None
    assert user_departments(User(id=2, username="b", role="user", department="研发部")) == [
        "研发部"
    ]
    assert user_departments(User(id=3, username="c", role="user", department="")) == []


def test_register_requires_department(_cleanup: None) -> None:
    resp = _client.post(
        "/api/v1/auth/register",
        json={"username": "sec_nodep", "password": "passw0rd123"},
    )
    assert resp.status_code == 422


def test_regular_user_denied_document_management(_cleanup: None, _no_enqueue: None) -> None:
    """普通用户（含空部门）访问文档管理 → 403（RBAC 管理员化）。"""
    _upload()
    time.sleep(0.2)
    nodep = User(id=9, username="nodep", role="user", department="", is_active=True)
    app.dependency_overrides[get_current_user] = lambda: nodep
    resp = _client.get("/api/v1/documents")
    assert resp.status_code == 403  # 空部门普通用户：无文档管理权限
    assert (
        _client.post(
            "/api/v1/documents/upload",
            files={"file": ("x.md", b"x", "text/markdown")},
            data={"department": "研发部"},
        ).status_code
        == 403
    )


def test_admin_without_departments_sees_nothing(_cleanup: None, _no_enqueue: None) -> None:
    """部门管理员未分配负责部门 → 空列表（零可见）。"""
    _upload()
    time.sleep(0.2)
    admin = User(id=12, username="noadmin_dept", role="admin", department="", is_active=True)
    app.dependency_overrides[get_current_user] = lambda: admin
    resp = _client.get("/api/v1/documents")
    assert resp.status_code == 200
    assert resp.json()["items"] == []


def test_admin_scoped_to_assigned_departments(_cleanup: None, _no_enqueue: None) -> None:
    """部门管理员仅见负责部门（多部门集合正确）。"""
    from app.models import AdminDepartment

    _upload(department="研发部")
    _upload(filename="sec_fin.md", doc_id="sec-fin-doc", department="财务部")
    time.sleep(0.2)
    admin = User(id=13, username="rd_admin2", role="admin", department="", is_active=True)

    async def seed() -> None:
        from app.db import session_factory

        async with session_factory() as session:
            session.add_all(AdminDepartment(user_id=13, department=d) for d in ["研发部", "人事部"])
            await session.commit()

    asyncio.run(seed())
    app.dependency_overrides[get_current_user] = lambda: admin
    resp = _client.get("/api/v1/documents")
    ids = [d["doc_id"] for d in resp.json()["items"]]
    assert TEST_DOC_ID in ids  # 研发部可见
    assert "sec-fin-doc" not in ids  # 财务部不可见（未负责）


# ---- A2：doc_id 白名单（Milvus filter 注入）----


def test_upload_rejects_injection_doc_id(_cleanup: None, _no_enqueue: None) -> None:
    evil = 'x" || doc_id != "x'
    resp = _upload(doc_id=evil)
    assert resp.status_code == 422
    assert "doc_id" in resp.json()["detail"]


def test_delete_rejects_injection_doc_id(_cleanup: None, _no_enqueue: None) -> None:
    resp = _client.delete("/api/v1/documents/x%22%20||%20doc_id%20!=%20%22x")
    assert resp.status_code == 404  # 不进入 Milvus 表达式


def test_delete_by_doc_raises_on_unsafe_id() -> None:
    from app.ingestion.indexer import MilvusIndexer

    indexer = MilvusIndexer.__new__(MilvusIndexer)  # 不连接，只测校验
    with pytest.raises(ValueError):
        indexer.delete_by_doc('x" || doc_id != "x')


def test_safe_doc_id_allows_chinese_and_dash() -> None:
    assert SAFE_DOC_ID.match("研发部-入职手册")
    assert SAFE_DOC_ID.match("connector-watch-demo")
    assert not SAFE_DOC_ID.match('x" || 1')
    assert not SAFE_DOC_ID.match("a b")
    assert not SAFE_DOC_ID.match("a" * 65)  # 超长


# ---- A3：doc_id 冲突归属校验（文档接管）----


def test_upload_conflict_with_other_user_rejected(_cleanup: None, _no_enqueue: None) -> None:
    """doc_id 冲突且归属其他管理员 → 409（防文档接管）。"""
    from app.models import AdminDepartment

    _upload()  # sec_tester（super_admin）先上传到研发部
    time.sleep(0.2)
    other = User(id=10, username="fin_admin2", role="admin", department="", is_active=True)

    async def seed() -> None:
        from app.db import session_factory

        async with session_factory() as session:
            session.add(AdminDepartment(user_id=10, department="财务部"))
            await session.commit()

    asyncio.run(seed())
    app.dependency_overrides[get_current_user] = lambda: other
    resp = _upload(department="财务部")  # 同 doc_id，归属研发部（他人）
    assert resp.status_code == 409


def test_upload_conflict_same_user_allowed(_cleanup: None, _no_enqueue: None) -> None:
    first = _upload()
    assert first.status_code == 202
    time.sleep(0.2)
    resp = _upload()  # 同用户重传（版本更新语义 FR-04）
    assert resp.status_code == 202


# ---- A4：worker 跳过已删除文档 ----


def test_worker_skips_disabled_document(_sqlite_engine, monkeypatch: pytest.MonkeyPatch) -> None:
    """删除竞态：已 disabled 文档的摄入任务直接丢弃，不复活向量。"""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.ingestion.queue import process_ingestion

    _upload()
    time.sleep(0.2)

    # 手动置为已删除状态
    factory = async_sessionmaker(_sqlite_engine, expire_on_commit=False)

    async def mark_disabled() -> None:
        async with factory() as session:
            doc = await session.scalar(select(Document).where(Document.doc_id == TEST_DOC_ID))
            doc.status = "disabled"
            await session.commit()

    asyncio.run(mark_disabled())

    called: list[str] = []

    async def fake_ingest(session, path, doc_id, **kwargs):  # noqa: ANN001, ANN002, ANN003
        called.append(doc_id)
        return 1

    monkeypatch.setattr("app.ingestion.pipeline.ingest_document", fake_ingest)

    async def fake_get_session():
        async with factory() as session:
            yield session

    monkeypatch.setattr("app.db.get_session", fake_get_session)
    payload = {
        "doc_id": TEST_DOC_ID,
        "stored_path": "data/uploads/whatever.md",
        "department": "研发部",
        "source_name": "sec_test.md",
        "username": "sec_tester",
    }
    asyncio.run(process_ingestion(payload))
    assert called == []  # 摄入未执行（任务被丢弃）


# ---- B 批：密码/审计/JWT/limit ----


def test_register_rejects_password_over_72_bytes(_cleanup: None) -> None:
    resp = _client.post(
        "/api/v1/auth/register",
        json={
            "username": "sec_longpw",
            "password": "p" * 80,  # bcrypt 只取前 72 字节
            "department": "研发部",
        },
    )
    assert resp.status_code == 422
    assert "72" in resp.json()["detail"]


def test_delete_writes_audit(
    _cleanup: None, _no_enqueue: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    _upload()
    time.sleep(0.2)
    monkeypatch.setattr(
        "app.ingestion.indexer.MilvusIndexer",
        lambda *a, **k: type("FakeIdx", (), {"delete_by_doc": lambda self, d: None})(),
    )
    resp = _client.delete(f"/api/v1/documents/{TEST_DOC_ID}")
    assert resp.status_code == 200

    async def check() -> list[AuditLog]:
        from app.db import session_factory

        async with session_factory() as session:
            return list(
                (
                    await session.execute(
                        select(AuditLog).where(
                            AuditLog.action == "delete", AuditLog.resource == TEST_DOC_ID
                        )
                    )
                ).scalars()
            )

    rows = asyncio.run(check())
    assert len(rows) == 1
    assert rows[0].user == "sec_tester"


def test_denied_delete_writes_audit(_cleanup: None, _no_enqueue: None) -> None:
    _upload()
    time.sleep(0.2)
    other = User(id=11, username="fin_guy", role="user", department="财务部", is_active=True)
    app.dependency_overrides[get_current_user] = lambda: other
    resp = _client.delete(f"/api/v1/documents/{TEST_DOC_ID}")
    assert resp.status_code == 403

    async def check() -> list[AuditLog]:
        from app.db import session_factory

        async with session_factory() as session:
            return list(
                (
                    await session.execute(
                        select(AuditLog).where(
                            AuditLog.action == "denied", AuditLog.resource == TEST_DOC_ID
                        )
                    )
                ).scalars()
            )

    rows = asyncio.run(check())
    assert len(rows) == 1
    assert rows[0].user == "fin_guy"


def test_prod_env_requires_strong_jwt_secret() -> None:
    with pytest.raises(ValueError, match="jwt_secret"):
        Settings(app_env="prod", jwt_secret="dev-only-secret-change-me")
    with pytest.raises(ValueError, match="jwt_secret"):
        Settings(app_env="prod", jwt_secret="short")
    with pytest.raises(ValueError, match="DEBUG"):
        Settings(app_env="prod", jwt_secret="x" * 40)  # debug 默认 True 也不允许
    with pytest.raises(ValueError, match="AUTH_DISABLE_SIGNUP"):
        Settings(app_env="prod", jwt_secret="x" * 40, debug=False)  # 注册开关未显式开启
    ok = Settings(app_env="prod", jwt_secret="x" * 40, debug=False, auth_disable_signup=True)
    assert ok.jwt_secret  # 显式强密钥 + debug 关闭 + 注册关闭放行
    # dev 环境默认值不阻塞（本地开发）
    assert Settings(app_env="dev").jwt_secret


def test_list_limit_clamped(_cleanup: None, _no_enqueue: None) -> None:
    for i in range(3):
        _upload(filename=f"sec_{i}.md", doc_id=f"sec-limit-{i}")
    time.sleep(0.2)
    resp = _client.get("/api/v1/documents?limit=100000")
    assert resp.status_code == 200
    assert len(resp.json()["items"]) <= 100


# ---- 登录暴力破解限速 ----


def test_login_lockout_after_repeated_failures() -> None:
    """同一用户名连续失败 5 次后锁定（第 6 次登录 429）。需要本地 Redis。"""
    import asyncio
    import socket

    from app.api.routes import auth as auth_routes
    from app.api.routes.auth import _LOGIN_FAIL_LIMIT

    # CI/离线环境无 Redis：登录限速有降级逻辑（auth 层吞连接异常），锁定行为仅在有 Redis 时验证
    try:
        sock = socket.create_connection(("localhost", 6379), timeout=1)
        sock.close()
    except OSError:
        pytest.skip("Redis 不可达，跳过登录锁定行为测试")

    username = "sec_lockout_test"

    async def reset() -> None:
        await auth_routes.clear_login_failures(username)

    asyncio.run(reset())

    async def fail_n_times(n: int) -> int:
        last = 0
        for _ in range(n):
            resp = _client.post(
                "/api/v1/auth/login",
                json={"username": username, "password": "wrong-pass-000"},
            )
            last = resp.status_code
        return last

    code = asyncio.run(fail_n_times(_LOGIN_FAIL_LIMIT))
    # 前 5 次都是 401（用户不存在）；第 5 次已触发锁定
    assert code == 401

    async def locked() -> bool:
        return await auth_routes.login_is_locked(username)

    assert asyncio.run(locked()) is True

    locked_resp = _client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": "wrong-pass-000"},
    )
    assert locked_resp.status_code == 429

    asyncio.run(reset())  # 清理，避免影响其他用例
    assert asyncio.run(auth_routes.login_is_locked(username)) is False
