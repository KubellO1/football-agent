"""DataQualityEvaluator 的确定性规则单元测试。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from app.models.entities.team_match_statistics import TeamMatchStatistics
from app.models.value_objects.data_quality import DataFreshness
from app.models.value_objects.decision import EvidenceLevel
from app.models.value_objects.statistics import StatisticField, TeamMatchMetrics
from app.services.data_quality import DataQualityEvaluator, DataQualityPolicy

_NOW = datetime(2026, 7, 28, 18, 0, tzinfo=UTC)


def _metrics(*, xg: float | None = 1.5, shots: int = 10) -> TeamMatchMetrics:
    return TeamMatchMetrics(
        xg=xg,
        xg_against=0.8,
        shots=shots,
        shots_on_target=4,
        possession_percentage=54.0,
        ppda=9.5,
        big_chances=3,
        goalkeeper_saves=2,
        set_piece_shots=2,
        headed_shots=1,
        conversion_rate=0.15,
    )


def _snapshot(
    *,
    fixture_id: UUID | None = None,
    team_id: UUID | None = None,
    source: str = "professional-stats",
    captured_at: datetime = _NOW,
    is_final: bool = True,
    metrics: TeamMatchMetrics | None = None,
) -> TeamMatchStatistics:
    return TeamMatchStatistics(
        fixture_id=fixture_id or uuid4(),
        team_id=team_id or uuid4(),
        source=source,
        captured_at=captured_at,
        is_final=is_final,
        metrics=metrics or _metrics(),
    )


def _evaluator(**policy_overrides: object) -> DataQualityEvaluator:
    values: dict[str, object] = {
        "source_evidence": {"professional-stats": EvidenceLevel.B},
    }
    values.update(policy_overrides)
    return DataQualityEvaluator(DataQualityPolicy(**values))  # type: ignore[arg-type]


def test_complete_final_known_source_is_usable() -> None:
    assessment = _evaluator().evaluate(_snapshot(), evaluated_at=_NOW)

    assert assessment.completeness.value == 100.0
    assert assessment.evidence_level is EvidenceLevel.B
    assert assessment.freshness is DataFreshness.FRESH
    assert assessment.missing_fields == frozenset()
    assert assessment.is_usable()


def test_missing_xg_reduces_weighted_completeness_below_gate() -> None:
    assessment = _evaluator().evaluate(
        _snapshot(metrics=_metrics(xg=None)),
        evaluated_at=_NOW,
    )

    assert assessment.completeness.value == 85.0
    assert assessment.missing_fields == frozenset({StatisticField.XG})
    assert not assessment.is_usable()


def test_unknown_source_fails_closed_at_evidence_level_e() -> None:
    assessment = _evaluator().evaluate(
        _snapshot(source="unregistered-source"),
        evaluated_at=_NOW,
    )

    assert assessment.evidence_level is EvidenceLevel.E
    assert not assessment.is_usable()


def test_non_final_snapshot_has_unknown_freshness_by_default() -> None:
    assessment = _evaluator().evaluate(
        _snapshot(is_final=False),
        evaluated_at=_NOW,
    )

    assert assessment.freshness is DataFreshness.UNKNOWN
    assert not assessment.is_usable()


@pytest.mark.parametrize(
    ("age", "expected"),
    [
        (timedelta(minutes=10), DataFreshness.FRESH),
        (timedelta(minutes=16), DataFreshness.STALE),
    ],
)
def test_provisional_policy_uses_explicit_max_age(
    age: timedelta,
    expected: DataFreshness,
) -> None:
    evaluator = _evaluator(
        require_final=False,
        provisional_max_age=timedelta(minutes=15),
    )
    assessment = evaluator.evaluate(
        _snapshot(captured_at=_NOW - age, is_final=False),
        evaluated_at=_NOW,
    )

    assert assessment.freshness is expected


def test_future_snapshot_is_rejected_to_prevent_data_leakage() -> None:
    with pytest.raises(ValueError, match="later than evaluated_at"):
        _evaluator().evaluate(
            _snapshot(captured_at=_NOW + timedelta(seconds=1)),
            evaluated_at=_NOW,
        )


def test_cross_source_conflict_respects_float_tolerance() -> None:
    fixture_id, team_id = uuid4(), uuid4()
    primary = _snapshot(
        fixture_id=fixture_id,
        team_id=team_id,
        metrics=_metrics(xg=1.5, shots=10),
    )
    corroborating = _snapshot(
        fixture_id=fixture_id,
        team_id=team_id,
        source="official",
        metrics=_metrics(xg=1.6, shots=11),
    )

    assessment = _evaluator().evaluate(
        primary,
        evaluated_at=_NOW,
        corroborating=[corroborating],
    )

    assert StatisticField.XG not in assessment.conflicting_fields
    assert assessment.conflicting_fields == frozenset({StatisticField.SHOTS})
    assert not assessment.is_usable()


def test_mismatched_fixture_or_team_is_rejected() -> None:
    primary = _snapshot()
    unrelated = _snapshot(source="official")

    with pytest.raises(ValueError, match="same fixture and team"):
        _evaluator().evaluate(
            primary,
            evaluated_at=_NOW,
            corroborating=[unrelated],
        )


def test_duplicate_source_is_rejected() -> None:
    fixture_id, team_id = uuid4(), uuid4()
    primary = _snapshot(fixture_id=fixture_id, team_id=team_id)
    duplicate_source = _snapshot(fixture_id=fixture_id, team_id=team_id)

    with pytest.raises(ValueError, match="one snapshot per source"):
        _evaluator().evaluate(
            primary,
            evaluated_at=_NOW,
            corroborating=[duplicate_source],
        )


def test_policy_requires_all_weights_and_total_of_100() -> None:
    with pytest.raises(ValueError, match="every statistic field"):
        DataQualityPolicy(field_weights={StatisticField.XG: 100.0})

    invalid_total = dict.fromkeys(StatisticField, 1.0)
    with pytest.raises(ValueError, match="total 100"):
        DataQualityPolicy(field_weights=invalid_total)


def test_policy_rejects_untyped_source_evidence() -> None:
    with pytest.raises(ValueError, match="EvidenceLevel members"):
        DataQualityPolicy(source_evidence={"source": "B"})  # type: ignore[dict-item]
