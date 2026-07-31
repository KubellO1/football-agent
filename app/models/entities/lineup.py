"""比赛阵容实体。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from app.models.entities.base import Entity, utcnow
from app.models.value_objects.lineup import LineupStatus

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID

    from app.models.value_objects.lineup import Formation, LineupSource


@dataclass(eq=False, kw_only=True)
class Lineup(Entity):
    """一支球队在指定比赛和决策时点的阵容快照。"""

    fixture_id: UUID
    team_id: UUID
    status: LineupStatus
    source: LineupSource
    starting: tuple[UUID, ...]
    substitutes: tuple[UUID, ...] = field(default_factory=tuple)
    formation: Formation | None = None
    captured_at: datetime = field(default_factory=utcnow)
    source_updated_at: datetime | None = None

    def __post_init__(self) -> None:
        self.starting = tuple(self.starting)
        self.substitutes = tuple(self.substitutes)

        if len(self.starting) != 11:
            raise ValueError("lineup must contain exactly eleven starting players")
        if len(set(self.starting)) != len(self.starting):
            raise ValueError("starting players must be unique")
        if len(set(self.substitutes)) != len(self.substitutes):
            raise ValueError("substitute players must be unique")

        overlap = set(self.starting) & set(self.substitutes)
        if overlap:
            raise ValueError("a player cannot be both starting and a substitute")

        if not self._is_timezone_aware(self.captured_at):
            raise ValueError("captured_at must be timezone-aware")
        if self.source_updated_at is not None:
            if not self._is_timezone_aware(self.source_updated_at):
                raise ValueError("source_updated_at must be timezone-aware")
            if self.source_updated_at > self.captured_at:
                raise ValueError("source_updated_at cannot be later than captured_at")

    @property
    def is_confirmed(self) -> bool:
        return self.status is LineupStatus.CONFIRMED

    @property
    def bench(self) -> tuple[UUID, ...]:
        """兼容旧领域名称；新代码应使用 substitutes。"""
        return self.substitutes

    @staticmethod
    def _is_timezone_aware(value: datetime) -> bool:
        return value.tzinfo is not None and value.utcoffset() is not None
