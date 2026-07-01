"""Odds value object with format conversions.

Internally normalized to decimal (European) odds. Conversions to/from
fractional, American, and implied probability are pure arithmetic — no
betting *policy* lives here (that belongs in the service layer).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from fractions import Fraction

from app.models.value_objects.probability import Probability


class OddsFormat(str, Enum):
    DECIMAL = "decimal"
    FRACTIONAL = "fractional"
    AMERICAN = "american"


@dataclass(frozen=True, slots=True)
class Odds:
    """Betting odds normalized to decimal form (must be > 1.0)."""

    decimal: Decimal

    def __post_init__(self) -> None:
        if not isinstance(self.decimal, Decimal):
            object.__setattr__(self, "decimal", Decimal(str(self.decimal)))
        if self.decimal <= 1:
            raise ValueError(f"decimal odds must be > 1.0, got {self.decimal}")

    # --- factories ---------------------------------------------------------
    @classmethod
    def from_decimal(cls, value: Decimal | float | str) -> Odds:
        return cls(Decimal(str(value)))

    @classmethod
    def from_fractional(cls, numerator: int, denominator: int) -> Odds:
        return cls(Decimal(numerator) / Decimal(denominator) + 1)

    @classmethod
    def from_american(cls, value: int) -> Odds:
        if value > 0:
            return cls(Decimal(value) / Decimal(100) + 1)
        return cls(Decimal(100) / Decimal(-value) + 1)

    @classmethod
    def from_probability(cls, probability: Probability) -> Odds:
        if probability.value <= 0:
            raise ValueError("cannot derive odds from zero probability")
        return cls(Decimal(1) / Decimal(str(probability.value)))

    # --- conversions -------------------------------------------------------
    @property
    def implied_probability(self) -> Probability:
        """Bookmaker-implied probability (includes the overround / margin)."""
        return Probability(float(Decimal(1) / self.decimal))

    def to_american(self) -> int:
        if self.decimal >= 2:
            return int((self.decimal - 1) * 100)
        return int(-100 / (self.decimal - 1))

    def to_fractional(self) -> tuple[int, int]:
        frac = Fraction(self.decimal - 1).limit_denominator(1000)
        return frac.numerator, frac.denominator
