"""数据概览统计 API（管理员首页）：用户/部门/文档/提问统计 + 7 天趋势 + 部门占比。

权限：仅 super_admin / admin（运营统计属管理功能，普通用户 403）。
可见范围：文档相关统计按 RBAC 部门范围过滤（super_admin 全部，admin 限负责部门）；
提问趋势按会话所属用户的部门过滤，口径与权限模型一致。
"""

from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, user_visible_departments
from app.db import get_session
from app.models import ChatMessage, ChatSession, Document, User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/stats", tags=["stats"])


def _require_stats_view(user: User) -> None:
    if user.role not in ("super_admin", "admin"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "数据概览仅管理员可用")


def _local_day_start(days_ago: int = 0) -> datetime:
    """本地时区的 N 天前零点（aware），用于"今日/近7天"边界。"""
    now = datetime.now().astimezone()
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return start - timedelta(days=days_ago)


@router.get("/overview")
async def stats_overview(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """概览统计：用户/部门/文档/今日提问 + 近7天趋势 + 部门文档占比。"""
    _require_stats_view(user)
    visible = await user_visible_departments(user)

    # ---- 用户总数（可见范围内活跃用户）----
    user_query = select(func.count()).select_from(User).where(User.is_active.is_(True))
    if visible is not None:
        user_query = user_query.where(User.department.in_(visible or ["__none__"]))
    user_count = await session.scalar(user_query) or 0

    # ---- 文档统计（按部门分组：总数 / 部门数 / 分块总数 / 占比）----
    doc_query = (
        select(
            Document.department,
            func.count().label("cnt"),
            func.coalesce(func.sum(Document.chunk_count), 0).label("chunks"),
        )
        .where(Document.status != "disabled")
        .group_by(Document.department)
    )
    if visible is not None:
        doc_query = doc_query.where(Document.department.in_(visible or ["__none__"]))
    dept_rows = (await session.execute(doc_query)).all()
    doc_count = sum(r.cnt for r in dept_rows)
    doc_by_department = sorted(
        ({"department": r.department or "未分类", "count": r.cnt} for r in dept_rows),
        key=lambda d: d["count"],
        reverse=True,
    )

    # ---- 提问统计（近 7 天，按会话所属用户的部门过滤）----
    week_start = _local_day_start(days_ago=6)
    msg_query = (
        select(ChatMessage.created_at)
        .join(ChatSession, ChatMessage.session_id == ChatSession.id)
        .join(User, ChatSession.user_id == User.id)
        .where(ChatMessage.role == "user", ChatMessage.created_at >= week_start)
    )
    if visible is not None:
        msg_query = msg_query.where(User.department.in_(visible or ["__none__"]))
    created_ats = (await session.execute(msg_query)).scalars().all()

    # 按本地日期分组（缺数日补 0）
    counts_by_day: Counter = Counter()
    for dt in created_ats:
        counts_by_day[dt.astimezone().date()] += 1
    trend = []
    for i in range(6, -1, -1):
        day = _local_day_start(days_ago=i).date()
        trend.append({"date": day.strftime("%m-%d"), "count": counts_by_day.get(day, 0)})
    today_questions = trend[-1]["count"]

    return {
        "user_count": int(user_count),
        "dept_count": len(dept_rows),
        "doc_count": doc_count,
        "chunk_count": int(sum(r.chunks for r in dept_rows)),
        "today_questions": today_questions,
        "trend_7d": trend,
        "doc_by_department": doc_by_department,
    }
