"""ValueBet entity — a single betting recommendation (aggregate root).

Ties together the market selection, the best available price, the model's
probability, the computed edge, and the recommended stake. ``rationale`` holds
the human-readable reasoning (later produced by the LLM agent); the numbers
above it always come from the quantitative models, never the LLM.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from app.models.entities.base import Entity, utcnow
from app.models.value_objects.betting import Stake, ValueEdge
from app.models.value_objects.markets import Selection
from app.models.value_objects.odds import Odds
from app.models.value_objects.probability import Probability


@dataclass(eq=False, kw_only=True)
class ValueBet(Entity):
    """A recommended value bet on a fixture selection."""

    fixture_id: UUID
    selection: Selection
    odds: Odds
    model_probability: Probability
    edge: ValueEdge
    bookmaker_id: UUID | None = None
    stake: Stake | None = None
    confidence: float | None = None
    rationale: str | None = None
    created_at: datetime = field(default_factory=utcnow)

    def __post_init__(self) -> None:
        if self.confidence is not None and not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be in [0, 1]")
