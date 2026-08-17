"""pytest 全局配置。

- Windows：asyncpg 在 ProactorEventLoop 下存在跨 loop 竞态，统一用 SelectorEventLoop。
- 测试数据库：SQLite（aiosqlite，无 loop 绑定问题），覆盖 app 的 get_session 依赖，
  避免 asyncpg 连接池在测试多事件循环场景下崩溃（真实 PostgreSQL 路径由 uvicorn 实测覆盖）。
"""

import asyncio
import sys
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import Base, get_session
from app.main import app

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

_TEST_DB = Path(__file__).parent / "_test.db"


@pytest.fixture(scope="session", autouse=True)
def _sqlite_engine():
    if _TEST_DB.exists():
        _TEST_DB.unlink()
    engine = create_async_engine(f"sqlite+aiosqlite:///{_TEST_DB}")
    import asyncio as _asyncio

    async def _create() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    _asyncio.run(_create())
    yield engine
    _asyncio.run(engine.dispose())


@pytest.fixture(autouse=True)
def _override_db(_sqlite_engine, monkeypatch: pytest.MonkeyPatch):
    """把 app 的数据库依赖切换到 SQLite（每个测试独立会话，结束后清理）。

    session_factory 在使用方模块是 import 时绑定的引用，需逐处替换
    （认证、消息保存等独立短会话路径）。
    """
    factory = async_sessionmaker(_sqlite_engine, expire_on_commit=False)

    async def _get_session():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_session] = _get_session
    # deps.py 是模块级绑定（import 时）；sessions.py 是函数内 import（调用时解析）
    monkeypatch.setattr("app.api.deps.session_factory", factory)
    monkeypatch.setattr("app.db.session_factory", factory)
    yield
    app.dependency_overrides.pop(get_session, None)
