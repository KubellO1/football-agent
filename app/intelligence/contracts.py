"""零成本 point-in-time 情报的 provider-neutral 数据契约。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping


class CaptureWindow(StrEnum):
    DAILY = "DAILY"
    T24H = "T-24H"
    T6H = "T-6H"
    T90 = "T-90"
    T60 = "T-60"
    T30 = "T-30"
    POST_MATCH = "POST_MATCH"


class DataType(StrEnum):
    FIXTURE = "FIXTURE"
    TEAM_NEWS = "TEAM_NEWS"
    INJURY = "INJURY"
    SUSPENSION = "SUSPENSION"
    LINEUP = "LINEUP"
    LINEUP_CHANGE = "LINEUP_CHANGE"
    WEATHER = "WEATHER"
    ODDS = "ODDS"
    ODDS_MOVEMENT = "ODDS_MOVEMENT"
    POST_MATCH_STATISTICS = "POST_MATCH_STATISTICS"


class SourcePolicy(StrEnum):
    OPEN_DATA = "OPEN_DATA"
    FREE_OFFICIAL_API = "FREE_OFFICIAL_API"
    FREE_PUBLIC_DOWNLOAD = "FREE_PUBLIC_DOWNLOAD"
    PUBLIC_WEB_AUTOMATION_ALLOWED = "PUBLIC_WEB_AUTOMATION_ALLOWED"
    MANUAL_USER_SUPPLIED = "MANUAL_USER_SUPPLIED"
    RESEARCH_ONLY = "RESEARCH_ONLY"
    REJECTED = "REJECTED"


class SemanticStatus(StrEnum):
    VERIFIED = "VERIFIED"
    UNMAPPED = "UNMAPPED"
    AMBIGUOUS = "AMBIGUOUS"
    WEATHER_UNAVAILABLE = "WEATHER_UNAVAILABLE"
    REJECTED = "REJECTED"


def utc_datetime(value: datetime, *, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value.astimezone(UTC)


def canonical_json(payload: Mapping[str, object]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


@dataclass(frozen=True, slots=True)
class RawPayload:
    source: str
    source_url: str
    captured_at: datetime
    content: bytes
    media_type: str
    source_policy: SourcePolicy

    def __post_init__(self) -> None:
        if not self.source.strip() or not self.source_url.startswith("https://"):
            raise ValueError("source and HTTPS source_url are required")
        object.__setattr__(self, "captured_at", utc_datetime(self.captured_at, field="captured_at"))

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.content).hexdigest()


@dataclass(frozen=True, slots=True)
class Observation:
    fixture_id: str
    source: str
    source_url: str
    published_at: datetime | None
    captured_at: datetime
    raw_payload_hash: str
    parser_version: str
    data_type: DataType
    source_policy: SourcePolicy
    capture_window: CaptureWindow
    payload: Mapping[str, object]
    source_record_id: str | None = None
    observed_at: datetime | None = None
    schema_version: str = "1"
    semantic_status: SemanticStatus = SemanticStatus.VERIFIED

    def __post_init__(self) -> None:
        required = (
            self.fixture_id,
            self.source,
            self.source_url,
            self.raw_payload_hash,
            self.parser_version,
        )
        if any(not value.strip() for value in required):
            raise ValueError("observation identity fields cannot be empty")
        if len(self.raw_payload_hash) != 64:
            raise ValueError("raw_payload_hash must be a SHA-256 hex digest")
        object.__setattr__(self, "captured_at", utc_datetime(self.captured_at, field="captured_at"))
        if self.published_at is not None:
            object.__setattr__(
                self,
                "published_at",
                utc_datetime(self.published_at, field="published_at"),
            )
        observed_at = self.observed_at or self.published_at or self.captured_at
        object.__setattr__(self, "observed_at", utc_datetime(observed_at, field="observed_at"))
        if not self.schema_version.strip():
            raise ValueError("schema_version cannot be empty")

    @property
    def observation_id(self) -> str:
        observed_at = self.observed_at
        if observed_at is None:  # Defensive guard for static type checking.
            raise RuntimeError("observed_at was not initialized")
        identity = {
            "fixture_id": self.fixture_id,
            "source": self.source,
            "source_record_id": self.source_record_id,
            "observed_at": observed_at.isoformat(),
            "published_at": self.published_at.isoformat() if self.published_at else None,
            "raw_payload_hash": self.raw_payload_hash,
            "parser_version": self.parser_version,
            "schema_version": self.schema_version,
            "data_type": self.data_type,
            "semantic_status": self.semantic_status,
            "capture_window": self.capture_window,
            "payload": self.payload,
        }
        return hashlib.sha256(canonical_json(identity)).hexdigest()

    def to_dict(self) -> dict[str, object]:
        observed_at = self.observed_at
        if observed_at is None:  # Defensive guard for static type checking.
            raise RuntimeError("observed_at was not initialized")
        return {
            "observation_id": self.observation_id,
            "fixture_id": self.fixture_id,
            "source": self.source,
            "source_url": self.source_url,
            "source_record_id": self.source_record_id,
            "observed_at": observed_at.isoformat(),
            "published_at": self.published_at.isoformat() if self.published_at else None,
            "captured_at": self.captured_at.isoformat(),
            "raw_payload_hash": self.raw_payload_hash,
            "parser_version": self.parser_version,
            "schema_version": self.schema_version,
            "data_type": self.data_type,
            "source_policy": self.source_policy,
            "semantic_status": self.semantic_status,
            "capture_window": self.capture_window,
            "payload": dict(self.payload),
        }
