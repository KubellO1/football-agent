"""Abstract contract for the reasoning layer.

Services depend on ``ReasoningEngine``, not on Claude directly — so the LLM can
be swapped or faked in tests without touching business logic.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from app.schemas.reasoning import ReasoningContext, ReasoningOutput


class ReasoningEngine(ABC):
    """Reviews model-generated betting recommendations and returns a structured
    qualitative assessment. Implementations must never override the quantitative
    numbers in the context."""

    @abstractmethod
    async def analyze(self, context: ReasoningContext) -> ReasoningOutput:
        """Return a structured review of the fixture's candidate bets."""
        raise NotImplementedError
