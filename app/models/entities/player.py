"""Player entity."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.models.entities.base import Entity

if TYPE_CHECKING:
    from datetime import date
    from uuid import UUID

    from app.models.entities.enums import PlayerPosition


@dataclass(eq=False, kw_only=True)
class Player(Entity):
    """A football player, optionally linked to a current team."""

    name: str
    position: PlayerPosition
    team_id: UUID | None = None
    date_of_birth: date | None = None
    external_id: str | None = None
    external_source: str | None = None

    def __post_init__(self) -> None:
        if (self.external_id is None) != (self.external_source is None):
            raise ValueError(
                "external_id and external_source must be provided together",
            )
        if self.external_id is not None and self.external_source is not None:
            external_id = self.external_id.strip()
            external_source = self.external_source.strip()
            if not external_id:
                raise ValueError("external_id cannot be empty")
            if not external_source:
                raise ValueError("external_source cannot be empty")
            if len(external_id) > 120:
                raise ValueError("external_id cannot exceed 120 characters")
            if len(external_source) > 40:
                raise ValueError("external_source cannot exceed 40 characters")
            self.external_id = external_id
            self.external_source = external_source
