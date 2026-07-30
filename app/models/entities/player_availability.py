"""球员可用性观察实体。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from app.models.entities.base import Entity, utcnow

if TYPE_CHECKING:
    from datetime import date, datetime
    from uuid import UUID

    from app.models.value_objects.availability import AvailabilitySource, AvailabilityStatus


@dataclass(eq=False, kw_only=True)
class PlayerAvailabilityObservation(Entity):
    """某个数据源在特定时点对球员可用性的原始观察。"""

    fixture_id: UUID
    team_id: UUID
    player_id: UUID
    status: AvailabilityStatus
    source: AvailabilitySource
    captured_at: datetime = field(default_factory=utcnow)
    source_updated_at: datetime | None = None
    reason: str | None = None
    expected_return: date | None = None

    def __post_init__(self) -> None:
        if not self._is_timezone_aware(self.captured_at):
            raise ValueError("captured_at must be timezone-aware")
        if self.source_updated_at is not None:
            if not self._is_timezone_aware(self.source_updated_at):
                raise ValueError("source_updated_at must be timezone-aware")
            if self.source_updated_at > self.captured_at:
                raise ValueError("source_updated_at cannot be later than captured_at")

        if self.reason is not None:
            reason = self.reason.strip()
            if not reason:
                raise ValueError("reason cannot be empty")
            if len(reason) > 500:
                raise ValueError("reason cannot exceed 500 characters")
            self.reason = reason

    @property
    def has_known_status(self) -> bool:
        return self.status.is_known

    @property
    def rules_player_out(self) -> bool:
        return self.status.rules_player_out

    @staticmethod
    def _is_timezone_aware(value: datetime) -> bool:
        return value.tzinfo is not None and value.utcoffset() is not None
