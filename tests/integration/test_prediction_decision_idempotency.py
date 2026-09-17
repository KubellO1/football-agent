from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime
from types import SimpleNamespace
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.repositories.sqlalchemy.models import (
    PREDICTION_RECORD_DECISION,
    PredictionORM,
)
from app.services.fixture_analysis import (
    DetailedAnalysis,
    FixtureAnalysisResult,
    ReviewedSelection,
    SelectionAnalysis,
)
from app.services.prediction_decision_identity import PredictionDecisionContext
from app.services.prediction_logger import PREDICTION_VERSION, log_fixture_predictions
from app.services.recommendation_gate import GateDecision

if TYPE_CHECKING:
    from app.models.entities.fixture import Fixture

_RUN_DATE = date(2026, 7, 3)
_ANALYZED_AT = datetime(2026, 7, 3, 6, 0, tzinfo=UTC)


def _empty_analysis(fixture: Fixture, *, message: str = "insufficient history") -> DetailedAnalysis:
    return DetailedAnalysis(
        fixture=fixture,
        analysis_as_of=_ANALYZED_AT,
        model_input=None,
        model_output=None,
        reviewed=[],
        result=FixtureAnalysisResult(fixture_id=fixture.id, message=message),
    )


def _selection_analysis(
    fixture: Fixture,
    *,
    recommended: bool,
    expected_value: float,
    gate: GateDecision | None = None,
) -> DetailedAnalysis:
    selection = SelectionAnalysis(
        code="home",
        selection_label="Home",
        decimal_odds=2.1,
        model_probability=0.52,
        implied_probability=0.476,
        edge=0.044,
        expected_value=expected_value,
        kelly_fraction=0.02,
        kelly_stake=20.0,
        currency="EUR",
        recommended=recommended,
        confidence=0.74,
        reasons=[],
        explanation="test",
    )
    reviewed = []
    if gate is not None:
        candidate = SimpleNamespace(selection=SimpleNamespace(code="home"))
        reviewed = [ReviewedSelection(candidate=candidate, decision=gate)]  # type: ignore[arg-type]
    return DetailedAnalysis(
        fixture=fixture,
        analysis_as_of=_ANALYZED_AT,
        model_input=None,
        model_output=None,
        reviewed=reviewed,
        result=FixtureAnalysisResult(
            fixture_id=fixture.id,
            selections=[selection],
            data_completeness=90.0,
        ),
    )


async def _decision_count(session: AsyncSession, fixture_id) -> int:  # type: ignore[no-untyped-def]
    return (
        await session.execute(
            select(func.count(PredictionORM.id)).where(
                PredictionORM.fixture_id == fixture_id,
                PredictionORM.record_kind == PREDICTION_RECORD_DECISION,
            )
        )
    ).scalar_one()


async def _log(
    session: AsyncSession,
    detailed: DetailedAnalysis,
    context: PredictionDecisionContext,
    *,
    model_version: str = "model-v1",
):  # type: ignore[no-untyped-def]
    return await log_fixture_predictions(
        detailed,
        session=session,
        competition_name="Test League",
        home_team_name="Home",
        away_team_name="Away",
        model_version=model_version,
        decision_context=context,
    )


@pytest.mark.integration
async def test_watch_replay_is_idempotent_and_new_context_is_preserved(
    db_session: AsyncSession,
    persisted_fixture: Fixture,
) -> None:
    detailed = _empty_analysis(persisted_fixture)
    daily = PredictionDecisionContext.daily(_RUN_DATE)

    first = await _log(db_session, detailed, daily)
    replay = await _log(db_session, detailed, daily)
    t90 = await _log(db_session, detailed, PredictionDecisionContext.pre_kickoff("T90"))
    t60 = await _log(db_session, detailed, PredictionDecisionContext.pre_kickoff("T60"))
    t30 = await _log(db_session, detailed, PredictionDecisionContext.pre_kickoff("T30"))
    changed_input = await _log(
        db_session,
        _empty_analysis(persisted_fixture, message="different material input"),
        daily,
    )

    assert (first.inserted, replay.inserted, replay.reused) == (1, 0, 1)
    assert (t90.inserted, t60.inserted, t30.inserted) == (1, 1, 1)
    assert changed_input.inserted == 1
    assert await _decision_count(db_session, persisted_fixture.id) == 5


@pytest.mark.integration
async def test_bet_and_no_bet_paths_preserve_decision_and_replay_contract(
    db_session: AsyncSession,
    persisted_fixture: Fixture,
) -> None:
    context = PredictionDecisionContext.daily(_RUN_DATE)
    bet = _selection_analysis(persisted_fixture, recommended=True, expected_value=0.09)
    no_bet = _selection_analysis(
        persisted_fixture,
        recommended=False,
        expected_value=-0.01,
        gate=GateDecision(approved=False, reasons=["negative EV"]),
    )

    bet_first = await _log(db_session, bet, context)
    bet_replay = await _log(db_session, bet, context)
    no_bet_first = await _log(db_session, no_bet, context)
    no_bet_replay = await _log(db_session, no_bet, context)
    decisions = (
        (
            await db_session.execute(
                select(PredictionORM.final_decision).where(
                    PredictionORM.fixture_id == persisted_fixture.id
                )
            )
        )
        .scalars()
        .all()
    )

    assert (bet_first.bet_count, bet_first.inserted, bet_replay.inserted) == (1, 1, 0)
    assert (no_bet_first.no_bet_count, no_bet_first.inserted, no_bet_replay.inserted) == (1, 1, 0)
    assert sorted(decisions) == ["BET", "NO_BET"]


@pytest.mark.integration
async def test_legacy_random_uuid_decision_is_reused_without_historical_update(
    db_session: AsyncSession,
    persisted_fixture: Fixture,
) -> None:
    detailed = _empty_analysis(persisted_fixture)
    legacy_id = uuid4()
    db_session.add(
        PredictionORM(
            id=legacy_id,
            fixture_id=persisted_fixture.id,
            record_kind=PREDICTION_RECORD_DECISION,
            kickoff_time=persisted_fixture.kickoff,
            competition="Test League",
            home_team="Home",
            away_team="Away",
            prediction_timestamp=_ANALYZED_AT,
            prediction_version=PREDICTION_VERSION,
            model_version="model-v1",
            final_decision="WATCH",
            why_not_bet="insufficient history",
            provider_sources={"fixture_source": "unknown"},
            data_quality=0.0,
            generated_at=_ANALYZED_AT,
        )
    )
    await db_session.flush()

    report = await _log(
        db_session,
        detailed,
        PredictionDecisionContext.daily(_RUN_DATE),
    )
    persisted = await db_session.get(PredictionORM, legacy_id)

    assert report.inserted == 0
    assert report.reused == 1
    assert await _decision_count(db_session, persisted_fixture.id) == 1
    assert persisted is not None
    assert persisted.provider_sources == {"fixture_source": "unknown"}


@pytest.mark.integration
async def test_concurrent_replay_inserts_exactly_one_decision(
    db_session: AsyncSession,
    persisted_fixture: Fixture,
) -> None:
    await db_session.commit()
    bind = db_session.bind
    assert bind is not None
    factory = async_sessionmaker(bind, class_=AsyncSession, expire_on_commit=False)
    detailed = _empty_analysis(persisted_fixture)
    context = PredictionDecisionContext.daily(_RUN_DATE)

    async def persist_once() -> int:
        async with factory() as session:
            report = await _log(session, detailed, context)
            await session.commit()
            return report.inserted

    inserted = await asyncio.gather(persist_once(), persist_once())
    async with factory() as verification:
        count = await _decision_count(verification, persisted_fixture.id)

    assert sorted(inserted) == [0, 1]
    assert count == 1
