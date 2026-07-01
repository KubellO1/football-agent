"""Model-metric value objects: Expected Goals and Elo rating.

These carry *values* produced by the analytics layer. The algorithms that
compute them (xG models, the Elo update/expectation formulas, Poisson) live in
the service layer, not here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar


@dataclass(frozen=True, slots=True)
class ExpectedGoals:
    """Expected goals (xG) for both sides of a fixture."""

    home: float
    away: float

    def __post_init__(self) -> None:
        if self.home < 0 or self.away < 0:
            raise ValueError("expected goals cannot be negative")

    @property
    def total(self) -> float:
        return self.home + self.away


@dataclass(frozen=True, slots=True)
class EloRating:
    """An Elo strength rating for a team.

    Holds only the rating value and its invariant. Expected-score and update
    computations belong to the Elo service.
    """

    value: float

    DEFAULT: ClassVar[float] = 1500.0

    def __post_init__(self) -> None:
        if self.value <= 0:
            raise ValueError("Elo rating must be positive")

    def __float__(self) -> float:
        return self.value
