"""生产 observation 数据层使用的不可变持久化契约。"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from app.intelligence.contracts import (
    Observation,
    RawPayload,
    SourcePolicy,
    canonical_json,
    utc_datetime,
)

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import datetime
    from decimal import Decimal


class MappingStatus(StrEnum):
    MATCHED = "MATCHED"
    UNMAPPED = "UNMAPPED"
    AMBIGUOUS = "AMBIGUOUS"
    WEATHER_UNAVAILABLE = "WEATHER_UNAVAILABLE"
    REJECTED = "REJECTED"


class MappingMethod(StrEnum):
    EXACT_EXTERNAL_ID = "EXACT_EXTERNAL_ID"
    EXACT_CANONICAL_NAME = "EXACT_CANONICAL_NAME"
    MANUAL_REVIEWED = "MANUAL_REVIEWED"
    NONE = "NONE"


def _stable_key(payload: Mapping[str, object]) -> str:
    return hashlib.sha256(canonical_json(payload)).hexdigest()


@dataclass(frozen=True, slots=True)
class SourceDefinition:
    code: str
    display_name: str
    base_url: str
    source_policy: SourcePolicy
    automation_allowed: bool
    license_reference: str
    id: UUID = field(default_factory=uuid4)

    def __post_init__(self) -> None:
        if not self.code.strip() or not self.display_name.strip():
            raise ValueError("source code and display_name are required")
        if not self.base_url.startswith("https://"):
            raise ValueError("source base_url must use HTTPS")
        if not self.license_reference.startswith("https://"):
            raise ValueError("license_reference must use HTTPS")


@dataclass(frozen=True, slots=True)
class RawPayloadRecord:
    source_id: UUID
    raw: RawPayload
    storage_uri: str
    etag: str | None = None
    last_modified_at: datetime | None = None
    id: UUID = field(default_factory=uuid4)

    def __post_init__(self) -> None:
        if not self.storage_uri.strip():
            raise ValueError("storage_uri is required")

    @property
    def natural_key(self) -> str:
        return _stable_key(
            {
                "source_id": str(self.source_id),
                "source_url": self.raw.source_url,
                "captured_at": self.raw.captured_at.isoformat(),
                "raw_payload_hash": self.raw.sha256,
            }
        )


@dataclass(frozen=True, slots=True)
class ObservationRecord:
    source_id: UUID
    fixture_id: UUID
    observation: Observation
    raw_payload_id: UUID | None
    id: UUID = field(default_factory=uuid4)


@dataclass(frozen=True, slots=True)
class TeamIdentityMapping:
    source_id: UUID
    source_team_name: str
    status: MappingStatus
    method: MappingMethod
    captured_at: datetime
    team_id: UUID | None = None
    source_team_id: str | None = None
    competition_id: UUID | None = None
    reason_code: str | None = None
    raw_payload_id: UUID | None = None
    id: UUID = field(default_factory=uuid4)

    def __post_init__(self) -> None:
        if not self.source_team_name.strip():
            raise ValueError("source_team_name is required")
        if (self.status is MappingStatus.MATCHED) != (self.team_id is not None):
            raise ValueError("MATCHED team mapping requires exactly one canonical team")
        object.__setattr__(self, "captured_at", utc_datetime(self.captured_at, field="captured_at"))

    @property
    def mapping_key(self) -> str:
        return _stable_key(
            {
                "source_id": str(self.source_id),
                "source_team_id": self.source_team_id,
                "source_team_name": self.source_team_name,
                "competition_id": str(self.competition_id) if self.competition_id else None,
                "team_id": str(self.team_id) if self.team_id else None,
                "status": self.status,
                "method": self.method,
            }
        )


@dataclass(frozen=True, slots=True)
class FixtureIdentityMapping:
    source_id: UUID
    source_fixture_id: str
    source_competition: str
    source_home_team: str
    source_away_team: str
    source_kickoff: datetime
    status: MappingStatus
    method: MappingMethod
    captured_at: datetime
    fixture_id: UUID | None = None
    reason_code: str | None = None
    raw_payload_id: UUID | None = None
    id: UUID = field(default_factory=uuid4)

    def __post_init__(self) -> None:
        required = (
            self.source_fixture_id,
            self.source_competition,
            self.source_home_team,
            self.source_away_team,
        )
        if any(not value.strip() for value in required):
            raise ValueError("source fixture identity fields are required")
        if (self.status is MappingStatus.MATCHED) != (self.fixture_id is not None):
            raise ValueError("MATCHED fixture mapping requires exactly one canonical fixture")
        object.__setattr__(
            self,
            "source_kickoff",
            utc_datetime(self.source_kickoff, field="source_kickoff"),
        )
        object.__setattr__(self, "captured_at", utc_datetime(self.captured_at, field="captured_at"))

    @property
    def mapping_key(self) -> str:
        return _stable_key(
            {
                "source_id": str(self.source_id),
                "source_fixture_id": self.source_fixture_id,
                "source_kickoff": self.source_kickoff.isoformat(),
                "fixture_id": str(self.fixture_id) if self.fixture_id else None,
                "status": self.status,
                "method": self.method,
            }
        )


@dataclass(frozen=True, slots=True)
class VenueMapping:
    fixture_id: UUID
    source_id: UUID
    source_venue_name: str
    status: MappingStatus
    method: MappingMethod
    captured_at: datetime
    latitude: Decimal | None = None
    longitude: Decimal | None = None
    coordinate_source: str | None = None
    coordinate_source_url: str | None = None
    reason_code: str | None = None
    raw_payload_id: UUID | None = None
    id: UUID = field(default_factory=uuid4)

    def __post_init__(self) -> None:
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("latitude and longitude must be provided together")
        if self.status is MappingStatus.MATCHED and (
            self.latitude is None or not self.coordinate_source_url
        ):
            raise ValueError("matched venue coordinates require provenance")
        object.__setattr__(self, "captured_at", utc_datetime(self.captured_at, field="captured_at"))

    @property
    def mapping_key(self) -> str:
        return _stable_key(
            {
                "fixture_id": str(self.fixture_id),
                "source_id": str(self.source_id),
                "source_venue_name": self.source_venue_name,
                "latitude": str(self.latitude) if self.latitude is not None else None,
                "longitude": str(self.longitude) if self.longitude is not None else None,
                "coordinate_source_url": self.coordinate_source_url,
                "status": self.status,
                "method": self.method,
            }
        )
