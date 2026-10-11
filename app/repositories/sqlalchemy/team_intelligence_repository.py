"""Append-only PostgreSQL repository for Team Intelligence evidence."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID, uuid5

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.repositories.sqlalchemy.models import (
    TeamIntelligenceCaptureReceiptORM,
    TeamIntelligenceObservationORM,
    TeamIntelligenceRawPayloadORM,
    TeamIntelligenceSourceORM,
)
from app.team_intelligence.contracts import (
    CaptureWindow,
    DataType,
    EvidenceLevel,
    Observation,
    SourcePolicy,
    SourceStatus,
)
from app.team_intelligence.persistence import (
    CaptureWrite,
    PersistenceResult,
    SourceRegistration,
)

if TYPE_CHECKING:
    from collections.abc import Iterable
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncSession

_IDENTITY_NAMESPACE = UUID("6ba1e9f8-2953-5a1c-a936-b6a319f7d6b4")


class SourcePolicyViolation(ValueError):
    """Raised before persistence when reviewed source policy is not satisfied."""


class PersistenceIdentityCollision(RuntimeError):
    """Raised if deterministic identity resolves to incompatible persisted data."""


class SqlAlchemyTeamIntelligenceRepository:
    """No update/delete API: evidence writes are append-only and transaction-scoped."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def register_source(self, registration: SourceRegistration) -> bool:
        definition = registration.definition
        values = {
            "source_name": definition.source_key,
            "base_url": definition.base_url,
            "source_type": definition.source_type.value,
            "status": definition.status.value,
            "automation_allowed": definition.automation_allowed,
            "storage_allowed": definition.storage_allowed,
            "terms_url": definition.terms_url,
            "robots_url": definition.robots_url,
            "source_policy": definition.source_policy.value,
            "policy_reviewed_at": registration.policy_reviewed_at,
            "policy_hash": definition.policy_hash,
            "parser_version": registration.parser_version,
            "enabled": registration.enabled,
        }
        statement = (
            pg_insert(TeamIntelligenceSourceORM)
            .values(**values)
            .on_conflict_do_nothing(index_elements=[TeamIntelligenceSourceORM.source_name])
            .returning(TeamIntelligenceSourceORM.source_name)
        )
        inserted = (await self._session.execute(statement)).scalar_one_or_none()
        if inserted is not None:
            return True

        existing = await self._session.get(TeamIntelligenceSourceORM, definition.source_key)
        if existing is None or any(
            getattr(existing, field) != value for field, value in values.items()
        ):
            raise PersistenceIdentityCollision(
                f"source registration already exists with different policy: {definition.source_key}"
            )
        return False

    async def append_capture(
        self,
        capture: CaptureWrite,
        observations: Iterable[Observation],
    ) -> PersistenceResult:
        values = tuple(observations)
        source = await self._require_source(capture)
        self._validate_observations(capture, values)

        raw_id, raw_inserted = await self._insert_raw_payload(capture)
        receipt_id, receipt_inserted = await self._insert_capture_receipt(capture, raw_id)

        inserted = 0
        duplicates = 0
        for observation in values:
            if observation.source_policy.value != source.source_policy:
                raise SourcePolicyViolation("observation source policy differs from registry")
            if await self._insert_observation(
                observation,
                raw_payload_id=raw_id,
                capture_receipt_id=receipt_id,
            ):
                inserted += 1
            else:
                duplicates += 1
        return PersistenceResult(
            raw_payload_inserted=raw_inserted,
            capture_receipt_inserted=receipt_inserted,
            observations_inserted=inserted,
            observation_duplicates=duplicates,
        )

    async def list_by_fixture(
        self,
        fixture_id: UUID,
        *,
        as_of: datetime | None = None,
    ) -> list[Observation]:
        if as_of is not None and (as_of.tzinfo is None or as_of.utcoffset() is None):
            raise ValueError("as_of must be timezone-aware")
        statement = (
            select(
                TeamIntelligenceObservationORM,
                TeamIntelligenceRawPayloadORM.content_sha256,
            )
            .join(
                TeamIntelligenceRawPayloadORM,
                TeamIntelligenceRawPayloadORM.id == TeamIntelligenceObservationORM.raw_payload_id,
            )
            .where(TeamIntelligenceObservationORM.fixture_id == fixture_id)
            .order_by(
                TeamIntelligenceObservationORM.captured_at,
                TeamIntelligenceObservationORM.id,
            )
        )
        if as_of is not None:
            statement = statement.where(TeamIntelligenceObservationORM.captured_at <= as_of)
        rows = (await self._session.execute(statement)).all()
        return [self._to_domain(row, raw_hash) for row, raw_hash in rows]

    async def _require_source(self, capture: CaptureWrite) -> TeamIntelligenceSourceORM:
        row = await self._session.get(
            TeamIntelligenceSourceORM,
            capture.payload.source_name,
        )
        if row is None:
            raise SourcePolicyViolation("source is not registered")
        if row.status != SourceStatus.APPROVED.value or row.enabled is not True:
            raise SourcePolicyViolation("source is not approved and enabled")
        if row.storage_allowed is not True:
            raise SourcePolicyViolation("source policy does not permit storage")
        if capture.automated and row.automation_allowed is not True:
            raise SourcePolicyViolation("source policy does not permit automation")
        if row.source_policy != capture.payload.source_policy.value:
            raise SourcePolicyViolation("payload source policy differs from registry")
        return row

    @staticmethod
    def _validate_observations(
        capture: CaptureWrite,
        observations: tuple[Observation, ...],
    ) -> None:
        payload = capture.payload
        for observation in observations:
            if observation.source_name != payload.source_name:
                raise SourcePolicyViolation("observation source differs from raw payload")
            if observation.source_url != payload.source_url:
                raise SourcePolicyViolation("observation URL differs from capture receipt")
            if observation.raw_payload_hash != payload.sha256:
                raise SourcePolicyViolation("observation hash differs from raw payload")
            if observation.source_policy is not payload.source_policy:
                raise SourcePolicyViolation("observation policy differs from raw payload")
            if observation.captured_at != payload.captured_at:
                raise SourcePolicyViolation("observation capture time differs from receipt")

    async def _insert_raw_payload(self, capture: CaptureWrite) -> tuple[UUID, bool]:
        payload = capture.payload
        raw_id = _identity_uuid("raw", payload.source_name, payload.sha256)
        statement = (
            pg_insert(TeamIntelligenceRawPayloadORM)
            .values(
                id=raw_id,
                source_name=payload.source_name,
                source_url=payload.source_url,
                content_sha256=payload.sha256,
                media_type=payload.media_type,
                object_uri=capture.object_uri,
                source_policy=payload.source_policy.value,
                first_captured_at=payload.captured_at,
            )
            .on_conflict_do_nothing(constraint="uq_ti_raw_source_content")
            .returning(TeamIntelligenceRawPayloadORM.id)
        )
        inserted = (await self._session.execute(statement)).scalar_one_or_none()
        if inserted is not None:
            return inserted, True
        existing = (
            await self._session.execute(
                select(TeamIntelligenceRawPayloadORM).where(
                    TeamIntelligenceRawPayloadORM.source_name == payload.source_name,
                    TeamIntelligenceRawPayloadORM.content_sha256 == payload.sha256,
                )
            )
        ).scalar_one()
        if existing.id != raw_id or existing.source_policy != payload.source_policy.value:
            raise PersistenceIdentityCollision("raw payload identity collision")
        return existing.id, False

    async def _insert_capture_receipt(
        self,
        capture: CaptureWrite,
        raw_payload_id: UUID,
    ) -> tuple[UUID, bool]:
        payload = capture.payload
        receipt_id = _identity_uuid(
            "receipt",
            str(raw_payload_id),
            payload.source_url,
            payload.captured_at.isoformat(),
        )
        headers = {key.casefold(): value for key, value in payload.response_headers.items()}
        statement = (
            pg_insert(TeamIntelligenceCaptureReceiptORM)
            .values(
                id=receipt_id,
                raw_payload_id=raw_payload_id,
                source_url=payload.source_url,
                captured_at=payload.captured_at,
                http_status=capture.http_status,
                etag=headers.get("etag"),
                last_modified=headers.get("last-modified"),
            )
            .on_conflict_do_nothing(constraint="uq_ti_capture_natural")
            .returning(TeamIntelligenceCaptureReceiptORM.id)
        )
        inserted = (await self._session.execute(statement)).scalar_one_or_none()
        if inserted is not None:
            return inserted, True
        existing = (
            await self._session.execute(
                select(TeamIntelligenceCaptureReceiptORM).where(
                    TeamIntelligenceCaptureReceiptORM.raw_payload_id == raw_payload_id,
                    TeamIntelligenceCaptureReceiptORM.source_url == payload.source_url,
                    TeamIntelligenceCaptureReceiptORM.captured_at == payload.captured_at,
                )
            )
        ).scalar_one()
        if existing.id != receipt_id:
            raise PersistenceIdentityCollision("capture receipt identity collision")
        return existing.id, False

    async def _insert_observation(
        self,
        observation: Observation,
        *,
        raw_payload_id: UUID,
        capture_receipt_id: UUID,
    ) -> bool:
        semantic_key = observation.observation_id
        observation_id = _identity_uuid("observation", semantic_key)
        statement = (
            pg_insert(TeamIntelligenceObservationORM)
            .values(
                id=observation_id,
                semantic_key=semantic_key,
                team_id=_uuid(observation.team_id, field="team_id"),
                player_id=_optional_uuid(observation.player_id, field="player_id"),
                fixture_id=_optional_uuid(observation.fixture_id, field="fixture_id"),
                raw_payload_id=raw_payload_id,
                capture_receipt_id=capture_receipt_id,
                source_name=observation.source_name,
                source_url=observation.source_url,
                published_at=observation.published_at,
                captured_at=observation.captured_at,
                original_title=observation.original_title,
                original_text=observation.original_text,
                original_quote=observation.original_quote,
                language=observation.language,
                parser_version=observation.parser_version,
                data_type=observation.data_type.value,
                evidence_level=observation.evidence_level.value,
                source_policy=observation.source_policy.value,
                capture_window=observation.capture_window.value,
                provider_team_id=observation.provider_team_id,
                provider_player_id=observation.provider_player_id,
                provider_fixture_id=observation.provider_fixture_id,
                normalized=dict(observation.normalized),
            )
            .on_conflict_do_nothing(constraint="uq_ti_observation_semantic_key")
            .returning(TeamIntelligenceObservationORM.id)
        )
        inserted = (await self._session.execute(statement)).scalar_one_or_none()
        if inserted is not None:
            return True
        existing = (
            await self._session.execute(
                select(TeamIntelligenceObservationORM.id).where(
                    TeamIntelligenceObservationORM.semantic_key == semantic_key,
                )
            )
        ).scalar_one()
        if existing != observation_id:
            raise PersistenceIdentityCollision("observation semantic identity collision")
        return False

    @staticmethod
    def _to_domain(
        row: TeamIntelligenceObservationORM,
        raw_payload_hash: str,
    ) -> Observation:
        return Observation(
            team_id=str(row.team_id),
            player_id=str(row.player_id) if row.player_id is not None else None,
            fixture_id=str(row.fixture_id) if row.fixture_id is not None else None,
            source_name=row.source_name,
            source_url=row.source_url,
            published_at=row.published_at,
            captured_at=row.captured_at,
            original_title=row.original_title,
            original_text=row.original_text,
            original_quote=row.original_quote,
            language=row.language,
            parser_version=row.parser_version,
            raw_payload_hash=raw_payload_hash,
            data_type=DataType(row.data_type),
            evidence_level=EvidenceLevel(row.evidence_level),
            source_policy=SourcePolicy(row.source_policy),
            capture_window=CaptureWindow(row.capture_window),
            normalized=row.normalized,
            provider_team_id=row.provider_team_id,
            provider_player_id=row.provider_player_id,
            provider_fixture_id=row.provider_fixture_id,
        )


def _identity_uuid(kind: str, *parts: str) -> UUID:
    return uuid5(_IDENTITY_NAMESPACE, "\x1f".join((kind, *parts)))


def _uuid(value: str, *, field: str) -> UUID:
    try:
        return UUID(value)
    except ValueError as exc:
        raise ValueError(f"{field} must be a UUID") from exc


def _optional_uuid(value: str | None, *, field: str) -> UUID | None:
    return _uuid(value, field=field) if value is not None else None


__all__ = [
    "PersistenceIdentityCollision",
    "SourcePolicyViolation",
    "SqlAlchemyTeamIntelligenceRepository",
]
