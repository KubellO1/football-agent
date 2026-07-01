"""Player entity."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from uuid import UUID

from app.models.entities.base import Entity
from app.models.entities.enums import PlayerPosition


@dataclass(eq=False, kw_only=True)
class Player(Entity):
    """A football player, optionally linked to a current team."""

    name: str
    position: PlayerPosition
    team_id: UUID | None = None
    date_of_birth: date | None = None
