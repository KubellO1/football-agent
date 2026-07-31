"""球队阵容 Provider 的标准化传输对象。"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 - Pydantic 运行时解析字段类型
from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints, field_validator

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
OptionalReference = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=500),
]


class ProviderSquadPlayer(BaseModel):
    """供应商返回的单个球队阵容成员。"""

    player_external_id: ExternalIdentifier
    player_name: PlayerName
    raw_position: RawPosition


class ProviderSquadBatch(BaseModel):
    """一次球队阵容采集结果及其审计元数据。"""

    source: SourceName
    team_external_id: ExternalIdentifier
    captured_at: datetime
    response_complete: bool
    records: list[ProviderSquadPlayer] = Field(default_factory=list)
    request_reference: OptionalReference | None = None

    @field_validator("captured_at")
    @classmethod
    def validate_captured_at(cls, value: datetime) -> datetime:
        """采集时间必须带时区，供后续审计和时点查询使用。"""
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("captured_at must be timezone-aware")
        return value
