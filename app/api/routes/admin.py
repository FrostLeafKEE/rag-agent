"""管理员 API（仅 super_admin）：用户管理 + 部门管理员负责部门分配 + 审计查询。

RBAC 扩展（docs/RBAC_PLAN.md）：
- super_admin：全部用户/角色/部门分配/审计；
- admin（部门管理员）：由 super_admin 创建并分配负责部门（admin_departments）；
- 普通用户无管理权限（403）。
"""

from __future__ import annotations

import csv
import io
import logging

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.audit import VALID_ACTIONS, log_audit
from app.api.deps import get_current_user
from app.api.security import hash_password
from app.db import get_session
from app.models import AdminDepartment, AuditLog, User
from app.retrieval.base import SAFE_DEPARTMENT

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])

VALID_ROLES = ("super_admin", "admin", "user")


def _check_departments(departments: list[str]) -> None:
    """部门名白名单校验（防 Milvus filter 注入，FIX P0-1）。"""
    for dept in departments:
        if not SAFE_DEPARTMENT.match(dept):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"非法部门名：{dept!r}（仅允许中文/字母/数字/下划线/连字符/空格）",
            )


def _require_super_admin(user: User) -> None:
    if user.role != "super_admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "需要超级管理员权限")


class UserCreate(BaseModel):
    username: str = Field(min_length=2, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    password: str = Field(min_length=8, max_length=128)
    department: str = Field(default="", max_length=64)
    role: str = Field(default="user", pattern="^(super_admin|admin|user)$")
    admin_departments: list[str] = Field(default_factory=list, max_length=50)


class UserUpdate(BaseModel):
    role: str | None = Field(default=None, pattern="^(super_admin|admin|user)$")
    department: str | None = Field(default=None, max_length=64)
    is_active: bool | None = None


class DepartmentsUpdate(BaseModel):
    departments: list[str] = Field(max_length=50)


@router.post("/users", status_code=201)
async def create_user(
    body: UserCreate,
    admin: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    _require_super_admin(admin)
    exists = await session.scalar(select(User).where(User.username == body.username))
    if exists:
        raise HTTPException(status.HTTP_409_CONFLICT, "用户名已存在")
    if body.role == "admin" and not body.admin_departments:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "创建部门管理员必须指定至少一个负责部门",
        )
    _check_departments(body.admin_departments)
    user = User(
        username=body.username,
        password_hash=hash_password(body.password),
        department=body.department,
        role=body.role,
    )
    session.add(user)
    await session.flush()
    if body.role == "admin":
        session.add_all(
            AdminDepartment(user_id=user.id, department=d)
            for d in dict.fromkeys(body.admin_departments)
        )
    await session.commit()
    await session.refresh(user)
    await log_audit(
        session,
        admin.username,
        "user_admin",
        f"创建用户 {user.username}",
        f"role={body.role}, departments={body.admin_departments}",
    )
    return user.to_dict()


@router.get("/users")
async def list_users(
    admin: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    _require_super_admin(admin)
    rows = list((await session.scalars(select(User).order_by(User.id))).all())
    # 附带每个 admin 的负责部门（前端用户管理页展示）
    admin_ids = [u.id for u in rows if u.role == "admin"]
    dept_map: dict[int, list[str]] = {}
    if admin_ids:
        rows_dept = await session.scalars(
            select(AdminDepartment).where(AdminDepartment.user_id.in_(admin_ids))
        )
        for d in rows_dept:
            dept_map.setdefault(d.user_id, []).append(d.department)
    items = []
    for u in rows:
        item = u.to_dict()
        item["admin_departments"] = dept_map.get(u.id, [])
        items.append(item)
    return {"items": items}


@router.patch("/users/{user_id}")
async def update_user(
    user_id: int,
    body: UserUpdate,
    admin: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    _require_super_admin(admin)
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "用户不存在")
    # 防自锁：不能修改自己的角色 / 停用自己 / 变更自己的部门
    if user.id == admin.id and (body.role is not None or body.is_active is False):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "不能修改自己的角色或停用自己")
    # 防锁死：不能把最后一个 super_admin 降级/停用
    if user.role == "super_admin" and (body.role is not None or body.is_active is False):
        remaining = await session.scalar(
            select(User.id).where(
                User.role == "super_admin", User.is_active.is_(True), User.id != user.id
            )
        )
        if remaining is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "必须保留至少一个超级管理员")
    if body.role is not None:
        user.role = body.role
    if body.department is not None:
        user.department = body.department
    if body.is_active is not None:
        user.is_active = body.is_active
    await session.commit()
    await log_audit(
        session,
        admin.username,
        "user_admin",
        f"更新用户 {user.username}",
        str(body.model_dump(exclude_none=True)),
    )
    return user.to_dict()


@router.get("/users/{user_id}/departments")
async def get_admin_departments(
    user_id: int,
    admin: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """部门管理员的负责部门集合（super_admin 专属）。"""
    _require_super_admin(admin)
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "用户不存在")
    rows = await session.scalars(
        select(AdminDepartment.department).where(AdminDepartment.user_id == user_id)
    )
    return {"user_id": user_id, "departments": list(rows)}


@router.put("/users/{user_id}/departments")
async def set_admin_departments(
    user_id: int,
    body: DepartmentsUpdate,
    admin: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """覆盖式设置部门管理员的负责部门集合（super_admin 专属）。"""
    _require_super_admin(admin)
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "用户不存在")
    if user.role != "admin":
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "仅部门管理员（admin）可分配负责部门",
        )
    if not body.departments:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "负责部门不能为空（至少一个）")
    _check_departments(body.departments)
    await session.execute(delete(AdminDepartment).where(AdminDepartment.user_id == user_id))
    session.add_all(
        AdminDepartment(user_id=user_id, department=d) for d in dict.fromkeys(body.departments)
    )
    await session.commit()
    await log_audit(
        session,
        admin.username,
        "user_admin",
        f"分配部门管理员 {user.username} 的负责部门",
        ",".join(body.departments),
    )
    return {"user_id": user_id, "departments": body.departments}


@router.get("/audit")
async def list_audit(
    limit: int = 50,
    offset: int = 0,
    action: str | None = None,
    admin: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """审计日志查询（FR-34，super_admin 专属；R7：支持按 action 过滤与分页）。"""
    _require_super_admin(admin)
    query = select(AuditLog).order_by(AuditLog.id.desc())
    if action:
        if action not in VALID_ACTIONS:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"非法 action：{action}")
        query = query.where(AuditLog.action == action)
    total = await session.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = await session.scalars(query.offset(max(0, offset)).limit(max(1, min(limit, 500))))
    return {
        "items": [a.to_dict() for a in rows],
        "total": total,
        "limit": max(1, min(limit, 500)),
        "offset": max(0, offset),
    }


@router.get("/audit/export")
async def export_audit(
    admin: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """审计日志 CSV 导出（R7：合规审计用，最多 5000 条）。"""
    _require_super_admin(admin)
    rows = await session.scalars(select(AuditLog).order_by(AuditLog.id.desc()).limit(5000))
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["id", "user", "action", "resource", "detail", "ip", "created_at"])
    for a in rows:
        writer.writerow(
            [
                a.id,
                a.user,
                a.action,
                a.resource,
                a.detail,
                a.ip,
                a.created_at.isoformat() if a.created_at else "",
            ]
        )
    return Response(
        content=buffer.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="audit_log.csv"'},
    )
