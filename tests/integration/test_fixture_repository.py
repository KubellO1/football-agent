"""SqlAlchemyFixtureRepository 的集成测试（需真实 Postgres）。

覆盖：add + get 往返、按开赛时间窗查询。使用 reference_ids 满足 fixtures 的
外键约束（competitions/teams）。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities.enums import MatchStatus
from app.models.entities.fixture import Fixture
from app.models.value_objects.score import MatchResult, Score
from app.repositories.sqlalchemy.fixture_repository import SqlAlchemyFixtureRepository


def _fixture(
    kickoff: datetime, ids: tuple[UUID, UUID, UUID], *, finished: bool = False
) -> Fixture:
    competition_id, home_team_id, away_team_id = ids
    return Fixture(
        competition_id=competition_id,
        home_team_id=home_team_id,
        away_team_id=away_team_id,
        kickoff=kickoff,
        status=MatchStatus.FINISHED if finished else MatchStatus.SCHEDULED,
        score=Score(home=2, away=1) if finished else None,
    )


@pytest.mark.integration
async def test_add_and_get_roundtrip(
    db_session: AsyncSession, reference_ids: tuple[UUID, UUID, UUID]
) -> None:
    repo = SqlAlchemyFixtureRepository(db_session)
    fixture = _fixture(datetime(2026, 7, 2, 18, 0, tzinfo=timezone.utc), reference_ids, finished=True)

    saved = await repo.add(fixture)
    got = await repo.get(saved.id)

    assert got is not None
    assert got.id == fixture.id
    assert got.status is MatchStatus.FINISHED
    assert got.score is not None
    assert (got.score.home, got.score.away) == (2, 1)
    assert got.result is MatchResult.HOME


@pytest.mark.integration
async def test_scheduled_fixture_has_no_score(
    db_session: AsyncSession, reference_ids: tuple[UUID, UUID, UUID]
) -> None:
    repo = SqlAlchemyFixtureRepository(db_session)
    saved = await repo.add(
        _fixture(datetime(2026, 7, 3, 18, 0, tzinfo=timezone.utc), reference_ids)
    )
    got = await repo.get(saved.id)

    assert got is not None
    assert got.score is None
    assert got.result is None


@pytest.mark.integration
async def test_list_by_kickoff_window_filters_and_orders(
    db_session: AsyncSession, reference_ids: tuple[UUID, UUID, UUID]
) -> None:
    repo = SqlAlchemyFixtureRepository(db_session)
    base = datetime(2026, 7, 2, 12, 0, tzinfo=timezone.utc)

    await repo.add(_fixture(base - timedelta(days=1), reference_ids))
    await repo.add(_fixture(base + timedelta(hours=5), reference_ids))
    await repo.add(_fixture(base + timedelta(hours=1), reference_ids))
    await repo.add(_fixture(base + timedelta(days=2), reference_ids))

    result = await repo.list_by_kickoff_window(base, base + timedelta(hours=12))

    assert len(result) == 2
    assert result[0].kickoff < result[1].kickoff
