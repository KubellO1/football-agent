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
from uuid import UUID

from app.config.settings import get_settings
from app.core.container import Container
from app.core.logging import configure_logging, get_logger
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
    render_markdown,
    write_csv,
    write_markdown,
)
from app.services.fixture_analysis import FixtureAnalysisService
from app.services.modeling import MatchModel
from app.services.recommendation_gate import RecommendationGate

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


def _summary_row(name: str, s: BacktestStats) -> str:
    return (
        f"| {name} | {s.fixtures_evaluated} | {s.winner_accuracy:.1%} | "
        f"{_fmt(s.brier_score, '{:.3f}')} | {_fmt(s.log_loss, '{:.3f}')} | {s.bets_placed} | "
        f"{s.flat_roi:+.1%} | {s.kelly_roi:+.1%} | {_fmt(s.sharpe_ratio, '{:+.2f}')} | "
        f"{_fmt(s.clv, '{:+.1%}')} | {s.max_drawdown:.1f} |"
    )


async def _run(args: argparse.Namespace) -> None:
    configure_logging()
    settings = get_settings()
    container = Container(settings)
    container.init_resources()
    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)

    summary = [
        "# Model validation summary",
        "",
        "| Competition | Eval | Winner acc | Brier | LogLoss | Bets | "
        "ROI flat | ROI Kelly | Sharpe | CLV | MaxDD |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    try:
        async with container.database.session() as session:
            for target in _TARGETS:
                competition_id = await _resolve(session, target)
                if competition_id is None:
                    logger.warning("skip %s: competition not found", target["name"])
                    summary.append(f"| {target['name']} | _competition not found_ |||||||||| ")
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
                summary.append(_summary_row(target["name"], stats))
                logger.info(
                    "validated %s: eval=%d bets=%d brier=%s",
                    target["name"],
                    stats.fixtures_evaluated,
                    stats.bets_placed,
                    _fmt(stats.brier_score, "{:.3f}"),
                )
    finally:
        await container.shutdown_resources()

    summary_text = "\n".join(summary) + "\n"
    (out / "model_validation_summary.md").write_text(summary_text, encoding="utf-8")
    print(summary_text)
    print(f"[written] {out}/*.csv, *.md, model_validation_summary.md")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run model validation backtests across competitions."
    )
    parser.add_argument("--out", type=Path, default=Path("reports/model_validation"))
    asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    main()
