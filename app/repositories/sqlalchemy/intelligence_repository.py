"""零成本情报 observation 的幂等、仅追加 SQLAlchemy 仓储。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.repositories.sqlalchemy.models import (
    FixtureIdentityMappingORM,
    IntelligenceObservationORM,
    IntelligenceRawPayloadORM,
    IntelligenceSourceORM,
    TeamIdentityMappingORM,
    VenueMappingORM,
)

if TYPE_CHECKING:
    from sqlalchemy import Table
    from sqlalchemy.ext.asyncio import AsyncSession
    from sqlalchemy.sql.selectable import FromClause

    from app.intelligence.persistence import (
        FixtureIdentityMapping,
        ObservationRecord,
        RawPayloadRecord,
        SourceDefinition,
        TeamIdentityMapping,
        VenueMapping,
    )


class SqlAlchemyIntelligenceRepository:
    """用稳定自然键实现重放安全；仓储不提供 update/delete。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _insert_once(
        self,
        table: FromClause,
        values: dict[str, Any],
        *,
        key_column: str,
    ) -> bool:
        statement = (
            pg_insert(cast("Table", table))
            .values(**values)
            .on_conflict_do_nothing(index_elements=[key_column])
            .returning(table.c.id)
        )
        inserted_id = (await self._session.execute(statement)).scalar_one_or_none()
        return inserted_id is not None

    async def add_source(self, source: SourceDefinition) -> bool:
        return await self._insert_once(
            IntelligenceSourceORM.__table__,
            {
                "id": source.id,
                "code": source.code,
                "display_name": source.display_name,
                "base_url": source.base_url,
                "source_policy": source.source_policy.value,
                "automation_allowed": source.automation_allowed,
                "license_reference": source.license_reference,
            },
            key_column="code",
        )

    async def add_raw_payload(self, record: RawPayloadRecord) -> bool:
        return await self._insert_once(
            IntelligenceRawPayloadORM.__table__,
            {
                "id": record.id,
                "natural_key": record.natural_key,
                "source_id": record.source_id,
                "source_url": record.raw.source_url,
                "captured_at": record.raw.captured_at,
                "raw_payload_hash": record.raw.sha256,
                "media_type": record.raw.media_type,
                "byte_length": len(record.raw.content),
                "storage_uri": record.storage_uri,
                "etag": record.etag,
                "last_modified_at": record.last_modified_at,
                "source_policy": record.raw.source_policy.value,
            },
            key_column="natural_key",
        )

    async def add_observation(self, record: ObservationRecord) -> bool:
        observation = record.observation
        if observation.fixture_id != str(record.fixture_id):
            raise ValueError("observation fixture_id must match persistence record")
        return await self._insert_once(
            IntelligenceObservationORM.__table__,
            {
                "id": record.id,
                "observation_key": observation.observation_id,
                "fixture_id": record.fixture_id,
                "source_id": record.source_id,
                "raw_payload_id": record.raw_payload_id,
                "source_url": observation.source_url,
                "source_record_id": observation.source_record_id,
                "data_type": observation.data_type.value,
                "observed_at": observation.observed_at,
                "published_at": observation.published_at,
                "captured_at": observation.captured_at,
                "raw_payload_hash": observation.raw_payload_hash,
                "parser_version": observation.parser_version,
                "schema_version": observation.schema_version,
                "source_policy": observation.source_policy.value,
                "semantic_status": observation.semantic_status.value,
                "capture_window": observation.capture_window.value,
                "payload": dict(observation.payload),
            },
            key_column="observation_key",
        )

    async def add_team_mapping(self, mapping: TeamIdentityMapping) -> bool:
        return await self._insert_once(
            TeamIdentityMappingORM.__table__,
            {
                "id": mapping.id,
                "mapping_key": mapping.mapping_key,
                "source_id": mapping.source_id,
                "source_team_id": mapping.source_team_id,
                "source_team_name": mapping.source_team_name,
                "competition_id": mapping.competition_id,
                "team_id": mapping.team_id,
                "status": mapping.status.value,
                "method": mapping.method.value,
                "reason_code": mapping.reason_code,
                "captured_at": mapping.captured_at,
                "raw_payload_id": mapping.raw_payload_id,
            },
            key_column="mapping_key",
        )

    async def add_fixture_mapping(self, mapping: FixtureIdentityMapping) -> bool:
        return await self._insert_once(
            FixtureIdentityMappingORM.__table__,
            {
                "id": mapping.id,
                "mapping_key": mapping.mapping_key,
                "source_id": mapping.source_id,
                "source_fixture_id": mapping.source_fixture_id,
                "source_competition": mapping.source_competition,
                "source_home_team": mapping.source_home_team,
                "source_away_team": mapping.source_away_team,
                "source_kickoff": mapping.source_kickoff,
                "fixture_id": mapping.fixture_id,
                "status": mapping.status.value,
                "method": mapping.method.value,
                "reason_code": mapping.reason_code,
                "captured_at": mapping.captured_at,
                "raw_payload_id": mapping.raw_payload_id,
            },
            key_column="mapping_key",
        )

    async def add_venue_mapping(self, mapping: VenueMapping) -> bool:
        return await self._insert_once(
            VenueMappingORM.__table__,
            {
                "id": mapping.id,
                "mapping_key": mapping.mapping_key,
                "fixture_id": mapping.fixture_id,
                "source_id": mapping.source_id,
                "source_venue_name": mapping.source_venue_name,
                "latitude": mapping.latitude,
                "longitude": mapping.longitude,
                "coordinate_source": mapping.coordinate_source,
                "coordinate_source_url": mapping.coordinate_source_url,
                "status": mapping.status.value,
                "method": mapping.method.value,
                "reason_code": mapping.reason_code,
                "captured_at": mapping.captured_at,
                "raw_payload_id": mapping.raw_payload_id,
            },
            key_column="mapping_key",
        )
