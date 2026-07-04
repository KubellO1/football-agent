"""OddsIngestionService.backfill_historical 的集成测试（需真实 Postgres）。

用假的历史 OddsProvider 提供确定性快照，配合真实 SQLAlchemy 仓储，验证：
按天回放→保守匹配→写入快照、幂等（重复运行不新增）、按赛事限定候选消除歧义、
逐日步进且只处理「当天开赛」的事件。
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
from app.repositories.sqlalchemy.odds_snapshot_repository import SqlAlchemyOddsSnapshotRepository
from app.repositories.sqlalchemy.reference_repositories import (
    SqlAlchemyBookmakerRepository,
    SqlAlchemyCompetitionRepository,
    SqlAlchemyTeamRepository,
)
from app.services.odds_ingestion import OddsIngestionService

D17 = datetime(2024, 8, 17, 18, 30, tzinfo=UTC)
D18 = datetime(2024, 8, 18, 18, 30, tzinfo=UTC)


class FakeHistoricalProvider(OddsProvider):
    """按 at 记录调用；每次返回相同的事件列表（同日过滤交给服务）。"""

    def __init__(self, events: list[ProviderFixtureOdds]) -> None:
        self._events = events
        self.calls: list[datetime] = []

    async def get_odds(self, *, sport, markets=("h2h",), regions=("eu",)):  # type: ignore[no-untyped-def]
        return []

    async def get_historical_odds(self, *, sport, at, markets=("h2h",), regions=("eu",)):  # type: ignore[no-untyped-def]
        self.calls.append(at)
        return list(self._events)


def _event(home: str, away: str, *, commence: datetime) -> ProviderFixtureOdds:
    return ProviderFixtureOdds(
        provider_id=f"evt-{home}-{away}-{commence.date().isoformat()}",
        commence_time=commence,
        home_team=home,
        away_team=away,
        sport_key="soccer_epl",
        bookmakers=[
            BookmakerMarket(
                bookmaker_key="pinnacle",
                bookmaker_title="Pinnacle",
                market="h2h",
                last_update=datetime(commence.year, commence.month, commence.day, 12, tzinfo=UTC),
                outcomes=[
                    OddsOutcome(name=home, price=2.5),
                    OddsOutcome(name=away, price=2.9),
                    OddsOutcome(name="Draw", price=3.3),
                ],
            )
        ],
    )


async def _add_fixture(
    session: AsyncSession, comp_id, home_id, away_id, kickoff: datetime
) -> Fixture:
    return await SqlAlchemyFixtureRepository(session).add(
        Fixture(competition_id=comp_id, home_team_id=home_id, away_team_id=away_id, kickoff=kickoff)
    )


def _service(session: AsyncSession, provider: OddsProvider) -> OddsIngestionService:
    return OddsIngestionService(
        odds_provider=provider,
        fixtures=SqlAlchemyFixtureRepository(session),
        teams=SqlAlchemyTeamRepository(session),
        bookmakers=SqlAlchemyBookmakerRepository(session),
        odds_snapshots=SqlAlchemyOddsSnapshotRepository(session),
        sport_keys=["soccer_epl"],
        regions=["eu"],
        tolerance_minutes=180,
    )


@pytest.mark.integration
async def test_backfill_matches_and_is_idempotent(db_session: AsyncSession) -> None:
    comp = await SqlAlchemyCompetitionRepository(db_session).add(
        Competition(name="EPL", country="C")
    )
    a = await SqlAlchemyTeamRepository(db_session).add(Team(name="Alpha"))
    b = await SqlAlchemyTeamRepository(db_session).add(Team(name="Beta"))
    fixture = await _add_fixture(db_session, comp.id, a.id, b.id, D17)

    provider = FakeHistoricalProvider([_event("Alpha", "Beta", commence=D17)])
    service = _service(db_session, provider)

    first = await service.backfill_historical(
        sport="soccer_epl", start=date(2024, 8, 17), end=date(2024, 8, 17)
    )
    assert first.days_processed == 1
    assert provider.calls == [datetime(2024, 8, 17, 12, tzinfo=UTC)]  # 默认 snapshot_hour=12
    assert first.events_matched == 1
    assert first.snapshots_created == 3
    snaps = await SqlAlchemyOddsSnapshotRepository(db_session).list_by_fixture(fixture.id)
    assert {s.selection.code for s in snaps} == {"home", "away", "draw"}

    # 重复运行：同一快照 last_update 稳定 → 幂等，不新增
    second = await service.backfill_historical(
        sport="soccer_epl", start=date(2024, 8, 17), end=date(2024, 8, 17)
    )
    assert second.events_matched == 1
    assert second.snapshots_created == 0
    assert second.snapshots_existing == 3


@pytest.mark.integration
async def test_backfill_competition_filter_disambiguates(db_session: AsyncSession) -> None:
    comps = SqlAlchemyCompetitionRepository(db_session)
    comp_a = await comps.add(Competition(name="EPL", country="C"))
    comp_b = await comps.add(Competition(name="Cup", country="C"))
    a = await SqlAlchemyTeamRepository(db_session).add(Team(name="Alpha"))
    b = await SqlAlchemyTeamRepository(db_session).add(Team(name="Beta"))
    # 两场同名对阵、同一时间，分属不同赛事
    await _add_fixture(db_session, comp_a.id, a.id, b.id, D17)
    await _add_fixture(db_session, comp_b.id, a.id, b.id, D17)

    provider = FakeHistoricalProvider([_event("Alpha", "Beta", commence=D17)])
    service = _service(db_session, provider)

    # 不限定赛事 → 两个候选 → 歧义，拒绝猜测
    no_filter = await service.backfill_historical(
        sport="soccer_epl", start=date(2024, 8, 17), end=date(2024, 8, 17)
    )
    assert no_filter.events_ambiguous == 1
    assert no_filter.events_matched == 0
    assert no_filter.snapshots_created == 0

    # 限定到赛事 A → 唯一候选 → 命中
    filtered = await service.backfill_historical(
        sport="soccer_epl",
        start=date(2024, 8, 17),
        end=date(2024, 8, 17),
        competition_id=comp_a.id,
        competition_scope="EPL",
    )
    assert filtered.events_ambiguous == 0
    assert filtered.events_matched == 1
    assert filtered.competition_scope == "EPL"
    assert filtered.snapshots_created == 3


@pytest.mark.integration
async def test_backfill_steps_days_and_ignores_other_day_events(db_session: AsyncSession) -> None:
    comp = await SqlAlchemyCompetitionRepository(db_session).add(
        Competition(name="EPL", country="C")
    )
    a = await SqlAlchemyTeamRepository(db_session).add(Team(name="Alpha"))
    b = await SqlAlchemyTeamRepository(db_session).add(Team(name="Beta"))
    await _add_fixture(db_session, comp.id, a.id, b.id, D17)
    await _add_fixture(db_session, comp.id, a.id, b.id, D18)

    # provider 每天都返回两场（17 与 18）；服务只处理「当天开赛」的那场
    provider = FakeHistoricalProvider(
        [_event("Alpha", "Beta", commence=D17), _event("Alpha", "Beta", commence=D18)]
    )
    service = _service(db_session, provider)

    report = await service.backfill_historical(
        sport="soccer_epl", start=date(2024, 8, 17), end=date(2024, 8, 18)
    )
    assert report.days_processed == 2
    assert provider.calls == [
        datetime(2024, 8, 17, 12, tzinfo=UTC),
        datetime(2024, 8, 18, 12, tzinfo=UTC),
    ]
    # 每天仅当天那 1 场被处理（另一场因不在当天窗口被过滤，不计入 fetched）
    assert report.events_fetched == 2
    assert report.events_matched == 2
    assert report.snapshots_created == 6  # 2 场 × 3 赔项
