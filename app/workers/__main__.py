"""Worker 进程入口：每日调度器。

在配置的每日时间（UTC）依次执行：同步比赛 → 同步赔率 → 跑当日 Top Picks。
可选在启动时先跑一次（WORKER_RUN_ON_START）。复用应用容器与既有服务。
"""

from __future__ import annotations

import asyncio

from app.config.settings import get_settings
from app.core.container import container
from app.core.logging import configure_logging, get_logger
from app.workers.scheduler import DailyWorker

logger = get_logger(__name__)


async def main() -> None:
    configure_logging()
    settings = get_settings()
    container.init_resources()
    logger.info(
        "Worker started; daily schedule=%s UTC (run_on_start=%s)",
        settings.worker_schedule_time,
        settings.worker_run_on_start,
    )
    worker = DailyWorker(container, settings.worker_schedule_time)
    try:
        if settings.worker_run_on_start:
            await worker.run_once()
        await worker.run_forever()
    finally:
        await container.shutdown_resources()


if __name__ == "__main__":
    asyncio.run(main())
