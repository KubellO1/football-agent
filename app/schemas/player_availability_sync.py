"""球员可用性采集端点的响应结构。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class PlayerAvailabilitySyncReport(BaseModel):
    """一次球员可用性采集的可序列化结果。"""

    model_config = ConfigDict(from_attributes=True)

    source: str
    fixture_external_id: str
    records_received: int = Field(ge=0)
    records_created: int = Field(ge=0)
    duplicates_ignored: int = Field(ge=0)
