"""API 依赖注入：当前用户解析（JWT → 用户）。

注意：get_current_user 使用独立短会话（认证后立即释放连接），
避免 SSE 流式响应期间长期占用数据库连接（高并发下连接池耗尽）。
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer

from app.api.security import SecurityError, decode_access_token
from app.db import session_factory
from app.models import User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")


async def get_current_user(token: str = Depends(oauth2_scheme)) -> User:
    """从 Bearer token 解析并校验当前用户；失败抛 401。"""
    credentials_error = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="认证失败：请先登录",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = decode_access_token(token)
        user_id = int(payload["sub"])
    except (SecurityError, KeyError, ValueError):
        raise credentials_error from None

    async with session_factory() as session:
        user = await session.get(User, user_id)
    if user is None or not user.is_active:
        raise credentials_error
    return user


def user_departments(user: User) -> list[str] | None:
    """用户可检索的部门范围（同步版，仅 user/admin 场景用；RBAC 扩展后主入口为
    user_visible_departments，admin 的负责部门需查库）。"""
    if user.role in ("super_admin", "admin"):
        return None
    return [user.department] if user.department else []


async def user_visible_departments(user: User) -> list[str] | None:
    """RBAC 统一可见范围入口：
    - super_admin → None（不限）
    - admin → AdminDepartment 负责部门集合（空集 = 零可见）
    - user → [user.department]（空部门 = 零可见）

    文档管理（列表/删除/上传）与 QA 检索过滤共用此入口。
    """
    if user.role == "super_admin":
        return None
    if user.role == "admin":
        from sqlalchemy import select

        from app.db import session_factory
        from app.models import AdminDepartment

        async with session_factory() as session:
            rows = (
                await session.execute(
                    select(AdminDepartment.department).where(
                        AdminDepartment.user_id == user.id
                    )
                )
            ).scalars().all()
        return list(rows)
    return [user.department] if user.department else []


def is_doc_admin(user: User) -> bool:
    """文档管理权限：super_admin / admin 可用。"""
    return user.role in ("super_admin", "admin")
