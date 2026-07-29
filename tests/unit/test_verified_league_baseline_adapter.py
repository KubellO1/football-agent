"""VerifiedLeagueBaselineAdapter 的准入边界与指标映射单元测试。"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.models.value_objects.decision import DataCompleteness, EvidenceLevel
from app.services.models.lambda_estimator import BaselineMetric, LeagueBaseline
from app.services.verified_league_baseline import (
    LeagueBaselineStatus,
    LeagueXGBaseline,
    VerifiedLeagueBaselineResult,
)
from app.services.verified_league_baseline_adapter import VerifiedLeagueBaselineAdapter


def _verified_result() -> VerifiedLeagueBaselineResult:
    return VerifiedLeagueBaselineResult(
        status=LeagueBaselineStatus.VERIFIED,
        baseline=LeagueXGBaseline(
            xg_per_team_match=1.35,
            fixture_count=30,
        ),
        data_completeness=DataCompleteness(96.0),
        evidence_level=EvidenceLevel.B,
        maximum_fixtures=100,
        minimum_fixtures=30,
        history_count=30,
        verified_fixture_count=30,
    )


@pytest.mark.unit
def test_adapt_maps_verified_xg_baseline_to_explicit_model_contract() -> None:
    baseline = VerifiedLeagueBaselineAdapter.adapt(_verified_result())

    assert baseline == LeagueBaseline(
        rate_per_team_match=1.35,
        metric=BaselineMetric.XG,
    )
    assert baseline.metric is BaselineMetric.XG


@pytest.mark.unit
def test_adapt_rejects_insufficient_history_result() -> None:
    result = VerifiedLeagueBaselineResult(
        status=LeagueBaselineStatus.INSUFFICIENT_HISTORY,
        baseline=None,
        data_completeness=DataCompleteness(100.0),
        evidence_level=EvidenceLevel.B,
        maximum_fixtures=100,
        minimum_fixtures=30,
        history_count=10,
        verified_fixture_count=10,
    )

    with pytest.raises(ValueError, match="verified league baseline"):
        VerifiedLeagueBaselineAdapter.adapt(result)


@pytest.mark.unit
def test_adapt_rejects_quality_rejected_result() -> None:
    rejected_fixture_id = uuid4()
    result = VerifiedLeagueBaselineResult(
        status=LeagueBaselineStatus.QUALITY_REJECTED,
        baseline=None,
        data_completeness=DataCompleteness(96.0),
        evidence_level=EvidenceLevel.B,
        maximum_fixtures=100,
        minimum_fixtures=30,
        history_count=30,
        verified_fixture_count=29,
        rejected_fixture_ids=(rejected_fixture_id,),
    )

    with pytest.raises(ValueError, match="verified league baseline"):
        VerifiedLeagueBaselineAdapter.adapt(result)
