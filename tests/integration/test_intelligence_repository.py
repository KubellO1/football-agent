from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.intelligence.contracts import (
    CaptureWindow,
    DataType,
    Observation,
    RawPayload,
    SemanticStatus,
    SourcePolicy,
)
from app.intelligence.persistence import (
    FixtureIdentityMapping,
    MappingMethod,
    MappingStatus,
    ObservationRecord,
    RawPayloadRecord,
    SourceDefinition,
    TeamIdentityMapping,
    VenueMapping,
)
from app.repositories.sqlalchemy.intelligence_repository import (
    SqlAlchemyIntelligenceRepository,
)
from app.repositories.sqlalchemy.models import (
    FixtureIdentityMappingORM,
    IntelligenceObservationORM,
    IntelligenceRawPayloadORM,
    IntelligenceSourceORM,
    TeamIdentityMappingORM,
    VenueMappingORM,
)

pytestmark = pytest.mark.integration

NOW = datetime(2026, 9, 14, 12, tzinfo=UTC)


@pytest.mark.asyncio
async def test_repository_replay_is_idempotent(db_session, persisted_fixture) -> None:
    repository = SqlAlchemyIntelligenceRepository(db_session)
    source = SourceDefinition(
        code="openfootball",
        display_name="OpenFootball",
        base_url="https://github.com/openfootball/football.json",
        source_policy=SourcePolicy.OPEN_DATA,
        automation_allowed=True,
        license_reference="https://creativecommons.org/publicdomain/zero/1.0/",
    )
    raw = RawPayload(
        source=source.code,
        source_url="https://example.test/2026-27/fr.1.json",
        captured_at=NOW,
        content=b'{"round":"1"}',
        media_type="application/json",
        source_policy=SourcePolicy.OPEN_DATA,
    )
    raw_record = RawPayloadRecord(
        source_id=source.id,
        raw=raw,
        storage_uri="sha256://openfootball/payload.json",
    )
    observation = Observation(
        fixture_id=str(persisted_fixture.id),
        source=source.code,
        source_url=raw.source_url,
        source_record_id="fr.1-2026-1",
        observed_at=NOW,
        published_at=None,
        captured_at=NOW,
        raw_payload_hash=raw.sha256,
        parser_version="openfootball-v1",
        schema_version="1",
        data_type=DataType.FIXTURE,
        source_policy=SourcePolicy.OPEN_DATA,
        semantic_status=SemanticStatus.VERIFIED,
        capture_window=CaptureWindow.DAILY,
        payload={"home": "Toulouse", "away": "Lille"},
    )
    observation_record = ObservationRecord(
        source_id=source.id,
        fixture_id=persisted_fixture.id,
        observation=observation,
        raw_payload_id=raw_record.id,
    )
    team_mapping = TeamIdentityMapping(
        source_id=source.id,
        source_team_id="toulouse",
        source_team_name="Toulouse",
        status=MappingStatus.MATCHED,
        method=MappingMethod.EXACT_EXTERNAL_ID,
        captured_at=NOW,
        team_id=persisted_fixture.home_team_id,
        competition_id=persisted_fixture.competition_id,
        raw_payload_id=raw_record.id,
    )
    fixture_mapping = FixtureIdentityMapping(
        source_id=source.id,
        source_fixture_id="fr.1-2026-1",
        source_competition="Ligue 1",
        source_home_team="Toulouse",
        source_away_team="Lille",
        source_kickoff=persisted_fixture.kickoff,
        status=MappingStatus.MATCHED,
        method=MappingMethod.EXACT_EXTERNAL_ID,
        captured_at=NOW,
        fixture_id=persisted_fixture.id,
        raw_payload_id=raw_record.id,
    )
    venue_mapping = VenueMapping(
        fixture_id=persisted_fixture.id,
        source_id=source.id,
        source_venue_name="Stadium de Toulouse",
        status=MappingStatus.MATCHED,
        method=MappingMethod.MANUAL_REVIEWED,
        captured_at=NOW,
        latitude=Decimal("43.583300"),
        longitude=Decimal("1.434000"),
        coordinate_source="official-club",
        coordinate_source_url="https://example.test/venues/toulouse",
        raw_payload_id=raw_record.id,
    )

    assert await repository.add_source(source) is True
    assert await repository.add_source(source) is False
    assert await repository.add_raw_payload(raw_record) is True
    assert await repository.add_raw_payload(raw_record) is False
    assert await repository.add_observation(observation_record) is True
    assert await repository.add_observation(observation_record) is False
    assert await repository.add_team_mapping(team_mapping) is True
    assert await repository.add_team_mapping(team_mapping) is False
    assert await repository.add_fixture_mapping(fixture_mapping) is True
    assert await repository.add_fixture_mapping(fixture_mapping) is False
    assert await repository.add_venue_mapping(venue_mapping) is True
    assert await repository.add_venue_mapping(venue_mapping) is False
    await db_session.commit()

    source_count = await db_session.scalar(select(func.count(IntelligenceSourceORM.id)))
    raw_count = await db_session.scalar(select(func.count(IntelligenceRawPayloadORM.id)))
    observation_count = await db_session.scalar(select(func.count(IntelligenceObservationORM.id)))
    team_mapping_count = await db_session.scalar(select(func.count(TeamIdentityMappingORM.id)))
    fixture_mapping_count = await db_session.scalar(
        select(func.count(FixtureIdentityMappingORM.id))
    )
    venue_mapping_count = await db_session.scalar(select(func.count(VenueMappingORM.id)))
    assert (source_count, raw_count, observation_count) == (1, 1, 1)
    assert (team_mapping_count, fixture_mapping_count, venue_mapping_count) == (1, 1, 1)


@pytest.mark.asyncio
async def test_observation_rejects_fixture_identity_mismatch(db_session, persisted_fixture) -> None:
    source = SourceDefinition(
        code="manual",
        display_name="Manual evidence",
        base_url="https://example.test/manual",
        source_policy=SourcePolicy.MANUAL_USER_SUPPLIED,
        automation_allowed=False,
        license_reference="https://example.test/manual-policy",
    )
    observation = Observation(
        fixture_id=str(persisted_fixture.id),
        source=source.code,
        source_url="https://example.test/manual/1",
        published_at=None,
        captured_at=NOW,
        raw_payload_hash="a" * 64,
        parser_version="manual-v1",
        data_type=DataType.TEAM_NEWS,
        source_policy=SourcePolicy.MANUAL_USER_SUPPLIED,
        capture_window=CaptureWindow.T24H,
        payload={"note": "confirmed by user"},
    )
    record = ObservationRecord(
        source_id=source.id,
        fixture_id=uuid4(),
        observation=observation,
        raw_payload_id=None,
    )

    with pytest.raises(ValueError, match="fixture_id"):
        await SqlAlchemyIntelligenceRepository(db_session).add_observation(record)
