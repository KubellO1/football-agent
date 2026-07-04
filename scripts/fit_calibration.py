"""拟合概率校准温度 T：在历史回测样本上最小化多分类对数损失。

用**未校准**模型（原始概率）对五大联赛 2024/25 回测采样，按开赛时间做 train/test
时间序分割，在 train 上拟合 T，在 test 上给出诚实的前后对比（对数损失 + Brier）。
打印推荐 T 与启用方法；不改任何配置、不落库。

用法：
    python scripts/fit_calibration.py
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import UTC, date, datetime
from decimal import Decimal

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
from app.services.backtest import BacktestInputBuilder, BacktestService, MatchOutcome
from app.services.fixture_analysis import FixtureAnalysisService
from app.services.models.calibration import apply_temperature, fit_temperature, log_loss
from app.services.models.ensemble import EnsembleMatchModel
from app.services.recommendation_gate import RecommendationGate

logger = get_logger(__name__)

_IDX = {"home": 0, "draw": 1, "away": 2}
_LEAGUES = [39, 140, 78, 135, 61]  # EPL / La Liga / Bundesliga / Serie A / Ligue 1
_START = datetime.combine(date(2024, 7, 1), datetime.min.time(), UTC)
_END = datetime.combine(date(2025, 6, 30), datetime.min.time(), UTC)

Sample = tuple[Sequence[float], int]


def _brier(samples: Sequence[Sample], temperature: float) -> float:
    if not samples:
        return 0.0
    total = 0.0
    for probs, actual in samples:
        s = apply_temperature(probs, temperature)
        total += sum((s[k] - (1.0 if k == actual else 0.0)) ** 2 for k in range(3))
    return total / len(samples)


async def _run() -> None:
    configure_logging()
    settings = get_settings()
    container = Container(settings)
    container.init_resources()
    outcomes: list[MatchOutcome] = []
    try:
        async with container.database.session() as session:
            comps = SqlAlchemyCompetitionRepository(session)
            for lid in _LEAGUES:
                comp = await comps.get_by_external_id("api-football", str(lid))
                if comp is None:
                    logger.warning("league %s not found; skipping", lid)
                    continue
                builder = BacktestInputBuilder(
                    fixtures=SqlAlchemyFixtureRepository(session),
                    teams=SqlAlchemyTeamRepository(session),
                    odds_snapshots=SqlAlchemyOddsSnapshotRepository(session),
                    bankroll=Money(
                        Decimal(str(settings.analysis_default_bankroll)), settings.analysis_currency
                    ),
                    form_window=settings.analysis_form_window,
                )
                # 用未校准模型（默认 T=1）采样，得到原始概率用于拟合。
                service = BacktestService(
                    fixtures=SqlAlchemyFixtureRepository(session),
                    analysis=FixtureAnalysisService(
                        builder=builder, model=EnsembleMatchModel(), gate=RecommendationGate()
                    ),
                )
                _, got = await service.run(competition_id=comp.id, start=_START, end=_END)
                outcomes.extend(got)
                logger.info("collected league %s: %d fixtures", lid, len(got))
    finally:
        await container.shutdown_resources()

    ordered = sorted(outcomes, key=lambda o: o.kickoff)
    samples: list[Sample] = [((o.p_home, o.p_draw, o.p_away), _IDX[o.actual]) for o in ordered]
    n = len(samples)
    if n == 0:
        print("no samples collected")
        return

    split = int(n * 0.7)
    train, test = samples[:split], samples[split:]
    temperature = fit_temperature(train)

    print(f"samples: total={n} train={len(train)} test={len(test)}")
    print(f"fitted temperature T = {temperature:.3f}\n")
    print(f"{'set':<14}{'log loss':>12}{'Brier':>10}")
    print(f"{'test  T=1.00':<14}{log_loss(test, 1.0):>12.4f}{_brier(test, 1.0):>10.4f}")
    print(
        f"{'test  T=' + format(temperature, '.2f'):<14}"
        f"{log_loss(test, temperature):>12.4f}{_brier(test, temperature):>10.4f}"
    )
    print(f"{'full  T=1.00':<14}{log_loss(samples, 1.0):>12.4f}{_brier(samples, 1.0):>10.4f}")
    print(
        f"{'full  T=' + format(temperature, '.2f'):<14}"
        f"{log_loss(samples, temperature):>12.4f}{_brier(samples, temperature):>10.4f}"
    )
    print(f"\nTo activate: set ANALYSIS_CALIBRATION_TEMPERATURE={temperature:.3f} in .env")


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
