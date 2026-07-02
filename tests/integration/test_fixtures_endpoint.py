"""GET /fixtures/today 读取逻辑的集成测试（需真实 Postgres）。

直接调用端点的组装函数 build_today_fixtures，配合真实 SQLAlchemy 仓储，验证：
只返回目标日期（UTC）的比赛、正确解析赛事/球队名称与比分、按开赛时间排序、
窗口外的比赛被排除、无数据时返回空。不涉及任何外部 API 或预测逻辑。
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.endpoints.fixtures import build_today_fixtures
from app.models.entities.enums import MatchStatus
from app.models.entities.fixture import Fixture
from app.models.value_objects.score import Score
from app.repositories.sqlalchemy.fixture_repository import SqlAlchemyFixtureRepository
from app.repositories.sqlalchemy.reference_repositories import (
    SqlAlchemyCompetitionRepository,
    SqlAlchemyTeamRepository,
)

TARGET = date(2026, 7, 10)


def _fixture(
    ids: tuple[UUID, UUID, UUID],
    kickoff: datetime,
    *,
    finished: bool = False,
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


async def _build(session: AsyncSession, target: date):  # type: ignore[no-untyped-def]
    return await build_today_fixtures(
        fixtures=SqlAlchemyFixtureRepository(session),
        teams=SqlAlchemyTeamRepository(session),
        competitions=SqlAlchemyCompetitionRepository(session),
        target=target,
    )


@pytest.mark.integration
async def test_returns_todays_fixtures_with_details(
    db_session: AsyncSession, reference_ids: tuple[UUID, UUID, UUID]
) -> None:
    repo = SqlAlchemyFixtureRepository(db_session)
    day = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)
    await repo.add(_fixture(reference_ids, day + timedelta(hours=6)))  # 18:00 未开赛
    await repo.add(_fixture(reference_ids, day, finished=True))  # 12:00 已完赛
    await repo.add(_fixture(reference_ids, day - timedelta(days=1)))  # 前一天，应排除

    resp = await _build(db_session, TARGET)

    assert resp.date == "2026-07-10"
    assert resp.count == 2
    # 按开赛时间升序：12:00 在 18:00 之前
    assert resp.fixtures[0].kickoff < resp.fixtures[1].kickoff

    first = resp.fixtures[0]
    assert first.competition.name == "测试联赛"
    assert first.competition.country == "测试国"
    assert first.home_team.name == "主队"
    assert first.away_team.name == "客队"
    assert first.status == MatchStatus.FINISHED.value
    assert first.score is not None
    assert (first.score.home, first.score.away) == (2, 1)

    # 未开赛比赛无比分
    assert resp.fixtures[1].status == MatchStatus.SCHEDULED.value
    assert resp.fixtures[1].score is None


@pytest.mark.integration
async def test_empty_day_returns_no_fixtures(db_session: AsyncSession) -> None:
    resp = await _build(db_session, TARGET)
    assert resp.count == 0
    assert resp.fixtures == []
