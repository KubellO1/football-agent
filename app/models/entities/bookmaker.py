"""Bookmaker entity."""

from __future__ import annotations

from dataclasses import dataclass

from app.models.entities.base import Entity


@dataclass(eq=False, kw_only=True)
class Bookmaker(Entity):
    """An odds provider / bookmaker."""

    name: str
    country: str | None = None
    # Upstream feed key + source (idempotency key for odds ingestion).
    external_id: str | None = None
    external_source: str | None = None
