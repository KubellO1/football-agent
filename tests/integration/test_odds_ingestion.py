"""OddsIngestionService 的集成测试（需真实 Postgres）。

用假的 OddsProvider 提供确定性赔率事件，配合真实 SQLAlchemy 仓储，验证：
匹配→写入快照、幂等（ON CONFLICT DO NOTHING）、未匹配/歧义跳过并报告、
非法赔率(<=1.0)与未知赔项跳过、博彩公司 get-or-create。
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities.competition import Competition
from app.models.entities.fixture import Fixture
from app.models.entities.team import Team
from app.providers.interfaces.odds_provider import OddsProvider
from app.providers.schemas.odds import BookmakerMarket, OddsOutcome, ProviderFixtureOdds
from app.repositories.sqlalchemy.fixture_repository import SqlAlchemyFixtureRepository
from app.repositories.sqlalchemy.odds_snapshot_repository import (
    SqlAlchemyOddsSnapshotRepository,
)
from app.repositories.sqlalchemy.reference_repositories import (
    SqlAlchemyBookmakerRepository,
    SqlAlchemyCompetitionRepository,
    SqlAlchemyTeamRepository,
)
from app.services.odds_ingestion import OddsIngestionService

KICKOFF = datetime(2026, 7, 2, 18, 30, tzinfo=UTC)
LAST_UPDATE = datetime(2026, 7, 2, 12, 0, tzinfo=UTC)
TARGET = date(2026, 7, 2)


class FakeOddsProvider(OddsProvider):
    def __init__(self, events: list[ProviderFixtureOdds]) -> None:
        self._events = events

    async def get_odds(self, *, sport, markets=("h2h",), regions=("eu",)):  # type: ignore[no-untyped-def]
        return self._events


async def _insert_fixture(
    session: AsyncSession, *, home: str, away: str, kickoff: datetime = KICKOFF
) -> Fixture:
    competition = await SqlAlchemyCompetitionRepository(session).add(
        Competition(name="League", country="Country")
    )
    home_team = await SqlAlchemyTeamRepository(session).add(Team(name=home))
    away_team = await SqlAlchemyTeamRepository(session).add(Team(name=away))
    return await SqlAlchemyFixtureRepository(session).add(
        Fixture(
            competition_id=competition.id,
            home_team_id=home_team.id,
            away_team_id=away_team.id,
            kickoff=kickoff,
        )
    )


def _event(
    home: str,
    away: str,
    *,
    commence: datetime = KICKOFF,
    prices: tuple[float, float, float] = (2.5, 2.9, 3.3),
    extra_outcomes: list[OddsOutcome] | None = None,
    last_update: datetime | None = LAST_UPDATE,
    bookmaker_key: str = "pinnacle",
) -> ProviderFixtureOdds:
    outcomes = [
        OddsOutcome(name=home, price=prices[0]),
        OddsOutcome(name=away, price=prices[1]),
        OddsOutcome(name="Draw", price=prices[2]),
    ]
    if extra_outcomes:
        outcomes.extend(extra_outcomes)
    return ProviderFixtureOdds(
        provider_id=f"evt-{home}-{away}",
        commence_time=commence,
        home_team=home,
        away_team=away,
        sport_key="soccer_epl",
        bookmakers=[
            BookmakerMarket(
                bookmaker_key=bookmaker_key,
                bookmaker_title="Pinnacle",
                market="h2h",
                last_update=last_update,
                outcomes=outcomes,
            )
        ],
    )


def _service(session: AsyncSession, provider: OddsProvider, *, tolerance: int = 180):
    return OddsIngestionService(
        odds_provider=provider,
        fixtures=SqlAlchemyFixtureRepository(session),
        teams=SqlAlchemyTeamRepository(session),
        bookmakers=SqlAlchemyBookmakerRepository(session),
        odds_snapshots=SqlAlchemyOddsSnapshotRepository(session),
        sport_keys=["soccer_epl"],
        regions=["eu"],
        tolerance_minutes=tolerance,
    )


@pytest.mark.integration
async def test_matched_event_creates_snapshots(db_session: AsyncSession) -> None:
    fixture = await _insert_fixture(db_session, home="Alpha", away="Beta")
    provider = FakeOddsProvider([_event("Alpha", "Beta")])

    report = await _service(db_session, provider).sync_odds_today(TARGET)

    assert report.events_matched == 1
    assert report.events_unmatched == 0
    assert report.snapshots_created == 3  # home / away / draw
    assert report.outcomes_skipped == 0

    snaps = await SqlAlchemyOddsSnapshotRepository(db_session).list_by_fixture(fixture.id)
    assert len(snaps) == 3
    codes = {s.selection.code for s in snaps}
    assert codes == {"home", "away", "draw"}

    # 博彩公司被 get-or-create（按 external key）
    bookmaker = await SqlAlchemyBookmakerRepository(db_session).get_by_external_id(
        "the-odds-api", "pinnacle"
    )
    assert bookmaker is not None


@pytest.mark.integration
async def test_odds_sync_is_idempotent(db_session: AsyncSession) -> None:
    await _insert_fixture(db_session, home="Alpha", away="Beta")
    service = _service(db_session, FakeOddsProvider([_event("Alpha", "Beta")]))

    first = await service.sync_odds_today(TARGET)
    assert first.snapshots_created == 3

    second = await service.sync_odds_today(TARGET)
    assert second.events_matched == 1
    assert second.snapshots_created == 0
    assert second.snapshots_existing == 3


@pytest.mark.integration
async def test_unmatched_event_is_skipped_and_reported(db_session: AsyncSession) -> None:
    await _insert_fixture(db_session, home="Alpha", away="Beta")
    # 赔率事件球队名对不上任何比赛
    provider = FakeOddsProvider([_event("Gamma", "Delta")])

    report = await _service(db_session, provider).sync_odds_today(TARGET)

    assert report.events_matched == 0
    assert report.events_unmatched == 1
    assert report.snapshots_created == 0
    assert any("Gamma vs Delta" in s for s in report.unmatched_samples)


@pytest.mark.integration
async def test_ambiguous_event_is_skipped(db_session: AsyncSession) -> None:
    # 两场同名对阵、同一开赛时间 → 歧义，拒绝猜测
    await _insert_fixture(db_session, home="Alpha", away="Beta")
    await _insert_fixture(db_session, home="Alpha", away="Beta")
    provider = FakeOddsProvider([_event("Alpha", "Beta")])

    report = await _service(db_session, provider).sync_odds_today(TARGET)

    assert report.events_ambiguous == 1
    assert report.events_matched == 0
    assert report.snapshots_created == 0


@pytest.mark.integration
async def test_invalid_and_unknown_outcomes_are_skipped(db_session: AsyncSession) -> None:
    fixture = await _insert_fixture(db_session, home="Alpha", away="Beta")
    # draw 赔率非法(1.0)，另加一个无法识别的赔项名
    provider = FakeOddsProvider(
        [
            _event(
                "Alpha",
                "Beta",
                prices=(2.5, 2.9, 1.0),
                extra_outcomes=[OddsOutcome(name="Nonsense", price=5.0)],
            )
        ]
    )

    report = await _service(db_session, provider).sync_odds_today(TARGET)

    assert report.events_matched == 1
    assert report.snapshots_created == 2  # 仅 home / away 有效
    assert report.outcomes_skipped == 2  # draw(1.0) + Nonsense

    snaps = await SqlAlchemyOddsSnapshotRepository(db_session).list_by_fixture(fixture.id)
    assert {s.selection.code for s in snaps} == {"home", "away"}
