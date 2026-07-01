"""Bookmaker entity."""

from __future__ import annotations

from dataclasses import dataclass

from app.models.entities.base import Entity


@dataclass(eq=False, kw_only=True)
class Bookmaker(Entity):
    """An odds provider / bookmaker."""

    name: str
    country: str | None = None
