"""赛前状态解析器的纯逻辑测试。"""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.config.settings import Settings
from app.models.value_objects.pre_kickoff import (
    PreKickoffCheckpoint,
    PreKickoffSnapshot,
    PreKickoffState,
)
from app.services.pre_kickoff_state_resolver import (
    PreKickoffStateResolver,
    PreKickoffThresholds,
)

_KICKOFF = datetime(2026, 8, 2, 20, 0, tzinfo=UTC)
_THRESHOLDS = PreKickoffThresholds(min_expected_value=0.05, min_confidence=0.70)
_RESOLVER = PreKickoffStateResolver(_THRESHOLDS)

_MINUTES_BEFORE_KICKOFF = {
    PreKickoffCheckpoint.T90: 90,
    PreKickoffCheckpoint.T60: 60,
    PreKickoffCheckpoint.T30: 30,
    PreKickoffCheckpoint.POST_T30: 15,
}


def _snapshot(
    checkpoint: PreKickoffCheckpoint = PreKickoffCheckpoint.T90,
    **changes: Any,
) -> PreKickoffSnapshot:
    defaults: dict[str, Any] = {
        "checkpoint": checkpoint,
        "historical_data_sufficient": True,
        "lineup_available": True,
        "odds_available": True,
        "model_probability_available": True,
        "expected_value": 0.06,
        "confidence": 0.71,
        "gate_passed": True,
        "risk_passed": True,
        "kickoff_time": _KICKOFF,
        "current_time": _KICKOFF - timedelta(minutes=_MINUTES_BEFORE_KICKOFF[checkpoint]),
        "previous_state": None,
    }
    defaults.update(changes)
    return PreKickoffSnapshot(**defaults)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({"historical_data_sufficient": False}, PreKickoffState.INSUFFICIENT_HISTORY),
        ({"lineup_available": False}, PreKickoffState.WAITING_FOR_LINEUP),
        ({"odds_available": False}, PreKickoffState.ODDS_MISSING),
        ({}, PreKickoffState.BET),
        ({"gate_passed": False}, PreKickoffState.WATCH),
    ],
)
def test_resolves_each_non_final_state_by_explicit_priority(
    changes: dict[str, Any],
    expected: PreKickoffState,
) -> None:
    assert _RESOLVER.resolve(_snapshot(**changes)) is expected


@pytest.mark.unit
def test_resolves_final_no_bet_for_complete_failed_evaluation_at_t30() -> None:
    state = _RESOLVER.resolve(_snapshot(PreKickoffCheckpoint.T30, expected_value=0.049999))

    assert state is PreKickoffState.FINAL_NO_BET


@pytest.mark.unit
@pytest.mark.parametrize(
    ("checkpoint", "expected"),
    [
        (PreKickoffCheckpoint.T90, PreKickoffState.WATCH),
        (PreKickoffCheckpoint.T60, PreKickoffState.WATCH),
        (PreKickoffCheckpoint.T30, PreKickoffState.FINAL_NO_BET),
        (PreKickoffCheckpoint.POST_T30, PreKickoffState.FINAL_NO_BET),
    ],
)
def test_failed_complete_evaluation_respects_all_checkpoints(
    checkpoint: PreKickoffCheckpoint,
    expected: PreKickoffState,
) -> None:
    state = _RESOLVER.resolve(_snapshot(checkpoint, confidence=0.69))

    assert state is expected


@pytest.mark.unit
def test_actual_time_at_t30_is_final_even_if_checkpoint_label_is_stale() -> None:
    state = _RESOLVER.resolve(
        _snapshot(
            PreKickoffCheckpoint.T60,
            current_time=_KICKOFF - timedelta(minutes=20),
            gate_passed=False,
        )
    )

    assert state is PreKickoffState.FINAL_NO_BET


@pytest.mark.unit
def test_exact_ev_and_confidence_thresholds_pass() -> None:
    state = _RESOLVER.resolve(_snapshot(expected_value=0.05, confidence=0.70))

    assert state is PreKickoffState.BET


@pytest.mark.unit
@pytest.mark.parametrize(
    "changes",
    [
        {"expected_value": 0.05 - 1e-12},
        {"confidence": 0.70 - 1e-12},
        {"gate_passed": False},
        {"risk_passed": False},
    ],
)
def test_each_production_rule_is_required_before_final_checkpoint(
    changes: dict[str, Any],
) -> None:
    assert _RESOLVER.resolve(_snapshot(**changes)) is PreKickoffState.WATCH


@pytest.mark.unit
@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({"lineup_available": False}, PreKickoffState.WAITING_FOR_LINEUP),
        ({"odds_available": False}, PreKickoffState.ODDS_MISSING),
    ],
)
def test_previous_bet_can_be_downgraded_when_later_data_disappears(
    changes: dict[str, Any],
    expected: PreKickoffState,
) -> None:
    snapshot = _snapshot(
        PreKickoffCheckpoint.T30,
        previous_state=PreKickoffState.BET,
        **changes,
    )

    assert _RESOLVER.resolve(snapshot) is expected


@pytest.mark.unit
def test_repeated_identical_input_is_idempotent() -> None:
    snapshot = _snapshot(previous_state=PreKickoffState.WATCH)

    first = _RESOLVER.resolve(snapshot)
    second = _RESOLVER.resolve(snapshot)

    assert first is PreKickoffState.BET
    assert second is first


@pytest.mark.unit
@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({"historical_data_sufficient": False}, PreKickoffState.INSUFFICIENT_HISTORY),
        ({"lineup_available": False}, PreKickoffState.WAITING_FOR_LINEUP),
        ({"odds_available": False}, PreKickoffState.ODDS_MISSING),
        ({"model_probability_available": False}, PreKickoffState.WATCH),
        ({"expected_value": None}, PreKickoffState.WATCH),
        ({"confidence": None}, PreKickoffState.WATCH),
    ],
)
def test_final_no_bet_cannot_occur_with_incomplete_data(
    changes: dict[str, Any],
    expected: PreKickoffState,
) -> None:
    state = _RESOLVER.resolve(_snapshot(PreKickoffCheckpoint.POST_T30, **changes))

    assert state is expected
    assert state is not PreKickoffState.FINAL_NO_BET


@pytest.mark.unit
def test_missing_odds_ignores_stale_ev_from_previous_evaluation() -> None:
    snapshot = _snapshot(
        PreKickoffCheckpoint.POST_T30,
        odds_available=False,
        expected_value=0.25,
        previous_state=PreKickoffState.BET,
    )

    assert _RESOLVER.resolve(snapshot) is PreKickoffState.ODDS_MISSING


@pytest.mark.unit
def test_thresholds_are_loaded_from_existing_settings() -> None:
    settings = Settings(
        recommendations_min_ev=0.08,
        recommendations_min_confidence=0.75,
    )
    resolver = PreKickoffStateResolver.from_settings(settings)

    assert (
        resolver.resolve(_snapshot(expected_value=0.079, confidence=0.80)) is PreKickoffState.WATCH
    )
    assert resolver.resolve(_snapshot(expected_value=0.08, confidence=0.75)) is PreKickoffState.BET


@pytest.mark.unit
def test_snapshot_is_immutable() -> None:
    snapshot = _snapshot()

    with pytest.raises(FrozenInstanceError):
        snapshot.lineup_available = False  # type: ignore[misc]


@pytest.mark.unit
def test_snapshot_rejects_naive_times_and_invalid_confidence() -> None:
    with pytest.raises(ValueError, match="时区"):
        _snapshot(kickoff_time=datetime(2026, 8, 2, 20, 0))
    with pytest.raises(ValueError, match="confidence"):
        _snapshot(confidence=1.01)


@pytest.mark.unit
def test_replacing_snapshot_does_not_mutate_original() -> None:
    original = _snapshot()
    changed = replace(original, lineup_available=False, previous_state=PreKickoffState.BET)

    assert _RESOLVER.resolve(original) is PreKickoffState.BET
    assert _RESOLVER.resolve(changed) is PreKickoffState.WAITING_FOR_LINEUP
