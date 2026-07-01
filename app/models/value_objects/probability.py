"""Probability value object."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Probability:
    """A probability constrained to the closed interval [0, 1]."""

    value: float

    def __post_init__(self) -> None:
        if not 0.0 <= self.value <= 1.0:
            raise ValueError(f"probability must be in [0, 1], got {self.value}")

    @property
    def complement(self) -> Probability:
        """The probability of the event not occurring."""
        return Probability(1.0 - self.value)

    @property
    def percent(self) -> float:
        return self.value * 100.0

    def __float__(self) -> float:
        return self.value
