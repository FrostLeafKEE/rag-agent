"""摄入任务队列（Redis Stream）：上传与摄入解耦，失败自动重投（FR-02）。

队列：rag:ingestion（Stream）；消费组 ingestion-workers。
消息：{doc_id, stored_path, department, source_name, username, retry}
"""

from __future__ import annotations

import logging
from pathlib import Path

import redis.asyncio as aioredis

from app.config import get_settings

logger = logging.getLogger(__name__)

STREAM = "rag:ingestion"
GROUP = "ingestion-workers"
MAX_RETRIES = 3


async def _redis() -> aioredis.Redis:
    # redis-py 8.x 默认 socket_timeout=5s，会打断 XREADGROUP 的 BLOCK 等待；
    # 阻塞读需显式 None（连接失败仍由 socket_connect_timeout=5 兜底）
    return aioredis.from_url(
        get_settings().redis_url,
        decode_responses=True,
        socket_timeout=None,
        socket_connect_timeout=5,
    )


async def enqueue(
    doc_id: str,
    stored_path: Path,
    department: str,
    source_name: str,
    username: str,
    retry: int = 0,
) -> None:
    """发布摄入任务。"""
    client = await _redis()
    try:
        await client.xadd(
            STREAM,
            {
                "doc_id": doc_id,
                "stored_path": str(stored_path),
                "department": department,
                "source_name": source_name,
                "username": username,
                "retry": str(retry),
            },
        )
        logger.info("摄入任务已入队：%s（retry=%d）", doc_id, retry)
    finally:
        await client.aclose()


async def _ensure_group(client: aioredis.Redis) -> None:
    try:
        await client.xgroup_create(STREAM, GROUP, id="0", mkstream=True)
    except Exception:
        pass  # 组已存在


async def worker_loop(process_one) -> None:  # noqa: ANN001
    """独立 worker 消费循环：处理成功 XACK；失败重投（≤MAX_RETRIES），超限标记失败。"""
    client = await _redis()
    await _ensure_group(client)
    logger.info("worker 启动，监听 %s（consumer=%s）", STREAM, "worker-1")
    while True:
        try:
            items = await client.xreadgroup(
                GROUP, "worker-1", {STREAM: ">"}, count=1, block=5000
            )
        except Exception:
            logger.exception("读取队列失败，5s 后重试")
            import asyncio

            await asyncio.sleep(5)
            continue
        for _, messages in items:
            for msg_id, payload in messages:
                retry = int(payload.get("retry", 0) or 0)
                try:
                    await process_one(payload)
                    await client.xack(STREAM, GROUP, msg_id)
                except Exception:
                    logger.exception("任务处理失败：%s", payload.get("doc_id"))
                    if retry < MAX_RETRIES:
                        await client.xadd(STREAM, {**payload, "retry": str(retry + 1)})
                        logger.warning("任务重投：%s（第 %d 次）", payload.get("doc_id"), retry + 1)
                    await client.xack(STREAM, GROUP, msg_id)  # 原消息出队


async def process_ingestion(payload: dict) -> None:
    """单条任务处理：更新状态 → 摄入 → indexed/failed（由 ingest_document 内部更新）。"""
    from sqlalchemy import select

    from app.db import get_session
    from app.ingestion.pipeline import ingest_document
    from app.models import Document

    doc_id = payload["doc_id"]
    async for session in get_session():
        doc = await session.scalar(select(Document).where(Document.doc_id == doc_id))
        if doc is not None and doc.status == "disabled":
            # 删除竞态（FR-06/FR-02）：已删除文档的任务直接丢弃，防止向量"复活"
            logger.info("丢弃已删除文档的摄入任务：%s", doc_id)
            return
        if doc is not None:
            doc.status = "processing"
            await session.commit()
        await ingest_document(
            session,
            Path(payload["stored_path"]),
            doc_id,
            department=payload.get("department", ""),
            source_name=payload.get("source_name", ""),
            uploaded_by=payload.get("username", ""),
        )
        # 摄入成功（幂等写入完成）后清理上传副本，防 data/uploads 无限增长
        try:
            Path(payload["stored_path"]).unlink(missing_ok=True)
        except OSError:
            logger.warning("上传副本清理失败：%s", payload.get("stored_path"))
        logger.info("摄入完成：%s", doc_id)
        return
