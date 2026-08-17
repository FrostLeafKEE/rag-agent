"""数据库层：SQLAlchemy async engine / session / 建表（PostgreSQL，rag 库）。

Schema 演进：Alembic（R4/ROADMAP）——生产/已有库用 `alembic upgrade head` 迁移，
初始迁移已 stamp 基线；init_db 仅对**未纳入 Alembic 管理**的库（如测试 SQLite）
执行 create_all 兜底，避免双轨漂移。
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator

from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


_engine = create_async_engine(
    get_settings().postgres_dsn,
    echo=False,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
)
session_factory = async_sessionmaker(_engine, expire_on_commit=False)


async def _has_alembic_version(conn) -> bool:  # noqa: ANN001
    return await conn.run_sync(lambda sync_conn: inspect(sync_conn).has_table("alembic_version"))


async def init_db() -> None:
    """建表引导（幂等）：

    - 已纳入 Alembic 管理（存在 alembic_version 表）→ 提示走迁移，不 create_all；
    - 未纳入（新库/测试 SQLite）→ create_all 兜底。
    """
    from app import models  # noqa: F401 - 确保模型注册到 Base.metadata

    async with _engine.begin() as conn:
        if await _has_alembic_version(conn):
            logger.info(
                "数据库已由 Alembic 管理（alembic_version 存在），"
                "schema 变更请执行 `uv run alembic upgrade head`"
            )
            return
        await conn.run_sync(Base.metadata.create_all)
        logger.info("数据库表结构就绪（create_all 兜底）")


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI 依赖：请求级数据库会话（仅用于短事务；长连接场景用 session_factory）。"""
    async with session_factory() as session:
        yield session
