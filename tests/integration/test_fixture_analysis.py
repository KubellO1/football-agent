"""FixtureAnalysisService 的集成测试（需真实 Postgres）。

用真实 SQLAlchemy 仓储 + 真实数学模型（EnsembleMatchModel）+ 真实 gate，
从库里读比赛/战绩/赔率，验证：概率、EV、Kelly、推荐、信心、解释的产出，
以及「历史数据不足」「无赔率」两条边界路径。不涉及任何外部 API。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities.bookmaker import Bookmaker
from app.models.entities.competition import Competition
from app.models.entities.enums import MatchStatus
from app.models.entities.fixture import Fixture
from app.models.entities.odds_snapshot import OddsSnapshot
from app.models.entities.team import Team
from app.models.value_objects.markets import MarketType, Selection
from app.models.value_objects.money import Money
from app.models.value_objects.odds import Odds
from app.models.value_objects.score import Score
from app.repositories.sqlalchemy.fixture_repository import SqlAlchemyFixtureRepository
from app.repositories.sqlalchemy.odds_snapshot_repository import (
    SqlAlchemyOddsSnapshotRepository,
)
from app.repositories.sqlalchemy.reference_repositories import (
    SqlAlchemyBookmakerRepository,
    SqlAlchemyCompetitionRepository,
    SqlAlchemyTeamRepository,
)
from app.services.fixture_analysis import (
    INSUFFICIENT_DATA_MESSAGE,
    NO_ODDS_MESSAGE,
    FixtureAnalysisService,
    MatchAnalysisInputBuilder,
)
from app.services.models.ensemble import EnsembleMatchModel
from app.services.recommendation_gate import RecommendationGate

PAST = datetime(2026, 6, 1, 18, 0, tzinfo=UTC)
KICKOFF = datetime(2026, 7, 10, 18, 0, tzinfo=UTC)


def _service(session: AsyncSession) -> FixtureAnalysisService:
    builder = MatchAnalysisInputBuilder(
        fixtures=SqlAlchemyFixtureRepository(session),
        teams=SqlAlchemyTeamRepository(session),
        odds_snapshots=SqlAlchemyOddsSnapshotRepository(session),
        bankroll=Money(Decimal("1000"), "EUR"),
        form_window=10,
    )
    return FixtureAnalysisService(
        builder=builder, model=EnsembleMatchModel(), gate=RecommendationGate()
    )


async def _finished(
    session: AsyncSession, comp: UUID, home: UUID, away: UUID, hs: int, a_s: int, *, day: int
) -> Fixture:
    return await SqlAlchemyFixtureRepository(session).add(
        Fixture(
            competition_id=comp,
            home_team_id=home,
            away_team_id=away,
            kickoff=PAST + timedelta(days=day),
            status=MatchStatus.FINISHED,
            score=Score(home=hs, away=a_s),
        )
    )


async def _scheduled(session: AsyncSession, comp: UUID, home: UUID, away: UUID) -> Fixture:
    return await SqlAlchemyFixtureRepository(session).add(
        Fixture(
            competition_id=comp,
            home_team_id=home,
            away_team_id=away,
            kickoff=KICKOFF,
            status=MatchStatus.SCHEDULED,
        )
    )


async def _add_odds(session: AsyncSession, fixture_id: UUID) -> None:
    bookmaker = await SqlAlchemyBookmakerRepository(session).add(Bookmaker(name="BM"))
    repo = SqlAlchemyOddsSnapshotRepository(session)
    for code, price in [("home", "2.00"), ("draw", "3.40"), ("away", "4.00")]:
        await repo.add(
            OddsSnapshot(
                fixture_id=fixture_id,
                bookmaker_id=bookmaker.id,
                selection=Selection(market=MarketType.MATCH_RESULT, code=code),
                odds=Odds(Decimal(price)),
                captured_at=KICKOFF - timedelta(hours=6),
            )
        )


async def _seed_with_history(session: AsyncSession) -> Fixture:
    """建赛事 + 两队 + 若干已完赛记录，返回一场待分析的已排期比赛。"""
    comp = (
        await SqlAlchemyCompetitionRepository(session).add(Competition(name="L", country="C"))
    ).id
    teams_repo = SqlAlchemyTeamRepository(session)
    home = (await teams_repo.add(Team(name="Home"))).id
    away = (await teams_repo.add(Team(name="Away"))).id
    o1 = (await teams_repo.add(Team(name="Opp1"))).id
    o2 = (await teams_repo.add(Team(name="Opp2"))).id

    # 主队近况（进攻较强）
    await _finished(session, comp, home, o1, 2, 0, day=1)
    await _finished(session, comp, o1, home, 1, 2, day=2)
    await _finished(session, comp, home, o2, 1, 1, day=3)
    # 客队近况
    await _finished(session, comp, away, o1, 0, 1, day=4)
    await _finished(session, comp, o2, away, 2, 2, day=5)
    await _finished(session, comp, away, o2, 1, 0, day=6)

    return await _scheduled(session, comp, home, away)


@pytest.mark.integration
async def test_analysis_produces_full_output(db_session: AsyncSession) -> None:
    fixture = await _seed_with_history(db_session)
    await _add_odds(db_session, fixture.id)

    result = await _service(db_session).analyze(fixture)

    # 概率齐全且归一
    assert set(result.probabilities) == {"home", "draw", "away"}
    assert abs(sum(result.probabilities.values()) - 1.0) < 1e-6
    assert result.expected_goals_home is not None and result.expected_goals_away is not None
    assert result.data_completeness > 0

    # 三个 1X2 选项都产出分析（含 EV / Kelly / 信心 / 解释）
    assert len(result.selections) == 3
    for s in result.selections:
        assert 0.0 <= s.model_probability <= 1.0
        assert 0.0 <= s.confidence <= 1.0
        assert s.kelly_fraction >= 0.0
        assert abs(s.expected_value - s.edge) < 1e-9  # EV/单位 == edge
        assert s.explanation
        assert isinstance(s.recommended, bool)


@pytest.mark.integration
async def test_insufficient_history_returns_message(db_session: AsyncSession) -> None:
    # 两队均无历史完赛记录 → 数据不足
    comp = (
        await SqlAlchemyCompetitionRepository(db_session).add(Competition(name="L", country="C"))
    ).id
    teams_repo = SqlAlchemyTeamRepository(db_session)
    home = (await teams_repo.add(Team(name="H"))).id
    away = (await teams_repo.add(Team(name="A"))).id
    fixture = await _scheduled(db_session, comp, home, away)

    result = await _service(db_session).analyze(fixture)

    assert result.message == INSUFFICIENT_DATA_MESSAGE
    assert result.probabilities == {}
    assert result.selections == []


@pytest.mark.integration
async def test_sufficient_history_but_no_odds(db_session: AsyncSession) -> None:
    fixture = await _seed_with_history(db_session)  # 有战绩，但不加赔率

    result = await _service(db_session).analyze(fixture)

    assert result.message == NO_ODDS_MESSAGE
    assert set(result.probabilities) == {"home", "draw", "away"}  # 仍给出概率
    assert result.selections == []
