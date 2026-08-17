"""摄入管道入口（CLI）：文件 → 解析 → 分块 → 嵌入 → 写 Milvus → 登记元数据。

用法：
  uv run python -m app.ingestion.ingest docs/PRD.md [--department 研发部] [--doc-id 自定义ID]
  uv run python -m app.ingestion.ingest docs/ --recursive

同 doc-id 重复摄入是幂等的（upsert 覆盖），文档内容更新时直接重跑即可。
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from app.db import get_session
from app.ingestion.parser import SUPPORTED_EXTENSIONS
from app.ingestion.pipeline import ingest_document

logger = logging.getLogger("ingest")


async def _ingest(path: Path, doc_id: str, department: str, chunk_size: int, overlap: int) -> int:
    async for session in get_session():  # type: ignore  # get_session 是 async generator 依赖（FastAPI 模式）
        return await ingest_document(
            session,
            path,
            doc_id,
            department=department,
            chunk_size=chunk_size,
            overlap=overlap,
        )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="摄入文档到向量库")
    parser.add_argument("target", help="文件或目录路径")
    parser.add_argument("--recursive", "-r", action="store_true", help="递归处理目录")
    parser.add_argument("--doc-id", help="文档 ID（默认取文件名的 slug）")
    parser.add_argument("--department", default="", help="权限标签（检索过滤用）")
    parser.add_argument("--chunk-size", type=int, default=800)
    parser.add_argument("--overlap", type=int, default=100)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

    target = Path(args.target)
    files = (
        [target]
        if target.is_file()
        else sorted(p for p in target.rglob("*") if p.suffix.lower() in SUPPORTED_EXTENSIONS)
        if args.recursive
        else sorted(p for p in target.iterdir() if p.suffix.lower() in SUPPORTED_EXTENSIONS)
    )
    if not files:
        logger.error("未找到可摄入的文件：%s", target)
        return 1

    total = 0
    for path in files:
        doc_id = args.doc_id or path.stem
        try:
            total += asyncio.run(
                _ingest(path, doc_id, args.department, args.chunk_size, args.overlap)
            )
        except Exception:
            logger.exception("摄入失败：%s", path)
            return 1
    logger.info("完成：共摄入 %d 个分块（%d 个文件）", total, len(files))
    return 0


if __name__ == "__main__":
    sys.exit(main())
