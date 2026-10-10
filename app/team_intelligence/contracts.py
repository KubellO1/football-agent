"""Immutable contracts for point-in-time team intelligence evidence."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
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
    T15 = "T-15"
    POST_MATCH = "POST_MATCH"


class DataType(StrEnum):
    INJURY = "INJURY"
    RECOVERY_STATUS = "RECOVERY_STATUS"
    ILLNESS = "ILLNESS"
    SUSPENSION = "SUSPENSION"
    TRAINING_STATUS = "TRAINING_STATUS"
    SQUAD_CALLUP = "SQUAD_CALLUP"
    SQUAD_OMISSION = "SQUAD_OMISSION"
    CONFIRMED_LINEUP = "CONFIRMED_LINEUP"
    SUBSTITUTES = "SUBSTITUTES"
    FORMATION = "FORMATION"
    ROTATION_PLAN = "ROTATION_PLAN"
    MINUTES_RESTRICTION = "MINUTES_RESTRICTION"
    MANAGER_QUOTE = "MANAGER_QUOTE"
    TACTICAL_CHANGE = "TACTICAL_CHANGE"
    PLAYER_RETURN = "PLAYER_RETURN"
    LATE_WITHDRAWAL = "LATE_WITHDRAWAL"
    INTERNATIONAL_DUTY = "INTERNATIONAL_DUTY"
    GENERAL_TEAM_NEWS = "GENERAL_TEAM_NEWS"


class EvidenceLevel(StrEnum):
    OFFICIAL_CONFIRMED = "OFFICIAL_CONFIRMED"
    DIRECT_COACH_QUOTE = "DIRECT_COACH_QUOTE"
    OFFICIAL_MATCH_PAGE = "OFFICIAL_MATCH_PAGE"
    OFFICIAL_MEDICAL_REPORT = "OFFICIAL_MEDICAL_REPORT"
    OFFICIAL_DISCIPLINARY_NOTICE = "OFFICIAL_DISCIPLINARY_NOTICE"
    INFERRED = "INFERRED"
    UNVERIFIED = "UNVERIFIED"


class SourcePolicy(StrEnum):
    OFFICIAL_PUBLIC_API = "OFFICIAL_PUBLIC_API"
    EXISTING_LICENSED_API = "EXISTING_LICENSED_API"
    PUBLIC_WEB_AUTOMATION_ALLOWED = "PUBLIC_WEB_AUTOMATION_ALLOWED"
    MANUAL_OFFICIAL_EVIDENCE = "MANUAL_OFFICIAL_EVIDENCE"
    RESEARCH_ONLY = "RESEARCH_ONLY"
    REJECTED = "REJECTED"


class SourceStatus(StrEnum):
    APPROVED = "APPROVED"
    RESEARCH_ONLY = "RESEARCH_ONLY"
    LICENSE_UNCLEAR = "LICENSE_UNCLEAR"
    REJECTED = "REJECTED"


class SourceType(StrEnum):
    LEAGUE_SITE = "LEAGUE_SITE"
    CLUB_SITE = "CLUB_SITE"
    PROVIDER_API = "PROVIDER_API"
    MANUAL = "MANUAL"


def utc_datetime(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def canonical_json(payload: Mapping[str, object]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


@dataclass(frozen=True, slots=True)
class SourceDefinition:
    name: str
    base_url: str
    source_type: SourceType
    leagues: tuple[str, ...]
    clubs: tuple[str, ...]
    data_types: frozenset[DataType]
    terms_url: str | None
    robots_url: str | None
    automation_allowed: bool | None
    storage_allowed: bool | None
    login_required: bool
    page_structure: str
    update_frequency: str
    parser_difficulty: str
    status: SourceStatus
    source_policy: SourcePolicy
    notes: str

    def __post_init__(self) -> None:
        if not self.name.strip() or not self.base_url.startswith("https://"):
            raise ValueError("source name and HTTPS base URL are required")
        if (
            self.status is SourceStatus.APPROVED
            and self.source_type is not SourceType.MANUAL
            and (self.automation_allowed is not True or self.storage_allowed is not True)
        ):
            raise ValueError("approved sources require explicit automation and storage permission")
        if self.source_type is SourceType.MANUAL and self.storage_allowed is not True:
            raise ValueError("manual evidence must be explicitly permitted for storage")


@dataclass(frozen=True, slots=True)
class RawPayload:
    source_name: str
    source_url: str
    captured_at: datetime
    content: bytes
    media_type: str
    source_policy: SourcePolicy
    response_headers: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.source_name.strip() or not self.source_url.startswith("https://"):
            raise ValueError("source name and HTTPS source URL are required")
        if not self.content:
            raise ValueError("raw payload cannot be empty")
        object.__setattr__(
            self,
            "captured_at",
            utc_datetime(self.captured_at, field_name="captured_at"),
        )
        object.__setattr__(self, "response_headers", MappingProxyType(dict(self.response_headers)))

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.content).hexdigest()


@dataclass(frozen=True, slots=True)
class Observation:
    team_id: str
    player_id: str | None
    fixture_id: str | None
    source_name: str
    source_url: str
    published_at: datetime | None
    captured_at: datetime
    original_title: str | None
    original_text: str | None
    original_quote: str | None
    language: str
    parser_version: str
    raw_payload_hash: str
    data_type: DataType
    evidence_level: EvidenceLevel
    source_policy: SourcePolicy
    capture_window: CaptureWindow
    normalized: Mapping[str, object]
    provider_team_id: str | None = None
    provider_player_id: str | None = None
    provider_fixture_id: str | None = None

    def __post_init__(self) -> None:
        required = (
            self.team_id,
            self.source_name,
            self.source_url,
            self.language,
            self.parser_version,
            self.raw_payload_hash,
        )
        if any(not item.strip() for item in required):
            raise ValueError("observation identity fields cannot be empty")
        if not self.source_url.startswith("https://"):
            raise ValueError("source_url must use HTTPS")
        if len(self.raw_payload_hash) != 64 or any(
            char not in "0123456789abcdef" for char in self.raw_payload_hash.casefold()
        ):
            raise ValueError("raw_payload_hash must be a SHA-256 hexadecimal digest")
        if not any((self.original_title, self.original_text, self.original_quote, self.normalized)):
            raise ValueError("an observation must preserve evidence or normalized data")
        object.__setattr__(
            self,
            "captured_at",
            utc_datetime(self.captured_at, field_name="captured_at"),
        )
        if self.published_at is not None:
            object.__setattr__(
                self,
                "published_at",
                utc_datetime(self.published_at, field_name="published_at"),
            )
        object.__setattr__(self, "normalized", MappingProxyType(dict(self.normalized)))

    @property
    def observation_id(self) -> str:
        identity: dict[str, object] = {
            "team_id": self.team_id,
            "player_id": self.player_id,
            "fixture_id": self.fixture_id,
            "source_name": self.source_name,
            "source_url": self.source_url,
            "published_at": self.published_at.isoformat() if self.published_at else None,
            "raw_payload_hash": self.raw_payload_hash,
            "parser_version": self.parser_version,
            "data_type": self.data_type,
            "capture_window": self.capture_window,
            "normalized": self.normalized,
        }
        return hashlib.sha256(canonical_json(identity)).hexdigest()

    def to_dict(self) -> dict[str, object]:
        return {
            "observation_id": self.observation_id,
            "team_id": self.team_id,
            "player_id": self.player_id,
            "fixture_id": self.fixture_id,
            "source_name": self.source_name,
            "source_url": self.source_url,
            "published_at": self.published_at.isoformat() if self.published_at else None,
            "captured_at": self.captured_at.isoformat(),
            "original_title": self.original_title,
            "original_text": self.original_text,
            "original_quote": self.original_quote,
            "language": self.language,
            "parser_version": self.parser_version,
            "raw_payload_hash": self.raw_payload_hash,
            "data_type": self.data_type,
            "evidence_level": self.evidence_level,
            "source_policy": self.source_policy,
            "capture_window": self.capture_window,
            "normalized": dict(self.normalized),
            "provider_team_id": self.provider_team_id,
            "provider_player_id": self.provider_player_id,
            "provider_fixture_id": self.provider_fixture_id,
        }
