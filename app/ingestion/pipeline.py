"""摄入管线核心：文件 → 解析 → 分块 → 嵌入 → 写 Milvus → 登记元数据。

CLI（ingest.py）与上传 API（documents.py）共用本模块，保证两条路径行为一致。
同步核心 ingest_document_sync 供线程池调用，避免阻塞事件循环。
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.ingestion.chunker import chunk_document
from app.ingestion.embedder import get_embedder
from app.ingestion.indexer import MilvusIndexer
from app.ingestion.parser import parse_document
from app.models import Document
from app.security.redaction import get_engine

logger = logging.getLogger(__name__)


def ingest_document_sync(
    path: Path,
    doc_id: str,
    *,
    department: str = "",
    chunk_size: int = 800,
    overlap: int = 100,
    on_redact_hit=None,  # noqa: ANN001  (rules: list[str]) -> None，脱敏命中回调
) -> int:
    """同步摄入核心（解析/嵌入/Milvus 均为阻塞调用）：返回分块数。

    分块在嵌入/写索引前统一过脱敏（FR-36）：敏感信息不进 Milvus、不被检索展示；
    命中经回调上报（上层写审计日志）。
    """
    settings = get_settings()
    embedder = get_embedder()
    indexer = MilvusIndexer(settings.milvus_uri, settings.milvus_collection, embedder.dim)
    indexer.ensure_collection()

    logger.info("解析：%s", path)
    blocks = parse_document(path)
    chunks = chunk_document(
        blocks,
        doc_id=doc_id,
        chunk_size=chunk_size,
        overlap=overlap,
        department=department,
    )
    if not chunks:
        raise ValueError(f"文档未产生任何分块：{path.name}")

    hit_rules: set[str] = set()
    engine = get_engine(settings.redaction_config)
    for chunk in chunks:
        masked, hits = engine.redact(chunk.content)
        if hits:
            chunk.content = masked
            hit_rules.update(hits)
    if hit_rules:
        rules = sorted(hit_rules)
        logger.warning("文档 %s 含敏感信息，已脱敏（规则：%s）", doc_id, rules)
        if on_redact_hit is not None:
            on_redact_hit(rules)

    vectors = embedder.embed([c.content for c in chunks])
    # 先删后写：重传变短的文档时清掉旧序号残留的块（防止过期内容仍可检索）
    indexer.delete_by_doc(doc_id)
    indexer.upsert(chunks, vectors)
    logger.info("摄入完成：%s → %d 个分块", doc_id, len(chunks))
    return len(chunks)


async def ingest_document(
    session: AsyncSession,
    path: Path,
    doc_id: str,
    *,
    department: str = "",
    title: str = "",
    source_name: str = "",
    uploaded_by: str = "",
    chunk_size: int = 800,
    overlap: int = 100,
) -> int:
    """异步摄入：同步核心跑线程池，主协程登记元数据。

    同一 doc_id 重复摄入幂等（upsert 覆盖），并同步更新 documents 元数据。
    """
    redact_hits: list[str] = []

    def _on_redact(rules: list[str]) -> None:
        redact_hits.extend(rules)

    chunk_count = await asyncio.to_thread(
        ingest_document_sync,
        path,
        doc_id,
        department=department,
        chunk_size=chunk_size,
        overlap=overlap,
        on_redact_hit=_on_redact,
    )

    doc = await session.scalar(select(Document).where(Document.doc_id == doc_id))
    if doc is None:
        doc = Document(doc_id=doc_id)
        session.add(doc)
    doc.title = title or path.stem
    doc.source_name = source_name or path.name
    doc.department = department
    doc.status = "indexed"
    doc.chunk_count = chunk_count
    doc.error = ""
    doc.uploaded_by = uploaded_by
    if redact_hits:
        # 审计：敏感信息命中（FR-36），resource 与 detail 记录文档与规则
        from app.api.audit import log_audit

        await log_audit(
            session,
            uploaded_by or "system",
            "redact",
            resource=doc_id,
            detail=",".join(redact_hits),
        )
    await session.commit()
    return chunk_count
