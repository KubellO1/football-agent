"""Injury / availability entity."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from uuid import UUID

from app.models.entities.base import Entity, utcnow
from app.models.entities.enums import InjuryStatus


@dataclass(eq=False, kw_only=True)
class Injury(Entity):
    """A player's injury/availability record affecting selection for a fixture."""

    player_id: UUID
    status: InjuryStatus
    reason: str | None = None
    reported_at: datetime = field(default_factory=utcnow)
    expected_return: date | None = None

    @property
    def rules_player_out(self) -> bool:
        return self.status in (InjuryStatus.OUT, InjuryStatus.SUSPENDED)
