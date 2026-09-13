"""追加式归档、观察存储、健康和陈旧检测。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from app.intelligence.contracts import DataType, Observation, RawPayload

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping
    from pathlib import Path


@dataclass(frozen=True, slots=True)
class ArchiveResult:
    payload_path: str
    metadata_path: str
    created: bool


class ContentAddressedArchive:
    """按内容哈希存放原始响应；已有内容永不覆盖。"""

    def __init__(self, root: Path) -> None:
        self._root = root

    def archive(self, payload: RawPayload) -> ArchiveResult:
        day = payload.captured_at.strftime("%Y/%m/%d")
        directory = self._root / payload.source / day
        directory.mkdir(parents=True, exist_ok=True)
        extension = ".json" if "json" in payload.media_type.casefold() else ".bin"
        candidates = tuple(directory / f"{payload.sha256}{suffix}" for suffix in (".json", ".bin"))
        existing_path = next((path for path in candidates if path.exists()), None)
        content_path = existing_path or directory / f"{payload.sha256}{extension}"
        metadata_path = directory / f"{payload.sha256}.metadata.json"
        created = False
        try:
            with content_path.open("xb") as handle:
                handle.write(payload.content)
            created = True
        except FileExistsError:
            if content_path.read_bytes() != payload.content:
                raise ValueError("content-addressed archive hash collision") from None
        metadata = {
            "source": payload.source,
            "source_url": payload.source_url,
            "captured_at": payload.captured_at.isoformat(),
            "raw_payload_hash": payload.sha256,
            "media_type": payload.media_type,
            "source_policy": payload.source_policy,
        }
        if not metadata_path.exists():
            metadata_path.write_text(
                json.dumps(metadata, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        return ArchiveResult(str(content_path), str(metadata_path), created)


@dataclass(frozen=True, slots=True)
class AppendResult:
    inserted: int
    duplicates: int


class JsonlObservationStore:
    """隔离研究环境用追加式存储；不更新已存在 observation。"""

    def __init__(self, path: Path) -> None:
        self._path = path

    def append(self, observations: Iterable[Observation]) -> AppendResult:
        known = self._known_ids()
        inserted = 0
        duplicates = 0
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8", newline="\n") as handle:
            for observation in observations:
                if observation.observation_id in known:
                    duplicates += 1
                    continue
                handle.write(json.dumps(observation.to_dict(), ensure_ascii=False, sort_keys=True))
                handle.write("\n")
                known.add(observation.observation_id)
                inserted += 1
        return AppendResult(inserted, duplicates)

    def _known_ids(self) -> set[str]:
        if not self._path.exists():
            return set()
        values: set[str] = set()
        for line in self._path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                values.add(str(json.loads(line)["observation_id"]))
        return values


@dataclass(frozen=True, slots=True)
class SourceHealth:
    source: str
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
        source: str,
        *,
        success: bool,
        empty: bool,
        captured_at: datetime,
        error_code: str | None = None,
    ) -> None:
        self._events.append((source, success, empty, captured_at, error_code))

    def snapshot(self, source: str) -> SourceHealth:
        events = [event for event in self._events if event[0] == source]
        successes = [event for event in events if event[1]]
        failures = [event for event in events if not event[1]]
        return SourceHealth(
            source=source,
            attempts=len(events),
            successes=len(successes),
            empty_results=sum(event[2] for event in events),
            failures=len(failures),
            last_success_at=max((event[3] for event in successes), default=None),
            last_error_code=failures[-1][4] if failures else None,
        )


DEFAULT_MAX_AGES: Mapping[DataType, timedelta] = {
    DataType.FIXTURE: timedelta(hours=24),
    DataType.TEAM_NEWS: timedelta(hours=12),
    DataType.INJURY: timedelta(hours=6),
    DataType.SUSPENSION: timedelta(hours=24),
    DataType.LINEUP: timedelta(minutes=90),
    DataType.LINEUP_CHANGE: timedelta(minutes=30),
    DataType.WEATHER: timedelta(hours=6),
    DataType.ODDS: timedelta(minutes=30),
    DataType.ODDS_MOVEMENT: timedelta(minutes=30),
    DataType.POST_MATCH_STATISTICS: timedelta(hours=24),
}


def is_stale(
    observation: Observation,
    *,
    now: datetime,
    max_ages: Mapping[DataType, timedelta] = DEFAULT_MAX_AGES,
) -> bool:
    return now - observation.captured_at > max_ages[observation.data_type]
