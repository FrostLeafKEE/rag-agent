"""认证 API：注册 / 登录 / 当前用户（FR-32，P0 开放注册，P1 收口为管理员创建）。"""

from __future__ import annotations

import logging

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.audit import log_audit
from app.api.deps import get_current_user
from app.api.security import create_access_token, hash_password, verify_password
from app.config import get_settings
from app.db import get_session
from app.models import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

# 登录暴力破解防护：单用户名 5 分钟窗口内失败 5 次锁定（Redis 计数）
_LOGIN_FAIL_LIMIT = 5
_LOGIN_FAIL_WINDOW = 300


async def _login_lock_key(username: str) -> str:
    return f"login:lock:{username}"


async def _login_fail_key(username: str) -> str:
    return f"login:fail:{username}"


async def _redis() -> aioredis.Redis:
    return aioredis.from_url(
        get_settings().redis_url, decode_responses=True, socket_timeout=None
    )


async def login_is_locked(username: str) -> bool:
    """该用户名是否处于锁定状态；Redis 不可用时放行（登录不因防护不可用而瘫痪）。"""
    try:
        client = await _redis()
        try:
            return await client.get(await _login_lock_key(username)) is not None
        finally:
            await client.aclose()
    except Exception:  # noqa: BLE001
        logger.warning("登录锁定检查失败（Redis 不可用？），放行")
        return False


async def record_login_failure(username: str) -> None:
    """失败计数；达到阈值设置锁定。Redis 不可用时静默降级。"""
    try:
        client = await _redis()
        try:
            key = await _login_fail_key(username)
            count = await client.incr(key)
            if count == 1:
                await client.expire(key, _LOGIN_FAIL_WINDOW)
            if count >= _LOGIN_FAIL_LIMIT:
                await client.set(
                    await _login_lock_key(username), "1", ex=_LOGIN_FAIL_WINDOW
                )
                await client.delete(key)
        finally:
            await client.aclose()
    except Exception:  # noqa: BLE001
        logger.warning("登录失败计数失败（Redis 不可用？）")


async def clear_login_failures(username: str) -> None:
    """登录成功后清除计数与锁定。"""
    try:
        client = await _redis()
        try:
            await client.delete(await _login_lock_key(username))
            await client.delete(await _login_fail_key(username))
        finally:
            await client.aclose()
    except Exception:  # noqa: BLE001
        pass


class RegisterRequest(BaseModel):
    username: str = Field(min_length=2, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    password: str = Field(min_length=8, max_length=128)
    # 部门名白名单（防 Milvus filter 注入，FIX P0-1）：中文/字母/数字/下划线/连字符/空格
    department: str = Field(
        min_length=1, max_length=64, pattern=r"^[\w\u4e00-\u9fff \-]{1,64}$"
    )  # 必填：空部门=全库不可见（RBAC 兜底）
    # 注意：不接受 role 字段——注册永远创建普通用户，admin 只能由已认证管理员授予（防提权）


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


@router.post("/register", status_code=201)
async def register(
    body: RegisterRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    exists = await session.scalar(select(User).where(User.username == body.username))
    if exists:
        raise HTTPException(status.HTTP_409_CONFLICT, "用户名已存在")
    if len(body.password.encode("utf-8")) > 72:
        # bcrypt 只取前 72 字节，超长静默截断会导致不同密码等价
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "密码过长（bcrypt 上限 72 字节）")

    user = User(
        username=body.username,
        password_hash=hash_password(body.password),
        department=body.department,
        role="user",  # 注册固定为普通用户；admin 由管理员授予（防提权）
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    await log_audit(session, body.username, "register", detail="新用户注册", request=request)
    logger.info("新用户注册：%s（dept=%s）", user.username, user.department)
    return user.to_dict()


@router.post("/login")
async def login(
    body: LoginRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> TokenResponse:
    if await login_is_locked(body.username):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "失败次数过多，账号已临时锁定，请稍后再试")
    user = await session.scalar(select(User).where(User.username == body.username))
    if user is None or not verify_password(body.password, user.password_hash):
        await record_login_failure(body.username)
        await log_audit(session, body.username, "login_failed", detail="密码错误", request=request)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "用户名或密码错误")
    if not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "账号已停用")
    await clear_login_failures(user.username)
    await log_audit(session, user.username, "login", detail="登录成功", request=request)
    return TokenResponse(access_token=create_access_token(user.to_dict()))


@router.get("/me")
async def me(user: User = Depends(get_current_user)) -> dict:
    return user.to_dict()
