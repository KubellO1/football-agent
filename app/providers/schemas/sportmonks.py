# DEPRECATED: 2026-07-17 - Removed from production. Retained for reference.
"""Response models for Sportmonks feed.

Normalized DTOs for predictions, statistics, transfers, odds, lineups,
injuries, recent form, standings, match centre, and TV stations from Sportmonks v3.
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 - Pydantic 会在运行时解析字段注解
from typing import Any

from pydantic import BaseModel, Field


class SportmonksPrediction(BaseModel):
    """A single prediction type for a fixture (29 types available)."""

    prediction_type_id: int
    prediction_type_name: str = ""
    probability: float | None = None  # percentage 0-100
    winner: str | None = None
    advice: str | None = None


class SportmonksFixturePredictions(BaseModel):
    """All predictions for one fixture (embedded in fixtures response)."""

    fixture_id: int
    predictions: list[SportmonksPrediction] = Field(default_factory=list)


class SportmonksTeamStats(BaseModel):
    """Team statistics for a single fixture (from statistics endpoint)."""

    fixture_id: int
    team_id: int
    team_name: str = ""
    stats: dict[str, Any] = Field(default_factory=dict)
    # Note: xG is NOT available from Sportmonks; use API-Football for xG data.


class SportmonksTransfer(BaseModel):
    """A single transfer record (camelCase from Sportmonks)."""

    transfer_id: int
    player_name: str = ""
    from_team: str = ""
    to_team: str = ""
    transfer_type: str = ""  # "in" / "out"
    amount: float | None = None
    date: datetime | None = None
    season_id: int | None = None


class SportmonksOdds(BaseModel):
    """Odds from Sportmonks (888Sport/Dafabet only — secondary source)."""

    fixture_id: int
    bookmaker_name: str = ""
    market_name: str = ""
    market_id: int | None = None
    outcomes: list[dict[str, Any]] = Field(default_factory=list)


# ── Phase 1: New data types ──


class LineupPlayer(BaseModel):
    """Single player in a lineup (starter or substitute)."""

    player_name: str = ""
    jersey_number: int | None = None
    position_id: int | None = None
    type_id: int | None = None  # 11 = starter, 12 = substitute
    formation_position: int | None = None
    is_starter: bool = True


class TeamLineup(BaseModel):
    """Lineup for one team in a fixture."""

    team_id: int = 0
    team_name: str = ""
    formation: str | None = None
    starters: list[LineupPlayer] = Field(default_factory=list)
    substitutes: list[LineupPlayer] = Field(default_factory=list)


class LineupReport(BaseModel):
    """Complete lineup report for a fixture (both teams)."""

    fixture_id: int = 0
    lineups: list[TeamLineup] = Field(default_factory=list)


class SidelinedPlayer(BaseModel):
    """Injury or suspension record for a player."""

    player_name: str = ""
    player_id: int = 0
    type: str = ""  # "injury" / "suspension"
    type_id: int | None = None
    description: str = ""


class InjuryReport(BaseModel):
    """Injury & suspension report for a fixture.

    IMPORTANT: Explanatory signal only — do NOT feed into models/weights.
    """

    fixture_id: int = 0
    players: list[SidelinedPlayer] = Field(default_factory=list)


class RecentMatch(BaseModel):
    """Single recent match result."""

    fixture_id: int = 0
    opponent: str = ""
    result: str = ""  # "W" / "D" / "L"
    goals_for: int = 0
    goals_against: int = 0
    is_home: bool = True
    date: str = ""


class RecentForm(BaseModel):
    """Last 5 match results with trend indicator."""

    team_id: int = 0
    team_name: str = ""
    matches: list[RecentMatch] = Field(default_factory=list)
    trend: str = ""  # "↑" / "→" / "↓" based on last 5 W/D/L


class StandingsRow(BaseModel):
    """Single row in a league standings table."""

    position: int = 0
    team_name: str = ""
    team_id: int = 0
    played: int = 0
    wins: int = 0
    draws: int = 0
    losses: int = 0
    goals_for: int = 0
    goals_against: int = 0
    goal_diff: int = 0
    points: int = 0


class StandingsTable(BaseModel):
    """League standings table."""

    season_id: int = 0
    group_name: str = ""
    rows: list[StandingsRow] = Field(default_factory=list)


class MatchEvent(BaseModel):
    """Single match event (goal, card, substitution, etc.)."""

    minute: int = 0
    extra_minute: int | None = None
    type: str = ""  # "goal" / "card" / "substitution"
    player_name: str = ""
    related_player_name: str | None = None
    result: str = ""  # e.g., "1-0"
    info: str = ""


class MatchCentreData(BaseModel):
    """Combined match centre: events + timeline + statistics."""

    fixture_id: int = 0
    events: list[MatchEvent] = Field(default_factory=list)
    timeline: list[MatchEvent] = Field(default_factory=list)
    statistics: list[dict[str, Any]] = Field(default_factory=list)


class TVStation(BaseModel):
    """TV broadcast channel for a fixture."""

    name: str = ""
    url: str | None = None
    type: str = ""  # "channel" / "stream"
