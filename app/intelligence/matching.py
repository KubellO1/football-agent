"""保守的 fixture/team identity matching。"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from app.intelligence.persistence import MappingMethod, MappingStatus

if TYPE_CHECKING:
    from collections.abc import Mapping
    from decimal import Decimal


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


@dataclass(frozen=True, slots=True)
class CanonicalTeam:
    team_id: str
    name: str
    external_ids: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class TeamMatchResult:
    team_id: str | None
    status: MappingStatus
    method: MappingMethod
    reason_code: str


@dataclass(frozen=True, slots=True)
class VerifiedVenue:
    venue_name: str
    latitude: Decimal
    longitude: Decimal
    provenance_url: str


@dataclass(frozen=True, slots=True)
class VenueMatchResult:
    venue: VerifiedVenue | None
    status: MappingStatus
    method: MappingMethod
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


def resolve_team_identity(
    *,
    source: str,
    source_team_name: str,
    source_team_id: str | None,
    teams: tuple[CanonicalTeam, ...],
) -> TeamMatchResult:
    """只接受唯一外部 ID 或唯一规范名，不做模糊写穿。"""

    if source_team_id:
        external_matches = [
            team for team in teams if team.external_ids.get(source) == source_team_id
        ]
        if len(external_matches) == 1:
            return TeamMatchResult(
                external_matches[0].team_id,
                MappingStatus.MATCHED,
                MappingMethod.EXACT_EXTERNAL_ID,
                "MATCHED_EXTERNAL_ID",
            )
        if len(external_matches) > 1:
            return TeamMatchResult(
                None,
                MappingStatus.AMBIGUOUS,
                MappingMethod.NONE,
                "AMBIGUOUS_EXTERNAL_ID",
            )
    normalized = canonical_name(source_team_name)
    name_matches = [team for team in teams if canonical_name(team.name) == normalized]
    if len(name_matches) == 1:
        return TeamMatchResult(
            name_matches[0].team_id,
            MappingStatus.MATCHED,
            MappingMethod.EXACT_CANONICAL_NAME,
            "MATCHED_CANONICAL_NAME",
        )
    if len(name_matches) > 1:
        return TeamMatchResult(
            None,
            MappingStatus.AMBIGUOUS,
            MappingMethod.NONE,
            "AMBIGUOUS_CANONICAL_NAME",
        )
    return TeamMatchResult(
        None,
        MappingStatus.UNMAPPED,
        MappingMethod.NONE,
        "NO_EXACT_TEAM_MATCH",
    )


def resolve_venue_coordinates(
    source_venue_name: str,
    verified_venues: tuple[VerifiedVenue, ...],
) -> VenueMatchResult:
    """只使用预先审核的唯一球场坐标，不调用地理编码或猜测。"""

    normalized = canonical_name(source_venue_name)
    matches = [venue for venue in verified_venues if canonical_name(venue.venue_name) == normalized]
    if len(matches) == 1:
        return VenueMatchResult(
            matches[0],
            MappingStatus.MATCHED,
            MappingMethod.MANUAL_REVIEWED,
            "MATCHED_VERIFIED_VENUE",
        )
    if len(matches) > 1:
        return VenueMatchResult(
            None,
            MappingStatus.AMBIGUOUS,
            MappingMethod.NONE,
            "AMBIGUOUS_VENUE",
        )
    return VenueMatchResult(
        None,
        MappingStatus.WEATHER_UNAVAILABLE,
        MappingMethod.NONE,
        "WEATHER_UNAVAILABLE_NO_VERIFIED_COORDINATES",
    )
