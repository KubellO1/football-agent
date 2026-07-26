"""Response models for injury data.

Normalized DTOs from API-Football /v3/injuries endpoint.
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field


class PlayerInjury(BaseModel):
    """A single player injury record."""

    player_id: int
    player_name: str = ""
    player_photo: str = ""
    team_id: int
    team_name: str = ""
    team_logo: str = ""
    injury_type: str = ""  # "Missing Fixture", "Questionable", etc.
    injury_reason: str = ""
    fixture_id: int | None = None
    fixture_date: date | None = None
    league_id: int | None = None
    league_name: str = ""
    league_season: int | None = None


class TeamInjuries(BaseModel):
    """Injury list for one team in one fixture."""

    fixture_id: int
    team_id: int
    team_name: str = ""
    players: list[PlayerInjury] = Field(default_factory=list)
    total_injured: int = 0
