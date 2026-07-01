"""Score value object and match result outcome."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class MatchResult(str, Enum):
    """1X2 outcome from the home team's perspective."""

    HOME = "home"
    DRAW = "draw"
    AWAY = "away"


@dataclass(frozen=True, slots=True)
class Score:
    """A goals-scored pair (non-negative integers)."""

    home: int
    away: int

    def __post_init__(self) -> None:
        if self.home < 0 or self.away < 0:
            raise ValueError("goals cannot be negative")

    @property
    def result(self) -> MatchResult:
        if self.home > self.away:
            return MatchResult.HOME
        if self.home < self.away:
            return MatchResult.AWAY
        return MatchResult.DRAW

    @property
    def total_goals(self) -> int:
        return self.home + self.away

    @property
    def both_teams_scored(self) -> bool:
        return self.home > 0 and self.away > 0

    @property
    def is_draw(self) -> bool:
        return self.home == self.away
