"""球员可用性采集服务的 PostgreSQL 集成测试。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.core.exceptions import ExternalServiceError, NotFoundError, ValidationError
from app.models.entities.enums import PlayerPosition
from app.models.entities.fixture import Fixture
from app.models.entities.player import Player
from app.models.value_objects.availability import AvailabilityStatus
from app.models.value_objects.decision import EvidenceLevel
from app.providers.interfaces.player_availability_provider import (
    PlayerAvailabilityProvider,
)
from app.providers.schemas.player_availability import (
    ProviderAvailabilityBatch,
    ProviderPlayerAvailability,
)
from app.repositories.sqlalchemy.fixture_repository import SqlAlchemyFixtureRepository
from app.repositories.sqlalchemy.models import (
    CompetitionORM,
    PlayerAvailabilityObservationORM,
    TeamORM,
)
from app.repositories.sqlalchemy.player_availability_repository import (
    SqlAlchemyPlayerAvailabilityObservationRepository,
)
from app.repositories.sqlalchemy.player_repository import SqlAlchemyPlayerRepository
from app.repositories.sqlalchemy.reference_repositories import SqlAlchemyTeamRepository
from app.services.player_availability_ingestion import (
    PlayerAvailabilityIngestionService,
)

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

SOURCE = "api-football"
CAPTURED_AT = datetime(2026, 7, 31, 10, 0, tzinfo=UTC)


class FakePlayerAvailabilityProvider(PlayerAvailabilityProvider):
    def __init__(self, batch: ProviderAvailabilityBatch) -> None:
        self._batch = batch

    async def get_fixture_availability(
        self,
        *,
        fixture_external_id: str,
    ) -> ProviderAvailabilityBatch:
        return self._batch


def _batch(
    *,
    records: list[ProviderPlayerAvailability] | None = None,
    response_complete: bool = True,
) -> ProviderAvailabilityBatch:
    return ProviderAvailabilityBatch(
        source=SOURCE,
        fixture_external_id="fixture-1",
        captured_at=CAPTURED_AT,
        response_complete=response_complete,
        records=records or [],
        request_reference="/injuries?fixture=fixture-1",
    )


def _record(
    *,
    team_external_id: str = "team-home",
    player_external_id: str = "player-1",
    raw_status: str = "Missing Fixture",
) -> ProviderPlayerAvailability:
    return ProviderPlayerAvailability(
        team_external_id=team_external_id,
        player_external_id=player_external_id,
        player_name="Test Player",
        raw_status=raw_status,
        reason="Muscle injury",
    )


async def _seed_references(
    session: AsyncSession,
) -> tuple[Fixture, UUID, Player]:
    competition_id = uuid4()
    home_id = uuid4()
    away_id = uuid4()
    session.add_all(
        [
            CompetitionORM(id=competition_id, name="Test League", country="GB"),
            TeamORM(
                id=home_id,
                name="Home",
                external_source=SOURCE,
                external_id="team-home",
            ),
            TeamORM(
                id=away_id,
                name="Away",
                external_source=SOURCE,
                external_id="team-away",
            ),
        ],
    )
    await session.flush()

    fixture = await SqlAlchemyFixtureRepository(session).add(
        Fixture(
            competition_id=competition_id,
            home_team_id=home_id,
            away_team_id=away_id,
            kickoff=datetime(2026, 8, 1, 18, 0, tzinfo=UTC),
            external_source=SOURCE,
            external_id="fixture-1",
        ),
    )
    player = await SqlAlchemyPlayerRepository(session).add(
        Player(
            name="Test Player",
            position=PlayerPosition.FORWARD,
            team_id=home_id,
            external_source=SOURCE,
            external_id="player-1",
        ),
    )
    return fixture, home_id, player


def _service(
    session: AsyncSession,
    batch: ProviderAvailabilityBatch,
) -> PlayerAvailabilityIngestionService:
    return PlayerAvailabilityIngestionService(
        provider=FakePlayerAvailabilityProvider(batch),
        fixtures=SqlAlchemyFixtureRepository(session),
        teams=SqlAlchemyTeamRepository(session),
        players=SqlAlchemyPlayerRepository(session),
        observations=SqlAlchemyPlayerAvailabilityObservationRepository(session),
        source=SOURCE,
        evidence_level=EvidenceLevel.B,
    )


async def _count_observations(session: AsyncSession) -> int:
    return (
        await session.execute(
            select(func.count()).select_from(PlayerAvailabilityObservationORM),
        )
    ).scalar_one()


@pytest.mark.integration
async def test_sync_persists_verified_identity_and_status(
    db_session: AsyncSession,
) -> None:
    fixture, home_id, player = await _seed_references(db_session)
    report = await _service(db_session, _batch(records=[_record()])).sync_fixture(
        fixture_external_id="fixture-1",
    )

    assert report.records_received == 1
    assert report.records_created == 1
    assert report.duplicates_ignored == 0
    rows = await SqlAlchemyPlayerAvailabilityObservationRepository(
        db_session,
    ).list_by_fixture(fixture.id)
    assert len(rows) == 1
    observation = rows[0]
    assert observation.team_id == home_id
    assert observation.player_id == player.id
    assert observation.status is AvailabilityStatus.OUT
    assert observation.source.evidence_level is EvidenceLevel.B
    assert observation.source.reference == "/injuries?fixture=fixture-1"


@pytest.mark.integration
async def test_sync_is_idempotent_for_same_capture_batch(
    db_session: AsyncSession,
) -> None:
    await _seed_references(db_session)
    service = _service(db_session, _batch(records=[_record()]))

    first = await service.sync_fixture(fixture_external_id="fixture-1")
    second = await service.sync_fixture(fixture_external_id="fixture-1")

    assert first.records_created == 1
    assert second.records_created == 0
    assert second.duplicates_ignored == 1
    assert await _count_observations(db_session) == 1


@pytest.mark.integration
async def test_sync_rejects_incomplete_batch_without_writes(
    db_session: AsyncSession,
) -> None:
    await _seed_references(db_session)

    with pytest.raises(ExternalServiceError, match="response is incomplete"):
        await _service(
            db_session,
            _batch(records=[_record()], response_complete=False),
        ).sync_fixture(fixture_external_id="fixture-1")

    assert await _count_observations(db_session) == 0


@pytest.mark.integration
async def test_sync_rejects_missing_player_without_partial_writes(
    db_session: AsyncSession,
) -> None:
    await _seed_references(db_session)
    records = [_record(), _record(player_external_id="missing-player")]

    with pytest.raises(NotFoundError, match="missing-player"):
        await _service(db_session, _batch(records=records)).sync_fixture(
            fixture_external_id="fixture-1",
        )

    assert await _count_observations(db_session) == 0


@pytest.mark.integration
async def test_sync_rejects_team_outside_fixture_without_writes(
    db_session: AsyncSession,
) -> None:
    await _seed_references(db_session)
    db_session.add(
        TeamORM(
            id=uuid4(),
            name="Other",
            external_source=SOURCE,
            external_id="team-other",
        ),
    )
    await db_session.flush()

    with pytest.raises(ValidationError, match="does not belong to fixture"):
        await _service(
            db_session,
            _batch(records=[_record(team_external_id="team-other")]),
        ).sync_fixture(fixture_external_id="fixture-1")

    assert await _count_observations(db_session) == 0


@pytest.mark.integration
async def test_sync_preserves_unknown_status_without_guessing(
    db_session: AsyncSession,
) -> None:
    fixture, _, _ = await _seed_references(db_session)

    await _service(
        db_session,
        _batch(records=[_record(raw_status="Vendor-specific status")]),
    ).sync_fixture(fixture_external_id="fixture-1")

    observations = await SqlAlchemyPlayerAvailabilityObservationRepository(
        db_session,
    ).list_by_fixture(fixture.id)
    assert observations[0].status is AvailabilityStatus.UNKNOWN
