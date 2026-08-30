"""知识库管理 API（内容分组层，位于文档之上）。

权限（RBAC 对齐）：
- 建/改/删：仅 super_admin；
- 查看：super_admin 全部，部门管理员仅负责部门内的知识库；普通用户 403；
- 上传文档到某知识库：该库 department 必须 ∈ 上传者负责部门（文档权限继承库的部门）。
删除保护：知识库下仍有文档时 409 拒绝（先清空或迁移文档）。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, is_doc_admin, user_visible_departments
from app.api.routes.documents import SAFE_DEPARTMENT
from app.db import get_session
from app.models import Document, KnowledgeBase, User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/kbs", tags=["kbs"])


def _require_super_admin(user: User) -> None:
    if user.role != "super_admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "仅超级管理员可管理知识库")


class KbCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    description: str = Field(default="", max_length=256)
    department: str = Field(min_length=1, max_length=64)


class KbUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    description: str | None = Field(default=None, max_length=256)
    department: str | None = Field(default=None, min_length=1, max_length=64)


async def _get_kb_or_404(kb_id: int, session: AsyncSession) -> KnowledgeBase:
    kb = await session.get(KnowledgeBase, kb_id)
    if kb is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "知识库不存在")
    return kb


@router.get("")
async def list_kbs(
    keyword: str | None = None,
    limit: int = 50,
    offset: int = 0,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """知识库列表（含文档数动态统计；admin 仅见负责部门内的库）。"""
    if not is_doc_admin(user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "仅管理员可查看知识库")
    visible = await user_visible_departments(user)
    limit = max(1, min(limit, 100))
    offset = max(0, offset)

    query = select(KnowledgeBase).order_by(KnowledgeBase.id)
    if visible is not None:
        query = query.where(KnowledgeBase.department.in_(visible or ["__none__"]))
    if keyword:
        pattern = f"%{keyword.strip()}%"
        query = query.where(
            KnowledgeBase.name.ilike(pattern) | KnowledgeBase.description.ilike(pattern)
        )
    total = await session.scalar(select(func.count()).select_from(query.subquery())) or 0
    kbs = list(await session.scalars(query.offset(offset).limit(limit)))

    items = []
    for kb in kbs:
        doc_count = await session.scalar(
            select(func.count())
            .select_from(Document)
            .where(Document.kb_id == kb.id, Document.status != "disabled")
        )
        items.append(
            {
                "id": kb.id,
                "name": kb.name,
                "description": kb.description,
                "department": kb.department,
                "created_by": kb.created_by,
                "created_at": kb.created_at.isoformat() if kb.created_at else None,
                "doc_count": doc_count,
            }
        )
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.post("", status_code=201)
async def create_kb(
    body: KbCreate,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """新增知识库（仅 super_admin；部门即权限归属）。"""
    _require_super_admin(user)
    if not SAFE_DEPARTMENT.match(body.department):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "部门名仅允许中文/字母/数字/下划线/连字符/空格（≤64 字符）",
        )
    exists = await session.scalar(select(KnowledgeBase).where(KnowledgeBase.name == body.name))
    if exists:
        raise HTTPException(status.HTTP_409_CONFLICT, "同名知识库已存在")
    kb = KnowledgeBase(
        name=body.name,
        description=body.description,
        department=body.department,
        created_by=user.username,
    )
    session.add(kb)
    await session.commit()
    await session.refresh(kb)
    logger.info("知识库已创建：%s（dept=%s, by=%s）", kb.name, kb.department, user.username)
    return {
        "id": kb.id,
        "name": kb.name,
        "description": kb.description,
        "department": kb.department,
        "created_by": kb.created_by,
        "doc_count": 0,
    }


@router.put("/{kb_id}")
async def update_kb(
    kb_id: int,
    body: KbUpdate,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """编辑知识库（仅 super_admin）。改名/描述；改部门时同步迁移其下文档归属。"""
    _require_super_admin(user)
    kb = await _get_kb_or_404(kb_id, session)
    if body.department is not None and body.department != kb.department:
        if not SAFE_DEPARTMENT.match(body.department):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "部门名仅允许中文/字母/数字/下划线/连字符/空格（≤64 字符）",
            )
        old = kb.department
        kb.department = body.department
        # 同步迁移其下文档的部门标签（权限继承保持一致）
        docs = await session.scalars(select(Document).where(Document.kb_id == kb.id))
        for d in docs:
            d.department = body.department
        logger.warning("知识库 %s 部门迁移：%s → %s（含其下文档）", kb.name, old, body.department)
    if body.name is not None and body.name != kb.name:
        exists = await session.scalar(
            select(KnowledgeBase).where(KnowledgeBase.name == body.name, KnowledgeBase.id != kb.id)
        )
        if exists:
            raise HTTPException(status.HTTP_409_CONFLICT, "同名知识库已存在")
        kb.name = body.name
    if body.description is not None:
        kb.description = body.description
    await session.commit()
    await log_audit_action(session, user, kb)
    return {
        "id": kb.id,
        "name": kb.name,
        "description": kb.description,
        "department": kb.department,
        "created_by": kb.created_by,
    }


async def log_audit_action(session: AsyncSession, user: User, kb: KnowledgeBase) -> None:
    from app.api.audit import log_audit

    await log_audit(
        session, user.username, "user_admin", resource=f"kb:{kb.id}", detail=f"知识库 {kb.name}"
    )


@router.delete("/{kb_id}")
async def delete_kb(
    kb_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """删除知识库（仅 super_admin）；其下仍有文档时 409 拒绝。"""
    _require_super_admin(user)
    kb = await _get_kb_or_404(kb_id, session)
    doc_count = await session.scalar(
        select(func.count()).select_from(Document).where(Document.kb_id == kb.id)
    )
    if doc_count:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"知识库下仍有 {doc_count} 篇文档，请先删除或迁移后再删除知识库",
        )
    await session.delete(kb)
    await session.commit()
    logger.info("知识库已删除：%s（by=%s）", kb.name, user.username)
    return {"deleted": kb_id, "name": kb.name}
