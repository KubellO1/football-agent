"""VerifiedLeagueBaselineBuilder 的窗口、质量准入与 xG 聚合单元测试。"""

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
from app.services.verified_league_baseline import (
    LeagueBaselineStatus,
    VerifiedLeagueBaselineBuilder,
    VerifiedLeagueBaselinePolicy,
)
from app.services.verified_statistics import (
    VerificationStatus,
    VerifiedStatisticsResult,
    VerifiedStatisticsService,
)

_AS_OF = datetime(2026, 7, 29, 18, 0, tzinfo=UTC)
_DEFAULT_SCORE = Score(home=1, away=1)


def _fixture(
    competition_id: UUID,
    season_id: UUID,
    *,
    days_ago: int,
    score: Score | None = _DEFAULT_SCORE,
) -> Fixture:
    return Fixture(
        competition_id=competition_id,
        season_id=season_id,
        home_team_id=uuid4(),
        away_team_id=uuid4(),
        kickoff=_AS_OF - timedelta(days=days_ago),
        status=MatchStatus.FINISHED,
        score=score,
    )


def _result(
    fixture: Fixture,
    team_id: UUID,
    *,
    xg: float | None,
    xga: float | None,
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


def _pair_results(
    fixture: Fixture,
    *,
    home_xg: float = 1.5,
    away_xg: float = 0.8,
    home_quality: float = 100.0,
    away_quality: float = 100.0,
    home_evidence: EvidenceLevel = EvidenceLevel.B,
    away_evidence: EvidenceLevel = EvidenceLevel.B,
) -> list[VerifiedStatisticsResult]:
    return [
        _result(
            fixture,
            fixture.home_team_id,
            xg=home_xg,
            xga=away_xg,
            completeness=home_quality,
            evidence=home_evidence,
        ),
        _result(
            fixture,
            fixture.away_team_id,
            xg=away_xg,
            xga=home_xg,
            completeness=away_quality,
            evidence=away_evidence,
        ),
    ]


def _builder(
    fixtures: list[Fixture],
    results: list[VerifiedStatisticsResult],
    *,
    policy: VerifiedLeagueBaselinePolicy,
) -> tuple[VerifiedLeagueBaselineBuilder, AsyncMock, AsyncMock]:
    fixture_repository = AsyncMock(spec=FixtureRepository)
    fixture_repository.list_finished_by_competition.return_value = fixtures
    statistics_service = AsyncMock(spec=VerifiedStatisticsService)
    statistics_service.verify.side_effect = results
    builder = VerifiedLeagueBaselineBuilder(
        fixture_repository=cast("FixtureRepository", fixture_repository),
        statistics_service=cast("VerifiedStatisticsService", statistics_service),
        policy=policy,
    )
    return builder, fixture_repository, statistics_service


@pytest.mark.unit
async def test_build_aggregates_verified_pairs_into_xg_baseline() -> None:
    competition_id, season_id, target_fixture_id = uuid4(), uuid4(), uuid4()
    fixtures = [
        _fixture(competition_id, season_id, days_ago=1),
        _fixture(competition_id, season_id, days_ago=2),
    ]
    results = [
        *_pair_results(
            fixtures[0],
            home_xg=1.6,
            away_xg=0.9,
            home_quality=100.0,
            away_quality=90.0,
            home_evidence=EvidenceLevel.A,
        ),
        *_pair_results(
            fixtures[1],
            home_xg=1.2,
            away_xg=1.1,
            home_quality=95.0,
            away_quality=95.0,
        ),
    ]
    builder, repository, statistics_service = _builder(
        fixtures,
        results,
        policy=VerifiedLeagueBaselinePolicy(
            maximum_fixtures=3,
            minimum_fixtures=2,
        ),
    )

    result = await builder.build(
        competition_id,
        season_id,
        target_fixture_id=target_fixture_id,
        as_of=_AS_OF,
    )

    assert result.accepted
    assert result.status is LeagueBaselineStatus.VERIFIED
    assert result.data_completeness.value == pytest.approx(95.0)
    assert result.evidence_level is EvidenceLevel.B
    assert result.baseline is not None
    assert result.baseline.fixture_count == 2
    assert result.baseline.xg_per_team_match == pytest.approx(1.2)
    repository.list_finished_by_competition.assert_awaited_once_with(
        competition_id,
        season_id=season_id,
        limit=3,
        exclude_fixture_id=target_fixture_id,
        before=_AS_OF,
    )
    assert statistics_service.verify.await_count == 4


@pytest.mark.unit
async def test_available_history_below_minimum_is_rejected() -> None:
    competition_id, season_id = uuid4(), uuid4()
    fixture = _fixture(competition_id, season_id, days_ago=1)
    builder, _, _ = _builder(
        [fixture],
        _pair_results(fixture),
        policy=VerifiedLeagueBaselinePolicy(
            maximum_fixtures=3,
            minimum_fixtures=2,
        ),
    )

    result = await builder.build(
        competition_id,
        season_id,
        target_fixture_id=uuid4(),
        as_of=_AS_OF,
    )

    assert result.status is LeagueBaselineStatus.INSUFFICIENT_HISTORY
    assert result.data_completeness.value == 100.0
    assert result.baseline is None


@pytest.mark.unit
async def test_rejected_pair_reduces_coverage_without_older_backfill() -> None:
    competition_id, season_id = uuid4(), uuid4()
    fixtures = [
        _fixture(competition_id, season_id, days_ago=1),
        _fixture(competition_id, season_id, days_ago=2),
        _fixture(competition_id, season_id, days_ago=3),
    ]
    rejected_home = _result(
        fixtures[2],
        fixtures[2].home_team_id,
        xg=1.0,
        xga=1.0,
        accepted=False,
    )
    builder, repository, statistics_service = _builder(
        fixtures,
        [
            *_pair_results(fixtures[0]),
            *_pair_results(fixtures[1]),
            rejected_home,
        ],
        policy=VerifiedLeagueBaselinePolicy(
            maximum_fixtures=3,
            minimum_fixtures=2,
        ),
    )

    result = await builder.build(
        competition_id,
        season_id,
        target_fixture_id=uuid4(),
        as_of=_AS_OF,
    )

    assert result.status is LeagueBaselineStatus.QUALITY_REJECTED
    assert result.verified_fixture_count == 2
    assert result.data_completeness.value == pytest.approx(66.666667)
    assert result.rejected_fixture_ids == (fixtures[2].id,)
    assert result.baseline is None
    assert repository.list_finished_by_competition.await_count == 1
    assert statistics_service.verify.await_count == 5


@pytest.mark.unit
async def test_reciprocal_xg_conflict_rejects_entire_fixture() -> None:
    competition_id, season_id = uuid4(), uuid4()
    fixture = _fixture(competition_id, season_id, days_ago=1)
    results = [
        _result(
            fixture,
            fixture.home_team_id,
            xg=1.7,
            xga=0.8,
        ),
        _result(
            fixture,
            fixture.away_team_id,
            xg=0.8,
            xga=1.3,
        ),
    ]
    builder, _, _ = _builder(
        [fixture],
        results,
        policy=VerifiedLeagueBaselinePolicy(
            maximum_fixtures=1,
            minimum_fixtures=1,
            pair_tolerance=0.1,
        ),
    )

    result = await builder.build(
        competition_id,
        season_id,
        target_fixture_id=uuid4(),
        as_of=_AS_OF,
    )

    assert result.status is LeagueBaselineStatus.QUALITY_REJECTED
    assert result.verified_fixture_count == 0
    assert result.rejected_fixture_ids == (fixture.id,)


@pytest.mark.unit
async def test_reciprocal_difference_exactly_at_tolerance_is_accepted() -> None:
    competition_id, season_id = uuid4(), uuid4()
    fixture = _fixture(competition_id, season_id, days_ago=1)
    results = [
        _result(
            fixture,
            fixture.home_team_id,
            xg=1.6,
            xga=0.8,
        ),
        _result(
            fixture,
            fixture.away_team_id,
            xg=0.8,
            xga=1.5,
        ),
    ]
    builder, _, _ = _builder(
        [fixture],
        results,
        policy=VerifiedLeagueBaselinePolicy(
            maximum_fixtures=1,
            minimum_fixtures=1,
            pair_tolerance=0.1,
        ),
    )

    result = await builder.build(
        competition_id,
        season_id,
        target_fixture_id=uuid4(),
        as_of=_AS_OF,
    )

    assert result.status is LeagueBaselineStatus.VERIFIED
    assert result.baseline is not None


@pytest.mark.unit
async def test_missing_xg_rejects_pair_even_if_upstream_marks_it_verified() -> None:
    competition_id, season_id = uuid4(), uuid4()
    fixture = _fixture(competition_id, season_id, days_ago=1)
    builder, _, statistics_service = _builder(
        [fixture],
        [
            _result(
                fixture,
                fixture.home_team_id,
                xg=None,
                xga=0.8,
            )
        ],
        policy=VerifiedLeagueBaselinePolicy(
            maximum_fixtures=1,
            minimum_fixtures=1,
        ),
    )

    result = await builder.build(
        competition_id,
        season_id,
        target_fixture_id=uuid4(),
        as_of=_AS_OF,
    )

    assert result.status is LeagueBaselineStatus.QUALITY_REJECTED
    assert result.baseline is None
    assert statistics_service.verify.await_count == 1


@pytest.mark.unit
async def test_lowest_side_evidence_controls_gate() -> None:
    competition_id, season_id = uuid4(), uuid4()
    fixture = _fixture(competition_id, season_id, days_ago=1)
    builder, _, _ = _builder(
        [fixture],
        _pair_results(
            fixture,
            home_evidence=EvidenceLevel.A,
            away_evidence=EvidenceLevel.C,
        ),
        policy=VerifiedLeagueBaselinePolicy(
            maximum_fixtures=1,
            minimum_fixtures=1,
        ),
    )

    result = await builder.build(
        competition_id,
        season_id,
        target_fixture_id=uuid4(),
        as_of=_AS_OF,
    )

    assert result.status is LeagueBaselineStatus.QUALITY_REJECTED
    assert result.evidence_level is EvidenceLevel.C
    assert result.baseline is None


@pytest.mark.unit
async def test_zero_xg_baseline_is_rejected() -> None:
    competition_id, season_id = uuid4(), uuid4()
    fixture = _fixture(competition_id, season_id, days_ago=1)
    builder, _, _ = _builder(
        [fixture],
        _pair_results(fixture, home_xg=0.0, away_xg=0.0),
        policy=VerifiedLeagueBaselinePolicy(
            maximum_fixtures=1,
            minimum_fixtures=1,
        ),
    )

    result = await builder.build(
        competition_id,
        season_id,
        target_fixture_id=uuid4(),
        as_of=_AS_OF,
    )

    assert result.status is LeagueBaselineStatus.QUALITY_REJECTED
    assert result.data_completeness.value == 100.0
    assert result.baseline is None


@pytest.mark.unit
async def test_finished_fixture_without_score_is_rejected_before_snapshot_lookup() -> None:
    competition_id, season_id = uuid4(), uuid4()
    fixture = _fixture(competition_id, season_id, days_ago=1, score=None)
    builder, _, statistics_service = _builder(
        [fixture],
        [],
        policy=VerifiedLeagueBaselinePolicy(
            maximum_fixtures=1,
            minimum_fixtures=1,
        ),
    )

    result = await builder.build(
        competition_id,
        season_id,
        target_fixture_id=uuid4(),
        as_of=_AS_OF,
    )

    assert result.status is LeagueBaselineStatus.QUALITY_REJECTED
    assert result.rejected_fixture_ids == (fixture.id,)
    statistics_service.verify.assert_not_awaited()


@pytest.mark.unit
async def test_naive_as_of_is_rejected_before_repository_access() -> None:
    builder, repository, _ = _builder(
        [],
        [],
        policy=VerifiedLeagueBaselinePolicy(),
    )

    with pytest.raises(ValueError, match="timezone-aware"):
        await builder.build(
            uuid4(),
            uuid4(),
            target_fixture_id=uuid4(),
            as_of=datetime(2026, 7, 29, 18, 0),
        )

    repository.list_finished_by_competition.assert_not_awaited()


@pytest.mark.unit
async def test_repository_cannot_cross_season_boundary() -> None:
    competition_id, season_id = uuid4(), uuid4()
    fixture = _fixture(competition_id, uuid4(), days_ago=1)
    builder, _, statistics_service = _builder(
        [fixture],
        [],
        policy=VerifiedLeagueBaselinePolicy(
            maximum_fixtures=1,
            minimum_fixtures=1,
        ),
    )

    with pytest.raises(ValueError, match="outside the requested season"):
        await builder.build(
            competition_id,
            season_id,
            target_fixture_id=uuid4(),
            as_of=_AS_OF,
        )

    statistics_service.verify.assert_not_awaited()


@pytest.mark.parametrize("value", [0, -1, True, 1.5])
def test_policy_rejects_invalid_fixture_counts(value: int) -> None:
    with pytest.raises(ValueError, match="positive integer"):
        VerifiedLeagueBaselinePolicy(maximum_fixtures=value)


def test_policy_rejects_minimum_above_maximum() -> None:
    with pytest.raises(ValueError, match="cannot exceed"):
        VerifiedLeagueBaselinePolicy(
            maximum_fixtures=10,
            minimum_fixtures=11,
        )


@pytest.mark.parametrize("value", [float("nan"), -1.0, 101.0, True])
def test_policy_rejects_invalid_completeness(value: float) -> None:
    with pytest.raises(ValueError, match="between 0 and 100"):
        VerifiedLeagueBaselinePolicy(minimum_completeness=value)


@pytest.mark.parametrize("value", [float("nan"), -0.1, True])
def test_policy_rejects_invalid_pair_tolerance(value: float) -> None:
    with pytest.raises(ValueError, match="finite and non-negative"):
        VerifiedLeagueBaselinePolicy(pair_tolerance=value)
