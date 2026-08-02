"""赛前检查点到期解析器。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.models.value_objects.pre_kickoff import PreKickoffCheckpoint

if TYPE_CHECKING:
    from collections.abc import Collection
    from datetime import datetime
    from uuid import UUID

_CHECKPOINT_KEY_LABELS = {
    PreKickoffCheckpoint.T90: "T-90",
    PreKickoffCheckpoint.T60: "T-60",
    PreKickoffCheckpoint.T30: "T-30",
}


def checkpoint_idempotency_key(
    fixture_id: UUID,
    checkpoint: PreKickoffCheckpoint,
) -> str:
    """构造稳定的 fixture + checkpoint 幂等键。"""
    try:
        label = _CHECKPOINT_KEY_LABELS[checkpoint]
    except KeyError as exc:
        raise ValueError(f"不支持的赛前检查点: {checkpoint}") from exc
    return f"{fixture_id}:{label}"


def completed_checkpoints(
    fixture_id: UUID,
    completed_keys: Collection[str],
) -> frozenset[PreKickoffCheckpoint]:
    """从持久化幂等键恢复某场比赛已完成的检查点。"""
    return frozenset(
        checkpoint
        for checkpoint in _CHECKPOINT_KEY_LABELS
        if checkpoint_idempotency_key(fixture_id, checkpoint) in completed_keys
    )


class PreKickoffCheckpointResolver:
    """根据时间跨越确定本次仅需执行的最新检查点，无任何 I/O。"""

    def resolve(
        self,
        *,
        kickoff_time: datetime,
        current_time: datetime,
        completed: Collection[PreKickoffCheckpoint] = (),
    ) -> PreKickoffCheckpoint | None:
        if kickoff_time.tzinfo is None or current_time.tzinfo is None:
            raise ValueError("kickoff_time 和 current_time 必须包含时区")

        minutes_to_kickoff = (kickoff_time - current_time).total_seconds() / 60
        if minutes_to_kickoff <= 0 or minutes_to_kickoff > 90:
            return None

        if minutes_to_kickoff <= 30:
            latest = PreKickoffCheckpoint.T30
        elif minutes_to_kickoff <= 60:
            latest = PreKickoffCheckpoint.T60
        else:
            latest = PreKickoffCheckpoint.T90

        return None if latest in completed else latest
