"""回测既有预测管线：读历史比赛、赛前时点回放、与真实结果比对，导出 CSV + Markdown。

不新增算法、不评审、不落库。过滤：--league（competition external_id）/ --season（年）/
--from/--to（开赛日期区间）/ --competition-name（按赛事名，用于导入的数据集）。

用法：
    python scripts/backtest.py --league 39 --season 2024 --out reports/epl_2024
    python scripts/backtest.py --competition-name "World Cup" --out reports/wc
    python scripts/backtest.py --from 2024-01-01 --to 2024-12-31 --out reports/all_2024
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from app.config.settings import get_settings
from app.core.container import Container
from app.core.logging import configure_logging
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
    render_markdown,
    write_csv,
    write_markdown,
)
from app.services.fixture_analysis import FixtureAnalysisService
from app.services.modeling import MatchModel
from app.services.recommendation_gate import RecommendationGate


def _window(args: argparse.Namespace) -> tuple[datetime | None, datetime | None]:
    if args.date_from or args.date_to:
        start = (
            datetime.combine(args.date_from, datetime.min.time(), UTC) if args.date_from else None
        )
        end = (
            datetime.combine(args.date_to, datetime.min.time(), UTC) + timedelta(days=1)
            if args.date_to
            else None
        )
        return start, end
    if args.season:
        return datetime(args.season, 1, 1, tzinfo=UTC), datetime(args.season + 1, 1, 1, tzinfo=UTC)
    return None, None


async def _resolve_competition_id(session, args: argparse.Namespace) -> UUID | None:  # type: ignore[no-untyped-def]
    comps = SqlAlchemyCompetitionRepository(session)
    if args.league is not None:
        comp = await comps.get_by_external_id("api-football", str(args.league))
        if comp is None:
            raise SystemExit(f"no competition with external_id={args.league} (api-football)")
        return comp.id
    if args.competition_name:
        comp = await comps.get_by_name(args.competition_name)
        if comp is None:
            raise SystemExit(f"no competition named {args.competition_name!r}")
        return comp.id
    return None


async def _run(args: argparse.Namespace) -> None:
    configure_logging()
    settings = get_settings()
    container = Container(settings)
    container.init_resources()
    try:
        async with container.database.session() as session:
            competition_id = await _resolve_competition_id(session, args)
            start, end = _window(args)
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
                poisson=None,
            )
            stats, outcomes = await service.run(competition_id=competition_id, start=start, end=end)
    finally:
        await container.shutdown_resources()

    out = args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    write_csv(f"{out}.csv", outcomes)
    write_markdown(f"{out}.md", stats, title="Backtest report")
    print(render_markdown(stats))
    print(f"[written] {out}.csv  {out}.md")


def main() -> None:
    parser = argparse.ArgumentParser(description="Backtest the deterministic prediction pipeline.")
    parser.add_argument(
        "--league", type=int, default=None, help="competition external_id (e.g. 39)"
    )
    parser.add_argument("--competition-name", type=str, default=None)
    parser.add_argument("--season", type=int, default=None, help="kickoff calendar year")
    parser.add_argument("--from", dest="date_from", type=date.fromisoformat, default=None)
    parser.add_argument("--to", dest="date_to", type=date.fromisoformat, default=None)
    parser.add_argument("--out", type=Path, default=Path("reports/backtest"))
    args = parser.parse_args()
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
