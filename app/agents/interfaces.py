"""Abstract contract for the reasoning layer.

Services depend on ``ReasoningEngine``, not on a specific LLM provider — so it can
be swapped or faked in tests without touching business logic.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.schemas.committee_review import CommitteeReview, CommitteeReviewContext
    from app.schemas.reasoning import ReasoningContext, ReasoningOutput


class ReasoningEngine(ABC):
    """Reviews model-generated betting recommendations and returns a structured
    qualitative assessment. Implementations must never override the quantitative
    numbers in the context."""

    @abstractmethod
    async def analyze(self, context: ReasoningContext) -> ReasoningOutput:
        """Return a structured review of the fixture's candidate bets."""
        raise NotImplementedError


class CommitteeReviewer(ABC):
    """Expert-committee review layer over the deterministic analysis.

    Explains and critiques the model's output; it must never recompute or alter
    any number. Disagreements are recorded, not acted upon.
    """

    @abstractmethod
    async def review(self, context: CommitteeReviewContext) -> CommitteeReview:
        """Return a structured qualitative review of the fixture's analysis."""
        raise NotImplementedError
