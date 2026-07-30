"""球员可用性领域对象单元测试。"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest

from app.models.entities.player_availability import (
    PlayerAvailabilityObservation,
)
from app.models.value_objects.availability import (
    AvailabilitySource,
    AvailabilityStatus,
)
from app.models.value_objects.decision import EvidenceLevel

_CAPTURED_AT = datetime(2026, 7, 30, 12, 0, tzinfo=UTC)


def _source(**overrides: object) -> AvailabilitySource:
    values: dict[str, object] = {
        "name": "official-club",
        "evidence_level": EvidenceLevel.A,
        "reference": "https://example.com/team-news",
    }
    values.update(overrides)
    return AvailabilitySource(**values)  # type: ignore[arg-type]


def _observation(**overrides: object) -> PlayerAvailabilityObservation:
    values: dict[str, object] = {
        "fixture_id": uuid4(),
        "team_id": uuid4(),
        "player_id": uuid4(),
        "status": AvailabilityStatus.OUT,
        "source": _source(),
        "captured_at": _CAPTURED_AT,
        "source_updated_at": _CAPTURED_AT - timedelta(minutes=5),
        "reason": " hamstring injury ",
        "expected_return": date(2026, 8, 15),
    }
    values.update(overrides)
    return PlayerAvailabilityObservation(**values)  # type: ignore[arg-type]


def test_source_normalizes_identity_and_preserves_evidence() -> None:
    source = _source(name=" Official Club ", reference=" https://example.com/report ")

    assert source.name == "Official Club"
    assert source.reference == "https://example.com/report"
    assert source.evidence_level is EvidenceLevel.A


@pytest.mark.parametrize(
    ("status", "is_known", "rules_player_out"),
    [
        (AvailabilityStatus.UNKNOWN, False, False),
        (AvailabilityStatus.AVAILABLE, True, False),
        (AvailabilityStatus.DOUBTFUL, True, False),
        (AvailabilityStatus.OUT, True, True),
        (AvailabilityStatus.SUSPENDED, True, True),
        (AvailabilityStatus.RETURNED, True, False),
    ],
)
def test_status_keeps_unknown_separate_from_availability(
    status: AvailabilityStatus,
    is_known: bool,
    rules_player_out: bool,
) -> None:
    assert status.is_known is is_known
    assert status.rules_player_out is rules_player_out


def test_observation_preserves_provenance_and_normalizes_reason() -> None:
    observation = _observation()

    assert observation.has_known_status is True
    assert observation.rules_player_out is True
    assert observation.reason == "hamstring injury"
    assert observation.source.evidence_level is EvidenceLevel.A
    assert observation.source_updated_at == _CAPTURED_AT - timedelta(minutes=5)


@pytest.mark.parametrize(
    ("captured_at", "source_updated_at", "message"),
    [
        (
            datetime(2026, 7, 30, 12, 0),
            None,
            "captured_at must be timezone-aware",
        ),
        (
            _CAPTURED_AT,
            datetime(2026, 7, 30, 11, 59),
            "source_updated_at must be timezone-aware",
        ),
        (
            _CAPTURED_AT,
            _CAPTURED_AT + timedelta(seconds=1),
            "source_updated_at cannot be later",
        ),
    ],
)
def test_observation_rejects_invalid_timestamps(
    captured_at: datetime,
    source_updated_at: datetime | None,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        _observation(captured_at=captured_at, source_updated_at=source_updated_at)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"name": " "}, "source name cannot be empty"),
        ({"reference": " "}, "source reference cannot be empty"),
    ],
)
def test_source_rejects_blank_identity_fields(
    kwargs: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        _source(**kwargs)


def test_observation_rejects_blank_reason() -> None:
    with pytest.raises(ValueError, match="reason cannot be empty"):
        _observation(reason=" ")
