"""Stable semantic identity for persisted prediction decisions.

Decision rows are append-only audit records.  Their identity therefore excludes
wall-clock persistence timestamps and includes the workflow checkpoint, the
model/prediction versions, the material analysis input, and the persisted
decision payload.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, fields, is_dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import Enum, StrEnum
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid5

if TYPE_CHECKING:
    from app.services.fixture_analysis import DetailedAnalysis

_DECISION_NAMESPACE = UUID("6d819c3a-49a0-4f2a-ae09-97772ba37877")


class PredictionDecisionWorkflow(StrEnum):
    DAILY = "daily"
    PRE_KICKOFF = "pre_kickoff"


@dataclass(frozen=True, slots=True)
class PredictionDecisionContext:
    """Execution context that distinguishes legitimate decision observations."""

    workflow: PredictionDecisionWorkflow
    checkpoint: str
    run_date: date | None = None

    def __post_init__(self) -> None:
        if self.workflow is PredictionDecisionWorkflow.DAILY:
            if self.checkpoint != "DAILY" or self.run_date is None:
                raise ValueError("daily decisions require checkpoint=DAILY and run_date")
            return
        if self.run_date is not None:
            raise ValueError("pre-kickoff decisions must not carry a run_date")
        if self.checkpoint not in {"T90", "T60", "T30"}:
            raise ValueError("pre-kickoff checkpoint must be T90, T60, or T30")

    @classmethod
    def daily(cls, run_date: date) -> PredictionDecisionContext:
        return cls(
            workflow=PredictionDecisionWorkflow.DAILY,
            checkpoint="DAILY",
            run_date=run_date,
        )

    @classmethod
    def pre_kickoff(cls, checkpoint: str) -> PredictionDecisionContext:
        normalized = checkpoint.upper().replace("-", "")
        return cls(
            workflow=PredictionDecisionWorkflow.PRE_KICKOFF,
            checkpoint=normalized,
        )

    def as_dict(self) -> dict[str, str | None]:
        return {
            "workflow": self.workflow.value,
            "checkpoint": self.checkpoint,
            "run_date": self.run_date.isoformat() if self.run_date is not None else None,
        }


@dataclass(frozen=True, slots=True)
class PredictionDecisionIdentity:
    key: str
    row_id: UUID
    input_fingerprint: str
    decision_fingerprint: str


def _canonicalize(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if value != value or value in {float("inf"), float("-inf")}:
            raise ValueError("decision identity cannot contain non-finite floats")
        return value
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("decision identity datetime must be timezone-aware")
        return value.astimezone(UTC).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Enum):
        return _canonicalize(value.value)
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _canonicalize(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, dict):
        return {
            str(key): _canonicalize(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_canonicalize(item) for item in value]
    if isinstance(value, (set, frozenset)):
        normalized = [_canonicalize(item) for item in value]
        return sorted(normalized, key=_canonical_json)
    raise TypeError(f"unsupported decision identity value: {type(value).__name__}")


def _canonical_json(value: Any) -> str:
    return json.dumps(
        _canonicalize(value),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def analysis_input_fingerprint(detailed: DetailedAnalysis) -> str:
    """Hash material model/lineup inputs while excluding analysis wall-clock time."""

    if detailed.model_input is None:
        snapshot: dict[str, Any] = {
            "fixture": detailed.fixture,
            "model_input": None,
            "lineup_admission": detailed.lineup_admission,
            "unavailable_result": {
                "message": detailed.result.message,
                "confidence_killer": detailed.result.confidence_killer,
                "data_completeness": detailed.result.data_completeness,
            },
        }
    else:
        snapshot = {
            "model_input": detailed.model_input,
            "lineup_admission": detailed.lineup_admission,
        }
    return _fingerprint(snapshot)


def build_prediction_decision_identity(
    *,
    detailed: DetailedAnalysis,
    context: PredictionDecisionContext,
    prediction_version: str,
    model_version: str,
    decision_payload: dict[str, Any],
) -> PredictionDecisionIdentity:
    """Build a deterministic key and primary-key UUID for one decision row."""

    input_fingerprint = analysis_input_fingerprint(detailed)
    decision_fingerprint = _fingerprint(decision_payload)
    key_payload = {
        "version": 1,
        "fixture_id": detailed.fixture.id,
        "record_kind": "decision",
        "context": context.as_dict(),
        "prediction_version": prediction_version,
        "model_version": model_version,
        "input_fingerprint": input_fingerprint,
        "decision_fingerprint": decision_fingerprint,
    }
    key = _fingerprint(key_payload)
    return PredictionDecisionIdentity(
        key=key,
        row_id=uuid5(_DECISION_NAMESPACE, key),
        input_fingerprint=input_fingerprint,
        decision_fingerprint=decision_fingerprint,
    )
