"""RBAC 扩展测试（docs/RBAC_PLAN.md）：三角色 × 管理端点 / 部门分配 / 防自锁。"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.api.deps import get_current_user, user_visible_departments
from app.main import app
from app.models import AdminDepartment, User

_client = TestClient(app)


@pytest.fixture(autouse=True)
def _auth():
    sa = User(id=1, username="boss", role="super_admin", department="", is_active=True)
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
            await session.execute(delete(AdminDepartment))
            await session.execute(delete(User).where(User.username.like("rbac%")))
            await session.commit()

    asyncio.run(clean())


def _seed_users() -> None:
    async def seed() -> None:
        from sqlalchemy import text

        from app.db import session_factory

        async with session_factory() as session:
            await session.execute(text("DELETE FROM users WHERE username LIKE 'rbac%' OR username = 'boss'"))
            session.add_all(
                [
                    User(id=1, username="boss", role="super_admin", department="", password_hash="x"),
                    User(id=20, username="rbac_admin", role="admin", department="", password_hash="x"),
                    User(id=21, username="rbac_user", role="user", department="研发部", password_hash="x"),
                    User(id=22, username="rbac_sa2", role="super_admin", department="", password_hash="x"),
                ]
            )
            await session.commit()

    asyncio.run(seed())


# ---- user_visible_departments 矩阵 ----

def test_visible_departments_matrix(_cleanup: None) -> None:
    _seed_users()

    async def check() -> tuple:
        from app.db import session_factory

        async with session_factory() as session:
            boss = await session.get(User, 1)
            admin = await session.get(User, 20)
            user = await session.get(User, 21)
            # admin 未分配负责部门
            none_dept_admin = await user_visible_departments(admin)
            session.add_all(
                [AdminDepartment(user_id=20, department=d) for d in ["研发部", "财务部"]]
            )
            await session.commit()
            dept_admin = await user_visible_departments(admin)
            plain_user = await user_visible_departments(user)
            return (
                await user_visible_departments(boss),  # super_admin → None
                none_dept_admin,  # admin 无分配 → []
                dept_admin,  # admin 分配后 → [研发部, 财务部]
                plain_user,  # user → [研发部]
            )

    sa, empty, assigned, user = asyncio.run(check())
    assert sa is None
    assert empty == []
    assert sorted(assigned) == ["研发部", "财务部"]
    assert user == ["研发部"]


# ---- 管理端点权限 ----

def test_admin_endpoints_require_super_admin(_cleanup: None) -> None:
    _seed_users()

    def set_user(user: User) -> None:
        app.dependency_overrides[get_current_user] = lambda: user

    # 普通用户：403
    set_user(User(id=21, username="rbac_user", role="user", department="研发部", is_active=True))
    for path in ("/api/v1/admin/users", "/api/v1/admin/audit"):
        assert _client.get(path).status_code == 403
    assert (
        _client.post(
            "/api/v1/admin/users",
            json={"username": "rbac_x", "password": "password1", "role": "user"},
        ).status_code
        == 403
    )

    # 部门管理员：403（无用户管理权）
    set_user(User(id=20, username="rbac_admin", role="admin", department="", is_active=True))
    assert _client.get("/api/v1/admin/users").status_code == 403

    # super_admin：200
    set_user(User(id=1, username="boss", role="super_admin", department="", is_active=True))
    assert _client.get("/api/v1/admin/users").status_code == 200


def test_create_admin_requires_departments(_cleanup: None) -> None:
    resp = _client.post(
        "/api/v1/admin/users",
        json={
            "username": "rbac_new_admin",
            "password": "password1",
            "role": "admin",
            "admin_departments": [],
        },
    )
    assert resp.status_code == 422  # 部门管理员必须指定负责部门


def test_create_admin_with_departments(_cleanup: None) -> None:
    resp = _client.post(
        "/api/v1/admin/users",
        json={
            "username": "rbac_new_admin",
            "password": "password1",
            "role": "admin",
            "admin_departments": ["研发部", "财务部"],
        },
    )
    assert resp.status_code == 201
    user_id = resp.json()["id"]

    async def check() -> list[str]:
        from app.db import session_factory

        async with session_factory() as session:
            return list(
                (
                    await session.execute(
                        select(AdminDepartment.department).where(
                            AdminDepartment.user_id == user_id
                        )
                    )
                ).scalars()
            )

    assert sorted(asyncio.run(check())) == ["研发部", "财务部"]


def test_set_departments_overwrite(_cleanup: None) -> None:
    _seed_users()
    # 先分配
    r1 = _client.put(
        "/api/v1/admin/users/20/departments", json={"departments": ["研发部", "人事部"]}
    )
    assert r1.status_code == 200
    # 覆盖式更新
    r2 = _client.put(
        "/api/v1/admin/users/20/departments", json={"departments": ["财务部"]}
    )
    assert r2.status_code == 200
    got = _client.get("/api/v1/admin/users/20/departments").json()
    assert got["departments"] == ["财务部"]


def test_set_departments_requires_admin_role(_cleanup: None) -> None:
    _seed_users()
    resp = _client.put(
        "/api/v1/admin/users/21/departments", json={"departments": ["研发部"]}
    )
    assert resp.status_code == 422  # 21 是普通用户


def test_set_departments_rejects_empty(_cleanup: None) -> None:
    _seed_users()
    resp = _client.put("/api/v1/admin/users/20/departments", json={"departments": []})
    assert resp.status_code == 422


# ---- 防自锁 ----

def test_cannot_demote_self(_cleanup: None) -> None:
    resp = _client.patch(
        "/api/v1/admin/users/1", json={"role": "user"}
    )  # 当前用户 id=1（boss）
    assert resp.status_code == 400


def test_cannot_disable_self(_cleanup: None) -> None:
    resp = _client.patch("/api/v1/admin/users/1", json={"is_active": False})
    assert resp.status_code == 400


def test_cannot_demote_last_super_admin(_cleanup: None) -> None:
    # 库中仅 boss（id=1）一个 super_admin
    resp = _client.patch(
        "/api/v1/admin/users/1", json={"role": "admin"}
    )
    assert resp.status_code == 400  # 被"必须保留一个 super_admin"拦截（先于自锁校验）


def test_can_demote_super_admin_when_another_exists(_cleanup: None) -> None:
    _seed_users()  # 22 是第二个 super_admin
    resp = _client.patch("/api/v1/admin/users/22", json={"role": "admin"})
    assert resp.status_code == 200