"""摄入 worker 进程：消费 Redis Stream 队列（独立部署，与 API 解耦）。

用法：uv run python -m app.ingestion.worker
"""

from __future__ import annotations

import asyncio
import logging
import sys

from app.ingestion.queue import process_ingestion, worker_loop


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s"
    )
    await worker_loop(process_ingestion)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
