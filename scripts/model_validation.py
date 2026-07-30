"""模型验证：用既有回测框架对多个赛事跑完整历史回测，产出全套度量。

不改模型、不评审、不落库——只测量。对每个赛事导出 <slug>.csv + <slug>.md，并汇总
一张跨赛事对比表 model_validation_summary.md。

注意：五大联赛来自 API-Football（无赔率）→ 只能测概率类指标（Brier/LogLoss/校准/
命中率）；下注类指标（ROI/Kelly/CLV/Sharpe/分桶）需赔率，目前仅 World Cup 2022
（导入的 Bet365 赔率）可得。

用法：
    python scripts/model_validation.py
    python scripts/model_validation.py --out reports/model_validation
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

from app.config.settings import get_settings
from app.core.container import Container
from app.core.logging import configure_logging, get_logger
from app.core.service_factory import build_market_quote_policy
from app.models.value_objects.money import Money
from app.repositories.sqlalchemy.fixture_repository import SqlAlchemyFixtureRepository
from app.repositories.sqlalchemy.odds_snapshot_repository import SqlAlchemyOddsSnapshotRepository
from app.repositories.sqlalchemy.reference_repositories import (
    SqlAlchemyCompetitionRepository,
    SqlAlchemyTeamRepository,
)
from app.services.backtest import (
    BacktestInputBuilder,
    BacktestService,
    BacktestStats,
    MatchOutcome,
    compute_stats,
    write_csv,
    write_markdown,
)
from app.services.fixture_analysis import FixtureAnalysisService
from app.services.modeling import MatchModel
from app.services.recommendation_gate import RecommendationGate

if TYPE_CHECKING:
    from uuid import UUID

logger = get_logger(__name__)

# 2024/25 赛季窗口（含）：8 月开赛到次年 5 月收官，取 7/1–6/30 覆盖全季。
_SEASON_START = date(2024, 7, 1)
_SEASON_END = date(2025, 6, 30)

# 每个目标：显示名 + 解析方式（league=API-Football external_id / competition_name=按名）+ 时间窗。
_TARGETS: list[dict] = [
    {"slug": "epl_2024_25", "name": "Premier League 2024/25", "league": 39},
    {"slug": "laliga_2024_25", "name": "La Liga 2024/25", "league": 140},
    {"slug": "bundesliga_2024_25", "name": "Bundesliga 2024/25", "league": 78},
    {"slug": "seriea_2024_25", "name": "Serie A 2024/25", "league": 135},
    {"slug": "ligue1_2024_25", "name": "Ligue 1 2024/25", "league": 61},
    {"slug": "worldcup_2022", "name": "World Cup 2022", "competition_name": "World Cup 2022"},
]


def _window(target: dict) -> tuple[datetime | None, datetime | None]:
    if target.get("competition_name"):  # 导入的赛事：不按日期过滤，取全部
        return None, None
    start = datetime.combine(_SEASON_START, datetime.min.time(), UTC)
    end = datetime.combine(_SEASON_END, datetime.min.time(), UTC)
    return start, end


async def _resolve(session, target: dict) -> UUID | None:  # type: ignore[no-untyped-def]
    comps = SqlAlchemyCompetitionRepository(session)
    if "league" in target:
        comp = await comps.get_by_external_id("api-football", str(target["league"]))
    else:
        comp = await comps.get_by_name(target["competition_name"])
    return comp.id if comp else None


def _fmt(value: float | None, spec: str) -> str:
    return "n/a" if value is None else spec.format(value)


def _unmatched_pct(outcomes: list[MatchOutcome]) -> float:
    if not outcomes:
        return 0.0
    return 100.0 * sum(1 for o in outcomes if not o.has_odds) / len(outcomes)


def _league_row(name: str, s: BacktestStats, outcomes: list[MatchOutcome]) -> str:
    return (
        f"| {name} | {s.bets_placed} | {s.flat_roi:+.1%} | {s.kelly_roi:+.1%} | "
        f"{s.winner_accuracy:.1%} | {_fmt(s.brier_score, '{:.3f}')} | "
        f"{_fmt(s.log_loss, '{:.3f}')} | {_unmatched_pct(outcomes):.1f}% |"
    )


async def _run(args: argparse.Namespace) -> None:
    configure_logging()
    settings = get_settings()
    container = Container(settings)
    container.init_resources()
    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)

    results: list[tuple[str, BacktestStats, list[MatchOutcome]]] = []
    all_outcomes: list[MatchOutcome] = []
    try:
        async with container.database.session() as session:
            for target in _TARGETS:
                competition_id = await _resolve(session, target)
                if competition_id is None:
                    logger.warning("skip %s: competition not found", target["name"])
                    continue
                start, end = _window(target)
                builder = BacktestInputBuilder(
                    fixtures=SqlAlchemyFixtureRepository(session),
                    teams=SqlAlchemyTeamRepository(session),
                    odds_snapshots=SqlAlchemyOddsSnapshotRepository(session),
                    bankroll=Money(
                        Decimal(str(settings.analysis_default_bankroll)), settings.analysis_currency
                    ),
                    form_window=settings.analysis_form_window,
                    market_quote_policy=build_market_quote_policy(settings),
                )
                service = BacktestService(
                    fixtures=SqlAlchemyFixtureRepository(session),
                    analysis=FixtureAnalysisService(
                        builder=builder,
                        model=container.resolve(MatchModel),
                        gate=container.resolve(RecommendationGate),
                    ),
                    odds_snapshots=SqlAlchemyOddsSnapshotRepository(session),
                )
                stats, outcomes = await service.run(
                    competition_id=competition_id, start=start, end=end
                )
                write_csv(str(out / f"{target['slug']}.csv"), outcomes)
                write_markdown(
                    str(out / f"{target['slug']}.md"), stats, title=f"Backtest — {target['name']}"
                )
                results.append((target["name"], stats, outcomes))
                all_outcomes.extend(outcomes)
                logger.info(
                    "validated %s: eval=%d bets=%d",
                    target["name"],
                    stats.fixtures_evaluated,
                    stats.bets_placed,
                )
    finally:
        await container.shutdown_resources()

    # 汇总：把全部逐场结果按开赛时间排序后合并计算总体指标（Kelly/回撤按时序才连贯）。
    overall = compute_stats(sorted(all_outcomes, key=lambda o: o.kickoff))
    ranked = sorted(results, key=lambda r: r[1].flat_roi, reverse=True)

    lines = [
        "Overall:",
        f"  Total fixtures: {overall.fixtures_evaluated}",
        f"  Total bets:     {overall.bets_placed}",
        f"  ROI (flat):     {overall.flat_roi:+.1%}",
        f"  Kelly ROI:      {overall.kelly_roi:+.1%}",
        f"  Sharpe:         {_fmt(overall.sharpe_ratio, '{:+.2f}')}",
        f"  Max drawdown:   {overall.max_drawdown:.2f} units",
        "",
        "Per league (ranked by ROI, best to worst):",
        "| League | Bets | ROI | Kelly ROI | Winner acc | Brier | LogLoss | Unmatched % |",
        "|---|---|---|---|---|---|---|---|",
        *(_league_row(name, s, o) for name, s, o in ranked),
    ]
    summary_text = "\n".join(lines) + "\n"
    (out / "model_validation_summary.md").write_text(summary_text, encoding="utf-8")
    print(summary_text)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run model validation backtests across competitions."
    )
    parser.add_argument("--out", type=Path, default=Path("reports/model_validation"))
    asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    main()
