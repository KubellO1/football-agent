"""Persistence boundary contracts for immutable team-intelligence evidence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.team_intelligence.contracts import (
    RawPayload,
    SourceDefinition,
    SourceType,
    utc_datetime,
)

if TYPE_CHECKING:
    from datetime import datetime


@dataclass(frozen=True, slots=True)
class SourceRegistration:
    definition: SourceDefinition
    policy_reviewed_at: datetime
    parser_version: str | None
    enabled: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "policy_reviewed_at",
            utc_datetime(self.policy_reviewed_at, field_name="policy_reviewed_at"),
        )
        if self.parser_version is not None and not self.parser_version.strip():
            raise ValueError("parser_version must be non-empty when supplied")
        if self.enabled and not self.definition.permits_persistence(
            automated=self.definition.source_type is not SourceType.MANUAL
        ):
            raise ValueError("source policy does not permit enabling persistence")


@dataclass(frozen=True, slots=True)
class CaptureWrite:
    payload: RawPayload
    object_uri: str
    http_status: int
    automated: bool

    def __post_init__(self) -> None:
        if not self.object_uri.strip():
            raise ValueError("object_uri cannot be empty")
        if not 100 <= self.http_status <= 599:
            raise ValueError("http_status must be between 100 and 599")


@dataclass(frozen=True, slots=True)
class PersistenceResult:
    raw_payload_inserted: bool
    capture_receipt_inserted: bool
    observations_inserted: int
    observation_duplicates: int
