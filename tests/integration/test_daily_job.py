"""run_daily_job 的集成测试（需真实 Postgres）。

用绑定到测试库的独立容器 + 假 provider/评审器，验证：三步按序执行并落库、
复用既有服务、且重复运行不重复调用 Claude。不触达真实外部 API。
"""

from __future__ import annotations

import os
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from itertools import combinations

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.interfaces import CommitteeReviewer
from app.config.settings import Settings, get_settings
from app.core.container import Container
from app.database.base import Base
from app.models.entities.bookmaker import Bookmaker
from app.models.entities.competition import Competition
from app.models.entities.enums import MatchStatus
from app.models.entities.fixture import Fixture
from app.models.entities.odds_snapshot import OddsSnapshot
from app.models.entities.team import Team
from app.models.value_objects.markets import MarketType, Selection
from app.models.value_objects.metrics import EloRating
from app.models.value_objects.odds import Odds
from app.models.value_objects.score import Score
from app.providers.interfaces.fixtures_provider import FixturesProvider
from app.providers.interfaces.odds_provider import OddsProvider
from app.providers.schemas.fixtures import ProviderFixture, ProviderTeam
from app.providers.schemas.odds import BookmakerMarket, OddsOutcome, ProviderFixtureOdds
from app.repositories.sqlalchemy import models  # noqa: F401 - 注册 ORM 表
from app.repositories.sqlalchemy.fixture_repository import SqlAlchemyFixtureRepository
from app.repositories.sqlalchemy.models import DecisionLogORM, FixtureORM, OddsSnapshotORM
from app.repositories.sqlalchemy.odds_snapshot_repository import (
    SqlAlchemyOddsSnapshotRepository,
)
from app.repositories.sqlalchemy.reference_repositories import (
    SqlAlchemyBookmakerRepository,
    SqlAlchemyCompetitionRepository,
    SqlAlchemyTeamRepository,
)
from app.schemas.committee_review import CommitteeReview, CommitteeReviewContext, SelectionReview
from app.workers.daily_job import run_daily_job

TARGET = date(2026, 7, 2)
KICKOFF = datetime(2026, 7, 2, 18, 0, tzinfo=UTC)
PAST = datetime(2026, 5, 1, 18, 0, tzinfo=UTC)


def _test_dsn() -> str:
    return os.environ.get("TEST_DATABASE_URL") or get_settings().sqlalchemy_dsn


class FakeFixturesProvider(FixturesProvider):
    def __init__(self, fixtures: list[ProviderFixture]) -> None:
        self._fixtures = fixtures

    async def get_fixtures(self, *, on_date=None, league=None, season=None):  # type: ignore[no-untyped-def]
        return self._fixtures

    async def get_fixture(self, provider_id: str):  # type: ignore[no-untyped-def]
        return next((f for f in self._fixtures if f.provider_id == provider_id), None)


class FakeOddsProvider(OddsProvider):
    def __init__(self, events: list[ProviderFixtureOdds]) -> None:
        self._events = events

    async def get_odds(self, *, sport, markets=("h2h",), regions=("eu",)):  # type: ignore[no-untyped-def]
        return self._events


class FakeReviewer(CommitteeReviewer):
    def __init__(self) -> None:
        self.calls = 0

    async def review(self, context: CommitteeReviewContext) -> CommitteeReview:
        self.calls += 1
        return CommitteeReview(
            executive_summary="摘要",
            key_strengths=["优势"],
            key_risks=["风险"],
            why_market_may_be_wrong="市场偏慢",
            why_model_recommends_or_rejects="模型依据",
            confidence_explanation="信心",
            betting_recommendation_explanation="小额价值",
            disagreements=[],
            selection_reviews=[
                SelectionReview(
                    selection_label=s.selection_label,
                    stance="support",  # type: ignore[arg-type]
                    agrees_with_model=True,
                    explanation="解释",
                )
                for s in context.selections
            ],
        )


@pytest_asyncio.fixture
async def container():
    settings = Settings(database_url=_test_dsn(), anthropic_api_key="test")
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


async def _count(ctx: Container, orm) -> int:  # type: ignore[no-untyped-def]
    async with ctx.database.session() as s:
        return (await s.execute(select(func.count()).select_from(orm))).scalar_one()


def _pf(pid: str, home: str, away: str) -> ProviderFixture:
    return ProviderFixture(
        provider_id=pid,
        kickoff=KICKOFF,
        status="NS",
        home=ProviderTeam(provider_id=f"t-{home}", name=home),
        away=ProviderTeam(provider_id=f"t-{away}", name=away),
        league="Dev League",
        league_id="99",
        league_country="Testland",
        season=2026,
    )


def _odds_event(home: str, away: str) -> ProviderFixtureOdds:
    return ProviderFixtureOdds(
        provider_id=f"e-{home}-{away}",
        commence_time=KICKOFF,
        home_team=home,
        away_team=away,
        sport_key="soccer_epl",
        bookmakers=[
            BookmakerMarket(
                bookmaker_key="bk",
                bookmaker_title="BK",
                market="h2h",
                last_update=KICKOFF - timedelta(hours=6),
                outcomes=[
                    OddsOutcome(name=home, price=2.5),
                    OddsOutcome(name=away, price=3.0),
                    OddsOutcome(name="Draw", price=3.4),
                ],
            )
        ],
    )


@pytest.mark.integration
async def test_job_runs_all_steps_and_persists(container: Container) -> None:
    container.register(FixturesProvider, FakeFixturesProvider([_pf("1001", "Alpha FC", "Beta FC")]))
    container.register(OddsProvider, FakeOddsProvider([_odds_event("Alpha FC", "Beta FC")]))
    reviewer = FakeReviewer()
    container.register(CommitteeReviewer, reviewer)

    report = await run_daily_job(container, TARGET)

    # Step 1：比赛已入库
    assert report.fixtures.fixtures_created == 1
    assert await _count(container, FixtureORM) == 1
    # Step 2：赔率匹配并入库
    assert report.odds.events_matched == 1
    assert await _count(container, OddsSnapshotORM) == 3
    # Step 3：跑了数学分析；新同步的球队没有历史 → 无合格项、不调用 Claude
    assert report.picks.fixtures_analyzed == 1
    assert report.picks.fixtures_reviewed == 0
    assert reviewer.calls == 0


async def _seed_qualifying(session: AsyncSession) -> None:
    """强主队情景：足够历史 + 慷慨赔率，能通过严格 gate 与阈值。"""
    comp = (
        await SqlAlchemyCompetitionRepository(session).add(Competition(name="L", country="C"))
    ).id
    teams_repo = SqlAlchemyTeamRepository(session)
    elos = {"Alpha": 1650.0, "Bravo": 1350.0, "Charlie": 1500.0, "Delta": 1500.0}
    ids = {n: (await teams_repo.add(Team(name=n, elo=EloRating(elos[n])))).id for n in elos}
    fixtures_repo = SqlAlchemyFixtureRepository(session)
    day = 0
    for rnd in range(4):
        for a, b in combinations(list(elos), 2):
            home, away = (a, b) if rnd % 2 == 0 else (b, a)
            day += 1
            if "Alpha" in (home, away):
                score = Score(home=3, away=1) if home == "Alpha" else Score(home=1, away=2)
            elif "Bravo" in (home, away):
                score = Score(home=1, away=2) if home == "Bravo" else Score(home=2, away=1)
            else:
                score = Score(home=1, away=1)
            await fixtures_repo.add(
                Fixture(
                    competition_id=comp,
                    home_team_id=ids[home],
                    away_team_id=ids[away],
                    kickoff=PAST + timedelta(days=day),
                    status=MatchStatus.FINISHED,
                    score=score,
                )
            )
    target = await fixtures_repo.add(
        Fixture(
            competition_id=comp,
            home_team_id=ids["Alpha"],
            away_team_id=ids["Bravo"],
            kickoff=KICKOFF,
            status=MatchStatus.SCHEDULED,
        )
    )
    bookmaker = await SqlAlchemyBookmakerRepository(session).add(Bookmaker(name="BK"))
    snaps = SqlAlchemyOddsSnapshotRepository(session)
    for code, price in [("home", "2.50"), ("draw", "3.80"), ("away", "6.00")]:
        await snaps.add(
            OddsSnapshot(
                fixture_id=target.id,
                bookmaker_id=bookmaker.id,
                selection=Selection(market=MarketType.MATCH_RESULT, code=code),
                odds=Odds(Decimal(price)),
                captured_at=KICKOFF - timedelta(hours=6),
            )
        )


@pytest.mark.integration
async def test_job_reviews_qualifying_once_and_no_duplicate_claude(
    container: Container,
) -> None:
    # 预置一个可通过严格 gate 的强主队情景（直接入库并提交）。
    async with container.database.session() as session:
        await _seed_qualifying(session)

    # provider 返回空（同步步骤为空操作），只验证 Top Picks 对预置数据的评审。
    container.register(FixturesProvider, FakeFixturesProvider([]))
    container.register(OddsProvider, FakeOddsProvider([]))
    reviewer = FakeReviewer()
    container.register(CommitteeReviewer, reviewer)

    first = await run_daily_job(container, TARGET)
    assert first.picks.fixtures_reviewed == 1
    assert reviewer.calls == 1
    assert await _count(container, DecisionLogORM) == 1

    second = await run_daily_job(container, TARGET)
    assert second.picks.fixtures_skipped_existing == 1
    assert second.picks.fixtures_reviewed == 0
    assert reviewer.calls == 1  # 未再调用 Claude
    assert await _count(container, DecisionLogORM) == 1  # 未重复落库
