"""赛前检查点 crossing 与幂等契约测试。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.models.value_objects.pre_kickoff import PreKickoffCheckpoint
from app.services.pre_kickoff_checkpoint_resolver import (
    PreKickoffCheckpointResolver,
    checkpoint_idempotency_key,
    completed_checkpoints,
)

_NOW = datetime(2026, 8, 2, 11, 13, tzinfo=UTC)
_RESOLVER = PreKickoffCheckpointResolver()


def _resolve(
    minutes_to_kickoff: float,
    *,
    completed: tuple[PreKickoffCheckpoint, ...] = (),
) -> PreKickoffCheckpoint | None:
    return _RESOLVER.resolve(
        kickoff_time=_NOW + timedelta(minutes=minutes_to_kickoff),
        current_time=_NOW,
        completed=completed,
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("minutes_to_kickoff", "expected"),
    [
        (82, PreKickoffCheckpoint.T90),
        (47, PreKickoffCheckpoint.T60),
        (18, PreKickoffCheckpoint.T30),
    ],
)
def test_existing_and_new_checkpoint_crossings(
    minutes_to_kickoff: float,
    expected: PreKickoffCheckpoint,
) -> None:
    assert _resolve(minutes_to_kickoff) is expected


@pytest.mark.unit
def test_arbitrary_kickoff_minute_does_not_miss_t60() -> None:
    current_time = datetime(2026, 8, 2, 11, 59, tzinfo=UTC)
    kickoff_time = datetime(2026, 8, 2, 12, 47, tzinfo=UTC)

    checkpoint = _RESOLVER.resolve(
        kickoff_time=kickoff_time,
        current_time=current_time,
    )

    assert checkpoint is PreKickoffCheckpoint.T60


@pytest.mark.unit
def test_scheduler_delay_still_returns_due_checkpoint() -> None:
    assert _resolve(38) is PreKickoffCheckpoint.T60


@pytest.mark.unit
def test_only_latest_relevant_checkpoint_is_returned_per_run() -> None:
    assert _resolve(18) is PreKickoffCheckpoint.T30


@pytest.mark.unit
def test_repeated_invocation_is_idempotent_after_completion() -> None:
    assert _resolve(47, completed=(PreKickoffCheckpoint.T60,)) is None


@pytest.mark.unit
def test_failed_processing_remains_retryable_when_not_marked_complete() -> None:
    first_attempt = _resolve(47)
    retry_attempt = _resolve(47)

    assert first_attempt is PreKickoffCheckpoint.T60
    assert retry_attempt is first_attempt


@pytest.mark.unit
def test_outside_pre_kickoff_window_has_no_due_checkpoint() -> None:
    assert _resolve(91) is None
    assert _resolve(0) is None
    assert _resolve(-1) is None


@pytest.mark.unit
def test_fixture_checkpoint_key_round_trip_preserves_existing_format() -> None:
    fixture_id = uuid4()
    keys = {
        checkpoint_idempotency_key(fixture_id, PreKickoffCheckpoint.T90),
        checkpoint_idempotency_key(fixture_id, PreKickoffCheckpoint.T30),
    }

    assert keys == {f"{fixture_id}:T-90", f"{fixture_id}:T-30"}
    assert completed_checkpoints(fixture_id, keys) == frozenset(
        {PreKickoffCheckpoint.T90, PreKickoffCheckpoint.T30}
    )
