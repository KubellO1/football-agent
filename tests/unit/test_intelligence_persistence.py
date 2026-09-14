from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from app.intelligence.contracts import (
    CaptureWindow,
    DataType,
    Observation,
    SemanticStatus,
    SourcePolicy,
)
from app.intelligence.matching import (
    CanonicalTeam,
    VerifiedVenue,
    resolve_team_identity,
    resolve_venue_coordinates,
)
from app.intelligence.persistence import (
    FixtureIdentityMapping,
    MappingMethod,
    MappingStatus,
    TeamIdentityMapping,
    VenueMapping,
)

NOW = datetime(2026, 9, 14, 12, tzinfo=UTC)


def test_observation_identity_includes_new_point_in_time_contract() -> None:
    observation = Observation(
        fixture_id=str(uuid4()),
        source="openfootball",
        source_url="https://example.test/fixture.json",
        source_record_id="eng.1-2026-1",
        observed_at=NOW,
        published_at=None,
        captured_at=NOW,
        raw_payload_hash="a" * 64,
        parser_version="openfootball-v1",
        schema_version="1",
        data_type=DataType.FIXTURE,
        source_policy=SourcePolicy.OPEN_DATA,
        semantic_status=SemanticStatus.VERIFIED,
        capture_window=CaptureWindow.DAILY,
        payload={"home": "Arsenal", "away": "Liverpool"},
    )

    assert observation.to_dict()["observed_at"] == NOW.isoformat()
    assert len(observation.observation_id) == 64


def test_team_resolution_prefers_unique_external_id() -> None:
    result = resolve_team_identity(
        source="openfootball",
        source_team_name="Different display name",
        source_team_id="arsenal",
        teams=(CanonicalTeam("team-1", "Arsenal", {"openfootball": "arsenal"}),),
    )

    assert result.team_id == "team-1"
    assert result.method is MappingMethod.EXACT_EXTERNAL_ID


def test_team_resolution_never_fuzzy_matches() -> None:
    result = resolve_team_identity(
        source="openfootball",
        source_team_name="Arsen",
        source_team_id=None,
        teams=(CanonicalTeam("team-1", "Arsenal", {}),),
    )

    assert result.status is MappingStatus.UNMAPPED
    assert result.team_id is None


def test_team_resolution_marks_duplicate_normalized_name_ambiguous() -> None:
    teams = (
        CanonicalTeam("team-1", "Paris FC", {}),
        CanonicalTeam("team-2", "Paris-FC", {}),
    )
    result = resolve_team_identity(
        source="openfootball",
        source_team_name="Paris FC",
        source_team_id=None,
        teams=teams,
    )

    assert result.status is MappingStatus.AMBIGUOUS


def test_weather_requires_preverified_unique_coordinates() -> None:
    unavailable = resolve_venue_coordinates("Unknown Stadium", ())
    assert unavailable.status is MappingStatus.WEATHER_UNAVAILABLE

    verified = VerifiedVenue(
        "Stadium de Toulouse",
        Decimal("43.583300"),
        Decimal("1.434000"),
        "https://official.example.test/venues/toulouse",
    )
    matched = resolve_venue_coordinates("Stadium de Toulouse", (verified,))
    assert matched.status is MappingStatus.MATCHED
    assert matched.venue == verified


def test_mapping_contract_rejects_matched_without_target() -> None:
    with pytest.raises(ValueError, match="canonical team"):
        TeamIdentityMapping(
            source_id=uuid4(),
            source_team_name="Toulouse",
            status=MappingStatus.MATCHED,
            method=MappingMethod.EXACT_CANONICAL_NAME,
            captured_at=NOW,
        )


def test_fixture_mapping_key_is_replay_stable() -> None:
    fixture_id = uuid4()
    values = {
        "source_id": uuid4(),
        "source_fixture_id": "fr.1-2026-100",
        "source_competition": "Ligue 1",
        "source_home_team": "Toulouse",
        "source_away_team": "Lille",
        "source_kickoff": NOW,
        "status": MappingStatus.MATCHED,
        "method": MappingMethod.EXACT_EXTERNAL_ID,
        "captured_at": NOW,
        "fixture_id": fixture_id,
    }
    first = FixtureIdentityMapping(**values)
    second = FixtureIdentityMapping(**values)
    assert first.mapping_key == second.mapping_key


def test_venue_mapping_requires_coordinate_provenance() -> None:
    with pytest.raises(ValueError, match="provenance"):
        VenueMapping(
            fixture_id=uuid4(),
            source_id=uuid4(),
            source_venue_name="Stadium",
            status=MappingStatus.MATCHED,
            method=MappingMethod.MANUAL_REVIEWED,
            captured_at=NOW,
            latitude=Decimal("1"),
            longitude=Decimal("2"),
        )
