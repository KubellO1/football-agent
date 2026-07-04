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


# ---------------------------------------------------------------------------
# 定向回填：按 (联赛, 赛季) 网格（不按日期）
# ---------------------------------------------------------------------------

# 目标联赛的 API-Football league id（名称仅供参考/CLI 可读）。
LEAGUE_IDS: dict[str, int] = {
    "epl": 39,
    "laliga": 140,
    "serie_a": 135,
    "bundesliga": 78,
    "ligue_1": 61,
    "ucl": 2,
    "uel": 3,
    "uecl": 848,
    "world_cup": 1,
    "euros": 4,
}
DEFAULT_LEAGUES: list[int] = [39, 140, 135, 78, 61, 2, 3, 848, 1, 4]
DEFAULT_SEASONS: list[int] = [2022, 2023, 2024, 2025, 2026]


def _pair_key(league_id: int, season: int) -> str:
    return f"{league_id}:{season}"


def write_league_checkpoint(
    path: Path, leagues: list[int], seasons: list[int], completed: set[str]
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"leagues": leagues, "seasons": seasons, "completed": sorted(completed)}),
        encoding="utf-8",
    )


def completed_pairs(
    leagues: list[int], seasons: list[int], checkpoint: dict[str, Any] | None
) -> set[str]:
    """断点里已完成的 (联赛, 赛季) 集合；仅当配置一致时才复用，否则视为全新。"""
    if checkpoint and checkpoint.get("leagues") == leagues and checkpoint.get("seasons") == seasons:
        return set(checkpoint.get("completed", []))
    return set()


@dataclass
class LeagueBackfillReport:
    pairs_processed: int = 0
    pairs_skipped: int = 0
    fixtures_created: int = 0
    fixtures_updated: int = 0
    fixtures_skipped: int = 0
    competitions_created: int = 0
    teams_created: int = 0


class LeagueSeasonBackfill:
    """按 (联赛, 赛季) 网格回填（幂等 + 断点续跑 + 限速）。复用 sync_league_season。"""

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

    async def run(
        self, leagues: list[int], seasons: list[int], *, restart: bool = False
    ) -> LeagueBackfillReport:
        checkpoint = None if restart else read_checkpoint(self._checkpoint)
        done = completed_pairs(leagues, seasons, checkpoint)
        pairs = [(league, season) for league in leagues for season in seasons]
        todo = [(le, se) for (le, se) in pairs if _pair_key(le, se) not in done]

        report = LeagueBackfillReport(pairs_skipped=len(pairs) - len(todo))
        if report.pairs_skipped:
            self._progress(
                f"resume: {report.pairs_skipped} done, {len(todo)} remaining (checkpoint)"
            )

        for i, (league, season) in enumerate(todo):
            async with self._container.database.session() as session:
                sync = await build_ingestion_service(self._container, session).sync_league_season(
                    league, season
                )
            report.pairs_processed += 1
            report.fixtures_created += sync.fixtures_created
            report.fixtures_updated += sync.fixtures_updated
            report.fixtures_skipped += sync.fixtures_skipped
            report.competitions_created += sync.competitions_created
            report.teams_created += sync.teams_created
            done.add(_pair_key(league, season))
            write_league_checkpoint(self._checkpoint, leagues, seasons, done)
            self._progress(
                f"league={league} season={season} "
                f"+{sync.fixtures_created} ~{sync.fixtures_updated}"
            )
            if i < len(todo) - 1 and self._min_interval > 0:
                await self._sleep(self._min_interval)
        return report
