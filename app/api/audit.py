"""审计工具（FR-34）：敏感事件落 audit_logs 表。"""

from __future__ import annotations

import logging

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog

logger = logging.getLogger(__name__)

# 允许的 action 集合（防注入：只写白名单值）
VALID_ACTIONS = {
    "register",
    "login",
    "login_failed",
    "upload",
    "delete",
    "denied",
    "user_admin",
    "redact",
}


async def log_audit(
    session: AsyncSession,
    user: str,
    action: str,
    resource: str = "",
    detail: str = "",
    request: Request | None = None,
) -> None:
    """写审计日志（失败不影响主流程）。"""
    if action not in VALID_ACTIONS:
        logger.warning("非法审计动作被拒绝：%s", action)
        return
    ip = ""
    if request:
        ip = request.client.host if request.client else ""
    session.add(
        AuditLog(
            user=user,
            action=action,
            resource=str(resource)[:128],
            detail=str(detail)[:512],
            ip=ip,
        )
    )
    try:
        await session.commit()
    except Exception:
        logger.exception("审计日志写入失败")
        await session.rollback()
