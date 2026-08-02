"""比赛官方阵容同步的 API 响应模型。"""

from pydantic import BaseModel, ConfigDict, Field


class FixtureLineupSyncReport(BaseModel):
    """一次比赛官方阵容同步的可序列化审计摘要。"""

    model_config = ConfigDict(from_attributes=True)

    source: str
    fixture_external_id: str
    lineups_received: int = Field(ge=0)
    players_received: int = Field(ge=0)
    lineups_created: int = Field(ge=0)
    lineups_unchanged: int = Field(ge=0)
