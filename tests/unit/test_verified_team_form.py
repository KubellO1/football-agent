"""VerifiedTeamFormBuilder 的固定窗口、质量准入与聚合单元测试。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import cast
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from app.models.entities.enums import MatchStatus
from app.models.entities.fixture import Fixture
from app.models.entities.team_match_statistics import TeamMatchStatistics
from app.models.value_objects.data_quality import DataFreshness, DataQualityAssessment
from app.models.value_objects.decision import DataCompleteness, EvidenceLevel
from app.models.value_objects.score import Score
from app.models.value_objects.statistics import TeamMatchMetrics
from app.repositories.interfaces.fixture_repository import FixtureRepository
from app.services.verified_statistics import (
    VerificationStatus,
    VerifiedStatisticsResult,
    VerifiedStatisticsService,
)
from app.services.verified_team_form import (
    TeamFormStatus,
    VerifiedTeamFormBuilder,
    VerifiedTeamFormPolicy,
)

_AS_OF = datetime(2026, 7, 28, 18, 0, tzinfo=UTC)
_DEFAULT_SCORE = Score(home=1, away=0)


def _fixture(
    team_id: UUID,
    *,
    days_ago: int,
    score: Score | None = _DEFAULT_SCORE,
    team_is_home: bool = True,
) -> Fixture:
    opponent_id = uuid4()
    return Fixture(
        competition_id=uuid4(),
        home_team_id=team_id if team_is_home else opponent_id,
        away_team_id=opponent_id if team_is_home else team_id,
        kickoff=_AS_OF - timedelta(days=days_ago),
        status=MatchStatus.FINISHED,
        score=score,
    )


def _verified_result(
    fixture: Fixture,
    team_id: UUID,
    *,
    xg: float | None = 1.5,
    xga: float | None = 0.8,
    completeness: float = 100.0,
    evidence: EvidenceLevel = EvidenceLevel.B,
    accepted: bool = True,
) -> VerifiedStatisticsResult:
    snapshot = TeamMatchStatistics(
        fixture_id=fixture.id,
        team_id=team_id,
        source="professional",
        captured_at=_AS_OF,
        is_final=True,
        metrics=TeamMatchMetrics(xg=xg, xg_against=xga),
    )
    assessment = DataQualityAssessment(
        completeness=DataCompleteness(completeness),
        evidence_level=evidence,
        freshness=DataFreshness.FRESH,
        evaluated_at=_AS_OF,
    )
    return VerifiedStatisticsResult(
        status=VerificationStatus.VERIFIED if accepted else VerificationStatus.REJECTED,
        snapshot=snapshot,
        assessment=assessment,
    )


def _builder(
    fixtures: list[Fixture],
    results: list[VerifiedStatisticsResult],
    *,
    policy: VerifiedTeamFormPolicy,
) -> tuple[VerifiedTeamFormBuilder, AsyncMock, AsyncMock]:
    fixture_repository = AsyncMock(spec=FixtureRepository)
    fixture_repository.list_finished_by_team.return_value = fixtures
    statistics_service = AsyncMock(spec=VerifiedStatisticsService)
    statistics_service.verify.side_effect = results
    builder = VerifiedTeamFormBuilder(
        fixture_repository=cast("FixtureRepository", fixture_repository),
        statistics_service=cast("VerifiedStatisticsService", statistics_service),
        policy=policy,
    )
    return builder, fixture_repository, statistics_service


@pytest.mark.unit
async def test_build_aggregates_home_and_away_results_and_xg_totals() -> None:
    team_id, target_fixture_id = uuid4(), uuid4()
    fixtures = [
        _fixture(team_id, days_ago=1, score=Score(home=2, away=1)),
        _fixture(
            team_id,
            days_ago=2,
            score=Score(home=1, away=1),
            team_is_home=False,
        ),
        _fixture(team_id, days_ago=3, score=Score(home=0, away=2)),
    ]
    results = [
        _verified_result(fixtures[0], team_id, xg=1.7, xga=0.8),
        _verified_result(fixtures[1], team_id, xg=1.2, xga=1.1, completeness=95.0),
        _verified_result(fixtures[2], team_id, xg=0.5, xga=1.9),
    ]
    builder, repository, statistics_service = _builder(
        fixtures,
        results,
        policy=VerifiedTeamFormPolicy(window_size=3),
    )

    result = await builder.build(
        team_id,
        target_fixture_id=target_fixture_id,
        as_of=_AS_OF,
    )

    assert result.accepted
    assert result.status is TeamFormStatus.VERIFIED
    assert result.data_completeness.value == pytest.approx(98.333333)
    assert result.evidence_level is EvidenceLevel.B
    assert result.statistics is not None
    assert result.statistics.matches_played == 3
    assert (result.statistics.wins, result.statistics.draws, result.statistics.losses) == (1, 1, 1)
    assert (result.statistics.goals_for, result.statistics.goals_against) == (3, 4)
    assert result.statistics.xg_for == pytest.approx(3.4)
    assert result.statistics.xg_against == pytest.approx(3.8)
    repository.list_finished_by_team.assert_awaited_once_with(
        team_id,
        limit=3,
        exclude_fixture_id=target_fixture_id,
        before=_AS_OF,
    )
    assert statistics_service.verify.await_count == 3
    for fixture in fixtures:
        statistics_service.verify.assert_any_await(
            fixture.id,
            team_id,
            as_of=_AS_OF,
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("history_count", "snapshot_quality", "expected_status", "expected_completeness"),
    [
        (10, 95.0, TeamFormStatus.VERIFIED, 95.0),
        (9, 100.0, TeamFormStatus.VERIFIED, 90.0),
        (9, 95.0, TeamFormStatus.QUALITY_REJECTED, 85.5),
        (8, 100.0, TeamFormStatus.INSUFFICIENT_HISTORY, 80.0),
    ],
)
async def test_window_coverage_and_snapshot_quality_are_combined(
    history_count: int,
    snapshot_quality: float,
    expected_status: TeamFormStatus,
    expected_completeness: float,
) -> None:
    team_id = uuid4()
    fixtures = [_fixture(team_id, days_ago=index + 1) for index in range(history_count)]
    results = [
        _verified_result(
            fixture,
            team_id,
            completeness=snapshot_quality,
        )
        for fixture in fixtures
    ]
    builder, _, _ = _builder(
        fixtures,
        results,
        policy=VerifiedTeamFormPolicy(window_size=10),
    )

    result = await builder.build(team_id, target_fixture_id=uuid4(), as_of=_AS_OF)

    assert result.status is expected_status
    assert result.data_completeness.value == pytest.approx(expected_completeness)
    assert (result.statistics is not None) is (expected_status is TeamFormStatus.VERIFIED)


@pytest.mark.unit
async def test_rejected_snapshot_is_not_replaced_by_older_history() -> None:
    team_id = uuid4()
    fixtures = [_fixture(team_id, days_ago=index + 1) for index in range(10)]
    results = [
        _verified_result(fixture, team_id, accepted=index != 0)
        for index, fixture in enumerate(fixtures)
    ]
    builder, repository, statistics_service = _builder(
        fixtures,
        results,
        policy=VerifiedTeamFormPolicy(window_size=10),
    )

    result = await builder.build(team_id, target_fixture_id=uuid4(), as_of=_AS_OF)

    assert result.status is TeamFormStatus.VERIFIED
    assert result.verified_count == 9
    assert result.data_completeness.value == 90.0
    assert result.rejected_fixture_ids == (fixtures[0].id,)
    assert repository.list_finished_by_team.await_count == 1
    assert statistics_service.verify.await_count == 10


@pytest.mark.unit
async def test_missing_xg_fails_closed_even_if_upstream_marks_snapshot_verified() -> None:
    team_id = uuid4()
    fixture = _fixture(team_id, days_ago=1)
    builder, _, _ = _builder(
        [fixture],
        [_verified_result(fixture, team_id, xg=None)],
        policy=VerifiedTeamFormPolicy(window_size=1),
    )

    result = await builder.build(team_id, target_fixture_id=uuid4(), as_of=_AS_OF)

    assert result.status is TeamFormStatus.QUALITY_REJECTED
    assert result.statistics is None
    assert result.verified_count == 0
    assert result.rejected_fixture_ids == (fixture.id,)


@pytest.mark.unit
async def test_lowest_evidence_controls_form_gate() -> None:
    team_id = uuid4()
    fixtures = [_fixture(team_id, days_ago=1), _fixture(team_id, days_ago=2)]
    builder, _, _ = _builder(
        fixtures,
        [
            _verified_result(fixtures[0], team_id, evidence=EvidenceLevel.A),
            _verified_result(fixtures[1], team_id, evidence=EvidenceLevel.C),
        ],
        policy=VerifiedTeamFormPolicy(window_size=2),
    )

    result = await builder.build(team_id, target_fixture_id=uuid4(), as_of=_AS_OF)

    assert result.status is TeamFormStatus.QUALITY_REJECTED
    assert result.evidence_level is EvidenceLevel.C
    assert result.statistics is None


@pytest.mark.unit
async def test_naive_as_of_is_rejected_before_repository_access() -> None:
    builder, repository, _ = _builder(
        [],
        [],
        policy=VerifiedTeamFormPolicy(),
    )

    with pytest.raises(ValueError, match="timezone-aware"):
        await builder.build(
            uuid4(),
            target_fixture_id=uuid4(),
            as_of=datetime(2026, 7, 28, 18, 0),
        )

    repository.list_finished_by_team.assert_not_awaited()


@pytest.mark.unit
async def test_repository_cannot_return_future_fixture() -> None:
    team_id = uuid4()
    fixture = _fixture(team_id, days_ago=1)
    fixture.kickoff = _AS_OF
    builder, _, statistics_service = _builder(
        [fixture],
        [],
        policy=VerifiedTeamFormPolicy(window_size=1),
    )

    with pytest.raises(ValueError, match="not earlier than as_of"):
        await builder.build(team_id, target_fixture_id=uuid4(), as_of=_AS_OF)

    statistics_service.verify.assert_not_awaited()


@pytest.mark.unit
async def test_statistics_service_cannot_return_unrelated_snapshot() -> None:
    team_id = uuid4()
    fixture = _fixture(team_id, days_ago=1)
    unrelated = _verified_result(fixture, team_id)
    assert unrelated.snapshot is not None
    unrelated.snapshot.fixture_id = uuid4()
    builder, _, _ = _builder(
        [fixture],
        [unrelated],
        policy=VerifiedTeamFormPolicy(window_size=1),
    )

    with pytest.raises(ValueError, match="unrelated snapshot"):
        await builder.build(team_id, target_fixture_id=uuid4(), as_of=_AS_OF)


@pytest.mark.parametrize("value", [0, -1, True, 1.5])
def test_policy_rejects_invalid_window_size(value: int) -> None:
    with pytest.raises(ValueError, match="positive integer"):
        VerifiedTeamFormPolicy(window_size=value)


@pytest.mark.parametrize("value", [float("nan"), -1.0, 101.0, True])
def test_policy_rejects_invalid_completeness(value: float) -> None:
    with pytest.raises(ValueError, match="between 0 and 100"):
        VerifiedTeamFormPolicy(minimum_completeness=value)
