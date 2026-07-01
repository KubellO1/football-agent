"""Market movement value object.

Summarizes how the price for a selection moved between two points in time.
Derived purely from two ``Odds`` values.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from app.models.value_objects.odds import Odds


class MovementDirection(str, Enum):
    SHORTENING = "shortening"  # odds falling → implied probability rising
    DRIFTING = "drifting"  # odds rising → implied probability falling
    STABLE = "stable"


@dataclass(frozen=True, slots=True)
class MarketMovement:
    """Opening vs. current price for a selection."""

    opening: Odds
    current: Odds

    @property
    def decimal_delta(self) -> float:
        return float(self.current.decimal - self.opening.decimal)

    @property
    def implied_probability_shift(self) -> float:
        """Change in implied probability (current minus opening)."""
        return self.current.implied_probability.value - self.opening.implied_probability.value

    @property
    def direction(self) -> MovementDirection:
        if self.current.decimal < self.opening.decimal:
            return MovementDirection.SHORTENING
        if self.current.decimal > self.opening.decimal:
            return MovementDirection.DRIFTING
        return MovementDirection.STABLE
