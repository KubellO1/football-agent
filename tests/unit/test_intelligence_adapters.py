from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.intelligence.adapters import (
    FIVE_LEAGUES,
    ManualObservationAdapter,
    MetNoWeatherAdapter,
    OpenFootballJsonAdapter,
)
from app.intelligence.contracts import CaptureWindow, DataType, RawPayload, SourcePolicy

FIXTURES = Path(__file__).parents[1] / "fixtures" / "intelligence"


def _raw(name: str, *, source: str = "openfootball") -> RawPayload:
    return RawPayload(
        source=source,
        source_url=f"https://example.test/{name}",
        captured_at=datetime(2026, 9, 13, 12, tzinfo=UTC),
        content=(FIXTURES / name).read_bytes(),
        media_type="application/json",
        source_policy=SourcePolicy.OPEN_DATA,
    )


def test_openfootball_parses_fixture_and_completed_result() -> None:
    observations = OpenFootballJsonAdapter().parse(
        _raw("openfootball.json"),
        league=FIVE_LEAGUES[0],
        season="2026-27",
        capture_window=CaptureWindow.DAILY,
    )
    assert len(observations) == 3
    assert [item.data_type for item in observations] == [
        DataType.FIXTURE,
        DataType.POST_MATCH_STATISTICS,
        DataType.FIXTURE,
    ]
    assert observations[0].payload["home_team"] == "Arsenal"
    assert observations[0].payload["kickoff"] == "2026-08-15T14:00:00+00:00"
    assert observations[1].payload["home_goals"] == 2
    assert observations[0].fixture_id == observations[1].fixture_id


def test_met_no_selects_forecast_nearest_kickoff() -> None:
    observation = MetNoWeatherAdapter().parse(
        _raw("met_no.json", source="met-no"),
        fixture_id="fixture-1",
        kickoff=datetime(2026, 9, 20, 14, 50, tzinfo=UTC),
        capture_window=CaptureWindow.T6H,
    )
    assert observation.data_type is DataType.WEATHER
    assert observation.payload["forecast_time"] == "2026-09-20T15:00:00+00:00"
    assert observation.payload["air_temperature_c"] == 17.8
    assert observation.payload["precipitation_next_1h_mm"] == 0.6
    assert observation.published_at == datetime(2026, 9, 20, 12, tzinfo=UTC)


def test_manual_input_preserves_official_evidence_contract() -> None:
    observation = ManualObservationAdapter().create(
        fixture_id="fixture-1",
        source="official-club",
        source_url="https://club.example/news/lineup",
        published_at=datetime(2026, 9, 20, 13, tzinfo=UTC),
        captured_at=datetime(2026, 9, 20, 13, 1, tzinfo=UTC),
        data_type=DataType.LINEUP,
        capture_window=CaptureWindow.T90,
        payload={"confirmed": True, "players": ["Player A"]},
    )
    assert observation.source_policy is SourcePolicy.MANUAL_USER_SUPPLIED
    assert observation.raw_payload_hash


def test_manual_input_rejects_unsupported_derived_type() -> None:
    with pytest.raises(ValueError, match="unsupported manual"):
        ManualObservationAdapter().create(
            fixture_id="fixture-1",
            source="manual",
            source_url="https://example.test/result",
            published_at=None,
            captured_at=datetime(2026, 9, 20, 13, tzinfo=UTC),
            data_type=DataType.POST_MATCH_STATISTICS,
            capture_window=CaptureWindow.POST_MATCH,
            payload={},
        )
