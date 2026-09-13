"""保守的 fixture/team identity matching。"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True, slots=True)
class FixtureIdentity:
    fixture_id: str
    competition: str
    kickoff: datetime
    home_team: str
    away_team: str


@dataclass(frozen=True, slots=True)
class MatchResult:
    fixture_id: str | None
    reason_code: str


def canonical_name(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii").casefold()
    return re.sub(r"[^a-z0-9]+", " ", ascii_value).strip()


class FixtureMatcher:
    """只接受唯一精确规范名与接近开赛时间，禁止模糊猜测。"""

    def __init__(self, *, kickoff_tolerance: timedelta = timedelta(minutes=10)) -> None:
        self._kickoff_tolerance = kickoff_tolerance

    def match(
        self,
        candidate: FixtureIdentity,
        fixtures: tuple[FixtureIdentity, ...],
    ) -> MatchResult:
        matching = [
            fixture
            for fixture in fixtures
            if canonical_name(fixture.competition) == canonical_name(candidate.competition)
            and canonical_name(fixture.home_team) == canonical_name(candidate.home_team)
            and canonical_name(fixture.away_team) == canonical_name(candidate.away_team)
            and abs(fixture.kickoff - candidate.kickoff) <= self._kickoff_tolerance
        ]
        if not matching:
            return MatchResult(None, "NO_EXACT_FIXTURE_MATCH")
        if len(matching) > 1:
            return MatchResult(None, "AMBIGUOUS_FIXTURE_MATCH")
        return MatchResult(matching[0].fixture_id, "MATCHED")
