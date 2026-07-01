"""Lineup entity."""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from app.models.entities.base import Entity


@dataclass(eq=False, kw_only=True)
class Lineup(Entity):
    """A team's lineup for a fixture (predicted or confirmed).

    Players are referenced by id. ``is_confirmed`` distinguishes an official
    team sheet from a projected XI.
    """

    fixture_id: UUID
    team_id: UUID
    formation: str | None = None
    is_confirmed: bool = False
    starting: list[UUID] = field(default_factory=list)
    bench: list[UUID] = field(default_factory=list)

    def __post_init__(self) -> None:
        overlap = set(self.starting) & set(self.bench)
        if overlap:
            raise ValueError("a player cannot be both starting and on the bench")
