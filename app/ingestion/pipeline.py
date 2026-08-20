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
from app.ingestion.cleaning import (
    _text_of,
    build_ingestion_report,
    clean_chunks,
    content_hash_of,
)
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
    existing_hashes: set[str] | None = None,  # 已入库文档 content_hash 集合（文档级去重）
    on_report=None,  # noqa: ANN001  (report: dict, doc_hash: str|None, duplicate: bool) -> None
) -> int:
    """同步摄入核心（解析/嵌入/Milvus 均为阻塞调用）：返回分块数。

    管道：解析 → 分块 → 【规则清洗】→【LLM 清洗（可选）】→ 内容去重 → 脱敏 → 嵌入。
    清洗报告经 on_report 回调上报（上层落 IngestionReport）；文档级内容重复时
    返回 0 且不写 Milvus（duplicate=True 经回调标记）。
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

    # 数据清洗与质量门禁：规则清洗 → LLM 清洗（可选）→ 内容去重
    kept, stats, _ = clean_chunks(
        chunks,
        enable_llm=settings.enable_llm_cleaning,
        llm_min_len=settings.llm_cleaning_min_len,
    )
    # 文档级去重：清洗后全文 sha1 与已入库文档比对（解决"A.docx 与 A.pdf 同内容"）
    doc_hash = content_hash_of("\n".join(_text_of(c) for c in kept)) if kept else ""
    duplicate = bool(doc_hash and existing_hashes and doc_hash in existing_hashes)
    if duplicate:
        stats["dedup_skipped"] += len(kept)
        stats["doc_duplicate"] = True
        kept = []
        logger.warning("文档 %s 内容与已入库文档重复，跳过索引（hash=%s）", doc_id, doc_hash)
    if not kept and not duplicate:
        raise ValueError(f"文档清洗后无有效内容：{path.name}")
    if on_report is not None:
        on_report(build_ingestion_report(stats), doc_hash, duplicate)
    if duplicate:
        return 0

    hit_rules: set[str] = set()
    engine = get_engine(settings.redaction_config)
    for chunk in kept:
        masked, hits = engine.redact(chunk.content)
        if hits:
            chunk.content = masked
            hit_rules.update(hits)
    if hit_rules:
        rules = sorted(hit_rules)
        logger.warning("文档 %s 含敏感信息，已脱敏（规则：%s）", doc_id, rules)
        if on_redact_hit is not None:
            on_redact_hit(rules)

    vectors = embedder.embed([c.content for c in kept])
    # 先删后写：重传变短的文档时清掉旧序号残留的块（防止过期内容仍可检索）
    indexer.delete_by_doc(doc_id)
    indexer.upsert(kept, vectors)
    logger.info("摄入完成：%s → %d 个分块", doc_id, len(kept))
    return len(chunks)


async def _load_existing_hashes(session: AsyncSession) -> set[str]:
    """已入库文档 content_hash 集合（内容级去重查询键，B-Tree 索引加速）。"""
    rows = await session.scalars(
        select(Document.content_hash).where(Document.content_hash.is_not(None))
    )
    return {h for h in rows if h}


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

    同一 doc_id 重复摄入幂等（upsert 覆盖），并同步更新 documents 元数据；
    摄入质量报告落 IngestionReport；内容重复文档记录 content_hash 并跳过索引。
    """
    from app.models import IngestionReport

    redact_hits: list[str] = []
    existing_hashes = await _load_existing_hashes(session)
    report_holder: dict = {}

    def _on_redact(rules: list[str]) -> None:
        redact_hits.extend(rules)

    def _on_report(report: dict, doc_hash: str | None, duplicate: bool) -> None:
        report_holder.update(report=report, doc_hash=doc_hash, duplicate=duplicate)

    chunk_count = await asyncio.to_thread(
        ingest_document_sync,
        path,
        doc_id,
        department=department,
        chunk_size=chunk_size,
        overlap=overlap,
        on_redact_hit=_on_redact,
        existing_hashes=existing_hashes,
        on_report=_on_report,
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
    if report_holder:
        doc.content_hash = report_holder["doc_hash"]
        if report_holder["duplicate"]:
            doc.error = "内容与已有文档重复，未建立索引"
        # 摄入质量报告 upsert（重摄入覆盖）
        rep = await session.scalar(select(IngestionReport).where(IngestionReport.doc_id == doc_id))
        if rep is None:
            rep = IngestionReport(doc_id=doc_id)
            session.add(rep)
        for key, value in report_holder["report"].items():
            setattr(rep, key, value)
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
