"""只读比赛查询端点。

纯粹从 PostgreSQL 读取，不调用任何外部数据源，也不做预测/下注。
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING

from fastapi import APIRouter

from app.api.deps import (  # noqa: TC001 - FastAPI 会在运行时解析依赖注解
    CompetitionRepositoryDep,
    FixtureRepositoryDep,
    TeamRepositoryDep,
)
from app.schemas.fixtures import (
    CompetitionOut,
    FixtureOut,
    FixturesTodayResponse,
    ScoreOut,
    TeamOut,
)

if TYPE_CHECKING:
    from app.models.entities.fixture import Fixture
    from app.repositories.interfaces.fixture_repository import FixtureRepository
    from app.repositories.interfaces.reference import CompetitionRepository, TeamRepository

router = APIRouter(tags=["fixtures"])


async def build_today_fixtures(
    *,
    fixtures: FixtureRepository,
    teams: TeamRepository,
    competitions: CompetitionRepository,
    target: date,
) -> FixturesTodayResponse:
    """读取 ``target`` 当日（UTC）的比赛并组装成只读响应。

    单次窗口查询取出比赛，再各用一次批量查询解析赛事/球队名称，避免 N+1。
    """
    start = datetime(target.year, target.month, target.day, tzinfo=UTC)
    end = start + timedelta(days=1)

    day_fixtures: list[Fixture] = await fixtures.list_by_kickoff_window(start, end)

    team_ids = {f.home_team_id for f in day_fixtures} | {f.away_team_id for f in day_fixtures}
    competition_ids = {f.competition_id for f in day_fixtures}
    team_map = {t.id: t for t in await teams.list_by_ids(team_ids)}
    competition_map = {c.id: c for c in await competitions.list_by_ids(competition_ids)}

    items: list[FixtureOut] = []
    for f in day_fixtures:
        competition = competition_map[f.competition_id]
        home = team_map[f.home_team_id]
        away = team_map[f.away_team_id]
        items.append(
            FixtureOut(
                id=f.id,
                competition=CompetitionOut(
                    id=competition.id, name=competition.name, country=competition.country
                ),
                home_team=TeamOut(id=home.id, name=home.name),
                away_team=TeamOut(id=away.id, name=away.name),
                kickoff=f.kickoff,
                status=f.status.value,
                score=(
                    ScoreOut(home=f.score.home, away=f.score.away) if f.score is not None else None
                ),
            )
        )

    return FixturesTodayResponse(date=target.isoformat(), count=len(items), fixtures=items)


@router.get("/fixtures/today", response_model=FixturesTodayResponse)
async def fixtures_today(
    fixtures: FixtureRepositoryDep,
    teams: TeamRepositoryDep,
    competitions: CompetitionRepositoryDep,
    on_date: date | None = None,
) -> FixturesTodayResponse:
    """返回今日（UTC）已入库的比赛列表。

    只读：数据来自数据库中此前 ``POST /sync/today`` 同步的结果，本端点不访问
    外部 API。``on_date`` 查询参数（ISO 日期）用于回填/测试，缺省为当前 UTC 日期。
    """
    target = on_date or datetime.now(UTC).date()
    return await build_today_fixtures(
        fixtures=fixtures, teams=teams, competitions=competitions, target=target
    )
