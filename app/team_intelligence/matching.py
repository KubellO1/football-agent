"""Exact provider-to-canonical identity matching with no fuzzy guesses."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta


def canonical_name(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii").casefold()
    return re.sub(r"[^a-z0-9]+", " ", ascii_value).strip()


@dataclass(frozen=True, slots=True)
class FixtureIdentity:
    fixture_id: str
    competition: str
    kickoff: datetime
    home_team: str
    away_team: str


@dataclass(frozen=True, slots=True)
class MatchResult:
    canonical_id: str | None
    reason_code: str


class IdentityResolver:
    """Resolve stable external IDs first and exact normalized names second."""

    def __init__(self) -> None:
        self._external: dict[tuple[str, str, str], str] = {}
        self._names: dict[tuple[str, str], set[str]] = {}

    def register(
        self,
        *,
        entity_type: str,
        canonical_id: str,
        source_name: str,
        external_id: str,
        names: tuple[str, ...] = (),
    ) -> None:
        key = (entity_type, source_name.casefold(), external_id.strip())
        existing = self._external.get(key)
        if existing is not None and existing != canonical_id:
            raise ValueError("external identity maps to multiple canonical IDs")
        self._external[key] = canonical_id
        for name in names:
            normalized = canonical_name(name)
            if normalized:
                self._names.setdefault((entity_type, normalized), set()).add(canonical_id)

    def resolve(
        self,
        *,
        entity_type: str,
        source_name: str,
        external_id: str | None,
        name: str | None = None,
    ) -> MatchResult:
        if external_id:
            value = self._external.get((entity_type, source_name.casefold(), external_id.strip()))
            if value is not None:
                return MatchResult(value, "MATCHED_EXTERNAL_ID")
        if name:
            candidates = self._names.get((entity_type, canonical_name(name)), set())
            if len(candidates) == 1:
                return MatchResult(next(iter(candidates)), "MATCHED_EXACT_NAME")
            if len(candidates) > 1:
                return MatchResult(None, "AMBIGUOUS_EXACT_NAME")
        return MatchResult(None, "NO_EXACT_IDENTITY_MATCH")


class FixtureMatcher:
    def __init__(self, *, kickoff_tolerance: timedelta = timedelta(minutes=10)) -> None:
        self._kickoff_tolerance = kickoff_tolerance

    def match(
        self,
        candidate: FixtureIdentity,
        fixtures: tuple[FixtureIdentity, ...],
    ) -> MatchResult:
        candidates = [
            fixture
            for fixture in fixtures
            if canonical_name(fixture.competition) == canonical_name(candidate.competition)
            and canonical_name(fixture.home_team) == canonical_name(candidate.home_team)
            and canonical_name(fixture.away_team) == canonical_name(candidate.away_team)
            and abs(fixture.kickoff - candidate.kickoff) <= self._kickoff_tolerance
        ]
        if not candidates:
            return MatchResult(None, "NO_EXACT_FIXTURE_MATCH")
        if len(candidates) > 1:
            return MatchResult(None, "AMBIGUOUS_FIXTURE_MATCH")
        return MatchResult(candidates[0].fixture_id, "MATCHED_FIXTURE")
