"""MatchPrediction entity — aggregate root for a fixture's analysis output.

Collects the quantitative model outputs (outcome probabilities, expected goals)
and the resulting value-bet recommendations for a fixture. Produced by the
analysis pipeline; the model families that populate it (Poisson, Elo, Monte
Carlo) live in the service layer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from app.models.entities.base import Entity, utcnow
from app.models.entities.value_bet import ValueBet
from app.models.value_objects.metrics import ExpectedGoals
from app.models.value_objects.probability import Probability
from app.models.value_objects.score import MatchResult


@dataclass(eq=False, kw_only=True)
class MatchPrediction(Entity):
    """The full analytical output for a single fixture."""

    fixture_id: UUID
    outcome_probabilities: dict[MatchResult, Probability] = field(default_factory=dict)
    expected_goals: ExpectedGoals | None = None
    recommendations: list[ValueBet] = field(default_factory=list)
    model_version: str | None = None
    generated_at: datetime = field(default_factory=utcnow)

    @property
    def is_normalized(self) -> bool:
        """Whether the 1X2 outcome probabilities sum to ~1 (tolerance 1e-6)."""
        if not self.outcome_probabilities:
            return False
        total = sum(p.value for p in self.outcome_probabilities.values())
        return abs(total - 1.0) < 1e-6
