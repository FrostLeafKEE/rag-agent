"""文档管理 API（PRD FR-02/03/06）：上传（Redis Stream 异步摄入）、列表、删除、任务状态。

上传文件保存到 data/uploads/；摄入任务入队后由独立 worker 进程消费（app.ingestion.worker），
任务状态存 documents 表（跨进程可查）。
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.audit import log_audit
from app.api.deps import get_current_user, is_doc_admin, user_visible_departments
from app.db import get_session
from app.ingestion.indexer import SAFE_DOC_ID
from app.ingestion.parser import SUPPORTED_EXTENSIONS
from app.ingestion.queue import enqueue
from app.models import Document, User
from app.retrieval.base import SAFE_DEPARTMENT

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/documents", tags=["documents"])

UPLOAD_DIR = Path("data/uploads")
MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50MB


def _require_doc_admin(user: User) -> None:
    """文档管理仅 super_admin / admin 可用（RBAC 扩展；普通用户 403）。"""
    if not is_doc_admin(user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "文档管理仅管理员可用")


def _validate_doc_id(doc_id: str) -> str:
    """doc_id 白名单校验（防 Milvus filter 注入）；不合法抛 ValueError。"""
    if not SAFE_DOC_ID.match(doc_id):
        raise ValueError("doc_id 仅允许 1~64 位字母、数字、下划线或连字符")
    return doc_id


# 魔数交叉校验（FIX P1-11）：二进制格式按文件头字节核实，防改名伪装。
# 文本类（md/txt/html）无固定魔数，跳过校验。
_MAGIC_CHECK = {
    ".pdf": (b"%PDF",),
    ".docx": (b"PK",),  # OOXML 为 zip 容器
    ".pptx": (b"PK",),
}


def _check_magic_bytes(ext: str, content: bytes) -> bool:
    """扩展名与文件头魔数是否匹配；无规则（文本类）视为通过。"""
    expected = _MAGIC_CHECK.get(ext)
    if expected is None:
        return True
    return content[:4].startswith(expected[0])


def _slugify(name: str) -> str:
    stem = Path(name).stem
    safe = "".join(c for c in stem if c.isalnum() or c in "-_") or "doc"
    return safe[:64]


class UploadResponse(BaseModel):
    task_id: int
    doc_id: str
    status: str


class TaskStatus(BaseModel):
    task_id: int
    doc_id: str
    status: str
    error: str = ""
    chunk_count: int = 0


@router.post("/upload", status_code=202)
async def upload(
    file: UploadFile,
    request: Request,
    department: str = Form("", max_length=64),
    doc_id: str | None = Form(None),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> UploadResponse:
    _require_doc_admin(user)
    if department and not SAFE_DEPARTMENT.match(department):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "部门名仅允许中文/字母/数字/下划线/连字符/空格（≤64 字符）",
        )
    visible = await user_visible_departments(user)
    if visible is not None and department not in visible:
        # 部门管理员只能上传到负责部门；未分配部门的 admin 无任何上传权限
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"无权上传到部门「{department}」（仅限负责部门）",
        )
    ext = Path(file.filename or "").suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            f"不支持的格式：{ext or '未知'}（支持：{sorted(SUPPORTED_EXTENSIONS)}）",
        )
    # 先在流式读取前按 Content-Length 拒绝超大请求（防内存 DoS）
    content_length = request.headers.get("content-length")
    if content_length and content_length.isdigit() and int(content_length) > MAX_UPLOAD_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "文件超过 50MB 限制")

    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "文件超过 50MB 限制")
    if not _check_magic_bytes(ext, content):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"文件内容与扩展名 {ext} 不匹配")

    final_doc_id = _slugify(file.filename or "doc")
    if doc_id is not None:
        try:
            final_doc_id = _validate_doc_id(doc_id)
        except ValueError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from None

    await asyncio.to_thread(_ensure_upload_dir)
    stored_name = f"{uuid.uuid4().hex}{ext}"
    stored_path = UPLOAD_DIR / stored_name
    # 同步写盘移入线程池，避免阻塞事件循环（FIX P1-7）
    await asyncio.to_thread(stored_path.write_bytes, content)

    doc = await session.scalar(select(Document).where(Document.doc_id == final_doc_id))
    if doc is None:
        doc = Document(
            doc_id=final_doc_id,
            title=file.filename or final_doc_id,
            source_name=file.filename or "",
            department=department,
            status="uploading",
            uploaded_by=user.username,
        )
        session.add(doc)
        await session.commit()
        await session.refresh(doc)
    else:
        # 重传 = 版本更新（FR-04）：仅 super_admin 可覆盖任意文档；
        # 部门管理员只能更新自己上传、且归属负责部门的文档（防文档接管）
        if user.role != "super_admin" and (
            doc.uploaded_by != user.username or doc.department != department
        ):
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "doc_id 已被其他用户/部门占用，请更换 doc_id 或联系管理员",
            )
        doc.status = "uploading"
        doc.error = ""
        await session.commit()

    # 摄入任务入队（Redis Stream，独立 worker 消费）。
    # 入队失败：清理已写文件 + DB 行置 failed，不留孤儿（FIX P1-7）
    try:
        await enqueue(final_doc_id, stored_path, department, file.filename or "", user.username)
    except Exception:
        await asyncio.to_thread(stored_path.unlink, missing_ok=True)
        doc.status = "failed"
        doc.error = "入队失败（Redis 不可用）"
        await session.commit()
        logger.exception("上传入队失败：%s", final_doc_id)
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "摄入队列不可用，请稍后重试"
        ) from None
    await log_audit(session, user.username, "upload", final_doc_id, file.filename or "", request)
    logger.info("上传任务入队：%s（task=%s）", final_doc_id, doc.id)
    return UploadResponse(task_id=doc.id, doc_id=final_doc_id, status="uploading")


def _ensure_upload_dir() -> None:
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


@router.get("/tasks/{task_id}")
async def task_status(
    task_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> TaskStatus:
    """任务状态改查 documents 表（worker 独立进程更新，内存表不可跨进程）。"""
    _require_doc_admin(user)
    doc = await session.get(Document, task_id)
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "任务不存在")
    visible = await user_visible_departments(user)
    if visible is not None and doc.department not in visible:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "无权查看该任务")
    return TaskStatus(
        task_id=task_id,
        doc_id=doc.doc_id,
        status=doc.status,
        error=doc.error or "",
        chunk_count=doc.chunk_count,
    )


@router.get("/departments")
async def my_departments(
    user: User = Depends(get_current_user),
) -> dict:
    """当前用户的可见部门（RBAC）：super_admin → 全部（空列表表示不限）；
    admin → 负责部门；普通用户无文档管理权限（403）。前端上传下拉使用。"""
    _require_doc_admin(user)
    visible = await user_visible_departments(user)
    return {"departments": None if visible is None else visible}


# 搜索字段白名单（防注入：列名不可由用户输入直接拼接）
_SEARCH_FIELDS = {
    "doc_id": Document.doc_id,
    "title": Document.title,
    "department": Document.department,
    "uploader": Document.uploaded_by,
}


@router.get("")
async def list_documents(
    limit: int = 20,
    offset: int = 0,
    status_filter: str | None = None,
    keyword: str | None = None,
    search_field: str = "all",
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """文档列表（RBAC：仅 super_admin/admin；admin 仅可见负责部门）。

    keyword + search_field：按指定字段（或任一字段）模糊匹配，不区分大小写。
    status_filter：按状态精确筛选（uploading/indexed/failed/disabled）。
    """
    _require_doc_admin(user)
    visible = await user_visible_departments(user)
    limit = max(1, min(limit, 100))
    offset = max(0, offset)
    query = select(Document).order_by(Document.created_at.desc())
    if visible is not None:
        query = query.where(Document.department.in_(visible or ["__none__"]))
    if status_filter:
        query = query.where(Document.status == status_filter)
    kw = (keyword or "").strip()
    if kw:
        pattern = f"%{kw}%"
        column = _SEARCH_FIELDS.get(search_field)
        if search_field == "all":
            query = query.where(
                Document.title.ilike(pattern)
                | Document.doc_id.ilike(pattern)
                | Document.department.ilike(pattern)
                | Document.uploaded_by.ilike(pattern)
            )
        elif column is not None:
            query = query.where(column.ilike(pattern))
        else:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, f"非法检索字段：{search_field}"
            )
    docs = list(await session.scalars(query.offset(offset).limit(limit)))
    return {"items": [d.to_dict() for d in docs], "count": len(docs)}


@router.delete("/{doc_id}")
async def delete_document(
    doc_id: str,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """删除文档：移除向量数据并标记 disabled（FR-06，RBAC：仅管理员且限负责部门）。"""
    if not is_doc_admin(user):
        # 越权尝试（敏感写操作）落审计
        await log_audit(
            session, user.username, "denied", resource=doc_id, detail="非管理员访问文档管理"
        )
        await session.commit()
        raise HTTPException(status.HTTP_403_FORBIDDEN, "文档管理仅管理员可用")
    try:
        doc_id = _validate_doc_id(doc_id)
    except ValueError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "文档不存在") from None
    doc = await session.scalar(select(Document).where(Document.doc_id == doc_id))
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "文档不存在")
    visible = await user_visible_departments(user)
    if visible is not None and doc.department not in visible:
        await log_audit(
            session,
            user.username,
            "denied",
            resource=doc_id,
            detail="越权删除文档（超出负责部门）",
        )
        await session.commit()  # 审计先落库再抛错（请求级会话异常后回滚）
        raise HTTPException(status.HTTP_403_FORBIDDEN, "无权删除该文档")

    def _remove_vectors() -> None:
        from app.config import get_settings
        from app.ingestion.embedder import get_embedder
        from app.ingestion.indexer import MilvusIndexer

        settings = get_settings()
        indexer = MilvusIndexer(settings.milvus_uri, settings.milvus_collection, get_embedder().dim)
        indexer.delete_by_doc(doc_id)

    await asyncio.to_thread(_remove_vectors)
    doc.status = "disabled"
    doc.chunk_count = 0
    await log_audit(session, user.username, "delete", resource=doc_id, detail="删除文档")
    await session.commit()
    logger.info("文档已删除：%s（by %s）", doc_id, user.username)
    return {"deleted": doc_id, "status": "disabled"}
