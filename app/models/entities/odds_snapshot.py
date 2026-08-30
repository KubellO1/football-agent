"""Odds snapshot entity.

A single observation of a price for a selection from one bookmaker at one point
in time. A time-ordered series of snapshots is what market-movement analysis
consumes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from app.models.entities.base import Entity, utcnow

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID

    from app.models.value_objects.markets import Selection
    from app.models.value_objects.odds import Odds


@dataclass(eq=False, kw_only=True)
class OddsSnapshot(Entity):
    """A captured price for a selection from a bookmaker at a moment in time."""

    fixture_id: UUID
    bookmaker_id: UUID
    selection: Selection
    odds: Odds
    captured_at: datetime = field(default_factory=utcnow)
    provider_source: str | None = None
    provider_event_id: str | None = None
