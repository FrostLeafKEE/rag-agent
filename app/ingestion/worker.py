"""摄入 worker 进程：消费 Redis Stream 队列（独立部署，与 API 解耦）。

用法：uv run python -m app.ingestion.worker
支持优雅退出（FIX P1-5）：SIGINT/SIGBREAK（Windows）或 SIGTERM 触发后，
当前消息处理完再退出，避免在 XACK 前中断丢任务。
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys

from app.ingestion.queue import process_ingestion, worker_loop

logger = logging.getLogger(__name__)

_stop_requested = False


def _handle_signal(signum, frame) -> None:  # noqa: ANN001
    global _stop_requested
    _stop_requested = True
    logger.info("收到信号 %s，处理完当前消息后退出…", signum)


def _register_signal_handlers() -> None:
    for sig in (signal.SIGINT, getattr(signal, "SIGBREAK", None), getattr(signal, "SIGTERM", None)):
        if sig is not None:
            try:
                signal.signal(sig, _handle_signal)
            except (ValueError, OSError):
                pass  # 非主线程等场景忽略


def should_stop() -> bool:
    return _stop_requested


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    _register_signal_handlers()
    await worker_loop(process_ingestion, should_stop=should_stop)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
