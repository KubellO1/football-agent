"""Betting-specific value objects: Stake and ValueEdge."""

from __future__ import annotations

from dataclasses import dataclass

from app.models.value_objects.money import Money
from app.models.value_objects.odds import Odds
from app.models.value_objects.probability import Probability


@dataclass(frozen=True, slots=True)
class Stake:
    """A recommended stake: a monetary amount and its fraction of bankroll.

    This is a value holder. The staking *strategy* (e.g. Kelly Criterion) that
    produces ``fraction_of_bankroll`` lives in the service layer.
    """

    amount: Money
    fraction_of_bankroll: float = 0.0

    def __post_init__(self) -> None:
        if not 0.0 <= self.fraction_of_bankroll <= 1.0:
            raise ValueError("fraction_of_bankroll must be in [0, 1]")


@dataclass(frozen=True, slots=True)
class ValueEdge:
    """The edge of a model probability against offered odds.

    ``edge`` = model_probability × decimal_odds − 1. A positive edge indicates a
    value bet. The *decision threshold* (how much edge is required to bet) is a
    policy that lives in the value-betting service, not here.
    """

    model_probability: Probability
    odds: Odds

    @property
    def edge(self) -> float:
        return float(self.odds.decimal) * self.model_probability.value - 1.0

    @property
    def expected_value_per_unit(self) -> float:
        """Expected profit per unit staked (equal to the edge)."""
        return self.edge

    @property
    def is_value(self) -> bool:
        return self.edge > 0.0
