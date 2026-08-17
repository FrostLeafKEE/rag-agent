"""数据库层：SQLAlchemy async engine / session / 建表（PostgreSQL，rag 库）。

P0 用 create_all 建表；引入 schema 变更后升级为 Alembic 迁移（P1）。
"""

from __future__ import annotations

import logging

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


async def init_db() -> None:
    """建表（幂等）：应用启动时调用。"""
    from app import models  # noqa: F401 - 确保模型注册到 Base.metadata

    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("数据库表结构就绪")


async def get_session() -> AsyncSession:
    """FastAPI 依赖：请求级数据库会话（仅用于短事务；长连接场景用 session_factory）。"""
    async with session_factory() as session:
        yield session
