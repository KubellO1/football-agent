from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.providers.schemas.fixture_lineup import (
    ProviderFixtureLineupBatch,
    ProviderLineupPlayer,
    ProviderTeamLineup,
)
from app.providers.schemas.player_availability import (
    ProviderAvailabilityBatch,
    ProviderPlayerAvailability,
)
from app.team_intelligence.api_football import (
    ApiFootballTeamIntelligenceAdapter,
    MappingFailure,
)
from app.team_intelligence.contracts import CaptureWindow, DataType
from app.team_intelligence.matching import IdentityResolver
from app.team_intelligence.storage import ObservationStore

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)
RAW_HASH = "a" * 64
SOURCE_URL = "https://v3.football.api-sports.io/injuries?fixture=1000"


def _resolver() -> IdentityResolver:
    resolver = IdentityResolver()
    for entity, canonical, external in (
        ("fixture", "fixture-1", "1000"),
        ("team", "team-1", "10"),
        ("team", "team-2", "20"),
        ("player", "player-1", "101"),
    ):
        resolver.register(
            entity_type=entity,
            canonical_id=canonical,
            source_name="api-football",
            external_id=external,
        )
    return resolver


def _players(start: int) -> list[ProviderLineupPlayer]:
    return [
        ProviderLineupPlayer(
            player_external_id=str(start + index),
            player_name=f"Player {start + index}",
            raw_position="M",
            shirt_number=index + 1,
            grid_position=f"{index // 4 + 1}:{index % 4 + 1}",
        )
        for index in range(11)
    ]


def test_api_football_availability_maps_injury_and_suspension(tmp_path) -> None:
    batch = ProviderAvailabilityBatch(
        source="api-football",
        fixture_external_id="1000",
        captured_at=NOW,
        response_complete=True,
        records=[
            ProviderPlayerAvailability(
                team_external_id="10",
                player_external_id="101",
                player_name="Player One",
                raw_status="Missing Fixture",
                reason="Suspended",
            )
        ],
    )
    observations = ApiFootballTeamIntelligenceAdapter().availability(
        batch,
        resolver=_resolver(),
        capture_window=CaptureWindow.T24H,
        raw_payload_hash=RAW_HASH,
        source_url=SOURCE_URL,
    )

    assert len(observations) == 1
    assert observations[0].data_type is DataType.SUSPENSION
    assert observations[0].fixture_id == "fixture-1"
    assert observations[0].raw_payload_hash == RAW_HASH
    assert observations[0].source_url == SOURCE_URL
    store = ObservationStore(tmp_path / "observations")
    assert store.append(observations).inserted == 1
    assert store.append(observations).duplicates == 1


def test_api_football_lineups_create_three_evidence_types_per_team() -> None:
    first = ProviderTeamLineup(
        team_external_id="10",
        formation="4-3-3",
        starting=_players(1000),
        substitutes=[],
    )
    second = ProviderTeamLineup(
        team_external_id="20",
        formation="4-4-2",
        starting=_players(2000),
        substitutes=[],
    )
    batch = ProviderFixtureLineupBatch(
        source="api-football",
        fixture_external_id="1000",
        captured_at=NOW,
        response_complete=True,
        lineups=[first, second],
    )
    observations = ApiFootballTeamIntelligenceAdapter().lineups(
        batch,
        resolver=_resolver(),
        capture_window=CaptureWindow.T30,
        raw_payload_hash=RAW_HASH,
        source_url="https://v3.football.api-sports.io/fixtures/lineups?fixture=1000",
    )

    assert len(observations) == 6
    assert {observation.data_type for observation in observations} == {
        DataType.CONFIRMED_LINEUP,
        DataType.SUBSTITUTES,
        DataType.FORMATION,
    }


def test_api_football_adapter_fails_closed_on_unknown_identity() -> None:
    batch = ProviderAvailabilityBatch(
        source="api-football",
        fixture_external_id="unknown",
        captured_at=NOW,
        response_complete=True,
        records=[],
    )
    with pytest.raises(MappingFailure, match="fixture mapping failed"):
        ApiFootballTeamIntelligenceAdapter().availability(
            batch,
            resolver=_resolver(),
            capture_window=CaptureWindow.T24H,
            raw_payload_hash=RAW_HASH,
            source_url=SOURCE_URL,
        )
