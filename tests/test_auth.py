"""认证 API 测试（SQLite 测试库，conftest 已覆盖 get_session）。

覆盖：注册 / 登录 / me / 密码错误 / token 无效 / 停用账号 / 权限隔离。
"""

import asyncio
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.api.deps import user_departments
from app.main import app
from app.models import User

_client = TestClient(app)


@pytest.fixture()
def cleanup_users(_sqlite_engine):
    yield

    async def clean() -> None:
        factory = async_sessionmaker(_sqlite_engine, expire_on_commit=False)
        async with factory() as session:
            await session.execute(delete(User).where(User.username.like("itest%")))
            await session.commit()

    asyncio.run(clean())


def _register(username: str, **extra: Any) -> Any:
    # department 必填（安全加固后注册必须声明部门）；extra 可覆盖默认值
    body = {"username": username, "password": "passw0rd123", "department": "研发部", **extra}
    return _client.post("/api/v1/auth/register", json=body)


def test_register_and_login_flow(cleanup_users: None) -> None:
    resp = _register("itest_user1", department="研发部")
    assert resp.status_code == 201
    body = resp.json()
    assert body["role"] == "user"
    assert body["department"] == "研发部"
    assert "password" not in body  # 不泄露哈希

    # 登录
    login = _client.post(
        "/api/v1/auth/login", json={"username": "itest_user1", "password": "passw0rd123"}
    )
    assert login.status_code == 200
    token = login.json()["access_token"]

    # me
    me = _client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["username"] == "itest_user1"


def test_duplicate_username_conflict(cleanup_users: None) -> None:
    _register("itest_dup")
    resp = _register("itest_dup")
    assert resp.status_code == 409


def test_login_wrong_password(cleanup_users: None, monkeypatch: pytest.MonkeyPatch) -> None:
    # 限速计数不污染本用例（限速逻辑在 test_security_regression 单独覆盖）
    async def noop(username: str) -> None:
        return None

    monkeypatch.setattr("app.api.routes.auth.record_login_failure", noop)
    _register("itest_wrongpw")
    resp = _client.post(
        "/api/v1/auth/login", json={"username": "itest_wrongpw", "password": "wrong-pass"}
    )
    assert resp.status_code == 401


def test_login_unknown_user(monkeypatch: pytest.MonkeyPatch) -> None:
    async def noop(username: str) -> None:
        return None

    monkeypatch.setattr("app.api.routes.auth.record_login_failure", noop)
    resp = _client.post("/api/v1/auth/login", json={"username": "nobody", "password": "x" * 12})
    assert resp.status_code == 401


def test_me_without_token_rejected() -> None:
    resp = _client.get("/api/v1/auth/me")
    assert resp.status_code == 401


def test_me_with_garbage_token_rejected() -> None:
    resp = _client.get("/api/v1/auth/me", headers={"Authorization": "Bearer not-a-jwt"})
    assert resp.status_code == 401


def test_inactive_user_cannot_login(cleanup_users: None, _sqlite_engine) -> None:
    _register("itest_inactive")

    async def deactivate() -> None:
        factory = async_sessionmaker(_sqlite_engine, expire_on_commit=False)
        async with factory() as session:
            user = await session.scalar(select(User).where(User.username == "itest_inactive"))
            user.is_active = False
            await session.commit()

    asyncio.run(deactivate())
    resp = _client.post(
        "/api/v1/auth/login", json={"username": "itest_inactive", "password": "passw0rd123"}
    )
    assert resp.status_code == 403


def test_register_validation() -> None:
    bad = {"username": "x", "password": "short"}
    assert _client.post("/api/v1/auth/register", json=bad).status_code == 422
    bad_name = {"username": "bad name!", "password": "passw0rd123"}
    assert _client.post("/api/v1/auth/register", json=bad_name).status_code == 422


def test_register_cannot_escalate_to_admin(cleanup_users: None) -> None:
    """安全：注册时传入 role=admin 必须被忽略，永远创建普通用户。"""
    resp = _register("itest_escalate", role="admin")
    assert resp.status_code == 201
    assert resp.json()["role"] == "user"

    login = _client.post(
        "/api/v1/auth/login", json={"username": "itest_escalate", "password": "passw0rd123"}
    )
    token = login.json()["access_token"]
    me = _client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.json()["role"] == "user"


def test_user_departments_scoping() -> None:
    normal = User(role="user", department="研发部")
    assert user_departments(normal) == ["研发部"]
    no_dept = User(role="user", department="")
    assert user_departments(no_dept) == []
    admin = User(role="admin", department="研发部")
    assert user_departments(admin) is None
