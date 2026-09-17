from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from uuid import UUID

import pytest

from app.models.entities.fixture import Fixture
from app.services.fixture_analysis import DetailedAnalysis, FixtureAnalysisResult
from app.services.prediction_decision_identity import (
    PredictionDecisionContext,
    analysis_input_fingerprint,
    build_prediction_decision_identity,
)

_FIXTURE_ID = UUID("10000000-0000-0000-0000-000000000001")
_COMPETITION_ID = UUID("20000000-0000-0000-0000-000000000001")
_HOME_ID = UUID("30000000-0000-0000-0000-000000000001")
_AWAY_ID = UUID("40000000-0000-0000-0000-000000000001")


def _analysis(*, analyzed_at: datetime, message: str = "insufficient history") -> DetailedAnalysis:
    fixture = Fixture(
        id=_FIXTURE_ID,
        competition_id=_COMPETITION_ID,
        home_team_id=_HOME_ID,
        away_team_id=_AWAY_ID,
        kickoff=datetime(2026, 9, 17, 18, 0, tzinfo=UTC),
    )
    return DetailedAnalysis(
        fixture=fixture,
        analysis_as_of=analyzed_at,
        model_input=None,
        model_output=None,
        reviewed=[],
        result=FixtureAnalysisResult(fixture_id=fixture.id, message=message),
    )


def test_identity_ignores_wall_clock_but_is_stable_for_same_semantics() -> None:
    first = _analysis(analyzed_at=datetime(2026, 9, 17, 6, 0, tzinfo=UTC))
    replay = replace(first, analysis_as_of=first.analysis_as_of + timedelta(minutes=5))
    context = PredictionDecisionContext.daily(date(2026, 9, 17))
    payload = {"selection": None, "final_decision": "WATCH"}

    first_identity = build_prediction_decision_identity(
        detailed=first,
        context=context,
        prediction_version="1.0.0",
        model_version="model-v1",
        decision_payload=payload,
    )
    replay_identity = build_prediction_decision_identity(
        detailed=replay,
        context=context,
        prediction_version="1.0.0",
        model_version="model-v1",
        decision_payload=payload,
    )

    assert first_identity == replay_identity


def test_distinct_checkpoints_and_material_input_changes_have_distinct_identity() -> None:
    detailed = _analysis(analyzed_at=datetime(2026, 9, 17, 16, 30, tzinfo=UTC))
    changed = replace(
        detailed,
        result=replace(detailed.result, message="odds unavailable"),
    )
    payload = {"selection": None, "final_decision": "WATCH"}

    identities = {
        build_prediction_decision_identity(
            detailed=detailed,
            context=PredictionDecisionContext.pre_kickoff(checkpoint),
            prediction_version="1.0.0",
            model_version="model-v1",
            decision_payload=payload,
        ).key
        for checkpoint in ("T90", "T60", "T30")
    }

    assert len(identities) == 3
    assert analysis_input_fingerprint(changed) != analysis_input_fingerprint(detailed)

    model_v1 = build_prediction_decision_identity(
        detailed=detailed,
        context=PredictionDecisionContext.daily(date(2026, 9, 17)),
        prediction_version="1.0.0",
        model_version="model-v1",
        decision_payload=payload,
    )
    model_v2 = build_prediction_decision_identity(
        detailed=detailed,
        context=PredictionDecisionContext.daily(date(2026, 9, 17)),
        prediction_version="1.0.0",
        model_version="model-v2",
        decision_payload=payload,
    )
    assert model_v1.key != model_v2.key


def test_context_contract_rejects_ambiguous_workflow_identity() -> None:
    with pytest.raises(ValueError, match="checkpoint=DAILY"):
        PredictionDecisionContext.daily(None)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="T90, T60, or T30"):
        PredictionDecisionContext.pre_kickoff("T45")
