"""API-Football 历史回填的可复用核心（无预测/下注/Claude）。

逐日调用既有的 IngestionService.sync_today —— 已是幂等的（按 external_id 去重）。
本模块只负责：日期区间迭代、断点续跑、限速与计数。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from app.core.container import Container
from app.core.service_factory import build_ingestion_service


def date_range(start: date, end: date) -> Iterator[date]:
    """产出 [start, end] 闭区间内的每一天；start>end 时为空。"""
    day = start
    while day <= end:
        yield day
        day += timedelta(days=1)


def read_checkpoint(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]
    except (ValueError, OSError):
        return None


def write_checkpoint(path: Path, from_date: date, to_date: date, last_completed: date) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "from": from_date.isoformat(),
                "to": to_date.isoformat(),
                "last_completed": last_completed.isoformat(),
            }
        ),
        encoding="utf-8",
    )


def resume_start(from_date: date, to_date: date, checkpoint: dict[str, Any] | None) -> date:
    """据断点计算有效起始日：区间一致则从 last_completed 次日继续，否则从 from_date。"""
    if (
        checkpoint
        and checkpoint.get("from") == from_date.isoformat()
        and checkpoint.get("to") == to_date.isoformat()
        and checkpoint.get("last_completed")
    ):
        return date.fromisoformat(checkpoint["last_completed"]) + timedelta(days=1)
    return from_date


@dataclass
class BackfillReport:
    dates_processed: int = 0
    fixtures_created: int = 0
    fixtures_updated: int = 0
    fixtures_skipped: int = 0
    first_date: str | None = None
    last_date: str | None = None


class ApiFootballBackfill:
    """按日期区间回填 API-Football 赛程（幂等 + 断点续跑 + 限速）。"""

    def __init__(
        self,
        container: Container,
        *,
        checkpoint_path: Path,
        min_interval_seconds: float = 0.2,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        progress: Callable[[str], None] = print,
    ) -> None:
        self._container = container
        self._checkpoint = checkpoint_path
        self._min_interval = min_interval_seconds
        self._sleep = sleep
        self._progress = progress

    async def run(self, from_date: date, to_date: date, *, restart: bool = False) -> BackfillReport:
        checkpoint = None if restart else read_checkpoint(self._checkpoint)
        start = resume_start(from_date, to_date, checkpoint)
        report = BackfillReport()
        if start > from_date:
            self._progress(f"resume from {start.isoformat()} (checkpoint)")

        days = list(date_range(start, to_date))
        for i, day in enumerate(days):
            async with self._container.database.session() as session:
                sync = await build_ingestion_service(self._container, session).sync_today(day)
            report.dates_processed += 1
            report.fixtures_created += sync.fixtures_created
            report.fixtures_updated += sync.fixtures_updated
            report.fixtures_skipped += sync.fixtures_skipped
            report.first_date = report.first_date or day.isoformat()
            report.last_date = day.isoformat()
            write_checkpoint(self._checkpoint, from_date, to_date, day)
            self._progress(f"{day.isoformat()} +{sync.fixtures_created} ~{sync.fixtures_updated}")
            if i < len(days) - 1 and self._min_interval > 0:
                await self._sleep(self._min_interval)
        return report
