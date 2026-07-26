"""BacktestService 的集成测试（需真实 Postgres）：验证赛前时点回放、无未来信息。"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from decimal import Decimal

import pytest
import pytest_asyncio

from app.config.settings import Settings, get_settings
from app.core.container import Container
from app.database.base import Base
from app.models.entities.competition import Competition
from app.models.entities.enums import MatchStatus
from app.models.entities.fixture import Fixture
from app.models.entities.team import Team
from app.models.value_objects.money import Money
from app.models.value_objects.score import Score
from app.repositories.sqlalchemy import models  # noqa: F401 - 注册 ORM 表
from app.repositories.sqlalchemy.fixture_repository import SqlAlchemyFixtureRepository
from app.repositories.sqlalchemy.odds_snapshot_repository import SqlAlchemyOddsSnapshotRepository
from app.repositories.sqlalchemy.reference_repositories import (
    SqlAlchemyCompetitionRepository,
    SqlAlchemyTeamRepository,
)
from app.services.backtest import BacktestInputBuilder, BacktestService
from app.services.fixture_analysis import FixtureAnalysisService
from app.services.models.ensemble import EnsembleMatchModel
from app.services.recommendation_gate import RecommendationGate


def _test_dsn() -> str:
    return os.environ.get("TEST_DATABASE_URL") or get_settings().sqlalchemy_dsn


@pytest_asyncio.fixture
async def container():
    settings = Settings(database_url=_test_dsn(), openai_api_key="test")
    ctx = Container(settings)
    ctx.init_resources()
    async with ctx.database.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield ctx
    finally:
        async with ctx.database.engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await ctx.shutdown_resources()


def _service(session) -> BacktestService:  # type: ignore[no-untyped-def]
    builder = BacktestInputBuilder(
        fixtures=SqlAlchemyFixtureRepository(session),
        teams=SqlAlchemyTeamRepository(session),
        odds_snapshots=SqlAlchemyOddsSnapshotRepository(session),
        bankroll=Money(Decimal("1000"), "EUR"),
        form_window=10,
    )
    return BacktestService(
        fixtures=SqlAlchemyFixtureRepository(session),
        analysis=FixtureAnalysisService(
            builder=builder, model=EnsembleMatchModel(), gate=RecommendationGate()
        ),
    )


@pytest.mark.integration
async def test_backtest_is_point_in_time(container: Container) -> None:
    async with container.database.session() as session:
        comp = (
            await SqlAlchemyCompetitionRepository(session).add(Competition(name="L", country="C"))
        ).id
        teams = SqlAlchemyTeamRepository(session)
        a = (await teams.add(Team(name="A"))).id
        b = (await teams.add(Team(name="B"))).id
        fixtures = SqlAlchemyFixtureRepository(session)
        for day, (hs, as_) in [(1, (2, 1)), (2, (1, 2)), (3, (2, 2))]:
            await fixtures.add(
                Fixture(
                    competition_id=comp,
                    home_team_id=a,
                    away_team_id=b,
                    kickoff=datetime(2024, day, 1, 18, 0, tzinfo=UTC),
                    status=MatchStatus.FINISHED,
                    score=Score(home=hs, away=as_),
                )
            )

        stats, outcomes = await _service(session).run()

    # 最早一场没有任何先前比赛 -> 被跳过（证明只用赛前数据）；其余两场可评估
    assert stats.fixtures_evaluated == 2
    assert stats.fixtures_skipped == 1
    assert len(outcomes) == 2
    # 无赔率 -> 不产生下注
    assert stats.bets_placed == 0
    # 有 λ -> 有 O/U 预测
    assert stats.over_under_accuracy is not None
    for o in outcomes:
        assert o.predicted in {"home", "draw", "away"}
        assert o.actual in {"home", "draw", "away"}
