"""球队单场统计快照实体。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from app.models.entities.base import Entity, utcnow

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID

    from app.models.value_objects.statistics import TeamMatchMetrics


@dataclass(eq=False, kw_only=True)
class TeamMatchStatistics(Entity):
    """一支球队在一场比赛中的追加式原始统计快照。"""

    fixture_id: UUID
    team_id: UUID
    source: str
    metrics: TeamMatchMetrics
    captured_at: datetime = field(default_factory=utcnow)
    source_updated_at: datetime | None = None
    is_final: bool = False

    def __post_init__(self) -> None:
        self.source = self.source.strip()
        if not self.source:
            raise ValueError("source cannot be empty")
        if len(self.source) > 40:
            raise ValueError("source cannot exceed 40 characters")
        if not self._is_timezone_aware(self.captured_at):
            raise ValueError("captured_at must be timezone-aware")
        if self.source_updated_at is not None:
            if not self._is_timezone_aware(self.source_updated_at):
                raise ValueError("source_updated_at must be timezone-aware")
            if self.source_updated_at > self.captured_at:
                raise ValueError("source_updated_at cannot be later than captured_at")

    @staticmethod
    def _is_timezone_aware(value: datetime) -> bool:
        return value.tzinfo is not None and value.utcoffset() is not None
