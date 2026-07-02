"""SqlAlchemyFixtureRepository 的集成测试（需真实 Postgres）。

覆盖：add + get 往返、按开赛时间窗查询。验证领域实体 ↔ ORM 的映射与仓储读写。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities.enums import MatchStatus
from app.models.entities.fixture import Fixture
from app.models.value_objects.score import MatchResult, Score
from app.repositories.sqlalchemy.fixture_repository import SqlAlchemyFixtureRepository


def _fixture(kickoff: datetime, *, finished: bool = False) -> Fixture:
    return Fixture(
        competition_id=uuid4(),
        home_team_id=uuid4(),
        away_team_id=uuid4(),
        kickoff=kickoff,
        status=MatchStatus.FINISHED if finished else MatchStatus.SCHEDULED,
        score=Score(home=2, away=1) if finished else None,
    )


@pytest.mark.integration
async def test_add_and_get_roundtrip(db_session: AsyncSession) -> None:
    repo = SqlAlchemyFixtureRepository(db_session)
    kickoff = datetime(2026, 7, 2, 18, 0, tzinfo=timezone.utc)
    fixture = _fixture(kickoff, finished=True)

    saved = await repo.add(fixture)
    got = await repo.get(saved.id)

    assert got is not None
    assert got.id == fixture.id
    assert got.home_team_id == fixture.home_team_id
    assert got.status is MatchStatus.FINISHED
    # Score 值对象应完整往返
    assert got.score is not None
    assert (got.score.home, got.score.away) == (2, 1)
    assert got.result is MatchResult.HOME


@pytest.mark.integration
async def test_scheduled_fixture_has_no_score(db_session: AsyncSession) -> None:
    repo = SqlAlchemyFixtureRepository(db_session)
    saved = await repo.add(_fixture(datetime(2026, 7, 3, 18, 0, tzinfo=timezone.utc)))
    got = await repo.get(saved.id)

    assert got is not None
    assert got.score is None
    assert got.result is None  # 未结束不产生结果


@pytest.mark.integration
async def test_list_by_kickoff_window_filters_and_orders(db_session: AsyncSession) -> None:
    repo = SqlAlchemyFixtureRepository(db_session)
    base = datetime(2026, 7, 2, 12, 0, tzinfo=timezone.utc)

    # 窗口外（早）、窗口内两场（乱序插入）、窗口外（晚）
    await repo.add(_fixture(base - timedelta(days=1)))
    await repo.add(_fixture(base + timedelta(hours=5)))
    await repo.add(_fixture(base + timedelta(hours=1)))
    await repo.add(_fixture(base + timedelta(days=2)))

    window_start = base
    window_end = base + timedelta(hours=12)
    result = await repo.list_by_kickoff_window(window_start, window_end)

    assert len(result) == 2
    # 应按开赛时间升序
    assert result[0].kickoff < result[1].kickoff
