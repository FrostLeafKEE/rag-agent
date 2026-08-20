"""摄入质量报告 API（数据清洗与质量门禁）：查询单篇文档的清洗统计。

权限：与文档管理一致（super_admin / admin，限负责部门）。
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, is_doc_admin, user_visible_departments
from app.db import get_session
from app.models import Document, IngestionReport, User

router = APIRouter(prefix="/api/v1/ingestion", tags=["ingestion"])


@router.get("/report/{doc_id}")
async def ingestion_report(
    doc_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """摄入质量报告（清洗统计）：总块数/过滤数/去重数/平均长度/空页/原因分布。"""
    if not is_doc_admin(user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "文档管理仅管理员可用")
    doc = await session.scalar(select(Document).where(Document.doc_id == doc_id))
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "文档不存在")
    visible = await user_visible_departments(user)
    if visible is not None and doc.department not in visible:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "无权查看该文档")

    rep = await session.scalar(select(IngestionReport).where(IngestionReport.doc_id == doc_id))
    if rep is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "该文档暂无摄入质量报告")

    return {
        "doc_id": rep.doc_id,
        "total_blocks": rep.total_blocks,
        "filtered_blocks": rep.filtered_blocks,
        "dedup_skipped": rep.dedup_skipped,
        "avg_chunk_length": rep.avg_chunk_length,
        "empty_pages": rep.empty_pages,
        "noise_reasons": json.loads(rep.noise_reasons or "{}"),
        "llm_cleaned": rep.llm_cleaned,
        "llm_fallback": rep.llm_fallback,
        "created_at": rep.created_at.isoformat() if rep.created_at else None,
    }
