"""极简、无额外依赖的每日调度器。

``next_run_at`` 是纯函数（便于单测）：给定当前时刻与目标 HH:MM（UTC），返回下一次
运行时刻——今天该时刻若还没到就用今天，否则用明天。``DailyWorker`` 据此 sleep 到点
执行作业；作业异常只记录不致命，循环继续。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from app.core.logging import get_logger
from app.workers.daily_job import run_daily_job

if TYPE_CHECKING:
    from app.core.container import Container

logger = get_logger(__name__)


def parse_schedule_time(value: str) -> tuple[int, int]:
    """解析 'HH:MM' 为 (hour, minute)，非法则抛 ValueError。"""
    hh_str, mm_str = value.strip().split(":", 1)
    hour, minute = int(hh_str), int(mm_str)
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"invalid schedule time: {value}")
    return hour, minute


def next_run_at(now: datetime, hour: int, minute: int) -> datetime:
    """返回严格晚于 ``now`` 的下一个 HH:MM 时刻（与 now 同一时区）。"""
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate


class DailyWorker:
    """按配置的每日时间循环执行 run_daily_job。"""

    def __init__(self, container: Container, schedule_time: str) -> None:
        self._container = container
        self._hour, self._minute = parse_schedule_time(schedule_time)

    async def run_once(self) -> None:
        """立即执行一次当日作业（异常记录后吞掉，避免中断循环）。"""
        try:
            await run_daily_job(self._container, datetime.now(UTC).date())
        except Exception:  # noqa: BLE001 - 调度循环不能因单次作业失败而退出
            logger.exception("Daily job failed")

    async def run_forever(self) -> None:
        """睡到下一个计划时刻后执行，循环往复。"""
        while True:
            now = datetime.now(UTC)
            target = next_run_at(now, self._hour, self._minute)
            sleep_seconds = (target - now).total_seconds()
            logger.info(
                "Next daily run scheduled at %s UTC (in %.0f min)",
                target.isoformat(),
                sleep_seconds / 60,
            )
            await asyncio.sleep(sleep_seconds)
            await self.run_once()
