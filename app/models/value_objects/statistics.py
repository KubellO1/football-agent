"""Aggregated statistics value objects.

Immutable snapshots of form/output for a team or player over some window.
The window itself (season, last-N) is defined by whatever attaches these.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TeamStatistics:
    """Aggregated team performance over a window of matches."""

    matches_played: int
    wins: int
    draws: int
    losses: int
    goals_for: int
    goals_against: int
    xg_for: float = 0.0
    xg_against: float = 0.0

    def __post_init__(self) -> None:
        counts = [
            self.matches_played,
            self.wins,
            self.draws,
            self.losses,
            self.goals_for,
            self.goals_against,
        ]
        if any(c < 0 for c in counts):
            raise ValueError("statistical counts cannot be negative")
        if self.wins + self.draws + self.losses > self.matches_played:
            raise ValueError("results exceed matches played")

    @property
    def points(self) -> int:
        return self.wins * 3 + self.draws

    @property
    def goal_difference(self) -> int:
        return self.goals_for - self.goals_against


@dataclass(frozen=True, slots=True)
class PlayerStatistics:
    """Aggregated player output over a window of matches."""

    appearances: int
    minutes: int
    goals: int
    assists: int
    xg: float = 0.0
    xa: float = 0.0
    yellow_cards: int = 0
    red_cards: int = 0

    def __post_init__(self) -> None:
        values = [
            self.appearances,
            self.minutes,
            self.goals,
            self.assists,
            self.yellow_cards,
            self.red_cards,
        ]
        if any(v < 0 for v in values):
            raise ValueError("statistical values cannot be negative")
