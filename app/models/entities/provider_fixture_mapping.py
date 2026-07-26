"""Provider fixture mapping entity.

Associates an API-Football fixture with an external provider (Odds-API.io)
event, recording the confidence and match method that produced the link.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.models.entities.base import Entity


@dataclass(eq=False, kw_only=True)
class ProviderFixtureMapping(Entity):
    """Links an API-Football fixture to an external odds provider event."""

    api_football_fixture_id: str
    odds_api_io_event_id: str
    confidence: str  # "HIGH" / "MEDIUM" / "LOW"
    match_method: str  # "EXACT" / "FUZZY"
    matched_at: datetime
