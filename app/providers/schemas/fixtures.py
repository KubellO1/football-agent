"""Response models for the fixtures feed.

Provider-normalized DTOs — the shape a ``FixturesProvider`` returns after it has
parsed a raw upstream payload. These are intentionally decoupled from the domain
entities (``app.models.entities.fixture.Fixture``): mapping raw feeds onto the
domain is a service-layer concern and is *not* wired here yet.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class ProviderTeam(BaseModel):
    """A team as identified by the upstream provider."""

    provider_id: str = Field(description="The provider's own team identifier.")
    name: str


class ProviderFixture(BaseModel):
    """A single match as reported by the fixtures provider."""

    provider_id: str = Field(description="The provider's own fixture identifier.")
    kickoff: datetime = Field(description="Scheduled kick-off, timezone-aware UTC.")
    status: str = Field(description="Raw upstream status code (e.g. 'NS', 'FT').")
    home: ProviderTeam
    away: ProviderTeam
    league: str | None = None
    league_id: str | None = Field(default=None, description="The provider's league id.")
    league_country: str | None = None
    season: int | None = None
    home_score: int | None = None
    away_score: int | None = None
    venue: str | None = None
