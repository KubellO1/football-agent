"""比赛官方阵容 Provider 的标准化传输对象。"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 - Pydantic 运行时解析字段类型
from typing import Annotated, Self

from pydantic import BaseModel, Field, StringConstraints, field_validator, model_validator

ExternalIdentifier = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=120),
]
SourceName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=80),
]
PlayerName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=120),
]
RawPosition = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=80),
]
OptionalFormation = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=40),
]
OptionalGridPosition = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=20),
]
OptionalReference = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=500),
]


class ProviderLineupPlayer(BaseModel):
    """供应商返回的一名首发或替补球员。"""

    player_external_id: ExternalIdentifier
    player_name: PlayerName
    raw_position: RawPosition
    shirt_number: int | None = Field(default=None, ge=0, le=999)
    grid_position: OptionalGridPosition | None = None


class ProviderTeamLineup(BaseModel):
    """一支球队已经公布的完整比赛阵容。"""

    team_external_id: ExternalIdentifier
    formation: OptionalFormation | None = None
    starting: list[ProviderLineupPlayer]
    substitutes: list[ProviderLineupPlayer] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_roster_structure(self) -> Self:
        if len(self.starting) != 11:
            raise ValueError("team lineup must contain exactly eleven starting players")

        starting_ids = [player.player_external_id for player in self.starting]
        substitute_ids = [player.player_external_id for player in self.substitutes]
        if len(starting_ids) != len(set(starting_ids)):
            raise ValueError("starting players must be unique")
        if len(substitute_ids) != len(set(substitute_ids)):
            raise ValueError("substitute players must be unique")
        if set(starting_ids) & set(substitute_ids):
            raise ValueError("a player cannot be both starting and a substitute")
        return self


class ProviderFixtureLineupBatch(BaseModel):
    """一次比赛官方阵容采集结果及其审计元数据。"""

    source: SourceName
    fixture_external_id: ExternalIdentifier
    captured_at: datetime
    response_complete: bool
    lineups: list[ProviderTeamLineup] = Field(default_factory=list)
    request_reference: OptionalReference | None = None

    @field_validator("captured_at")
    @classmethod
    def validate_captured_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("captured_at must be timezone-aware")
        return value

    @model_validator(mode="after")
    def validate_fixture_structure(self) -> Self:
        if len(self.lineups) not in (0, 2):
            raise ValueError("fixture lineups must contain zero or two teams")

        team_ids = [lineup.team_external_id for lineup in self.lineups]
        if len(team_ids) != len(set(team_ids)):
            raise ValueError("fixture lineup teams must be unique")

        player_ids = [
            player.player_external_id
            for lineup in self.lineups
            for player in (*lineup.starting, *lineup.substitutes)
        ]
        if len(player_ids) != len(set(player_ids)):
            raise ValueError("a player cannot appear in both team lineups")
        return self
