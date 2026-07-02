"""每日作业：同步比赛 → 同步赔率 → 跑当日 Top Picks（复用既有服务）。

每一步各用独立的会话/事务，先落地的步骤即使后续步骤失败也不回滚。逐步记录日志。
「不重复调用 Claude」由 DailyTopPicksService 保证（已评审过的比赛会被跳过）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from app.core.container import Container
from app.core.logging import get_logger
from app.core.service_factory import (
    build_daily_top_picks_service,
    build_ingestion_service,
    build_odds_ingestion_service,
)
from app.schemas.odds_sync import OddsSyncReport
from app.schemas.sync import SyncReport
from app.services.daily_top_picks import DailyRunReport

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class DailyJobReport:
    """一次每日作业的合并结果。"""

    date: str
    fixtures: SyncReport
    odds: OddsSyncReport
    picks: DailyRunReport


async def run_daily_job(container: Container, on_date: date) -> DailyJobReport:
    """按序执行：同步比赛 → 同步赔率 → 跑当日 Top Picks。返回合并报告。"""
    iso = on_date.isoformat()
    logger.info("Daily job START for %s", iso)

    # Step 1：同步比赛/球队/赛事
    async with container.database.session() as session:
        fixtures = await build_ingestion_service(container, session).sync_today(on_date)
    logger.info(
        "Daily job step 1/3 fixtures: processed=%d created=%d updated=%d",
        fixtures.fixtures_processed,
        fixtures.fixtures_created,
        fixtures.fixtures_updated,
    )

    # Step 2：同步赔率快照
    async with container.database.session() as session:
        odds = await build_odds_ingestion_service(container, session).sync_odds_today(on_date)
    logger.info(
        "Daily job step 2/3 odds: fetched=%d matched=%d snapshots_created=%d",
        odds.events_fetched,
        odds.events_matched,
        odds.snapshots_created,
    )

    # Step 3：跑当日 Top Picks（数学分析全部 → 阈值筛选 → Top-N → Claude 评审并落库）
    async with container.database.session() as session:
        picks = await build_daily_top_picks_service(container, session).run(on_date)
    logger.info(
        "Daily job step 3/3 picks: analyzed=%d qualified=%d reviewed=%d skipped=%d value_bets=%d",
        picks.fixtures_analyzed,
        picks.fixtures_qualified,
        picks.fixtures_reviewed,
        picks.fixtures_skipped_existing,
        picks.value_bets_created,
    )

    logger.info("Daily job DONE for %s", iso)
    return DailyJobReport(date=iso, fixtures=fixtures, odds=odds, picks=picks)
