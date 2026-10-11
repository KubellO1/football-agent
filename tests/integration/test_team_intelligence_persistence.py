from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.repositories.sqlalchemy.team_intelligence_repository import (
    SourcePolicyViolation,
    SqlAlchemyTeamIntelligenceRepository,
)
from app.team_intelligence.conflicts import resolve_conflicts
from app.team_intelligence.contracts import (
    CaptureWindow,
    DataType,
    EvidenceLevel,
    Observation,
    RawPayload,
    SourceDefinition,
    SourcePolicy,
)
from app.team_intelligence.persistence import CaptureWrite, SourceRegistration
from app.team_intelligence.sources import load_source_registry

if TYPE_CHECKING:
    from uuid import UUID

    from app.models.entities.fixture import Fixture

NOW = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)


def _api_source() -> SourceDefinition:
    return next(source for source in load_source_registry() if source.name == "API-Football")


def _payload(*, captured_at: datetime = NOW, content: bytes = b'{"fixture":1}') -> RawPayload:
    return RawPayload(
        source_name="API-Football",
        source_url="https://v3.football.api-sports.io/injuries?fixture=1",
        captured_at=captured_at,
        content=content,
        media_type="application/json",
        source_policy=SourcePolicy.EXISTING_LICENSED_API,
        response_headers={"etag": '"fixture-1"'},
    )


def _observation(
    fixture: Fixture,
    team_id: UUID,
    payload: RawPayload,
    *,
    status: str = "out",
) -> Observation:
    return Observation(
        team_id=str(team_id),
        player_id=None,
        fixture_id=str(fixture.id),
        source_name="api-football",
        source_url=payload.source_url,
        published_at=payload.captured_at - timedelta(minutes=5),
        captured_at=payload.captured_at,
        original_title=None,
        original_text=f"Fixture availability: {status}",
        original_quote=None,
        language="en",
        parser_version="integration-v1",
        raw_payload_hash=payload.sha256,
        data_type=DataType.INJURY,
        evidence_level=EvidenceLevel.UNVERIFIED,
        source_policy=SourcePolicy.EXISTING_LICENSED_API,
        capture_window=CaptureWindow.T60,
        normalized={"status": status},
        provider_team_id="100",
        provider_fixture_id="1",
    )


async def _register_api_source(repo: SqlAlchemyTeamIntelligenceRepository) -> None:
    await repo.register_source(
        SourceRegistration(
            definition=_api_source(),
            policy_reviewed_at=NOW,
            parser_version="integration-v1",
            enabled=True,
        )
    )


@pytest.mark.integration
async def test_semantic_replay_excludes_capture_time_and_preserves_receipts(
    db_session: AsyncSession,
    persisted_fixture: Fixture,
    reference_ids: tuple[UUID, UUID, UUID],
) -> None:
    repo = SqlAlchemyTeamIntelligenceRepository(db_session)
    await _register_api_source(repo)
    first_payload = _payload()
    first_observation = _observation(persisted_fixture, reference_ids[1], first_payload)

    first = await repo.append_capture(
        CaptureWrite(first_payload, "file:///raw/fixture-1.json", 200, automated=True),
        (first_observation,),
    )
    exact_replay = await repo.append_capture(
        CaptureWrite(first_payload, "file:///raw/fixture-1.json", 200, automated=True),
        (first_observation,),
    )

    later_payload = _payload(captured_at=NOW + timedelta(minutes=30))
    later_observation = replace(
        first_observation,
        captured_at=later_payload.captured_at,
    )
    later_replay = await repo.append_capture(
        CaptureWrite(later_payload, "file:///raw/fixture-1.json", 200, automated=True),
        (later_observation,),
    )
    await db_session.flush()

    assert first.observations_inserted == 1
    assert exact_replay.observation_duplicates == 1
    assert exact_replay.capture_receipt_inserted is False
    assert later_replay.observations_inserted == 0
    assert later_replay.observation_duplicates == 1
    assert later_replay.capture_receipt_inserted is True
    assert len(await repo.list_by_fixture(persisted_fixture.id)) == 1


@pytest.mark.integration
async def test_source_conflicts_append_without_mutating_history(
    db_session: AsyncSession,
    persisted_fixture: Fixture,
    reference_ids: tuple[UUID, UUID, UUID],
) -> None:
    repo = SqlAlchemyTeamIntelligenceRepository(db_session)
    await _register_api_source(repo)
    missing_payload = _payload(content=b'{"status":"out"}')
    returned_payload = _payload(
        captured_at=NOW + timedelta(minutes=15),
        content=b'{"status":"returned"}',
    )
    missing = _observation(persisted_fixture, reference_ids[1], missing_payload, status="out")
    returned = _observation(
        persisted_fixture,
        reference_ids[1],
        returned_payload,
        status="returned",
    )

    await repo.append_capture(
        CaptureWrite(missing_payload, "file:///raw/out.json", 200, automated=True),
        (missing,),
    )
    await repo.append_capture(
        CaptureWrite(returned_payload, "file:///raw/returned.json", 200, automated=True),
        (returned,),
    )
    persisted = await repo.list_by_fixture(persisted_fixture.id)

    assert len(persisted) == 2
    assert resolve_conflicts(persisted)[0].conflict is True


@pytest.mark.integration
async def test_disabled_source_fails_closed_before_evidence_insert(
    db_session: AsyncSession,
    persisted_fixture: Fixture,
    reference_ids: tuple[UUID, UUID, UUID],
) -> None:
    repo = SqlAlchemyTeamIntelligenceRepository(db_session)
    source = _api_source()
    await repo.register_source(
        SourceRegistration(
            definition=source,
            policy_reviewed_at=NOW,
            parser_version="integration-v1",
            enabled=False,
        )
    )
    payload = _payload()

    with pytest.raises(SourcePolicyViolation, match="not approved and enabled"):
        await repo.append_capture(
            CaptureWrite(payload, "file:///raw/fixture-1.json", 200, automated=True),
            (_observation(persisted_fixture, reference_ids[1], payload),),
        )


@pytest.mark.integration
async def test_concurrent_semantic_replay_inserts_one_observation(
    db_session: AsyncSession,
    persisted_fixture: Fixture,
    reference_ids: tuple[UUID, UUID, UUID],
) -> None:
    repo = SqlAlchemyTeamIntelligenceRepository(db_session)
    await _register_api_source(repo)
    await db_session.commit()
    bind = db_session.bind
    assert bind is not None
    factory = async_sessionmaker(bind, class_=AsyncSession, expire_on_commit=False)
    payload = _payload()
    observation = _observation(persisted_fixture, reference_ids[1], payload)

    async def append_once() -> int:
        async with factory() as session:
            result = await SqlAlchemyTeamIntelligenceRepository(session).append_capture(
                CaptureWrite(payload, "file:///raw/fixture-1.json", 200, automated=True),
                (observation,),
            )
            await session.commit()
            return result.observations_inserted

    assert sorted(await asyncio.gather(append_once(), append_once())) == [0, 1]
