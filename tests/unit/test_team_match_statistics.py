"""球队单场统计领域对象单元测试。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.models.entities.team_match_statistics import TeamMatchStatistics
from app.models.value_objects.data_quality import DataFreshness, DataQualityAssessment
from app.models.value_objects.decision import DataCompleteness, EvidenceLevel
from app.models.value_objects.statistics import StatisticField, TeamMatchMetrics


def test_metrics_preserve_unknown_values_and_report_availability() -> None:
    metrics = TeamMatchMetrics(xg=0.0, shots=0, possession_percentage=None)

    assert metrics.available_fields == frozenset({StatisticField.XG, StatisticField.SHOTS})
    assert StatisticField.POSSESSION_PERCENTAGE in metrics.missing_fields
    assert metrics.possession_percentage is None


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"shots": -1}, "shots must be"),
        ({"shots": 3, "shots_on_target": 4}, "shots_on_target cannot exceed shots"),
        ({"shots": 3, "set_piece_shots": 4}, "set_piece_shots cannot exceed shots"),
        ({"shots": 3, "headed_shots": 4}, "headed_shots cannot exceed shots"),
        ({"xg": float("nan")}, "xg must be"),
        ({"possession_percentage": 101.0}, "possession_percentage must be"),
        ({"ppda": 0.0}, "ppda must be"),
        ({"conversion_rate": 1.1}, "conversion_rate must be"),
    ],
)
def test_metrics_reject_invalid_values(kwargs: dict[str, object], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        TeamMatchMetrics(**kwargs)  # type: ignore[arg-type]


def test_team_match_statistics_normalizes_source_and_tracks_final_snapshot() -> None:
    captured_at = datetime(2026, 7, 28, 18, 0, tzinfo=UTC)
    snapshot = TeamMatchStatistics(
        fixture_id=uuid4(),
        team_id=uuid4(),
        source=" api-football ",
        metrics=TeamMatchMetrics(xg=1.4, shots=12),
        captured_at=captured_at,
        source_updated_at=captured_at - timedelta(minutes=2),
        is_final=True,
    )

    assert snapshot.source == "api-football"
    assert snapshot.is_final is True
    assert snapshot.metrics.xg == 1.4


@pytest.mark.parametrize(
    ("captured_at", "source_updated_at", "message"),
    [
        (
            datetime(2026, 7, 28, 18, 0),
            None,
            "captured_at must be timezone-aware",
        ),
        (
            datetime(2026, 7, 28, 18, 0, tzinfo=UTC),
            datetime(2026, 7, 28, 17, 59),
            "source_updated_at must be timezone-aware",
        ),
        (
            datetime(2026, 7, 28, 18, 0, tzinfo=UTC),
            datetime(2026, 7, 28, 18, 1, tzinfo=UTC),
            "source_updated_at cannot be later",
        ),
    ],
)
def test_team_match_statistics_rejects_invalid_timestamps(
    captured_at: datetime,
    source_updated_at: datetime | None,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        TeamMatchStatistics(
            fixture_id=uuid4(),
            team_id=uuid4(),
            source="source",
            metrics=TeamMatchMetrics(),
            captured_at=captured_at,
            source_updated_at=source_updated_at,
        )


def _quality(**overrides: object) -> DataQualityAssessment:
    values: dict[str, object] = {
        "completeness": DataCompleteness(95.0),
        "evidence_level": EvidenceLevel.B,
        "freshness": DataFreshness.FRESH,
        "evaluated_at": datetime(2026, 7, 28, 18, 0, tzinfo=UTC),
    }
    values.update(overrides)
    return DataQualityAssessment(**values)  # type: ignore[arg-type]


def test_quality_assessment_requires_all_admission_conditions() -> None:
    assert _quality().is_usable()
    assert not _quality(completeness=DataCompleteness(89.9)).is_usable()
    assert not _quality(evidence_level=EvidenceLevel.C).is_usable()
    assert not _quality(freshness=DataFreshness.STALE).is_usable()
    assert not _quality(conflicting_fields=frozenset({StatisticField.XG})).is_usable()


def test_quality_assessment_rejects_overlapping_missing_and_conflicting_fields() -> None:
    fields = frozenset({StatisticField.XG})

    with pytest.raises(ValueError, match="both missing and conflicting"):
        _quality(missing_fields=fields, conflicting_fields=fields)


def test_quality_assessment_requires_timezone_aware_evaluation() -> None:
    with pytest.raises(ValueError, match="evaluated_at must be timezone-aware"):
        _quality(evaluated_at=datetime(2026, 7, 28, 18, 0))
