"""Bounded, isolated matching Canary for public five-league observations.

The caller supplies independently sourced fixture references.  This module never
queries production, calls a paid provider, or persists to application tables.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from app.intelligence.adapters import FIVE_LEAGUES, OpenFootballJsonAdapter
from app.intelligence.collection import ContentAddressedArchive, JsonlObservationStore
from app.intelligence.contracts import (
    CaptureWindow,
    DataType,
    Observation,
    SourcePolicy,
    utc_datetime,
)
from app.intelligence.matching import FixtureIdentity, FixtureMatcher

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from app.intelligence.contracts import RawPayload


@dataclass(frozen=True, slots=True)
class BoundedCanaryResult:
    candidate_fixtures: int
    matched_fixtures: int
    selected_fixtures: int
    inserted_observations: int
    replay_duplicates: int
    raw_payloads_created: int
    reason_counts: dict[str, int]
    selected_fixture_ids: tuple[str, ...]


def run_bounded_canary(
    *,
    raw_by_league: Mapping[str, RawPayload],
    references: tuple[FixtureIdentity, ...],
    output_dir: Path,
    now: datetime,
    season: str,
    max_fixtures: int = 10,
) -> BoundedCanaryResult:
    """Append at most ten future, exact-matched fixture observations."""

    if not 1 <= max_fixtures <= 10:
        raise ValueError("Canary fixture limit must be 1..10")
    now_utc = utc_datetime(now, field="now")
    eligible: list[tuple[datetime, Observation]] = []
    reason_counts: Counter[str] = Counter()
    archive = ContentAddressedArchive(output_dir / "raw")
    raw_created = 0
    matcher = FixtureMatcher()
    adapter = OpenFootballJsonAdapter()

    for league in FIVE_LEAGUES:
        raw = raw_by_league.get(league.code)
        if raw is None:
            reason_counts["SOURCE_MISSING"] += 1
            continue
        if raw.source_policy is not SourcePolicy.OPEN_DATA or raw.source_url != league.url(season):
            raise ValueError("unexpected Canary source policy or URL")
        raw_created += int(archive.archive(raw).created)
        for observation in adapter.parse(
            raw, league=league, season=season, capture_window=CaptureWindow.DAILY
        ):
            if observation.data_type is not DataType.FIXTURE:
                continue
            kickoff_value = observation.payload["kickoff"]
            if kickoff_value is None:
                reason_counts["KICKOFF_TIME_UNKNOWN"] += 1
                continue
            kickoff = datetime.fromisoformat(str(kickoff_value))
            if kickoff <= now_utc:
                continue
            eligible.append((kickoff, observation))

    eligible.sort(key=lambda item: (item[0], item[1].fixture_id))
    reason_counts["CANARY_LIMIT"] += max(0, len(eligible) - max_fixtures)
    selected: list[tuple[datetime, Observation]] = []
    for kickoff, observation in eligible[:max_fixtures]:
        league_name = str(observation.payload["competition"])
        country = str(observation.payload["country"])
        candidate = FixtureIdentity(
            fixture_id=observation.fixture_id,
            competition=league_name,
            country=country,
            kickoff=kickoff,
            home_team=str(observation.payload["home_team"]),
            away_team=str(observation.payload["away_team"]),
        )
        match = matcher.match(candidate, references)
        if match.fixture_id is None:
            reason_counts[match.reason_code] += 1
            continue
        mapped = Observation(
            fixture_id=match.fixture_id,
            source=observation.source,
            source_url=observation.source_url,
            published_at=observation.published_at,
            captured_at=observation.captured_at,
            raw_payload_hash=observation.raw_payload_hash,
            parser_version=observation.parser_version,
            data_type=observation.data_type,
            source_policy=observation.source_policy,
            capture_window=observation.capture_window,
            payload=observation.payload,
        )
        selected.append((kickoff, mapped))

    selected.sort(key=lambda item: (item[0], item[1].fixture_id))
    unique: list[Observation] = []
    seen: set[str] = set()
    for _, observation in selected:
        if observation.fixture_id in seen:
            reason_counts["DUPLICATE_FIXTURE_MATCH"] += 1
            continue
        seen.add(observation.fixture_id)
        unique.append(observation)
    result = JsonlObservationStore(output_dir / "observations.jsonl").append(unique)
    return BoundedCanaryResult(
        candidate_fixtures=min(len(eligible), max_fixtures),
        matched_fixtures=len(seen),
        selected_fixtures=len(unique),
        inserted_observations=result.inserted,
        replay_duplicates=result.duplicates,
        raw_payloads_created=raw_created,
        reason_counts=dict(sorted(reason_counts.items())),
        selected_fixture_ids=tuple(item.fixture_id for item in unique),
    )
