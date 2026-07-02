"""IngestionService 的集成测试（需真实 Postgres）。

用假的 FixturesProvider 提供确定性 DTO，配合真实 SQLAlchemy 仓储，验证：
映射与外键关联、状态/比分映射、以及**幂等性**（重复采集不产生重复行、就地更新、
计数正确）、缺失联赛 id 时跳过。
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities.enums import MatchStatus
from app.providers.interfaces.fixtures_provider import FixturesProvider
from app.providers.schemas.fixtures import ProviderFixture, ProviderTeam
from app.repositories.sqlalchemy.fixture_repository import SqlAlchemyFixtureRepository
from app.repositories.sqlalchemy.models import CompetitionORM, FixtureORM, TeamORM
from app.repositories.sqlalchemy.reference_repositories import (
    SqlAlchemyCompetitionRepository,
    SqlAlchemyTeamRepository,
)
from app.services.ingestion import IngestionService


class FakeFixturesProvider(FixturesProvider):
    """返回预置 ProviderFixture 列表的假 provider。"""

    def __init__(self, fixtures: list[ProviderFixture]) -> None:
        self._fixtures = fixtures

    async def get_fixtures(self, *, on_date=None, league=None, season=None):  # type: ignore[no-untyped-def]
        return self._fixtures

    async def get_fixture(self, provider_id: str):  # type: ignore[no-untyped-def]
        return next((f for f in self._fixtures if f.provider_id == provider_id), None)


def _pf(
    provider_id: str,
    *,
    home_id: str,
    home_name: str,
    away_id: str,
    away_name: str,
    status: str = "NS",
    home_score: int | None = None,
    away_score: int | None = None,
    league_id: str | None = "39",
) -> ProviderFixture:
    return ProviderFixture(
        provider_id=provider_id,
        kickoff=datetime(2026, 7, 2, 18, 30, tzinfo=UTC),
        status=status,
        home=ProviderTeam(provider_id=home_id, name=home_name),
        away=ProviderTeam(provider_id=away_id, name=away_name),
        league="Premier League",
        league_id=league_id,
        league_country="England",
        season=2026,
        home_score=home_score,
        away_score=away_score,
    )


def _service(session: AsyncSession, provider: FixturesProvider) -> IngestionService:
    return IngestionService(
        fixtures_provider=provider,
        competitions=SqlAlchemyCompetitionRepository(session),
        teams=SqlAlchemyTeamRepository(session),
        fixtures=SqlAlchemyFixtureRepository(session),
    )


async def _count(session: AsyncSession, orm) -> int:  # type: ignore[no-untyped-def]
    return (await session.execute(select(func.count()).select_from(orm))).scalar_one()


@pytest.mark.integration
async def test_sync_creates_entities_and_links(db_session: AsyncSession) -> None:
    provider = FakeFixturesProvider(
        [
            _pf("1001", home_id="33", home_name="Man Utd", away_id="40", away_name="Liverpool"),
            _pf(
                "1002",
                home_id="50",
                home_name="Man City",
                away_id="33",
                away_name="Man Utd",
                status="FT",
                home_score=2,
                away_score=1,
            ),
        ]
    )
    report = await _service(db_session, provider).sync_today(date(2026, 7, 2))

    assert report.fixtures_processed == 2
    assert report.fixtures_created == 2
    assert report.fixtures_updated == 0
    assert report.competitions_created == 1  # 同一联赛只建一次
    assert report.teams_created == 3  # Man Utd 复用，共 3 支不同球队

    assert await _count(db_session, CompetitionORM) == 1
    assert await _count(db_session, TeamORM) == 3
    assert await _count(db_session, FixtureORM) == 2

    # FT 比赛正确映射为 FINISHED + 比分
    finished = (
        await db_session.execute(select(FixtureORM).where(FixtureORM.external_id == "1002"))
    ).scalar_one()
    assert finished.status == MatchStatus.FINISHED.value
    assert (finished.score_home, finished.score_away) == (2, 1)

    # NS 比赛为 SCHEDULED、无比分
    scheduled = (
        await db_session.execute(select(FixtureORM).where(FixtureORM.external_id == "1001"))
    ).scalar_one()
    assert scheduled.status == MatchStatus.SCHEDULED.value
    assert scheduled.score_home is None


@pytest.mark.integration
async def test_sync_is_idempotent(db_session: AsyncSession) -> None:
    fixtures = [
        _pf("1001", home_id="33", home_name="Man Utd", away_id="40", away_name="Liverpool"),
        _pf("1002", home_id="50", home_name="Man City", away_id="33", away_name="Man Utd"),
    ]
    service = _service(db_session, FakeFixturesProvider(fixtures))

    first = await service.sync_today(date(2026, 7, 2))
    assert first.fixtures_created == 2

    # 第二次运行同样的数据：不应新增任何行
    second = await service.sync_today(date(2026, 7, 2))
    assert second.fixtures_created == 0
    assert second.fixtures_updated == 2
    assert second.competitions_created == 0
    assert second.teams_created == 0

    assert await _count(db_session, CompetitionORM) == 1
    assert await _count(db_session, TeamORM) == 3
    assert await _count(db_session, FixtureORM) == 2


@pytest.mark.integration
async def test_resync_updates_status_and_score_in_place(db_session: AsyncSession) -> None:
    # 先以 NS 采集
    before = FakeFixturesProvider(
        [_pf("2001", home_id="1", home_name="A", away_id="2", away_name="B")]
    )
    await _service(db_session, before).sync_today(date(2026, 7, 2))
    row = (
        await db_session.execute(select(FixtureORM).where(FixtureORM.external_id == "2001"))
    ).scalar_one()
    original_id = row.id

    # 同一场比赛已完赛后重采
    after = FakeFixturesProvider(
        [
            _pf(
                "2001",
                home_id="1",
                home_name="A",
                away_id="2",
                away_name="B",
                status="FT",
                home_score=3,
                away_score=0,
            )
        ]
    )
    report = await _service(db_session, after).sync_today(date(2026, 7, 2))

    assert report.fixtures_updated == 1
    assert report.fixtures_created == 0
    assert await _count(db_session, FixtureORM) == 1  # 未新增

    db_session.expire_all()
    updated = (
        await db_session.execute(select(FixtureORM).where(FixtureORM.external_id == "2001"))
    ).scalar_one()
    assert updated.id == original_id  # 主键不变，就地更新
    assert updated.status == MatchStatus.FINISHED.value
    assert (updated.score_home, updated.score_away) == (3, 0)


@pytest.mark.integration
async def test_fixture_without_league_id_is_skipped(db_session: AsyncSession) -> None:
    provider = FakeFixturesProvider(
        [_pf("3001", home_id="1", home_name="A", away_id="2", away_name="B", league_id=None)]
    )
    report = await _service(db_session, provider).sync_today(date(2026, 7, 2))

    assert report.fixtures_processed == 1
    assert report.fixtures_skipped == 1
    assert report.fixtures_created == 0
    assert await _count(db_session, FixtureORM) == 0
    assert await _count(db_session, CompetitionORM) == 0
