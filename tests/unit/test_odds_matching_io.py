"""Unit tests for odds_api_io → fixture matching logic.

Covers ``match_odds_event_to_fixture``: exact/fuzzy match, reversed rejection,
ambiguity, and time tolerance.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.models.entities.fixture import Fixture
from app.providers.schemas.odds import ProviderFixtureOdds
from app.services.odds_matching import match_odds_event_to_fixture

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _fixture(
    fixture_id: str,
    home_id: str,
    away_id: str,
    kickoff: datetime,
) -> Fixture:
    return Fixture(
        id=uuid.UUID(fixture_id),
        competition_id=uuid.uuid4(),
        home_team_id=uuid.UUID(home_id),
        away_team_id=uuid.UUID(away_id),
        kickoff=kickoff,
        external_id=str(uuid.uuid4()),
        external_source="api-football",
        score=None,
    )


def _odds_event(
    event_id: str,
    home: str,
    away: str,
    commence: str,
) -> ProviderFixtureOdds:
    return ProviderFixtureOdds(
        provider_id=event_id,
        home_team=home,
        away_team=away,
        commence_time=datetime.fromisoformat(commence),
        sport_key="soccer_epl",
        bookmakers=[],
    )


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------


def test_exact_match() -> None:
    """Team names are identical → EXACT."""
    home_id = str(uuid.uuid4())
    away_id = str(uuid.uuid4())
    team_names = {
        uuid.UUID(home_id): "Manchester United",
        uuid.UUID(away_id): "Liverpool",
    }
    kickoff = datetime(2026, 7, 23, 15, 0, tzinfo=UTC)
    fix = _fixture(
        "11111111-1111-1111-1111-111111111111",
        home_id,
        away_id,
        kickoff,
    )
    event = _odds_event(
        "ev-1",
        "Manchester United",
        "Liverpool",
        "2026-07-23T15:00:00+00:00",
    )

    matched, method = match_odds_event_to_fixture(event, [fix], team_names)
    assert matched is not None
    assert matched.id == uuid.UUID("11111111-1111-1111-1111-111111111111")
    assert method == "EXACT"


def test_fuzzy_match_names_are_normalised() -> None:
    """FC / AFC suffixes differ but normalise to same → FUZZY."""
    home_id = str(uuid.uuid4())
    away_id = str(uuid.uuid4())
    team_names = {
        uuid.UUID(home_id): "Manchester United",
        uuid.UUID(away_id): "Liverpool FC",
    }
    kickoff = datetime(2026, 7, 23, 15, 0, tzinfo=UTC)
    fix = _fixture(
        "22222222-2222-2222-2222-222222222222",
        home_id,
        away_id,
        kickoff,
    )
    event = _odds_event(
        "ev-2",
        "Manchester United FC",  # extra FC suffix
        "Liverpool",
        "2026-07-23T15:00:00+00:00",
    )

    matched, method = match_odds_event_to_fixture(event, [fix], team_names)
    assert matched is not None
    assert method == "FUZZY"


def test_reversed_home_away_rejected() -> None:
    """Home/away swapped → REVERSED, fixture=None."""
    home_id = str(uuid.uuid4())
    away_id = str(uuid.uuid4())
    team_names = {
        uuid.UUID(home_id): "Arsenal",
        uuid.UUID(away_id): "Tottenham",
    }
    kickoff = datetime(2026, 7, 23, 15, 0, tzinfo=UTC)
    fix = _fixture(
        "33333333-3333-3333-3333-333333333333",
        home_id,
        away_id,
        kickoff,
    )
    event = _odds_event(
        "ev-3",
        "Tottenham",  # reversed: event says Tottenham at home
        "Arsenal",
        "2026-07-23T15:00:00+00:00",
    )

    matched, method = match_odds_event_to_fixture(event, [fix], team_names)
    assert matched is None
    assert method == "REVERSED"


def test_multiple_matches_ambiguous() -> None:
    """Two fixtures with same names + kickoff → AMBIGUOUS."""
    home_id = str(uuid.uuid4())
    away_id = str(uuid.uuid4())
    team_names = {
        uuid.UUID(home_id): "Arsenal",
        uuid.UUID(away_id): "Chelsea",
    }
    kickoff = datetime(2026, 7, 23, 15, 0, tzinfo=UTC)
    fix1 = _fixture(
        "44444444-4444-4444-4444-444444444444",
        str(uuid.uuid4()),
        str(uuid.uuid4()),
        kickoff,
    )
    team_names[uuid.UUID(str(fix1.home_team_id))] = "Arsenal"
    team_names[uuid.UUID(str(fix1.away_team_id))] = "Chelsea"
    fix2 = _fixture(
        "55555555-5555-5555-5555-555555555555",
        str(uuid.uuid4()),
        str(uuid.uuid4()),
        kickoff,
    )
    team_names[uuid.UUID(str(fix2.home_team_id))] = "Arsenal"
    team_names[uuid.UUID(str(fix2.away_team_id))] = "Chelsea"

    event = _odds_event(
        "ev-4",
        "Arsenal",
        "Chelsea",
        "2026-07-23T15:00:00+00:00",
    )

    matched, method = match_odds_event_to_fixture(event, [fix1, fix2], team_names)
    assert matched is None
    assert method == "AMBIGUOUS"


def test_time_tolerance_boundary_pass() -> None:
    """14 minutes difference → passes with 15 min tolerance."""
    home_id = str(uuid.uuid4())
    away_id = str(uuid.uuid4())
    team_names = {
        uuid.UUID(home_id): "Arsenal",
        uuid.UUID(away_id): "Chelsea",
    }
    kickoff = datetime(2026, 7, 23, 15, 0, tzinfo=UTC)
    fix = _fixture(
        "66666666-6666-6666-6666-666666666666",
        home_id,
        away_id,
        kickoff,
    )
    # commence 14 min earlier → still within 15 min tolerance
    event = _odds_event(
        "ev-5",
        "Arsenal",
        "Chelsea",
        "2026-07-23T14:46:00+00:00",
    )

    matched, method = match_odds_event_to_fixture(event, [fix], team_names)
    assert matched is not None
    assert method == "EXACT"


def test_time_tolerance_boundary_fail() -> None:
    """16 minutes difference → FAILED with 15 min tolerance."""
    home_id = str(uuid.uuid4())
    away_id = str(uuid.uuid4())
    team_names = {
        uuid.UUID(home_id): "Arsenal",
        uuid.UUID(away_id): "Chelsea",
    }
    kickoff = datetime(2026, 7, 23, 15, 0, tzinfo=UTC)
    fix = _fixture(
        "77777777-7777-7777-7777-777777777777",
        home_id,
        away_id,
        kickoff,
    )
    event = _odds_event(
        "ev-6",
        "Arsenal",
        "Chelsea",
        "2026-07-23T14:44:00+00:00",
    )

    matched, method = match_odds_event_to_fixture(event, [fix], team_names)
    assert matched is None
    assert method == "FAILED"


def test_no_match_returns_failed() -> None:
    """Completely different teams → FAILED."""
    home_id = str(uuid.uuid4())
    away_id = str(uuid.uuid4())
    team_names = {
        uuid.UUID(home_id): "Arsenal",
        uuid.UUID(away_id): "Chelsea",
    }
    kickoff = datetime(2026, 7, 23, 15, 0, tzinfo=UTC)
    fix = _fixture(
        "88888888-8888-8888-8888-888888888888",
        home_id,
        away_id,
        kickoff,
    )
    event = _odds_event(
        "ev-7",
        "Real Madrid",
        "Barcelona",
        "2026-07-23T15:00:00+00:00",
    )

    matched, method = match_odds_event_to_fixture(event, [fix], team_names)
    assert matched is None
    assert method == "FAILED"
