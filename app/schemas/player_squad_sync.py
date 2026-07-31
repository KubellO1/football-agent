"""球队阵容采集端点的响应结构。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class PlayerSquadSyncReport(BaseModel):
    """一次球队阵容主数据同步的可序列化结果。"""

    model_config = ConfigDict(from_attributes=True)

    source: str
    team_external_id: str
    records_received: int = Field(ge=0)
    records_created: int = Field(ge=0)
    records_updated: int = Field(ge=0)
    records_unchanged: int = Field(ge=0)


class FixtureSquadSyncReport(BaseModel):
    """一次比赛主客两队阵容同步的可序列化汇总。"""

    model_config = ConfigDict(from_attributes=True)

    source: str
    fixture_external_id: str
    home_team: PlayerSquadSyncReport
    away_team: PlayerSquadSyncReport
    records_received: int = Field(ge=0)
    records_created: int = Field(ge=0)
    records_updated: int = Field(ge=0)
    records_unchanged: int = Field(ge=0)
