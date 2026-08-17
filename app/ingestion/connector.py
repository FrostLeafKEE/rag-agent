"""Connector 定时摄入（FR-08，P2 W3）：扫描指定目录，新文件/变更自动入队摄入。

闭环：扫描配置目录（可带 department 标签）→ 与 ingested_files 指纹比对
（sha1(size:mtime_ns)）→ 新文件/变更文件复制到 data/uploads/ → enqueue 入队
（复用上传链路，worker 消费）→ 记录指纹；指纹未变直接跳过。
摄入失败的文件不记录指纹，下轮自动重试；worker 侧失败可在管理台看 documents.status。

配置 config/connectors.json：
  [{"path": "D:/知识库/研发部", "department": "研发部"}, {"path": "D:/知识库/制度"}]

用法：
  uv run python -m app.ingestion.connector --once          # 单轮扫描（调试/CI）
  uv run python -m app.ingestion.connector                 # 常驻（--interval 秒）
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import shutil
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)

UPLOAD_DIR = Path("data/uploads")
IGNORED_PREFIXES = (".", "~$")  # 隐藏文件、Office 锁文件
MAX_FILES_PER_SCAN = 2000


def load_config(config_path: Path) -> list[dict]:
    """读取 connectors.json；缺失或解析失败返回 []。"""
    if not config_path.exists():
        return []
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        logger.warning("connectors.json 解析失败：%s", config_path)
        return []
    return [item for item in data if isinstance(item, dict) and item.get("path")]


def _fingerprint(size: int, mtime_ns: int) -> str:
    return hashlib.sha1(f"{size}:{mtime_ns}".encode()).hexdigest()


def _path_key(path: Path) -> str:
    return hashlib.sha1(str(path).encode("utf-8")).hexdigest()


def _doc_id_for(path: Path) -> str:
    """doc_id = 父目录名-文件名（可读；同目录同名文件稳定映射到同一 doc_id，重灌即更新）。"""
    parent = "".join(c if c.isalnum() else "-" for c in path.parent.name).strip("-") or "dir"
    stem = "".join(c if c.isalnum() else "-" for c in path.stem).strip("-") or "doc"
    return f"{parent}-{stem}"[:128]


async def _copy_to_upload(src: Path) -> Path:
    """复制到 data/uploads/{uuid}{ext}（与上传 API 同目录；保留摄入时快照，源文件变动不影响）。"""
    ext = src.suffix.lower()
    target = UPLOAD_DIR / f"{uuid.uuid4().hex}{ext}"
    await asyncio.to_thread(shutil.copyfile, src, target)
    return target


async def scan_once(config_path: Path | None = None) -> dict:
    """扫描一轮；返回统计 {scanned, new, updated, skipped, failed, errors}。"""
    from sqlalchemy import select

    from app.config import get_settings
    from app.db import session_factory  # 函数内 import：调用时解析，测试可覆盖
    from app.ingestion.parser import SUPPORTED_EXTENSIONS
    from app.ingestion.queue import enqueue
    from app.models import IngestedFile

    settings = get_settings()
    dirs = load_config(config_path or Path(settings.connector_config))
    stats: dict = {
        "scanned": 0, "new": 0, "updated": 0,
        "skipped": 0, "failed": 0, "errors": [],
    }
    if not dirs:
        logger.info("无 connector 目录配置（%s），跳过本轮", settings.connector_config)
        return stats

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    async with session_factory() as session:
        for item in dirs:
            root = Path(item["path"])
            if not root.is_dir():
                stats["errors"].append(f"目录不存在：{root}")
                continue
            department = item.get("department", "")
            for file in sorted(root.rglob("*")):
                if not file.is_file() or file.name.startswith(IGNORED_PREFIXES):
                    continue
                if file.suffix.lower() not in SUPPORTED_EXTENSIONS:
                    continue  # 图片/临时文件等不支持类型静默跳过
                stats["scanned"] += 1
                try:
                    st = file.stat()
                except OSError as exc:
                    stats["failed"] += 1
                    stats["errors"].append(f"{file}: {exc}")
                    continue

                fprint = _fingerprint(st.st_size, st.st_mtime_ns)
                key = _path_key(file)
                existing = await session.scalar(
                    select(IngestedFile).where(IngestedFile.path_key == key)
                )
                if existing is not None and existing.fingerprint == fprint:
                    stats["skipped"] += 1
                    continue

                doc_id = _doc_id_for(file)
                try:
                    stored = await _copy_to_upload(file)
                    await enqueue(doc_id, stored, department, file.name, "connector")
                except Exception as exc:  # noqa: BLE001
                    stats["failed"] += 1
                    stats["errors"].append(f"{file}: {exc}")
                    logger.warning("connector 入队失败：%s（%s）", file, exc)
                    continue  # 不记录指纹，下轮重试

                if existing is None:
                    session.add(
                        IngestedFile(
                            path=str(file), path_key=key, size=st.st_size,
                            mtime_ns=st.st_mtime_ns, fingerprint=fprint,
                            department=department, doc_id=doc_id,
                        )
                    )
                    stats["new"] += 1
                else:
                    existing.size, existing.mtime_ns = st.st_size, st.st_mtime_ns
                    existing.fingerprint = fprint
                    existing.doc_id = doc_id
                    existing.department = department or existing.department
                    stats["updated"] += 1

                if stats["scanned"] >= MAX_FILES_PER_SCAN:
                    logger.warning("单轮文件数达到上限 %d，提前结束", MAX_FILES_PER_SCAN)
                    await session.commit()
                    return stats
        await session.commit()
    return stats


async def _run_loop(interval: int) -> None:
    logger.info("connector 常驻扫描启动，间隔 %ds", interval)
    while True:
        try:
            stats = await scan_once()
            logger.info("扫描完成：%s", stats)
        except Exception:  # noqa: BLE001
            logger.exception("扫描异常，下轮重试")
        await asyncio.sleep(interval)


def main() -> int:
    parser = argparse.ArgumentParser(description="目录自动摄入（FR-08）")
    parser.add_argument("--once", action="store_true", help="只扫描一轮并退出")
    parser.add_argument("--interval", type=int, default=None, help="常驻扫描间隔秒")
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s"
    )

    if args.once:
        stats = asyncio.run(scan_once())
        print(
            f"扫描 {stats['scanned']} 个文件：新 {stats['new']} / 变更 {stats['updated']} / "
            f"跳过 {stats['skipped']} / 失败 {stats['failed']}"
        )
        for err in stats["errors"][:10]:
            print(f"  ✗ {err}")
        return 0

    from app.config import get_settings

    interval = args.interval or get_settings().connector_interval
    try:
        asyncio.run(_run_loop(interval))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())