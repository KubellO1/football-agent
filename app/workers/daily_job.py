"""每日作业：同步比赛 → 同步赔率 → 跑当日 Top Picks → 结算已完赛比赛 → 更新性能统计。

每一步各用独立的会话/事务，先落地的步骤即使后续步骤失败也不回滚。逐步记录日志。
「不重复调用 LLM」由 DailyTopPicksService 保证（已评审过的比赛会被跳过）。
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
    build_settlement_service,
    build_performance_tracker,
    derive_today_sport_keys,
)
from app.schemas.odds_sync import OddsSyncReport
from app.schemas.sync import SyncReport
from app.services.daily_top_picks import DailyRunReport
from app.services.settlement import SettlementReport
from app.services.performance_tracker import PerformanceReport

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class DailyJobReport:
    """一次每日作业的合并结果。"""

    date: str
    fixtures: SyncReport
    odds: OddsSyncReport
    picks: DailyRunReport
    settlement: SettlementReport | None = None
    performance: PerformanceReport | None = None


async def run_daily_job(container: Container, on_date: date) -> DailyJobReport:
    """按序执行：同步比赛 → 同步赔率 → 跑当日 Top Picks → 结算 → 性能更新。返回合并报告。"""
    iso = on_date.isoformat()
    logger.info("Daily job START for %s", iso)

    # Step 1：同步比赛/球队/赛事
    async with container.database.session() as session:
        fixtures = await build_ingestion_service(container, session).sync_today(on_date)
    logger.info(
        "Daily job step 1/6 fixtures: processed=%d created=%d updated=%d",
        fixtures.fixtures_processed,
        fixtures.fixtures_created,
        fixtures.fixtures_updated,
    )

    # Step 2：动态推导今日所需 sport keys（仅请求有比赛的赛事）
    dynamic_keys_info = None
    async with container.database.session() as session:
        dynamic_keys_info = await derive_today_sport_keys(session, on_date)
    logger.info(
        "Daily job step 2/7 odds optimization: configured=%d needed=%d saved=%d (%.1f%%)",
        dynamic_keys_info["configured"],
        len(dynamic_keys_info["needed"]),
        dynamic_keys_info["saved"],
        dynamic_keys_info["saved_pct"],
    )

    # Step 3：同步赔率快照（仅请求动态推导的 sport keys）
    async with container.database.session() as session:
        odds_svc = build_odds_ingestion_service(
            container, session,
            sport_keys=dynamic_keys_info["needed"],
        )
        odds = await odds_svc.sync_odds_today(on_date)
    logger.info(
        "Daily job step 3/7 odds: fetched=%d matched=%d snapshots_created=%d",
        odds.events_fetched,
        odds.events_matched,
        odds.snapshots_created,
    )

    # Step 3：跑当日 Top Picks（数学分析全部 → 阈值筛选 → Top-N → LLM 评审并落库）
    async with container.database.session() as session:
        picks = await build_daily_top_picks_service(container, session).run(on_date)
    logger.info(
        "Daily job step 4/7 picks: analyzed=%d qualified=%d reviewed=%d skipped=%d "
        "skipped_unsupported=%d value_bets=%d",
        picks.fixtures_analyzed,
        picks.fixtures_qualified,
        picks.fixtures_reviewed,
        picks.fixtures_skipped_existing,
        picks.fixtures_skipped_unsupported_competition,
        picks.value_bets_created,
    )

    # Step 4：自动结算已完赛的比赛
    settlement_report = None
    try:
        async with container.database.session() as session:
            settlement_svc = build_settlement_service(container, session)
            settlement_report = await settlement_svc.settle_all()
        logger.info(
            "Daily job step 5/7 settlement: checked=%d eligible=%d settled=%d skipped=%d total_pl=%s",
            settlement_report.fixtures_checked,
            settlement_report.bets_eligible,
            settlement_report.bets_settled,
            settlement_report.bets_skipped,
            settlement_report.total_pl,
        )
    except Exception:
        logger.exception("Settlement step failed (non-fatal)")

    # Step 5：更新性能统计快照
    performance_report = None
    try:
        async with container.database.session() as session:
            tracker = build_performance_tracker(container, session)
            performance_report = await tracker.update()
        logger.info(
            "Daily job step 6/7 performance: bets=%d win_rate=%s total_pl=%s",
            performance_report.total_bets,
            f"{performance_report.win_rate:.2%}" if performance_report.win_rate else "N/A",
            performance_report.total_pl,
        )
    except Exception:
        logger.exception("Performance update step failed (non-fatal)")

    # Step 6：生成 Dashboard HTML
    dashboard_path = ""
    try:
        from pathlib import Path

        from app.dashboard.db_builder import build_daily_dashboard
        from app.dashboard.renderer import DashboardRenderer

        async with container.database.session() as session:
            daily_data = await build_daily_dashboard(
                session, on_date,
                pipeline_version="production",
            )

        renderer = DashboardRenderer()
        html = renderer.render_daily_overview(daily_data)

        out_dir = Path(__file__).resolve().parents[2] / "output"
        out_dir.mkdir(parents=True, exist_ok=True)
        dashboard_path = str(out_dir / f"dashboard_{iso}.html")
        Path(dashboard_path).write_text(html, encoding="utf-8")
        logger.info("Daily job step 7/7 dashboard: written %d chars to %s",
                     len(html), dashboard_path)
    except Exception:
        logger.exception("Dashboard generation step failed (non-fatal)")

    logger.info("Daily job DONE for %s", iso)
    return DailyJobReport(
        date=iso,
        fixtures=fixtures,
        odds=odds,
        picks=picks,
        settlement=settlement_report,
        performance=performance_report,
    )
