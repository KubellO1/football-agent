"""Competition and Season entities."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from uuid import UUID

from app.models.entities.base import Entity


@dataclass(eq=False, kw_only=True)
class Competition(Entity):
    """A league or cup competition (e.g. Premier League)."""

    name: str
    country: str
    tier: int | None = None


@dataclass(eq=False, kw_only=True)
class Season(Entity):
    """A single season of a competition (e.g. 2025/26)."""

    competition_id: UUID
    label: str
    start_date: date | None = None
    end_date: date | None = None
