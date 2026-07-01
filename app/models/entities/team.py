"""Team entity."""

from __future__ import annotations

from dataclasses import dataclass

from app.models.entities.base import Entity
from app.models.value_objects.metrics import EloRating


@dataclass(eq=False, kw_only=True)
class Team(Entity):
    """A football club / national team."""

    name: str
    short_name: str | None = None
    country: str | None = None
    elo: EloRating | None = None
