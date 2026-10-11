"""Append-only raw payload and normalized observation storage."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from app.team_intelligence.contracts import DataType, Observation, RawPayload

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping
    from pathlib import Path


@dataclass(frozen=True, slots=True)
class ArchiveResult:
    payload_path: str
    receipt_path: str
    payload_created: bool
    receipt_created: bool


class ContentAddressedArchive:
    """Archive immutable bytes once and append capture receipts separately."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def archive(self, payload: RawPayload) -> ArchiveResult:
        source_dir = self._root / payload.source_name
        payload_dir = source_dir / "payloads" / payload.sha256[:2]
        receipt_dir = source_dir / "receipts" / payload.captured_at.strftime("%Y/%m/%d")
        payload_dir.mkdir(parents=True, exist_ok=True)
        receipt_dir.mkdir(parents=True, exist_ok=True)
        suffix = ".json" if "json" in payload.media_type.casefold() else ".bin"
        payload_path = payload_dir / f"{payload.sha256}{suffix}"
        payload_created = _write_exclusive(payload_path, payload.content)
        receipt_identity = {
            "source_name": payload.source_name,
            "source_url": payload.source_url,
            "captured_at": payload.captured_at.isoformat(),
            "raw_payload_hash": payload.sha256,
        }
        receipt_name = _json_hash(receipt_identity)
        receipt_path = receipt_dir / f"{receipt_name}.json"
        receipt = {
            **receipt_identity,
            "media_type": payload.media_type,
            "source_policy": payload.source_policy,
            "response_headers": _safe_headers(payload.response_headers),
        }
        receipt_created = _write_exclusive(
            receipt_path,
            json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8"),
        )
        return ArchiveResult(
            str(payload_path),
            str(receipt_path),
            payload_created,
            receipt_created,
        )


@dataclass(frozen=True, slots=True)
class AppendResult:
    inserted: int
    duplicates: int


class ObservationStore:
    """One immutable JSON document per observation for atomic replay detection."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def append(self, observations: Iterable[Observation]) -> AppendResult:
        inserted = 0
        duplicates = 0
        for observation in observations:
            target = (
                self._root / observation.observation_id[:2] / f"{observation.observation_id}.json"
            )
            target.parent.mkdir(parents=True, exist_ok=True)
            content = json.dumps(
                observation.to_dict(),
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
            ).encode("utf-8")
            if _write_exclusive(target, content):
                inserted += 1
            else:
                stored = json.loads(target.read_text(encoding="utf-8"))
                current = observation.to_dict()
                stored.pop("captured_at", None)
                current.pop("captured_at", None)
                if stored != current:
                    raise ValueError("observation identity collision")
                duplicates += 1
        return AppendResult(inserted=inserted, duplicates=duplicates)

    def count(self) -> int:
        return sum(1 for _ in self._root.glob("*/*.json")) if self._root.exists() else 0


@dataclass(frozen=True, slots=True)
class SourceHealth:
    source_name: str
    attempts: int
    successes: int
    empty_results: int
    failures: int
    last_success_at: datetime | None
    last_error_code: str | None


class SourceHealthTracker:
    def __init__(self) -> None:
        self._events: list[tuple[str, bool, bool, datetime, str | None]] = []

    def record(
        self,
        source_name: str,
        *,
        success: bool,
        empty: bool,
        captured_at: datetime,
        error_code: str | None = None,
    ) -> None:
        self._events.append((source_name, success, empty, captured_at, error_code))

    def snapshot(self, source_name: str) -> SourceHealth:
        events = [event for event in self._events if event[0] == source_name]
        successes = [event for event in events if event[1]]
        failures = [event for event in events if not event[1]]
        return SourceHealth(
            source_name=source_name,
            attempts=len(events),
            successes=len(successes),
            empty_results=sum(event[2] for event in events),
            failures=len(failures),
            last_success_at=max((event[3] for event in successes), default=None),
            last_error_code=failures[-1][4] if failures else None,
        )


DEFAULT_MAX_AGES: Mapping[DataType, timedelta] = {
    DataType.INJURY: timedelta(hours=6),
    DataType.RECOVERY_STATUS: timedelta(hours=12),
    DataType.ILLNESS: timedelta(hours=6),
    DataType.SUSPENSION: timedelta(hours=24),
    DataType.TRAINING_STATUS: timedelta(hours=12),
    DataType.SQUAD_CALLUP: timedelta(hours=24),
    DataType.SQUAD_OMISSION: timedelta(hours=24),
    DataType.CONFIRMED_LINEUP: timedelta(minutes=75),
    DataType.SUBSTITUTES: timedelta(minutes=75),
    DataType.FORMATION: timedelta(minutes=75),
    DataType.ROTATION_PLAN: timedelta(hours=24),
    DataType.MINUTES_RESTRICTION: timedelta(hours=24),
    DataType.MANAGER_QUOTE: timedelta(hours=24),
    DataType.TACTICAL_CHANGE: timedelta(hours=24),
    DataType.PLAYER_RETURN: timedelta(hours=24),
    DataType.LATE_WITHDRAWAL: timedelta(minutes=30),
    DataType.INTERNATIONAL_DUTY: timedelta(days=2),
    DataType.GENERAL_TEAM_NEWS: timedelta(hours=12),
}


def is_stale(
    observation: Observation,
    *,
    now: datetime,
    max_ages: Mapping[DataType, timedelta] = DEFAULT_MAX_AGES,
) -> bool:
    return now - observation.captured_at > max_ages[observation.data_type]


def _write_exclusive(path: Path, content: bytes) -> bool:
    try:
        with path.open("xb") as handle:
            handle.write(content)
    except FileExistsError:
        return False
    return True


def _json_hash(payload: Mapping[str, object]) -> str:
    import hashlib

    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _safe_headers(headers: Mapping[str, str]) -> dict[str, str]:
    allowed = {"content-type", "etag", "last-modified", "expires", "retry-after"}
    return {key.casefold(): value for key, value in headers.items() if key.casefold() in allowed}
