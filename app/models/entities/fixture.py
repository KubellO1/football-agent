"""Fixture (match) entity — aggregate root for a single match."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.models.entities.base import Entity
from app.models.entities.enums import MatchStatus
from app.models.value_objects.score import MatchResult, Score


@dataclass(eq=False, kw_only=True)
class Fixture(Entity):
    """A scheduled or played match between two teams.

    Aggregate root: lineups, odds snapshots, and predictions reference a fixture
    by id rather than being embedded, keeping the aggregate small.
    """

    competition_id: UUID
    season_id: UUID | None = None
    home_team_id: UUID
    away_team_id: UUID
    kickoff: datetime
    status: MatchStatus = MatchStatus.SCHEDULED
    score: Score | None = None

    def __post_init__(self) -> None:
        if self.home_team_id == self.away_team_id:
            raise ValueError("a fixture cannot have the same team on both sides")

    @property
    def is_finished(self) -> bool:
        return self.status is MatchStatus.FINISHED

    @property
    def result(self) -> MatchResult | None:
        """Final 1X2 result, or ``None`` if not finished / no score recorded."""
        if not self.is_finished or self.score is None:
            return None
        return self.score.result
