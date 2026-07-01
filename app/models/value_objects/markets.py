"""Betting market types and selections."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class MarketType(str, Enum):
    """Supported betting market families."""

    MATCH_RESULT = "1x2"  # home / draw / away
    DOUBLE_CHANCE = "double_chance"
    OVER_UNDER = "over_under"
    BOTH_TEAMS_TO_SCORE = "btts"
    ASIAN_HANDICAP = "asian_handicap"
    CORRECT_SCORE = "correct_score"
    DRAW_NO_BET = "draw_no_bet"


@dataclass(frozen=True, slots=True)
class Selection:
    """A specific bettable outcome within a market.

    ``code`` identifies the outcome within the market family (e.g. ``"home"``,
    ``"over"``, ``"yes"``). ``line`` carries the handicap/total threshold for
    markets that need one (e.g. Over/Under 2.5 → ``line=2.5``); it is ``None``
    for markets without a line (e.g. 1X2).
    """

    market: MarketType
    code: str
    line: float | None = None

    def __post_init__(self) -> None:
        if not self.code:
            raise ValueError("selection code must be non-empty")
        line_markets = {MarketType.OVER_UNDER, MarketType.ASIAN_HANDICAP}
        if self.market in line_markets and self.line is None:
            raise ValueError(f"{self.market.value} requires a line value")

    @property
    def label(self) -> str:
        base = f"{self.market.value}:{self.code}"
        return f"{base}@{self.line}" if self.line is not None else base
