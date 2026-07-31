"""球员可用性 Provider 的标准化传输对象。"""

from __future__ import annotations

from datetime import date, datetime  # noqa: TC003 - Pydantic 运行时解析字段类型
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
RawStatus = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=100),
]
OptionalReason = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=500),
]
OptionalReference = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=500),
]


class ProviderPlayerAvailability(BaseModel):
    """供应商对单个球员可用性的原始事实。"""

    team_external_id: ExternalIdentifier
    player_external_id: ExternalIdentifier
    player_name: Annotated[
        str,
        StringConstraints(strip_whitespace=True, max_length=120),
    ] = ""
    raw_status: RawStatus
    reason: OptionalReason | None = None
    source_updated_at: datetime | None = None
    expected_return: date | None = None

    @field_validator("source_updated_at")
    @classmethod
    def validate_source_updated_at(cls, value: datetime | None) -> datetime | None:
        """供应商更新时间必须带时区，避免决策时间边界不明确。"""
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("source_updated_at must be timezone-aware")
        return value


class ProviderAvailabilityBatch(BaseModel):
    """一次比赛级采集结果及其审计元数据。"""

    source: SourceName
    fixture_external_id: ExternalIdentifier
    captured_at: datetime
    response_complete: bool
    records: list[ProviderPlayerAvailability] = Field(default_factory=list)
    request_reference: OptionalReference | None = None

    @field_validator("captured_at")
    @classmethod
    def validate_captured_at(cls, value: datetime) -> datetime:
        """采集时间必须带时区，供后续 as-of 查询使用。"""
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("captured_at must be timezone-aware")
        return value
