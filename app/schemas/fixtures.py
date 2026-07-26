"""只读比赛查询的响应 DTO。"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 - Pydantic 会在运行时解析字段注解
from uuid import UUID  # noqa: TC003 - Pydantic 会在运行时解析字段注解

from pydantic import BaseModel, Field


class TeamOut(BaseModel):
    id: UUID
    name: str


class CompetitionOut(BaseModel):
    id: UUID
    name: str
    country: str


class ScoreOut(BaseModel):
    home: int
    away: int


class FixtureOut(BaseModel):
    """单场比赛的只读视图（含赛事、主/客队、开赛时间、状态、比分）。"""

    id: UUID
    competition: CompetitionOut
    home_team: TeamOut
    away_team: TeamOut
    kickoff: datetime
    status: str = Field(description="比赛状态（MatchStatus 值，如 scheduled/finished）。")
    score: ScoreOut | None = None


class FixturesTodayResponse(BaseModel):
    date: str = Field(description="查询的日期（ISO，UTC）。")
    count: int
    fixtures: list[FixtureOut]
