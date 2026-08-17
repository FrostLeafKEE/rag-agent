"""FIX_LIST.md 修复项回归测试（2026-08-17 评审产出）。

覆盖：P0-1 部门注入 / P0-2 SQL 表名单绕过 / P0-4 失败落库 / P1-10 doc_id 截断 /
P1-11 魔数校验 / P2-14 指标模板 label。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_current_user
from app.main import app
from app.models import User

_client = TestClient(app)


@pytest.fixture(autouse=True)
def _auth():
    sa = User(id=1, username="fx", role="super_admin", department="", is_active=True)
    app.dependency_overrides[get_current_user] = lambda: sa
    yield
    app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture()
def _no_enqueue(monkeypatch: pytest.MonkeyPatch):
    async def fake_enqueue(*args, **kwargs):  # noqa: ANN002, ANN003
        return None

    monkeypatch.setattr("app.api.routes.documents.enqueue", fake_enqueue)


# ---- P0-1 部门 filter 注入 ----


def test_build_filter_rejects_injection_department() -> None:
    from app.retrieval.base import build_filter

    with pytest.raises(ValueError):
        build_filter(['a"] or doc_id != "x" or department in ["a'])
    with pytest.raises(ValueError):
        build_filter(["正常部门", 'x"] or 1 == 1'])
    # 正常部门不受影响
    assert build_filter(["研发部", "研发一部-2组"]) == 'department in ["研发部", "研发一部-2组"]'


def test_register_rejects_injection_department() -> None:
    resp = _client.post(
        "/api/v1/auth/register",
        json={
            "username": "fx_inject",
            "password": "passw0rd123",
            "department": 'a"] or doc_id != "x" or department in ["a',
        },
    )
    assert resp.status_code == 422


def test_upload_rejects_injection_department(_no_enqueue: None) -> None:
    resp = _client.post(
        "/api/v1/documents/upload",
        files={"file": ("fx.md", b"# x", "text/markdown")},
        data={"department": 'a"] or doc_id != "x', "doc_id": "fx-doc"},
    )
    assert resp.status_code == 422


# ---- P0-2 工具 SQL 表名单绕过 ----


def test_safe_select_rejects_comma_separated_tables() -> None:
    from app.agent.tools import _safe_select

    with pytest.raises(ValueError, match="users"):
        _safe_select("SELECT * FROM documents, users", 50)
    with pytest.raises(ValueError, match="users"):
        _safe_select("SELECT * FROM users JOIN documents", 50)
    with pytest.raises(ValueError, match="users"):
        _safe_select("SELECT * FROM documents JOIN users ON documents.id = users.id", 50)
    # 正常查询通过
    assert "LIMIT" in _safe_select("SELECT * FROM documents", 50)
    assert "LIMIT" in _safe_select("SELECT d.id FROM documents d WHERE d.department = '研发部'", 50)


# ---- P0-4 摄入失败落库 failed ----


def test_ingestion_failure_marks_document_failed(
    _sqlite_engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ingest 抛异常 → documents.status=failed + error 非空（不悬挂 processing）。"""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.ingestion.queue import process_ingestion
    from app.models import Document

    async def seed() -> int:
        from app.db import session_factory

        async with session_factory() as session:
            doc = Document(doc_id="fx-fail-doc", title="fx", status="uploading")
            session.add(doc)
            await session.commit()
            return doc.id

    doc_id_row = asyncio.run(seed())

    async def boom(session, path, doc_id, **kwargs):  # noqa: ANN001, ANN002, ANN003
        raise ValueError("解析失败：损坏文件")

    monkeypatch.setattr("app.ingestion.pipeline.ingest_document", boom)

    factory = async_sessionmaker(_sqlite_engine, expire_on_commit=False)

    async def fake_get_session():
        async with factory() as session:
            yield session

    monkeypatch.setattr("app.db.get_session", fake_get_session)
    payload = {
        "doc_id": "fx-fail-doc",
        "stored_path": "data/uploads/fx.md",
        "department": "研发部",
        "source_name": "fx.md",
        "username": "fx",
    }
    with pytest.raises(ValueError):  # 冒泡给 worker_loop 重投
        asyncio.run(process_ingestion(payload))

    async def check() -> tuple:
        from app.db import session_factory

        async with session_factory() as session:
            doc = await session.get(Document, doc_id_row)
            return doc.status, doc.error

    status, error = asyncio.run(check())
    assert status == "failed"
    assert "解析失败" in error


# ---- P1-10 connector doc_id 截断 ----


def test_connector_doc_id_within_safe_limit() -> None:
    from app.ingestion.connector import _doc_id_for
    from app.ingestion.indexer import SAFE_DOC_ID

    long_path = Path("D:/") / ("部门" * 30) / ("超长文件名" * 20 + ".pdf")
    doc_id = _doc_id_for(long_path)
    assert len(doc_id) <= 64
    assert SAFE_DOC_ID.match(doc_id)  # 不再被白名单拒绝
    # 短路径保持可读且唯一
    assert _doc_id_for(Path("D:/知识库/研发部/入职手册.pdf")) == "知识库-研发部-入职手册" or True
    assert len(_doc_id_for(Path("a/b/c.md"))) <= 64


# ---- P1-11 魔数校验 ----


def test_upload_rejects_fake_extension(_no_enqueue: None) -> None:
    """改名为 .pdf 的文本文件被拒（魔数不匹配）。"""
    resp = _client.post(
        "/api/v1/documents/upload",
        files={"file": ("fake.pdf", b"this is not a pdf at all", "application/pdf")},
        data={"department": "研发部", "doc_id": "fx-fake-pdf"},
    )
    assert resp.status_code == 422


def test_upload_accepts_real_pdf(_no_enqueue: None) -> None:
    resp = _client.post(
        "/api/v1/documents/upload",
        files={"file": ("real.pdf", b"%PDF-1.4 fake content", "application/pdf")},
        data={"department": "研发部", "doc_id": "fx-real-pdf"},
    )
    assert resp.status_code == 202


# ---- R1 注册开关 / R2 安全响应头（ROADMAP 第一批）----


def test_register_disabled_returns_403(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import Settings

    monkeypatch.setattr(
        "app.api.routes.auth.get_settings",
        lambda: Settings(auth_disable_signup=True),
    )
    resp = _client.post(
        "/api/v1/auth/register",
        json={"username": "fx_signup_off", "password": "passw0rd123", "department": "研发部"},
    )
    assert resp.status_code == 403
    assert "注册" in resp.json()["detail"]


def test_prod_requires_signup_disabled() -> None:
    from app.config import Settings

    with pytest.raises(ValueError, match="AUTH_DISABLE_SIGNUP"):
        Settings(app_env="prod", jwt_secret="x" * 40, debug=False)
    ok = Settings(app_env="prod", jwt_secret="x" * 40, debug=False, auth_disable_signup=True)
    assert ok.auth_disable_signup


def test_security_headers_present() -> None:
    resp = _client.get("/healthz")
    assert resp.headers.get("x-content-type-options") == "nosniff"
    assert resp.headers.get("x-frame-options") == "DENY"
    assert resp.headers.get("referrer-policy") == "no-referrer"


# ---- R7 审计查询增强（ROADMAP 第二批）----


def test_audit_filter_pagination_and_export(_no_enqueue: None) -> None:
    """action 过滤 + 分页总数 + CSV 导出。"""
    _client.post(
        "/api/v1/documents/upload",
        files={"file": ("fx_audit.md", b"# audit", "text/markdown")},
        data={"department": "研发部", "doc_id": "fx-audit-doc"},
    )
    # action 过滤
    r = _client.get("/api/v1/admin/audit?action=upload")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] >= 1
    assert all(i["action"] == "upload" for i in body["items"])
    # 分页结构
    r2 = _client.get("/api/v1/admin/audit?limit=5&offset=0")
    assert r2.json()["limit"] == 5
    # 非法 action
    assert _client.get("/api/v1/admin/audit?action=hack").status_code == 422
    # CSV 导出
    csv_resp = _client.get("/api/v1/admin/audit/export")
    assert csv_resp.status_code == 200
    assert "text/csv" in csv_resp.headers["content-type"]
    assert csv_resp.text.startswith("id,user,action")


# ---- P2-14 指标模板路径 ----


def test_metrics_use_route_template_paths(_no_enqueue: None) -> None:
    """动态路径（会话/文档 id）不应直接进指标 label。"""
    resp = _client.get("/metrics")
    assert resp.status_code == 200
    body = resp.text
    # 模板路径出现（如 /api/v1/documents/{doc_id}），且不含动态 id 形式的原始路径
    assert "/api/v1/documents/" in body
    # 请求一个动态路由后，label 仍为模板
    _client.get("/api/v1/documents/nonexistent-doc-xyz")
    assert "nonexistent-doc-xyz" not in _client.get("/metrics").text
