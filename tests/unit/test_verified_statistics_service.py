"""VerifiedStatisticsService 的来源选择与准入单元测试。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import cast
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from app.models.entities.team_match_statistics import TeamMatchStatistics
from app.models.value_objects.decision import EvidenceLevel
from app.models.value_objects.statistics import StatisticField, TeamMatchMetrics
from app.repositories.interfaces.team_match_statistics_repository import (
    TeamMatchStatisticsRepository,
)
from app.services.data_quality import DataQualityEvaluator, DataQualityPolicy
from app.services.verified_statistics import (
    VerificationStatus,
    VerifiedStatisticsPolicy,
    VerifiedStatisticsService,
)

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
    fixture_id: UUID,
    team_id: UUID,
    *,
    source: str,
    captured_at: datetime = _NOW,
    is_final: bool = True,
    metrics: TeamMatchMetrics | None = None,
) -> TeamMatchStatistics:
    return TeamMatchStatistics(
        fixture_id=fixture_id,
        team_id=team_id,
        source=source,
        captured_at=captured_at,
        is_final=is_final,
        metrics=metrics or _metrics(),
    )


def _service(
    snapshots: list[TeamMatchStatistics],
) -> tuple[VerifiedStatisticsService, AsyncMock]:
    repository = AsyncMock(spec=TeamMatchStatisticsRepository)
    repository.list_by_fixture.return_value = snapshots
    evaluator = DataQualityEvaluator(
        DataQualityPolicy(
            source_evidence={
                "official": EvidenceLevel.A,
                "professional": EvidenceLevel.B,
            }
        )
    )
    service = VerifiedStatisticsService(
        repository=cast("TeamMatchStatisticsRepository", repository),
        evaluator=evaluator,
        policy=VerifiedStatisticsPolicy(
            source_priority=("official", "professional"),
        ),
    )
    return service, repository


@pytest.mark.unit
async def test_no_snapshots_returns_not_found() -> None:
    fixture_id, team_id = uuid4(), uuid4()
    service, repository = _service([])

    result = await service.verify(fixture_id, team_id, as_of=_NOW)

    assert result.status is VerificationStatus.NOT_FOUND
    assert not result.accepted
    assert result.snapshot is None
    assert result.assessment is None
    repository.list_by_fixture.assert_awaited_once_with(
        fixture_id,
        team_id=team_id,
        as_of=_NOW,
    )


@pytest.mark.unit
async def test_unknown_sources_are_ignored_and_reported() -> None:
    fixture_id, team_id = uuid4(), uuid4()
    service, _ = _service([_snapshot(fixture_id, team_id, source="unregistered")])

    result = await service.verify(fixture_id, team_id, as_of=_NOW)

    assert result.status is VerificationStatus.NOT_FOUND
    assert result.ignored_sources == ("unregistered",)


@pytest.mark.unit
async def test_source_priority_selects_authority_over_recency() -> None:
    fixture_id, team_id = uuid4(), uuid4()
    official = _snapshot(
        fixture_id,
        team_id,
        source="official",
        captured_at=_NOW - timedelta(minutes=5),
    )
    professional = _snapshot(
        fixture_id,
        team_id,
        source="professional",
        captured_at=_NOW,
    )
    service, _ = _service([professional, official])

    result = await service.verify(fixture_id, team_id, as_of=_NOW)

    assert result.accepted
    assert result.snapshot is official
    assert result.considered_sources == ("official", "professional")


@pytest.mark.unit
async def test_latest_snapshot_is_selected_within_primary_source() -> None:
    fixture_id, team_id = uuid4(), uuid4()
    older = _snapshot(
        fixture_id,
        team_id,
        source="official",
        captured_at=_NOW - timedelta(minutes=10),
        metrics=_metrics(xg=1.1),
    )
    latest = _snapshot(
        fixture_id,
        team_id,
        source="official",
        metrics=_metrics(xg=1.6),
    )
    service, _ = _service([latest, older])

    result = await service.verify(fixture_id, team_id, as_of=_NOW)

    assert result.accepted
    assert result.snapshot is latest
    assert result.assessment is not None
    assert result.assessment.completeness.value == 100.0


@pytest.mark.unit
async def test_incomplete_primary_is_rejected_without_source_fallback() -> None:
    fixture_id, team_id = uuid4(), uuid4()
    official = _snapshot(
        fixture_id,
        team_id,
        source="official",
        metrics=_metrics(xg=None),
    )
    professional = _snapshot(
        fixture_id,
        team_id,
        source="professional",
    )
    service, _ = _service([official, professional])

    result = await service.verify(fixture_id, team_id, as_of=_NOW)

    assert result.status is VerificationStatus.REJECTED
    assert not result.accepted
    assert result.snapshot is official
    assert result.assessment is not None
    assert result.assessment.completeness.value == 85.0
    assert result.assessment.missing_fields == frozenset({StatisticField.XG})


@pytest.mark.unit
async def test_unknown_source_cannot_create_conflict() -> None:
    fixture_id, team_id = uuid4(), uuid4()
    official = _snapshot(fixture_id, team_id, source="official")
    unknown = _snapshot(
        fixture_id,
        team_id,
        source="unregistered",
        metrics=_metrics(shots=20),
    )
    service, _ = _service([official, unknown])

    result = await service.verify(fixture_id, team_id, as_of=_NOW)

    assert result.accepted
    assert result.assessment is not None
    assert not result.assessment.conflicting_fields
    assert result.ignored_sources == ("unregistered",)


@pytest.mark.unit
async def test_whitelisted_cross_source_conflict_rejects() -> None:
    fixture_id, team_id = uuid4(), uuid4()
    official = _snapshot(fixture_id, team_id, source="official")
    professional = _snapshot(
        fixture_id,
        team_id,
        source="professional",
        metrics=_metrics(shots=11),
    )
    service, _ = _service([official, professional])

    result = await service.verify(fixture_id, team_id, as_of=_NOW)

    assert result.status is VerificationStatus.REJECTED
    assert result.assessment is not None
    assert result.assessment.conflicting_fields == frozenset({StatisticField.SHOTS})


@pytest.mark.unit
async def test_non_final_primary_is_rejected() -> None:
    fixture_id, team_id = uuid4(), uuid4()
    service, _ = _service(
        [
            _snapshot(
                fixture_id,
                team_id,
                source="official",
                is_final=False,
            )
        ]
    )

    result = await service.verify(fixture_id, team_id, as_of=_NOW)

    assert result.status is VerificationStatus.REJECTED
    assert result.assessment is not None
    assert not result.assessment.is_usable()


@pytest.mark.unit
async def test_repository_future_snapshot_is_rejected_defensively() -> None:
    fixture_id, team_id = uuid4(), uuid4()
    service, _ = _service(
        [
            _snapshot(
                fixture_id,
                team_id,
                source="official",
                captured_at=_NOW + timedelta(seconds=1),
            )
        ]
    )

    with pytest.raises(ValueError, match="later than as_of"):
        await service.verify(fixture_id, team_id, as_of=_NOW)


@pytest.mark.unit
async def test_naive_as_of_is_rejected_before_repository_access() -> None:
    service, repository = _service([])

    with pytest.raises(ValueError, match="timezone-aware"):
        await service.verify(uuid4(), uuid4(), as_of=datetime(2026, 7, 28, 18, 0))

    repository.list_by_fixture.assert_not_awaited()


def test_policy_rejects_ambiguous_source_priority() -> None:
    with pytest.raises(ValueError, match="cannot be empty"):
        VerifiedStatisticsPolicy(source_priority=())
    with pytest.raises(ValueError, match="duplicates"):
        VerifiedStatisticsPolicy(source_priority=("official", "official"))


@pytest.mark.parametrize("value", [float("nan"), -1.0, 101.0, True])
def test_policy_rejects_invalid_completeness_threshold(value: float) -> None:
    with pytest.raises(ValueError, match="between 0 and 100"):
        VerifiedStatisticsPolicy(
            source_priority=("official",),
            minimum_completeness=value,
        )
